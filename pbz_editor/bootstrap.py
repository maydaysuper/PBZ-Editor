"""Prepare user-local catalog/regions directly from an installed game."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import uuid
from .catalog_builder import build_catalog
from .region_builder import build_regions
from .resources_reader import IoStore, localization
from .pak_reader import PakReader

SCHEMA = 2
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
    root = Path(game)/'ProjectPBZ/Content/Paks'
    with (root/'ProjectPBZ-Windows.pak').open('rb') as stream:
        stream.seek(-221, 2)
        footer = stream.read(221)
    return sha((root/'ProjectPBZ-Windows.utoc').read_bytes() + footer +
               json.dumps(profile, sort_keys=True).encode())


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
    from .core import game_key, OodleCodec
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
    game_key(game)
    profile = json.loads(profile_path().read_text(encoding='utf8'))
    codec = OodleCodec()
    step('正在读取本机游戏文本…')
    pak = PakReader(game/'ProjectPBZ/Content/Paks/ProjectPBZ-Windows.pak', codec)
    locs = {lang: localization(pak.read_file('ProjectPBZ/Content/Localization/Game/'+lang+'/Game.locres'))
            for lang in ('zh-Hans', 'en-US')}
    step('正在生成物品目录…')
    store = IoStore(game/'ProjectPBZ/Content/Paks/ProjectPBZ-Windows.utoc', codec)
    catalog = build_catalog(store, locs['zh-Hans'], locs['en-US'], profile['resources'])
    if len(catalog['items']) != profile['catalog_entries'] or catalog['editable_entries'] != profile['editable_entries']:
        raise ValueError('物品目录未通过当前版本的数量校验。')
    step('正在生成地图分类…')
    lookup = {p.casefold(): p for p in store.files}
    assets = {}
    for name, path in MAP_ASSETS.items():
        data = store.read_file(lookup[path.casefold()])
        if sha(data) != profile['resources'][path]:
            raise ValueError('地图数据与已适配版本不一致。')
        assets[name] = data
    groups = pak.read_file('ProjectPBZ/Content/JavaScript/Library/MapAreaGroups.js')
    if sha(groups) != profile['map_groups_sha256']:
        raise ValueError('地图分组与已适配版本不一致。')
    regions = build_regions(assets, locs['zh-Hans'], catalog, groups.decode('utf8'), locs['en-US'])
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
    manifest = {'schema': SCHEMA, 'source': signature(game, profile), 'files': files}
    temporary = target/('.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(manifest), encoding='utf8')
        os.replace(temporary, target/'manifest.json')
    finally:
        if temporary.exists():
            temporary.unlink()
    return target/'catalog.json'
