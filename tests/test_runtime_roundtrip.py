from types import SimpleNamespace
import subprocess

import pytest

from tools import runtime_roundtrip as rt


def fake_session(slots, current='AutoSave0', digest='changed'):
    entries = []
    for name, system, raw, items in slots:
        entries.append({'name': name, 'raw': raw, 'json': {'Inventory': {}, 'PBZGameSystem': system}, 'items': items})
    return SimpleNamespace(entries=entries, source_sha256=digest, current_slot=current,
                           inventory=lambda slot: next(e['items'] for e in entries if e['name'] == slot))


@pytest.fixture
def baseline():
    item = {'category': 'weapon', 'id': 'SlitheringSword', 'quantity': 1, 'level': 2, 'name': 'sample'}
    system = {'DateTimeString': '2000.01.01-21.00.00', 'HasGameTime': 123}
    staged = fake_session([('AutoSave0', system, b'staged', [item])], digest='staged-hash')
    plan = {'slot': 'AutoSave0', 'staged_sha256': staged.source_sha256,
            'staged_slots': rt.summary(staged), 'expected': [item]}
    receipt = {'phase': 'running', 'verified_main_processes': [{'ProcessId': 1}]}
    return item, system, staged, plan, receipt


def test_unchanged_staged_file_is_not_runtime_proof(baseline):
    _, _, staged, plan, receipt = baseline
    assert rt.assess(staged, plan, receipt)['status'] == 'NOT_TESTED'


def test_only_global_or_ciphertext_change_is_not_proof(baseline):
    _, _, staged, plan, receipt = baseline
    staged.source_sha256 = 'new-encryption-only'
    assert rt.assess(staged, plan, receipt)['status'] == 'NOT_TESTED'


def test_engine_save_and_matching_records_pass(baseline):
    item, system, _, plan, receipt = baseline
    system = dict(system, DateTimeString='2000.01.01-23.00.00', HasGameTime=200)
    current = fake_session([('AutoSave0', system, b'engine-written', [item])])
    result = rt.assess(current, plan, receipt)
    assert result['status'] == 'PASS'
    assert result['display_equip_and_use'] == 'NOT_TESTED'


def test_unverified_process_cannot_pass(baseline):
    item, system, _, plan, _ = baseline
    current = fake_session([('AutoSave0', dict(system, HasGameTime=200), b'new', [item])])
    assert rt.assess(current, plan, {})['status'] == 'NOT_TESTED'


def test_rotated_autosave_slot_is_supported(baseline):
    item, system, staged, plan, receipt = baseline
    current = fake_session([('AutoSave0', system, b'staged', [item]),
                            ('AutoSave1', dict(system, HasGameTime=200), b'rotated', [item])], 'AutoSave1')
    result = rt.assess(current, plan, receipt)
    assert result['status'] == 'PASS'
    assert result['selected_slot'] == 'AutoSave1'


@pytest.mark.parametrize('items,status', [([], 'MISSING'),
    ([{'category': 'weapon', 'id': 'slitheringsword', 'quantity': 1, 'level': 0}], 'VALUE_CHANGED')])
def test_dropped_or_normalized_item_is_not_pass(baseline, items, status):
    _, system, _, plan, receipt = baseline
    current = fake_session([('AutoSave0', dict(system, HasGameTime=200), b'new', items)])
    result = rt.assess(current, plan, receipt)
    assert result['status'] == 'FAIL'
    assert result['engine_rewritten_slots'][0]['checks'][0]['status'] == status


def test_newest_failed_save_cannot_hide_behind_older_success(baseline):
    item, system, _, plan, receipt = baseline
    current = fake_session([
        ('AutoSave0', dict(system, DateTimeString='2000.01.01-22.00.00'), b'older', [item]),
        ('AutoSave1', dict(system, DateTimeString='2000.01.01-23.00.00'), b'newer', [])], 'AutoSave1')
    assert rt.assess(current, plan, receipt)['status'] == 'FAIL'


def test_actual_command_line_must_contain_exact_test_root(tmp_path):
    plan = {'game_dir': str(tmp_path / 'game'), 'test_root': str(tmp_path / 'test-saves')}
    proc = {'ExecutablePath': str(tmp_path / 'game/platform/steam/PBZ.exe'),
            'CommandLine': 'PBZ.exe "-SaveGamesDir=' + plan['test_root'] + '"'}
    assert rt.isolated_main(proc, plan)
    proc['CommandLine'] = 'PBZ.exe'
    assert not rt.isolated_main(proc, plan)
    proc['CommandLine'] = 'PBZ.exe -SaveGamesDir=' + plan['test_root'] + '-wrong'
    assert not rt.isolated_main(proc, plan)


def test_unchanged_originals_are_never_rewritten_even_if_copy_is_corrupt(tmp_path, monkeypatch):
    source = tmp_path / 'source.sav'
    source.write_bytes(b'original')
    rt.save_json(tmp_path / 'receipt.json', {'phase': 'running', 'verified_main_processes': [{}]})
    plan = {'game_dir': str(tmp_path), 'test_save': str(tmp_path / 'bad.sav'),
            'source': str(source), 'source_sha256': rt.guard.digest(source)}
    monkeypatch.setattr(rt, '_game_processes', lambda _: [])
    monkeypatch.setattr(rt.SaveSession, 'open', lambda *a: (_ for _ in ()).throw(ValueError('bad file')))
    monkeypatch.setattr(rt.guard, 'verify', lambda _: {'identical': True, 'groups': {'progress': {
        'changed': [], 'missing': [], 'new': [], 'mtime_changed': []}}})
    restored = []
    monkeypatch.setattr(rt.guard, 'restore', lambda _: restored.append(True))
    report = rt.finish(tmp_path, plan)
    assert restored == []
    assert report['inventory']['status'] == 'ERROR'
    assert report['recovery'] == 'verified_no_restore_needed'


def test_original_progress_timestamp_changes_are_not_rewritten(tmp_path, monkeypatch):
    source = tmp_path/'original.sav'
    source.write_bytes(b'original')
    rt.save_json(tmp_path/'receipt.json', {'phase': 'running'})
    plan = {'game_dir': str(tmp_path), 'test_save': str(tmp_path/'bad.sav'),
            'source': str(source), 'source_sha256': rt.guard.digest(source)}
    monkeypatch.setattr(rt, '_game_processes', lambda _: [])
    monkeypatch.setattr(rt.SaveSession, 'open', lambda *a: (_ for _ in ()).throw(ValueError('bad copy')))
    monkeypatch.setattr(rt.guard, 'verify', lambda _: {'identical': False, 'groups': {'progress': {
        'changed': [], 'missing': [], 'new': [], 'mtime_changed': ['original.sav']}}})
    monkeypatch.setattr(rt.guard, 'restore', lambda _: pytest.fail('must not rewrite original progress'))
    assert rt.finish(tmp_path, plan)['recovery'].startswith('BLOCKED')


def test_original_progress_changes_are_never_silently_rolled_back(tmp_path, monkeypatch):
    source = tmp_path / 'source.sav'
    source.write_bytes(b'new legitimate progress')
    rt.save_json(tmp_path / 'receipt.json', {'phase': 'running'})
    plan = {'game_dir': str(tmp_path), 'test_save': str(tmp_path / 'bad.sav'),
            'source': str(source), 'source_sha256': 'old'}
    monkeypatch.setattr(rt, '_game_processes', lambda _: [])
    monkeypatch.setattr(rt.SaveSession, 'open', lambda *a: (_ for _ in ()).throw(ValueError('bad file')))
    monkeypatch.setattr(rt.guard, 'verify', lambda _: {'identical': False, 'groups': {'progress': {
        'changed': ['main.sav'], 'missing': [], 'new': [], 'mtime_changed': []}}})
    monkeypatch.setattr(rt.guard, 'restore', lambda _: pytest.fail('must not overwrite new progress'))
    report = rt.finish(tmp_path, plan)
    assert report['recovery'].startswith('BLOCKED')
    assert source.read_bytes() == b'new legitimate progress'


def test_completed_session_never_reapplies_old_snapshot(tmp_path, monkeypatch):
    rt.save_json(tmp_path / 'receipt.json', {'phase': 'complete'})
    rt.save_json(tmp_path / 'result.json', {'inventory': {'status': 'PASS'}})
    monkeypatch.setattr(rt, '_game_processes', lambda _: [])
    monkeypatch.setattr(rt.guard, 'restore', lambda _: pytest.fail('must not restore twice'))
    assert rt.finish(tmp_path, {'game_dir': str(tmp_path)})['inventory']['status'] == 'PASS'


@pytest.fixture
def launchable(tmp_path, monkeypatch):
    test_root = tmp_path / 'test-saves'
    test_root.mkdir()
    test_save = test_root / 'main.sav'
    test_save.write_bytes(b'staged')
    (tmp_path / 'staged-before-game.sav').write_bytes(b'staged')
    plan = {'game_dir': str(tmp_path / 'game'), 'test_root': str(test_root),
            'test_save': str(test_save), 'staged_sha256': rt.guard.digest(test_save)}
    rt.save_json(tmp_path / 'receipt.json', {'phase': 'prepared', 'verified_main_processes': []})
    monkeypatch.setattr(rt.guard, 'verify', lambda _: {'identical': True})
    monkeypatch.setattr(rt, 'steam_path', lambda: tmp_path / 'steam.exe')
    monkeypatch.setattr(rt.SaveSession, 'open', lambda *args: None)
    monkeypatch.setattr(rt.time, 'sleep', lambda _: None)
    launched = []
    monkeypatch.setattr(rt.subprocess, 'Popen', lambda args, **_: launched.append(args))
    return tmp_path, plan, launched


def test_launch_uses_normal_steam_and_records_isolated_process(launchable, monkeypatch):
    root, plan, launched = launchable
    states = iter([[], [101], []])
    monkeypatch.setattr(rt, '_game_processes', lambda _: next(states))
    monkeypatch.setattr(rt, 'process_details', lambda: [{
        'ProcessId': 101, 'ExecutablePath': str(root / 'game/platform/steam/PBZ.exe'),
        'CommandLine': subprocess.list2cmdline(['PBZ.exe', '-SaveGamesDir=' + plan['test_root']])}])
    monkeypatch.setattr(rt, 'finish', lambda root, _: rt.read_json(root / 'receipt.json'))
    receipt = rt.run(root, plan)
    assert launched[0][1:3] == ['-applaunch', '4115450']
    assert '-SaveGamesDir=' + plan['test_root'] in launched[0]
    assert receipt['verified_main_processes'][0]['ProcessId'] == 101


def test_stale_original_prevents_game_launch(launchable, monkeypatch):
    root, plan, launched = launchable
    monkeypatch.setattr(rt, '_game_processes', lambda _: [])
    monkeypatch.setattr(rt.guard, 'verify', lambda _: {'identical': False})
    with pytest.raises(RuntimeError, match='原始文件已变化'):
        rt.run(root, plan)
    assert launched == []


@pytest.mark.parametrize('quoting', ['whole', 'value'])
def test_isolation_supports_chinese_spaces_and_both_quote_forms(tmp_path, quoting):
    plan = {'game_dir': str(tmp_path/'游戏 安装'), 'test_root': str(tmp_path/'独立 测试存档')}
    argument = ('"-SaveGamesDir='+plan['test_root']+'"') if quoting == 'whole' else '-SaveGamesDir="'+plan['test_root']+'"'
    process = {'ExecutablePath': str(tmp_path/'游戏 安装/platform/steam/PBZ.exe'), 'CommandLine': 'PBZ.exe '+argument}
    assert rt.isolated_main(process, plan)
    process['CommandLine'] += ' '+argument
    assert not rt.isolated_main(process, plan)
    process['CommandLine'] = subprocess.list2cmdline(['PBZ.exe', '-Other=embedded -SaveGamesDir='+plan['test_root']])
    assert not rt.isolated_main(process, plan)


def test_wrong_process_arguments_are_recorded_as_unsafe(launchable, monkeypatch):
    root, plan, _ = launchable
    states = iter([[], [101], []])
    monkeypatch.setattr(rt, '_game_processes', lambda _: next(states))
    monkeypatch.setattr(rt, 'process_details', lambda: [{
        'ProcessId': 101, 'ExecutablePath': str(root / 'game/platform/steam/PBZ.exe'),
        'CommandLine': 'PBZ.exe'}])
    monkeypatch.setattr(rt, 'finish', lambda root, _: rt.read_json(root / 'receipt.json'))
    receipt = rt.run(root, plan)
    assert receipt['unexpected_game_detected']
    assert receipt['verified_main_processes'] == []


def test_expected_medicine_shortcut_is_part_of_persistence_contract(baseline):
    _, system, _, plan, receipt = baseline
    item = {'category': 'medicine', 'id': 'Medicine_Recover', 'name': 'sample',
            'quantity': 1, 'level': 0, 'equipped_slots': ['top']}
    plan['expected'] = [item]
    actual = dict(item, equipped_slots=[])
    current = fake_session([('AutoSave0', dict(system, HasGameTime=200), b'new', [actual])])
    result = rt.assess(current, plan, receipt)
    assert result['status'] == 'FAIL'
    current.entries[0]['items'][0]['equipped_slots'] = ['top']
    assert rt.assess(current, plan, receipt)['status'] == 'PASS'
