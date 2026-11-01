"""Prepare user-local catalog/regions directly from an installed game."""
import hashlib
import json
import struct
import os
from pathlib import Path
import sys
import threading
import uuid
from .catalog_builder import build_catalog
from .region_builder import build_regions
from .resources_reader import IoStore, localization
from .pak_reader import PakReader
from .compatibility import executable_path
from .catalog import Catalog
from .catalog_builder import TABLES
from .regions import RegionIndex
from . import __version__

SCHEMA = 3
MAP_ASSETS = {
    'DT_MapItemData': 'ProjectPBZ/Content/PBZContent/Maps/DT_MapItemData.uasset',
    'DT_MapData': 'ProjectPBZ/Content/PBZContent/DataTable/UI/DT_MapData.uasset',
    'DA_MapIDAsset': 'ProjectPBZ/Content/PBZContent/UI/InGame/Map/DA_MapIDAsset.uasset',
}


def profile_path():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))/'data/profile.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def signature(game, profile):
    game = Path(game)
    root = game/'ProjectPBZ/Content/Paks'
    files = [executable_path(game), *sorted(root.glob('ProjectPBZ-Windows*'))]
    identities = [(p.relative_to(game).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in files if p.is_file()]
    # This invalidates local caches on normal game updates. Resource contents
    # are independently parsed/checked when generating a new cache.
    return sha(json.dumps([SCHEMA, __version__, identities, profile], sort_keys=True).encode())


def prepared_catalog(game, root):
    root = Path(root)/'data'
    try:
        profile = json.loads(profile_path().read_text(encoding='utf8'))
        manifest = json.loads((root/'manifest.json').read_text(encoding='utf8'))
        if manifest.get('schema') != SCHEMA or manifest.get('source') != signature(game, profile):
            return None
        for name in ('catalog.json', 'regions.json'):
            if sha((root/name).read_bytes()) != manifest['files'][name]:
                return None
        return root/'catalog.json'
    except (OSError, ValueError, KeyError):
        return None


def prepare_data(game, root, progress=None, cancel=None):
    from .core import OodleCodec
    game, root = Path(game), Path(root)
    cancel = cancel or threading.Event()
    def step(text):
        if cancel.is_set():
            raise InterruptedError('已取消准备')
        if progress:
            progress(text)
    cached = prepared_catalog(game, root)
    if cached:
        return cached
    step('正在核对游戏版本…')
    executable_path(game)
    profile = json.loads(profile_path().read_text(encoding='utf8'))
    initial_signature = signature(game, profile)
    codec = OodleCodec()
    warnings = []
    step('正在读取本机游戏文本…')
    pak = None
    locs = {}
    try:
        pak = PakReader(game/'ProjectPBZ/Content/Paks/ProjectPBZ-Windows.pak', codec)
    except (ValueError, OSError, struct.error):
        warnings.append('游戏文字暂不可用，物品将显示原始名称。')
    for lang in ('zh-Hans', 'en-US'):
        try:
            locs[lang] = localization(pak.read_file('ProjectPBZ/Content/Localization/Game/'+lang+'/Game.locres')) if pak else {}
        except (ValueError, KeyError, OSError, struct.error, UnicodeError):
            locs[lang] = {}
            warnings.append('部分游戏文字暂不可用，保留原始名称。')
    step('正在生成物品目录…')
    try:
        store = IoStore(game/'ProjectPBZ/Content/Paks/ProjectPBZ-Windows.utoc', codec)
    except (ValueError, IndexError, KeyError, struct.error, OSError):
        store = None
    if store:
        catalog = build_catalog(store, locs['zh-Hans'], locs['en-US'], profile['resources'],
                                profile.get('item_property_schemas'), tolerant=True)
    else:
        catalog = {'items': [], 'sources': [], 'available_categories': [],
                   'unavailable_categories': {category: 'Game resource format unavailable' for category, _ in TABLES.values()}}
    step('正在生成地图分类…')
    try:
        lookup = {p.casefold(): p for p in store.files}
        assets = {name: store.read_file(lookup[path.casefold()]) for name, path in MAP_ASSETS.items()}
        groups = pak.read_file('ProjectPBZ/Content/JavaScript/Library/MapAreaGroups.js')
        regions = build_regions(assets, locs['zh-Hans'], catalog, groups.decode('utf8'), locs['en-US'])
        RegionIndex(regions)
    except (ValueError, AssertionError, AttributeError, IndexError, KeyError, OSError, struct.error, UnicodeError):
        regions = {'schema_version': 1, 'regions': [], 'items': []}
        warnings.append('地图分类暂不可用，仍可搜索和按类别查找物品。')
    Catalog(catalog['items'])
    catalog['compatibility_warnings'] = list(dict.fromkeys(warnings))
    if signature(game, profile) != initial_signature:
        raise ValueError('游戏文件在检查期间发生变化，请重新打开。')
    step('正在校验并保存本地数据…')
    # Publish the ready manifest last. A cancelled/interrupted preparation is
    # not mistaken for a valid cache on the next run.
    target = root/'data'
    target.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, value in [('catalog.json', catalog), ('regions.json', regions)]:
        raw = json.dumps(value, ensure_ascii=False, indent=2).encode('utf8')
        files[name] = sha(raw)
        temporary = target/('.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_bytes(raw)
            os.replace(temporary, target/name)
        finally:
            if temporary.exists():
                temporary.unlink()
    manifest = {'schema': SCHEMA, 'source': initial_signature, 'files': files}
    temporary = target/('.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(manifest), encoding='utf8')
        os.replace(temporary, target/'manifest.json')
    finally:
        if temporary.exists():
            temporary.unlink()
    return target/'catalog.json'
