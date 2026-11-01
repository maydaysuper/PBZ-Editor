from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import hashlib
import uuid


def project_root() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def local_dir() -> Path:
    override = os.environ.get('PBZ_EDITOR_DATA_DIR')
    frozen = bool(getattr(sys, 'frozen', False))
    legacy = project_root() / '.local'
    if override:
        path = Path(override).expanduser().resolve()
    elif not frozen or (project_root()/'portable.flag').is_file():
        path = legacy
    else:
        path = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))/'PBZ-Editor'
    path.mkdir(parents=True, exist_ok=True)
    if frozen and not override and path.resolve() != legacy.resolve():
        migrate_legacy(legacy, path)
    return path


def catalog_path() -> Path:
    return local_dir() / 'data/catalog.json'


def migrate_legacy(legacy, target):
    """Copy verified old settings/backups/icons, without overwriting either side."""
    legacy, target = Path(legacy), Path(target)
    marker = target/'legacy-migration.json'
    if marker.exists() or not legacy.is_dir():
        return
    report = {'source': str(legacy), 'copied': [], 'conflicts': [], 'errors': []}
    candidates = [legacy/'settings.json']
    for folder in ('backups', 'icons'):
        if (legacy/folder).is_dir():
            candidates.extend(p for p in (legacy/folder).rglob('*') if p.is_file())
    for source in candidates:
        if not source.is_file() or not source.resolve().is_relative_to(legacy.resolve()):
            continue
        relative = source.relative_to(legacy)
        destination = target/relative
        if not destination.resolve().is_relative_to(target.resolve()):
            report['errors'].append(str(relative))
            continue
        try:
            data = source.read_bytes()
            if destination.exists():
                if destination.read_bytes() != data:
                    report['conflicts'].append(str(relative))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(data).digest():
                raise OSError('Migration copy verification failed')
            report['copied'].append(str(relative))
        except FileExistsError:
            report['conflicts'].append(str(relative))
        except OSError as error:
            report['errors'].append(str(relative) + ': ' + str(error))
    temporary = target/('.migration-'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(temporary, marker)


def load_settings() -> dict:
    try:
        return json.loads((local_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    target = local_dir() / "settings.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)


def steam_libraries() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
    roots.extend([Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam"])
    libraries = roots.copy()
    for root in roots:
        try:
            text = (root / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8")
            libraries.extend(Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s*"([^\"]+)"', text))
        except OSError:
            continue
    return list(dict.fromkeys(libraries))


def resolve_game(path):
    path = Path(path).resolve()
    if path.is_file():
        path = path.parent
    for candidate in [path, *list(path.parents)[:3]]:
        if (candidate/'platform/steam/PBZ.exe').is_file():
            return candidate
    return None


def find_game() -> Path | None:
    for library in steam_libraries():
        manifest = library / "steamapps/appmanifest_4115450.acf"
        try:
            text = manifest.read_text(encoding="utf-8")
            match = re.search(r'"installdir"\s*"([^\"]+)"', text)
            if match:
                game = library / "steamapps/common" / match.group(1)
                if (game / "platform/steam/PBZ.exe").is_file():
                    return game
        except OSError:
            continue
    return None


def find_saves(game_dir: str | Path) -> list[Path]:
    root = Path(game_dir) / "ProjectPBZ/Saved/SaveGames/Steam"
    return sorted(root.glob("*/PBZSaveGame.sav"), key=lambda p: p.stat().st_mtime, reverse=True)
