// Windows application-loopback bridge. COM and WASAPI stay on one MTA worker.
// API design follows Microsoft's ApplicationLoopback sample; no system fallback.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <mmdeviceapi.h>
#include <audiopolicy.h>
#include <wrl.h>
#include <wrl/implements.h>
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>

using namespace Microsoft::WRL;
constexpr UINT32 RATE = 48000, CHANNELS = 2, ALIGN = 4;

class Activation final : public RuntimeClass<RuntimeClassFlags<ClassicCom>,
                                            FtmBase, IActivateAudioInterfaceCompletionHandler> {
public:
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    HRESULT result = E_PENDING;
    ComPtr<IAudioClient> client;
    ~Activation() { if (event) CloseHandle(event); }
    STDMETHOD(ActivateCompleted)(IActivateAudioInterfaceAsyncOperation* operation) override {
        HRESULT activated = E_FAIL;
        ComPtr<IUnknown> value;
        result = operation->GetActivateResult(&activated, &value);
        if (SUCCEEDED(result)) result = activated;
        if (SUCCEEDED(result)) result = value.As(&client);
        SetEvent(event);
        return S_OK;
    }
};

class Capture {
public:
    HANDLE process = nullptr;
    HANDLE stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    std::thread worker;
    std::mutex mutex;
    std::condition_variable readyEvent;
    bool ready = false;
    HRESULT result = S_OK;
    // Bounded storage: fail explicitly if the consumer cannot keep up.
    std::vector<BYTE> ring = std::vector<BYTE>(RATE * ALIGN * 15);
    size_t head = 0, size = 0;
    DWORD pid = 0;

    ~Capture() { shutdown(); if (process) CloseHandle(process); if (stop) CloseHandle(stop); }
    void shutdown() {
        if (stop) SetEvent(stop);
        if (worker.joinable()) worker.join();
    }
    void publish(HRESULT hr) {
        std::lock_guard<std::mutex> lock(mutex);
        result = hr;
        ready = true;
        readyEvent.notify_all();
    }
    HRESULT append(const BYTE* data, UINT32 bytes, bool silence) {
        std::lock_guard<std::mutex> lock(mutex);
        if (size + bytes > ring.size()) return HRESULT_FROM_WIN32(ERROR_BUFFER_OVERFLOW);
        const size_t tail = (head + size) % ring.size();
        const size_t first = std::min<size_t>(bytes, ring.size() - tail);
        if (silence) {
            memset(ring.data() + tail, 0, first);
            memset(ring.data(), 0, bytes - first);
        } else {
            memcpy(ring.data() + tail, data, first);
            memcpy(ring.data(), data + first, bytes - first);
        }
        size += bytes;
        return S_OK;
    }
    HRESULT runAudio() {
        if (WaitForSingleObject(process, 0) != WAIT_TIMEOUT)
            return HRESULT_FROM_WIN32(ERROR_PROCESS_ABORTED);
        auto activated = Make<Activation>();
        if (!activated || !activated->event || !stop) return E_OUTOFMEMORY;
        AUDIOCLIENT_ACTIVATION_PARAMS params{};
        params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
        params.ProcessLoopbackParams.ProcessLoopbackMode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
        params.ProcessLoopbackParams.TargetProcessId = pid;
        PROPVARIANT prop{};
        prop.vt = VT_BLOB;
        prop.blob.cbSize = sizeof(params);
        prop.blob.pBlobData = reinterpret_cast<BYTE*>(&params);
        ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
        HRESULT hr = ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
            __uuidof(IAudioClient), &prop, activated.Get(), &operation);
        if (FAILED(hr)) return hr;
        HANDLE activationEvents[] = {stop, activated->event};
        DWORD wait = WaitForMultipleObjects(2, activationEvents, FALSE, 15000);
        if (wait != WAIT_OBJECT_0 + 1)
            return HRESULT_FROM_WIN32(wait == WAIT_TIMEOUT ? ERROR_TIMEOUT : ERROR_CANCELLED);
        if (FAILED(activated->result)) return activated->result;
        if (WaitForSingleObject(process, 0) != WAIT_TIMEOUT)
            return HRESULT_FROM_WIN32(ERROR_PROCESS_ABORTED);
        ComPtr<IAudioClient> client = activated->client;
        WAVEFORMATEX format{};
        format.wFormatTag = WAVE_FORMAT_PCM;
        format.nChannels = CHANNELS;
        format.nSamplesPerSec = RATE;
        format.wBitsPerSample = 16;
        format.nBlockAlign = ALIGN;
        format.nAvgBytesPerSec = RATE * ALIGN;
        hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
            AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM, 0, 0, &format, nullptr);
        if (FAILED(hr)) return hr;
        ComPtr<IAudioCaptureClient> capture;
        hr = client->GetService(IID_PPV_ARGS(&capture));
        if (FAILED(hr)) return hr;
        HANDLE audioEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (!audioEvent) return HRESULT_FROM_WIN32(GetLastError());
        hr = client->SetEventHandle(audioEvent);
        if (SUCCEEDED(hr)) hr = client->Start();
        if (FAILED(hr)) { CloseHandle(audioEvent); return hr; }
        publish(S_OK);
        HANDLE events[] = {stop, process, audioEvent};
        while (SUCCEEDED(hr)) {
            DWORD signaled = WaitForMultipleObjects(3, events, FALSE, 100);
            if (signaled == WAIT_OBJECT_0) break;
            if (signaled == WAIT_OBJECT_0 + 1) { hr = HRESULT_FROM_WIN32(ERROR_PROCESS_ABORTED); break; }
            if (signaled == WAIT_FAILED) { hr = HRESULT_FROM_WIN32(GetLastError()); break; }
            UINT32 available = 0;
            while (SUCCEEDED(hr = capture->GetNextPacketSize(&available)) && available) {
                BYTE* data = nullptr;
                UINT32 frames = 0;
                DWORD flags = 0;
                hr = capture->GetBuffer(&data, &frames, &flags, nullptr, nullptr);
                if (FAILED(hr)) break;
                hr = append(data, frames * ALIGN, (flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0);
                HRESULT released = capture->ReleaseBuffer(frames);
                if (SUCCEEDED(hr)) hr = released;
            }
        }
        client->Stop();
        capture.Reset();
        client.Reset();
        activated.Reset();
        CloseHandle(audioEvent);
        return hr;
    }
    void run() noexcept {
        HRESULT initialized = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        HRESULT hr = initialized;
        if (SUCCEEDED(initialized)) {
            try { hr = runAudio(); }
            catch (...) { hr = E_OUTOFMEMORY; }
            CoUninitialize();
        }
        publish(hr);
    }
};

extern "C" __declspec(dllexport) HRESULT __cdecl cn_create(DWORD pid, ULONGLONG created, Capture** output) noexcept {
    if (!output || !created || !pid) return E_INVALIDARG;
    *output = nullptr;
    try {
        Capture* value = new Capture;
        value->pid = pid;
        value->process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid);
        if (!value->process) { HRESULT hr = HRESULT_FROM_WIN32(GetLastError()); delete value; return hr; }
        FILETIME begin{}, end{}, kernel{}, user{};
        if (!GetProcessTimes(value->process, &begin, &end, &kernel, &user)) {
            HRESULT hr = HRESULT_FROM_WIN32(GetLastError()); delete value; return hr;
        }
        ULONGLONG actual = (static_cast<ULONGLONG>(begin.dwHighDateTime) << 32) | begin.dwLowDateTime;
        if (actual != created || WaitForSingleObject(value->process, 0) != WAIT_TIMEOUT) {
            delete value; return HRESULT_FROM_WIN32(ERROR_PROCESS_ABORTED);
        }
        *output = value;
        return S_OK;
    } catch (...) { return E_OUTOFMEMORY; }
}
extern "C" __declspec(dllexport) HRESULT __cdecl cn_start(Capture* value) noexcept {
    if (!value || value->worker.joinable()) return E_INVALIDARG;
    try {
        value->worker = std::thread([value] { value->run(); });
        std::unique_lock<std::mutex> lock(value->mutex);
        if (!value->readyEvent.wait_for(lock, std::chrono::seconds(18), [value] { return value->ready; }))
            return HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        return value->result;
    } catch (...) { return E_OUTOFMEMORY; }
}
extern "C" __declspec(dllexport) HRESULT __cdecl cn_available(Capture* value, UINT32* frames) noexcept {
    if (!value || !frames) return E_INVALIDARG;
    std::lock_guard<std::mutex> lock(value->mutex);
    *frames = static_cast<UINT32>(value->size / ALIGN);
    return value->result;
}
extern "C" __declspec(dllexport) HRESULT __cdecl cn_read(Capture* value, BYTE* output, UINT32 frames, UINT32* read) noexcept {
    if (!value || !output || !read) return E_INVALIDARG;
    std::lock_guard<std::mutex> lock(value->mutex);
    const size_t bytes = std::min<size_t>(frames * ALIGN, value->size);
    const size_t first = std::min<size_t>(bytes, value->ring.size() - value->head);
    memcpy(output, value->ring.data() + value->head, first);
    memcpy(output + first, value->ring.data(), bytes - first);
    value->head = (value->head + bytes) % value->ring.size();
    value->size -= bytes;
    *read = static_cast<UINT32>(bytes / ALIGN);
    return value->result;
}
extern "C" __declspec(dllexport) void __cdecl cn_stop(Capture* value) noexcept { if (value) value->shutdown(); }
extern "C" __declspec(dllexport) void __cdecl cn_close(Capture* value) noexcept { delete value; }

// Metadata only: identify the application's actual render processes before
// activation. Window processes may be several levels above their audio worker.
static HRESULT renderingPids(DWORD* pids, UINT32 capacity, UINT32* count) noexcept {
    if (!pids || !count) return E_INVALIDARG;
    *count = 0;
    HRESULT init = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(init) && init != RPC_E_CHANGED_MODE) return init;
    HRESULT result = S_OK;
    try {
        ComPtr<IMMDeviceEnumerator> enumerator;
        result = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, IID_PPV_ARGS(&enumerator));
        ComPtr<IMMDeviceCollection> endpoints;
        if (SUCCEEDED(result)) result = enumerator->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &endpoints);
        UINT devices = 0;
        if (SUCCEEDED(result)) result = endpoints->GetCount(&devices);
        for (UINT i = 0; SUCCEEDED(result) && i < devices; ++i) {
            ComPtr<IMMDevice> endpoint;
            ComPtr<IAudioSessionManager2> manager;
            ComPtr<IAudioSessionEnumerator> sessions;
            if (FAILED(endpoints->Item(i, &endpoint)) ||
                FAILED(endpoint->Activate(__uuidof(IAudioSessionManager2), CLSCTX_ALL, nullptr, &manager)) ||
                FAILED(manager->GetSessionEnumerator(&sessions))) continue;
            int n = 0;
            if (FAILED(sessions->GetCount(&n))) continue;
            for (int j = 0; j < n; ++j) {
                ComPtr<IAudioSessionControl> session;
                ComPtr<IAudioSessionControl2> details;
                AudioSessionState state{};
                DWORD pid = 0;
                if (FAILED(sessions->GetSession(j, &session)) || FAILED(session.As(&details)) ||
                    FAILED(session->GetState(&state)) || state == AudioSessionStateExpired ||
                    details->GetProcessId(&pid) != S_OK || !pid) continue;
                bool found = false;
                for (UINT32 k = 0; k < *count; ++k) if (pids[k] == pid) found = true;
                if (!found) {
                    if (*count >= capacity) { result = HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER); break; }
                    pids[(*count)++] = pid;
                }
            }
        }
    } catch (...) { result = E_OUTOFMEMORY; }
    if (SUCCEEDED(init)) CoUninitialize();
    return result;
}
extern "C" __declspec(dllexport) HRESULT __cdecl cn_rendering_pids(DWORD* pids, UINT32 capacity, UINT32* count) noexcept {
    // The Qt GUI uses STA. Session metadata is queried in a fresh MTA, without
    // changing the caller's apartment or depending on GUI message dispatch.
    try {
        HRESULT result = E_FAIL;
        std::thread query([&] { result = renderingPids(pids, capacity, count); });
        query.join();
        return result;
    } catch (...) { return E_OUTOFMEMORY; }
}
