$ErrorActionPreference = 'Stop'
$compiler = 'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.50.35717\bin\Hostx64\x64\cl.exe'
$msvc = Split-Path (Split-Path (Split-Path (Split-Path $compiler)))
$sdk = 'C:\Program Files (x86)\Windows Kits\10'
$version = '10.0.26100.0'
$env:INCLUDE = "$msvc\include;$sdk\Include\$version\ucrt;$sdk\Include\$version\shared;$sdk\Include\$version\um;$sdk\Include\$version\winrt"
$env:LIB = "$msvc\lib\x64;$sdk\Lib\$version\ucrt\x64;$sdk\Lib\$version\um\x64"
Push-Location $PSScriptRoot
try {
    & $compiler /nologo /utf-8 /std:c++17 /EHsc /O2 /MT /LD /W4 /D_WIN32_WINNT=0x0A00 process_loopback.cpp /link /OUT:process_loopback.dll Ole32.lib Mmdevapi.lib RuntimeObject.lib
    if ($LASTEXITCODE -ne 0) { throw 'Native loopback compilation failed' }
} finally { Pop-Location }
