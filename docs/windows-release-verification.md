# Windows 0.1.3 verification

- 107 source regression tests passed in the isolated distribution environment, including Qt 6.8.3 and the optional clustering dependencies.
- Frozen UI and worker binaries were exercised with isolated data/cache/account directories and only Windows System32 on their child PATH. No external Python or CUDA was available to those processes.
- Day and night startup screenshots were inspected; the native application icon and process-capture component are included.
- The actual setup executable installed the application into a dedicated test directory. The installed binaries then passed the same startup, audio import and CPU transcription checks.
- Synthetic test speech (“The budget is one thousand dollars. Send the draft on Friday.”) produced “the budget is $1,000” and “Send the draft on Friday”. This is a packaging/function check, not an accuracy or speed benchmark.
- The test installation was uninstalled using its own uninstaller; a separate user-data sentinel was preserved.
- Installer checksum and byte sizes are recorded in `windows-build-info.json` and the release `SHA256SUMS.txt`.

This does not establish performance on every audio device or completion of a two-hour real-call endurance test. CPU caption latency varies with the computer.
