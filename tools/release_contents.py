"""Build explicit public artifacts and reject private/extracted runtime content."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOTS = [
    'pbz_editor', 'tests', 'tools', 'legal', 'native/src', 'native/vendor',
    'native/Cargo.toml', 'native/Cargo.lock', 'data/profile.json', 'launcher.py',
    'pyproject.toml', 'requirements.txt', 'requirements-dev.txt', 'requirements-lock.txt',
    'README.md', 'README.en.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', '.gitignore', '.github',
    'docs/quick-start.txt', 'docs/quick-start.en.txt',
    'docs/validation.zh-CN.md', 'docs/validation.en.md',
    'docs/building.zh-CN.md', 'docs/building.en.md',
    'docs/releases/0.2.0.zh-CN.md', 'docs/releases/0.2.0.en.md', 'docs/releases/0.2.0.md',
]


def public_files():
    """Shared allowlist for the source archive and clean GitHub staging tree."""
    for relative in PUBLIC_ROOTS:
        source = ROOT/relative
        for path in sorted(source.rglob('*')) if source.is_dir() else [source]:
            if not path.is_file() or any(x in path.relative_to(ROOT).parts for x in ('__pycache__', 'target', '.git')):
                continue
            if path.suffix.lower() in {'.dll', '.sav', '.chunk', '.log', '.pyc'}:
                raise SystemExit('Unexpected source file: '+str(path))
            yield path


def validation_matches(project, codec, validation):
    implementation = ['pbz_editor/core.py', 'pbz_editor/compression.py', 'native/src/lib.rs']
    return (all(hashlib.sha256((project/name).read_bytes()).hexdigest() ==
                validation.get('implementation_sha256', {}).get(name) for name in implementation)
            and hashlib.sha256(codec.read_bytes()).hexdigest() == validation.get('codec_sha256'))


def unexpected_runtime_files(release):
    bad = []
    for file in release.rglob('*'):
        if not file.is_file():
            continue
        p = file.relative_to(release)
        if (p.suffix.lower() in {'.sav', '.ucas', '.utoc', '.pak', '.chunk'} or
            p.name.lower().startswith('oo2core') or p.name in {'catalog.json', 'regions.json', 'settings.json', 'portable.flag'} or
            any(x in p.parts for x in ('.local', '.venv')) or
            any(x in p.name.lower() for x in ('webengine', 'qt6quick', 'qt6qml', 'qt6pdf'))):
            bad.append(str(p))
    return bad


def finalize(release):
    licenses = release/'LICENSES'
    licenses.mkdir(exist_ok=True)
    records = []
    for name in ('PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'cryptography', 'cffi', 'pycparser', 'pyinstaller', 'texture2ddecoder'):
        dist = metadata.distribution(name)
        found = []
        for member in dist.files or []:
            if any(x in member.name.lower() for x in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(member))
                if source.is_file():
                    dest = licenses/name/member.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                    found.append(str(dest.relative_to(release)))
        records.append({'name': name, 'version': dist.version, 'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'), 'license_files': found})
    shutil.copy2(Path(sys.base_prefix)/'LICENSE.txt', licenses/'Python-LICENSE.txt')
    for source in (ROOT/'legal').glob('*.txt'):
        shutil.copy2(source, licenses/source.name)
    native_notices = ROOT/'.local/native/LICENSES'
    if not native_notices.is_dir():
        raise SystemExit('Build native notices with build_codec.ps1 before packaging')
    shutil.copytree(native_notices, licenses/'native', dirs_exist_ok=True)
    bad = unexpected_runtime_files(release)
    if bad:
        raise SystemExit('Unexpected release files: '+repr(bad))
    codec = list(release.rglob('pbz_codec.dll'))
    if len(codec) != 1 or not list(release.rglob('qwindows.dll')):
        raise SystemExit('Native codec or Qt platform plugin is missing')
    manifest = {'version': '0.2.0', 'python': sys.version.split()[0], 'dependencies': records,
                'decoder': json.loads((ROOT/'.local/native/manifest.json').read_text(encoding='utf-8-sig')),
                'runtime_game_verification': 'NOT_TESTED',
                'executable_sha256': hashlib.sha256((release/'PBZ-Editor.exe').read_bytes()).hexdigest()}
    validation_path = ROOT/'.local/validation.json'
    if validation_path.is_file():
        validation = json.loads(validation_path.read_text(encoding='utf8'))
        matches = validation_matches(ROOT, codec[0], validation)
        if matches and validation.get('status') == 'PASS':
            manifest['runtime_game_verification'] = 'FIVE_SAMPLE_ROUNDTRIP_PASSED'
            manifest['runtime_validation_scope'] = validation['scope']
        manifest['runtime_validation_fingerprints_match'] = matches
    (release/'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')


def source_zip(destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in public_files():
            archive.write(path, 'PBZ-Editor/'+path.relative_to(ROOT).as_posix())


def native_notices(cargo_home, rust_sysroot):
    root = ROOT/'.local/native/LICENSES'
    root.mkdir(parents=True, exist_ok=True)
    lock = tomllib.loads((ROOT/'native/Cargo.lock').read_text(encoding='utf8'))
    runtime = {(line.split()[0], line.split()[1].removeprefix('v')) for line in
               (ROOT/'.local/native/packages.txt').read_text(encoding='utf-8-sig').splitlines() if len(line.split()) >= 2}
    records = []
    for package in lock['package']:
        name, version = package['name'], package['version']
        if name == 'pbz-codec' or (name, version) not in runtime:
            continue
        matches = list((cargo_home/'registry/src').glob('*/'+name+'-'+version))
        source = ROOT/'native/vendor/oozextract' if name == 'oozextract' else (matches[0] if matches else None)
        if source is None:
            raise SystemExit('Missing dependency sources: '+name)
        manifest = tomllib.loads((source/'Cargo.toml').read_text(encoding='utf8'))['package']
        found = []
        for file in source.glob('*'):
            if file.is_file() and any(t in file.name.lower() for t in ('license', 'copying', 'notice')):
                target = root/(name+'-'+version)/file.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
                found.append(str(target.relative_to(root)))
        records.append({'name': name, 'version': version, 'license': manifest.get('license'), 'files': found})
    shutil.copy2(ROOT/'native/vendor/NOTICE.md', root/'oozextract-NOTICE.md')
    # Static Rust standard-library notices are installed by rustup's compiler component.
    rust_licenses = rust_sysroot/'share/doc/rust'
    for file in rust_licenses.glob('*'):
        if file.is_file() and ('license' in file.name.lower() or 'copyright' in file.name.lower()):
            shutil.copy2(file, root/file.name)
    (root/'dependencies.json').write_text(json.dumps(records, indent=2), encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['finalize', 'source', 'native-notices'])
    parser.add_argument('path', type=Path)
    parser.add_argument('--rust-sysroot', type=Path)
    args = parser.parse_args()
    if args.action == 'finalize': finalize(args.path)
    elif args.action == 'source': source_zip(args.path)
    else: native_notices(args.path, args.rust_sysroot)
