"""Create an offline Windows setup executable and integrity manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

PROJECT = Path(__file__).resolve().parent.parent
VERSION = '0.1.3'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    options = parser.parse_args()
    root = options.root.resolve()
    bundle = root / 'dist' / 'CallNotes'
    release = root / 'release'
    release.mkdir(exist_ok=True)
    compiler = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')

    def compile(destination, extra):
        args = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/utf8output',
                '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Core.dll',
                '/r:System.IO.Compression.dll', '/r:System.IO.Compression.FileSystem.dll', '/r:Microsoft.CSharp.dll',
                '/win32icon:' + str(PROJECT / 'assets' / 'arknights-bighead-v1.ico'),
                '/out:' + str(destination), *extra, str(PROJECT / 'packaging' / 'Installer.cs')]
        subprocess.run(args, check=True)

    compile(bundle / 'UninstallCallNotes.exe', ['/define:UNINSTALL'])
    archive = release / 'payload.zip'
    files = sorted(p for p in bundle.rglob('*') if p.is_file())
    forbidden = {'auth.json', 'records.sqlite3', 'settings.json'}
    if any(p.name in forbidden or p.suffix in ('.part', '.pcm') for p in files):
        raise RuntimeError('Personal/unfinished files found in bundle')
    print('Compressing', len(files), 'runtime/model files', flush=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as target:
        for file in files:
            target.write(file, file.relative_to(bundle).as_posix())
    if archive.stat().st_size >= 2_000_000_000:
        raise RuntimeError('Setup payload exceeds the planned single-file release size')
    setup = release / f'CallNotes-Setup-{VERSION}-Windows-x64.exe'
    print('Embedding', archive.stat().st_size, 'bytes', flush=True)
    compile(setup, ['/resource:' + str(archive) + ',payload.zip'])
    with setup.open('rb') as source:
        sha = hashlib.file_digest(source, 'sha256').hexdigest()
    (release / 'SHA256SUMS.txt').write_text(f'{sha}  {setup.name}\n', encoding='utf-8')
    metadata = {'version': VERSION, 'setup': setup.name, 'size': setup.stat().st_size,
                'sha256': sha, 'files': len(files), 'installed_bytes': sum(p.stat().st_size for p in files)}
    (release / 'build-info.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps(metadata), flush=True)


if __name__ == '__main__':
    main()
