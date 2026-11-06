"""Synthetic executables and saves exercise compatibility without player data."""
import hashlib
import json
from pathlib import Path
import struct
import zlib

import pytest
from pbz_editor import compatibility as compat
from pbz_editor import core, bootstrap, catalog_builder


def pe_image(material, key_offset=1024, anchor=True):
    raw = bytearray(8192)
    raw[:2] = b'MZ'
    struct.pack_into('<I', raw, 60, 128)
    raw[128:132] = b'PE\0\0'
    struct.pack_into('<HH', raw, 132, 0x8664, 1)
    struct.pack_into('<H', raw, 148, 112)
    struct.pack_into('<H', raw, 152, 0x20b)
    raw[264:272] = b'.rdata\0\0'
    struct.pack_into('<IIII', raw, 272, 7680, 4096, 7680, 512)
    if anchor:
        start = key_offset-len(compat.KEY_ANCHOR)-8
        raw[start:start+len(compat.KEY_ANCHOR)] = compat.KEY_ANCHOR
    raw[key_offset:key_offset+64] = material
    return bytes(raw)


def save_bytes(key, version=3, inventory=None):
    container = core.PBZSContainer(key)
    inventory = inventory if inventory is not None else {
        'InventoryInfos': {'Material': {'CurrentAmount': 3, 'MaxAmount': 3}},
        'AccessoryInfos': {},
    }
    entries = []
    for name, value in [('__Advanced__', {'LastContinueSlot': 'ManualSave0'}),
                         ('ManualSave0', {'Inventory': inventory})]:
        raw = json.dumps(value).encode()
        entries.append({'name': name, 'json': value, 'raw': raw, 'algorithm': 1,
                        'flag': 1, 'block': container.seal(zlib.compress(raw))})
    source = container.encode(entries, {})
    if version != 3:
        size = struct.unpack_from('<I', source, 8)[0]
        directory = bytearray(container.unseal(source[12:12+size]))
        directory[4] = version
        source = source[:4]+bytes([version])+source[5:12]+container.seal(directory)+source[12+size:]
    return source


@pytest.fixture
def installation(tmp_path, monkeypatch):
    key = bytes(range(32))
    left = bytes([0x55])*32
    material = left+bytes(a ^ b for a, b in zip(left, key))
    image = pe_image(material)
    game = tmp_path/'game'
    exe = game/'platform/steam/PBZ.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(image)
    profile = {'build': 'synthetic', 'exe_sha256': hashlib.sha256(image).hexdigest(),
               'save_key': {'reference_offset': 1024, 'material_sha256': hashlib.sha256(material).hexdigest()}}
    monkeypatch.setattr(compat, 'load_profile', lambda: profile)
    catalog = tmp_path/'catalog.json'
    catalog.write_text(json.dumps({'items': [], 'available_categories': list(core.CATEGORY_FIELDS)+['appearance']}))
    monkeypatch.setattr(bootstrap, 'prepare_data', lambda *_: catalog)
    monkeypatch.setattr(core, 'OodleCodec', lambda *_: None)
    monkeypatch.setattr(core, '_game_processes', lambda *_: [])
    return game, exe, profile, material, key, catalog


@pytest.mark.parametrize('offset', [1024, 3072])
def test_changed_executable_and_relocated_key_are_authenticated(installation, offset):
    game, exe, _, material, key, _ = installation
    raw = bytearray(pe_image(material, offset))
    raw[600] = 1  # A harmless difference must not require an exact EXE hash.
    exe.write_bytes(raw)
    found, report = compat.resolve_key(game, save_bytes(key))
    assert found == key and report['key_authenticated']
    assert report['executable'] == 'changed'
    assert report['key_locator'] == ('offset' if offset == 1024 else 'anchor')


def test_rotated_key_uses_save_authentication_not_old_material_hash(installation):
    game, exe, _, _, old_key, _ = installation
    new_key = bytes([0xa6])*32
    material = bytes(32)+new_key
    exe.write_bytes(pe_image(material, 3072))
    found, _ = compat.resolve_key(game, save_bytes(new_key))
    assert found == new_key
    with pytest.raises(core.EditorError, match='无法验证'):
        compat.resolve_key(game, save_bytes(old_key))
    with pytest.raises(core.EditorError):
        compat.resolve_key(game)  # No save: never guess an unverified key.


def test_key_in_overlay_is_not_accepted(installation):
    game, exe, _, material, key, _ = installation
    raw = bytearray(pe_image(bytes(64)))
    raw += compat.KEY_ANCHOR+bytes(8)+material
    exe.write_bytes(raw)
    with pytest.raises(core.EditorError):
        compat.resolve_key(game, save_bytes(key))


def open_synthetic(tmp_path, installation, **kwargs):
    game, _, _, _, key, _ = installation
    source = tmp_path/'synthetic.sav'
    source.write_bytes(save_bytes(key, **kwargs))
    return core.SaveSession.open(source, game, local_dir=tmp_path/'state')


def test_new_container_version_is_viewable_but_cannot_be_edited(tmp_path, installation):
    session = open_synthetic(tmp_path, installation, version=4)
    before = session.source
    assert session.read_only and session.inventory('ManualSave0')
    assert not session.can_edit('ManualSave0', 'goods')
    op = {'action': 'set', 'category': 'goods', 'id': 'Material', 'quantity': 5}
    with pytest.raises(core.EditorError, match='仅可查看'):
        session.apply('ManualSave0', [op])
    with pytest.raises(core.EditorError, match='仅可查看'):
        session.apply('ManualSave0', [{'action': 'clear_soul', 'category': 'weapon', 'id': 'Blade'}])
    assert session.save_path.read_bytes() == before and not session.backup_dir.exists()


def test_one_malformed_category_does_not_block_other_categories(tmp_path, installation):
    inventory = {'InventoryInfos': {'Material': {'CurrentAmount': 'new-format', 'MaxAmount': 3}},
                 'AccessoryInfos': {}}
    session = open_synthetic(tmp_path, installation, inventory=inventory)
    assert not session.can_edit('ManualSave0', 'goods')
    assert session.can_edit('ManualSave0', 'accessory')
    session.apply('ManualSave0', [{'action': 'add', 'category': 'accessory', 'id': 'AC_Test'}])
    assert session._entry('ManualSave0')['json']['Inventory']['InventoryInfos']['Material']['CurrentAmount'] == 'new-format'


def test_missing_new_build_fields_are_read_only_and_advanced_cannot_bypass(tmp_path, installation):
    _, exe, _, _, _, catalog = installation
    exe.write_bytes(exe.read_bytes()+b'new-build')
    session = open_synthetic(tmp_path, installation, inventory={'InventoryInfos': {}})
    assert session.can_edit('ManualSave0', 'goods')
    assert not session.can_edit('ManualSave0', 'accessory')
    with pytest.raises(core.EditorError, match='字段缺失'):
        session.preview('ManualSave0', [{'action': 'add', 'category': 'accessory', 'id': 'AC_Test'}])
    metadata = json.loads(catalog.read_text())
    metadata['available_categories'].remove('goods')
    catalog.write_text(json.dumps(metadata))
    session = core.SaveSession.open(session.save_path, session.game_dir, local_dir=tmp_path/'state2')
    with pytest.raises(core.EditorError, match='物品数据暂不兼容'):
        session.export('ManualSave0', [{'action': 'add', 'category': 'goods', 'id': 'Bypass'}], tmp_path/'out.sav')
    assert not (tmp_path/'out.sav').exists()


def test_game_update_after_open_blocks_save_before_backup(tmp_path, installation):
    session = open_synthetic(tmp_path, installation)
    _, exe, _, _, _, _ = installation
    before = session.source
    exe.write_bytes(exe.read_bytes()+b'update')
    with pytest.raises(core.EditorError, match='游戏文件已更新'):
        session.apply('ManualSave0', [{'action': 'set', 'category': 'goods', 'id': 'Material', 'quantity': 6}])
    assert session.save_path.read_bytes() == before and not session.backup_dir.exists()


def test_future_format_backup_cannot_bypass_read_only_policy(tmp_path, installation):
    session = open_synthetic(tmp_path, installation)
    before = session.source
    _, _, _, _, key, _ = installation
    backup = tmp_path/'future-backup.sav'
    data = save_bytes(key, version=4)
    backup.write_bytes(data)
    backup.with_suffix('.json').write_text(json.dumps({
        'source_path': str(session.save_path), 'sha256': hashlib.sha256(data).hexdigest(),
        'expected_current_sha256': session.source_sha256}))
    with pytest.raises(core.EditorError, match='仅可查看'):
        session.restore(backup)
    assert session.save_path.read_bytes() == before and not session.backup_dir.exists()


def test_table_schema_change_disables_only_affected_category(monkeypatch):
    tables = {source: category.encode() for category, source in catalog_builder.TABLES.values()}
    class Store:
        files = tables
        def read_file(self, name):
            return tables[name]
    expected = {path: hashlib.sha256(raw).hexdigest() for path, raw in tables.items()}
    goods = catalog_builder.TABLES['DT_InventoryData'][1]
    tables[goods] = b'changed'
    monkeypatch.setattr(catalog_builder, 'table_rows', lambda data, category: ([{'id': category, 'row_name': category}], 1))
    monkeypatch.setattr(catalog_builder, 'row_schema', lambda _: [0, 1, 2])
    monkeypatch.setattr(catalog_builder, 'item_metadata', lambda row, category, *args: {**row, 'category': category, 'supported': True})
    result = catalog_builder.build_catalog(Store(), {}, {}, expected, {'goods': [[0, 1]]}, tolerant=True)
    assert result['unavailable_categories'].keys() == {'goods'}
    assert len(result['items']) == 5
    result = catalog_builder.build_catalog(Store(), {}, {}, expected, {'goods': [[0, 1, 2]]}, tolerant=True)
    assert not result['unavailable_categories'] and len(result['items']) == 6
    assert next(s for s in result['sources'] if s['category'] == 'goods')['compatibility'] == 'structural'


def test_missing_map_and_text_resources_do_not_block_item_catalog(tmp_path, monkeypatch):
    class Store:
        files = {}
    monkeypatch.setattr(bootstrap, 'signature', lambda *_: 'stable')
    monkeypatch.setattr(bootstrap, 'executable_path', lambda *_: tmp_path/'demo.exe')
    monkeypatch.setattr(core, 'OodleCodec', lambda *_: None)
    monkeypatch.setattr(bootstrap, 'PakReader', lambda *_: (_ for _ in ()).throw(ValueError('New Pak format')))
    monkeypatch.setattr(bootstrap, 'IoStore', lambda *_: Store())
    monkeypatch.setattr(bootstrap, 'build_catalog', lambda *a, **kw: {'items': [
        {'id': 'Synthetic', 'category': 'goods', 'name': 'Synthetic', 'supported': True}],
        'available_categories': ['goods'], 'unavailable_categories': {}, 'sources': []})
    path = bootstrap.prepare_data(tmp_path/'game', tmp_path/'data-root')
    data = json.loads(path.read_text(encoding='utf8'))
    assert data['items'][0]['id'] == 'Synthetic'
    assert len(data['compatibility_warnings']) == 2
    assert json.loads(path.with_name('regions.json').read_text())['regions'] == []
    assert bootstrap.prepared_catalog(tmp_path/'game', tmp_path/'data-root') == path
