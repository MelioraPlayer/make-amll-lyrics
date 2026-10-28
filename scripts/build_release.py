"""Build a deterministic source archive using an explicit distribution allowlist."""
import argparse
import hashlib
import json
import re
import tomllib
import zipfile
from pathlib import Path

ROOT_FILES = ('SKILL.md', 'README.md', 'LICENSE', 'pyproject.toml', 'uv.lock', '.gitignore', '.gitattributes')
FOLDER_SUFFIXES = {'agents': {'.yaml'}, 'references': {'.md'}, 'scripts': {'.py'},
                   'examples': {'.json'}, 'requirements': {'.txt'}, 'tests': {'.py'},
                   '.github/workflows': {'.yml', '.yaml'}}


def collect(root):
    files = [root / name for name in ROOT_FILES]
    for folder, suffixes in FOLDER_SUFFIXES.items():
        files.extend(p for p in (root / folder).glob('*') if p.suffix in suffixes)
    for path in files:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Missing or unsafe release input: {path}')
        payload = path.read_bytes()
        if len(payload) > 1024 * 1024:
            raise ValueError(f'Unexpectedly large source file: {path.name}')
        payload.decode('utf-8')
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def build(root, out, force=False):
    root, out = Path(root).resolve(), Path(out).resolve()
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+.][A-Za-z0-9.]+)?', version):
        raise ValueError('Unsafe version string')
    files = collect(root)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f'amll-lyrics-{version}.zip'
    checksum = archive.with_suffix('.zip.sha256')
    manifest_path = out / f'amll-lyrics-{version}.manifest.json'
    if not force and any(p.exists() for p in (archive, checksum, manifest_path)):
        raise FileExistsError('Release outputs exist; use another directory or explicit --force')
    entries = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
    manifest = {'name': 'amll-lyrics', 'version': version, 'license': 'MIT',
                'files': [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in entries.items()]}
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, data in entries.items():
            info = zipfile.ZipInfo('amll-lyrics/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'archive': str(archive), 'sha256': digest, 'files': len(entries),
            'manifest': str(manifest_path), 'bytes': archive.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('dist'))
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.out, args.force), indent=2))


if __name__ == '__main__':
    main()
