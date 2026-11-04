"""Draft folding for soul forge: remove + add in one session clears AlreadySnapSoul."""
import json
import zlib
from pathlib import Path

from pbz_editor.core import PBZSContainer, SaveSession
from pbz_editor.draft import InventoryDraft


def soul_session(tmp_path, monkeypatch):
    container = PBZSContainer(bytes(range(32)))
    raw = (
        b'{"PBZGameSystem":{"CycleCount":1,"HasGameTime":10},"Inventory":{'
        b'"MainWeaponInfos":{'
        b'"BronzeSword":{"WeaponLevel":1,"AlreadySnapSoul":4,"DateTime":"2020.01.01-00.00.00","KeepMe":7},'
        b'"SpareBlade":{"WeaponLevel":6,"AlreadySnapSoul":2,"DateTime":"2019.05.05-00.00.00","KeepMe":3},'
        b'"PlainSword":{"WeaponLevel":1,"AlreadySnapSoul":0,"DateTime":"2018.01.01-00.00.00"}'
        b'},'
        b'"ShaChiWeaponInfo":{"PhantomHook":{"ShaChiWeaponLevel":1,"AlreadySnapSoul":true,"Stamp":"keep"}},'
        b'"MainWeapons":["None","None"],"ShaqiWeapons":["None"],"CurrentMainWeaponID":"None"}}'
    )
    entries = []
    for name, data in [('__Advanced__', b'{"LastContinueSlot":"AutoSave0"}'), ('AutoSave0', raw)]:
        entries.append({'name': name, 'raw': data, 'json': json.loads(data), 'algorithm': 1,
                        'flag': 1, 'block': container.seal(zlib.compress(data))})
    source = Path(tmp_path) / 'soul.sav'
    encoded = container.encode(entries, {})
    source.write_bytes(encoded)
    monkeypatch.setattr('pbz_editor.core._game_processes', lambda _: [])
    return SaveSession(source, Path(tmp_path) / 'game', container, encoded, Path(tmp_path) / 'private')


def _record(session, field, item_id):
    return session._entry('AutoSave0')['json']['Inventory'][field][item_id]


def test_remove_then_add_clears_soul_in_one_save(tmp_path, monkeypatch):
    session = soul_session(tmp_path, monkeypatch)
    draft = InventoryDraft(session, 'AutoSave0')
    assert draft.stage({'id': 'BronzeSword', 'category': 'weapon', 'action': 'remove'})
    assert draft.stage({'id': 'BronzeSword', 'category': 'weapon', 'action': 'add', 'quantity': 1, 'level': 0})
    assert draft.stage({'id': 'SpareBlade', 'category': 'weapon', 'action': 'remove'})
    assert draft.stage({'id': 'SpareBlade', 'category': 'weapon', 'action': 'add', 'quantity': 1, 'level': 0})
    assert draft.stage({'id': 'PhantomHook', 'category': 'phantom', 'action': 'remove'})
    assert draft.stage({'id': 'PhantomHook', 'category': 'phantom', 'action': 'add', 'quantity': 1, 'level': 0})
    assert ('weapon', 'bronzesword') in draft.changed_keys
    assert ('weapon', 'spareblade') in draft.changed_keys
    assert ('phantom', 'phantomhook') in draft.changed_keys
    ops = draft.validated_operations()
    souls = {op['id']: op.get('soul_level') for op in ops}
    assert souls == {'BronzeSword': 0, 'SpareBlade': 0, 'PhantomHook': 0}
    session.apply('AutoSave0', ops)
    bronze = _record(session, 'MainWeaponInfos', 'BronzeSword')
    assert bronze['AlreadySnapSoul'] == 0 and not isinstance(bronze['AlreadySnapSoul'], bool)
    assert bronze['WeaponLevel'] == 1
    assert bronze['DateTime'] == '2020.01.01-00.00.00'
    assert bronze['KeepMe'] == 7
    spare = _record(session, 'MainWeaponInfos', 'SpareBlade')
    assert spare['AlreadySnapSoul'] == 0 and not isinstance(spare['AlreadySnapSoul'], bool)
    assert spare['WeaponLevel'] == 1
    assert spare['DateTime'] == '2019.05.05-00.00.00'
    assert spare['KeepMe'] == 3
    hook = _record(session, 'ShaChiWeaponInfo', 'PhantomHook')
    assert hook['AlreadySnapSoul'] == 0 and not isinstance(hook['AlreadySnapSoul'], bool)
    assert hook['ShaChiWeaponLevel'] == 1
    assert hook['Stamp'] == 'keep'
    plain = _record(session, 'MainWeaponInfos', 'PlainSword')
    assert plain['AlreadySnapSoul'] == 0
    assert plain['DateTime'] == '2018.01.01-00.00.00'


def test_level_set_keeps_existing_soul(tmp_path, monkeypatch):
    session = soul_session(tmp_path, monkeypatch)
    draft = InventoryDraft(session, 'AutoSave0')
    assert draft.stage({'id': 'BronzeSword', 'category': 'weapon', 'action': 'set', 'quantity': 1, 'level': 2})
    ops = draft.validated_operations()
    assert len(ops) == 1 and ops[0]['id'] == 'BronzeSword' and 'soul_level' not in ops[0]
    session.apply('AutoSave0', ops)
    bronze = _record(session, 'MainWeaponInfos', 'BronzeSword')
    assert bronze['AlreadySnapSoul'] == 4
    assert bronze['WeaponLevel'] == 3
    assert bronze['DateTime'] == '2020.01.01-00.00.00'
    assert bronze['KeepMe'] == 7
    assert _record(session, 'ShaChiWeaponInfo', 'PhantomHook')['AlreadySnapSoul'] is True


def test_level_roundtrip_does_not_clear_soul(tmp_path, monkeypatch):
    session = soul_session(tmp_path, monkeypatch)
    draft = InventoryDraft(session, 'AutoSave0')
    assert draft.stage({'id': 'BronzeSword', 'category': 'weapon', 'action': 'set', 'quantity': 1, 'level': 2})
    assert draft.stage({'id': 'BronzeSword', 'category': 'weapon', 'action': 'set', 'quantity': 1, 'level': 0})
    assert ('weapon', 'bronzesword') not in draft.changed_keys
    assert draft.validated_operations() == []


def test_readd_of_unforged_weapon_is_not_dirty(tmp_path, monkeypatch):
    session = soul_session(tmp_path, monkeypatch)
    draft = InventoryDraft(session, 'AutoSave0')
    assert draft.stage({'id': 'PlainSword', 'category': 'weapon', 'action': 'remove'})
    assert draft.stage({'id': 'PlainSword', 'category': 'weapon', 'action': 'add', 'quantity': 1, 'level': 0})
    assert ('weapon', 'plainsword') not in draft.changed_keys
    assert draft.validated_operations() == []


def test_remove_only_drops_the_weapon_record(tmp_path, monkeypatch):
    session = soul_session(tmp_path, monkeypatch)
    draft = InventoryDraft(session, 'AutoSave0')
    assert draft.stage({'id': 'BronzeSword', 'category': 'weapon', 'action': 'remove'})
    ops = draft.validated_operations()
    assert len(ops) == 1 and ops[0]['action'] == 'remove' and ops[0]['id'] == 'BronzeSword'
    assert 'soul_level' not in ops[0]
    session.apply('AutoSave0', ops)
    weapons = session._entry('AutoSave0')['json']['Inventory']['MainWeaponInfos']
    assert 'BronzeSword' not in weapons
    assert weapons['SpareBlade']['AlreadySnapSoul'] == 2
    assert weapons['PlainSword']['AlreadySnapSoul'] == 0
    assert _record(session, 'ShaChiWeaponInfo', 'PhantomHook')['AlreadySnapSoul'] is True
