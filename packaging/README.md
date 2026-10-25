# Windows distribution

The release has two isolated frozen executables: the Qt application and a console worker for media import, transcription and optional speaker grouping. The caller hides worker consoles and reads their JSON output. An offline large-v3-turbo model and speaker models are included; no account credentials or recordings are staged.

The setup is a per-user .NET Framework installer with an embedded ZIP, desktop/start menu shortcuts, an uninstall entry, path traversal checks and a running-process check. It never force-stops a recording. Uninstall removes files listed in its installation manifest and leaves user data outside the program directory untouched.

## Build

Use a dedicated build directory and Python 3.11 environment. Install `requirements-ui.txt` and `requirements-worker.txt` there. Build `callnotes/native/process_loopback.cpp` using `build.ps1` (adjust compiler/SDK paths to the build machine).

```powershell
python packaging/download_assets.py --root D:\CallNotesBuild
python packaging/build_windows.py --root D:\CallNotesBuild --ui-python .venv\Scripts\python.exe --worker-python .venv\Scripts\python.exe
python packaging/package_release.py --root D:\CallNotesBuild
```

`build_windows.py` also copies `packaging/notices` into the release. When changing dependency versions, update the corresponding distribution notices and component inventory. The current release inventories are included in that directory. Assets download from pinned upstream revisions; large model and Codex archive checksums are verified.

## Verify

```powershell
python packaging/verify_windows.py --app D:\CallNotesBuild\dist\CallNotes --out D:\CallNotesBuild\verification --speech tests/fixtures/meeting-en.wav
```

Verification isolates the data, cache and Codex home. Child application processes receive only Windows System32 on PATH and force CPU inference. Both global themes, bundled worker dependencies, audio import and real speech transcription are checked. `--test-install DIRECTORY` installs without shortcuts or uninstall registry entries for CI. An existing unrelated non-empty directory is rejected.

The manually dispatched `Windows installer smoke test` workflow repeats this using the release installer on a clean Windows runner.
