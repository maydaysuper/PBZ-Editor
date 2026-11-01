"""Prepare an isolated game roundtrip and audit game-written inventory records."""
from __future__ import annotations

import argparse
import ctypes
from contextlib import contextmanager
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from pbz_editor.catalog import Catalog
from pbz_editor.core import SaveSession, _game_processes, sha256
from tools import runtime_save_guard as guard

RUNS = PROJECT / '.local/runtime-test'
ACTIVE = RUNS / 'active-roundtrip.json'
SAMPLES = [
    ('goods', 'Weapon_Material_01', 5, 0),
    ('weapon', 'SlitheringSword', 1, 2),
    ('phantom', 'HuoLiuXing', 1, 1),
    ('accessory', 'AC_ResistFire', 1, 0),
    ('medicine', 'Medicine_Recover', 1, 0),
]


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(temporary, path)


def read_json(path):
    return json.loads(path.read_text(encoding='utf8'))


@contextmanager
def operation_lock():
    """Hold one project-wide OS lock; closing a crashed console releases it."""
    RUNS.mkdir(parents=True, exist_ok=True)
    with (RUNS / 'roundtrip.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        import msvcrt
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('已有测试正在监控中，请直接切回游戏，不要重复启动测试入口。') from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def key(item):
    return item['category'], item['id'].casefold()


def summary(session):
    return {entry['name']: {
        'raw_sha256': sha256(entry['raw']),
        'system': entry['json'].get('PBZGameSystem', {}),
    } for entry in session.entries if isinstance(entry['json'].get('Inventory'), dict)}


def assess(session, plan, receipt):
    """No unchanged staged slot can qualify as proof of game persistence."""
    after = summary(session)
    staged = plan['staged_slots']
    newest_date = max((x['system'].get('DateTimeString', '') for x in staged.values()), default='')
    original_time = staged[plan['slot']]['system'].get('HasGameTime', 0)
    candidates = []
    for slot, data in after.items():
        old = staged.get(slot, {})
        system = data['system']
        rewritten = data['raw_sha256'] != old.get('raw_sha256')
        advanced = (system.get('DateTimeString', '') > newest_date or
                    system.get('HasGameTime', 0) > original_time)
        if rewritten and advanced:
            index = {key(item): item for item in session.inventory(slot)}
            checks = []
            for expected in plan['expected']:
                actual = index.get(key(expected))
                fields = ['quantity', 'level'] + (['equipped_slots'] if 'equipped_slots' in expected else [])
                if actual is None:
                    status = 'MISSING'
                elif all(actual.get(field) == expected[field] for field in fields):
                    status = 'PASS'
                else:
                    status = 'VALUE_CHANGED'
                checks.append({'id': expected['id'], 'name': expected['name'],
                               'category': expected['category'], 'status': status,
                               'expected': {k: expected[k] for k in fields},
                               'actual': None if actual is None else {k: actual.get(k) for k in fields}})
            candidates.append({'slot': slot, 'save_date': system.get('DateTimeString'),
                               'checks': checks, 'all_samples_retained': all(c['status'] == 'PASS' for c in checks)})
    changed = session.source_sha256 != plan['staged_sha256']
    verified = bool(receipt.get('verified_main_processes')) and not receipt.get('unexpected_game_detected')
    # The newest engine save is authoritative; an older retained copy cannot hide a newer failure.
    candidates.sort(key=lambda c: (c['save_date'] or '', c['slot'] == session.current_slot), reverse=True)
    selected = candidates[0] if candidates else None
    status = ('PASS' if selected['all_samples_retained'] else 'FAIL') if changed and verified and selected else 'NOT_TESTED'
    return {'status': status, 'test_file_changed': changed, 'isolated_process_verified': verified,
            'engine_rewritten_slots': candidates, 'selected_slot': selected['slot'] if selected else None,
            'display_equip_and_use': 'NOT_TESTED', 'appearance': 'NOT_TESTED',
            'note': 'PASS 仅表示样品在游戏重新写出的存档中保留且数量/等级吻合，不证明界面显示、装备效果或全部目录条目。'}


def prepare(game, source, medicine_quantity=None):
    if _game_processes(game):
        raise RuntimeError('请先退出游戏，再准备测试副本。')
    from pbz_editor.bootstrap import prepare_data
    from pbz_editor.paths import local_dir
    catalog = Catalog.load(prepare_data(game, local_dir()))
    original = SaveSession.open(source, game)
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    root = RUNS / ('roundtrip-' + stamp)
    root.parent.mkdir(parents=True, exist_ok=True)
    guard.snapshot(game, root)
    snapshot = read_json(root / 'snapshot.json')
    progress_root = Path(snapshot['groups']['progress']['root'])
    relative = source.resolve().relative_to(progress_root)
    test_root = root / 'test-saves'
    shutil.copytree(root / 'original/progress', test_root)
    for setting in (root / 'original/local_settings').glob('*.sav'):
        shutil.copy2(setting, test_root / setting.name)
    test_save = test_root / relative
    if sha256(test_save.read_bytes()) != original.source_sha256:
        raise RuntimeError('复制期间原档变化，未启用测试入口。')
    staged = SaveSession.open(test_save, game, local_dir=root / 'editor-state')
    before = {key(item): item for item in staged.inventory(staged.current_slot)}
    operations = []
    for category, item_id, quantity, level in SAMPLES:
        item = catalog.get(item_id, category)
        if not item or not item.get('supported', True):
            raise RuntimeError('测试样品不在已适配目录中：' + item_id)
        op = {'action': 'add', 'category': category, 'id': item_id,
              'name': item['name'], 'quantity': quantity, 'level': level}
        if category == 'medicine':
            op['base_capacity'] = item['base_capacity']
            if medicine_quantity is not None:
                if not 1 <= medicine_quantity <= 999:
                    raise ValueError('测试药品次数必须在 1–999 之间。')
                op['quantity'] = medicine_quantity
            # Explicitly equip the test-only sample; native load drops unequipped counts.
            op['equip_slot'] = 'top'
        # If already owned, retain that unique item's current level.
        owned = before.get((category, item_id.casefold()))
        if owned and category not in ('goods', 'medicine'):
            op['level'] = max(level, owned['level'])
        operations.append(op)
    result = staged.apply(staged.current_slot, operations)
    inventory = {key(item): item for item in staged.inventory(staged.current_slot)}
    expected = []
    for op in operations:
        item = inventory[key(op)]
        expected.append({k: item[k] for k in ('id', 'category', 'quantity', 'level')})
        expected[-1]['name'] = op['name']
        expected[-1]['newly_added'] = key(op) not in before
        if op['category'] == 'medicine':
            expected[-1]['equipped_slots'] = item['equipped_slots']
    shutil.copy2(test_save, root / 'staged-before-game.sav')
    plan = {'level_semantics': 'strengthening_zero_based_v2',
            'codec_backend': getattr(staged.container.codec, 'backend', 'explicit-external'),
            'writer': 'oodle-stored-blocks' if getattr(staged.container.codec, 'backend', None) == 'bundled-open' else 'external',
            'root': str(root), 'game_dir': str(game.resolve()), 'source': str(source.resolve()),
            'test_root': str(test_root), 'test_save': str(test_save), 'slot': staged.current_slot,
            'source_sha256': original.source_sha256, 'staged_sha256': result['sha256'],
            'staged_slots': summary(staged), 'operations': operations, 'expected': expected}
    if not guard.verify(root)['identical'] or _game_processes(game):
        raise RuntimeError('准备期间原始文件或游戏状态发生变化，未启用测试入口。')
    save_json(root / 'plan.json', plan)
    save_json(root / 'receipt.json', {'phase': 'prepared', 'verified_main_processes': []})
    save_json(ACTIVE, {'root': str(root)})
    print('已准备独立测试副本：' + str(root))
    for item in expected:
        print(f"  {item['name']}：数量 {item['quantity']}，等级 {item['level']}" )
    return root


def load_plan(root=None):
    root = (root or Path(read_json(ACTIVE)['root'])).resolve()
    if not root.is_relative_to(RUNS.resolve()) or root == RUNS.resolve():
        raise RuntimeError('测试会话路径不在项目测试目录内。')
    plan = read_json(root / 'plan.json')
    if Path(plan['root']).resolve() != root:
        raise RuntimeError('测试会话路径不匹配。')
    if Path(plan['test_root']).resolve() != root / 'test-saves':
        raise RuntimeError('测试存档根目录不匹配。')
    if not Path(plan['test_save']).resolve().is_relative_to(root / 'test-saves'):
        raise RuntimeError('测试存档路径越界。')
    snapshot = read_json(root / 'snapshot.json')
    if Path(snapshot['game_dir']).resolve() != Path(plan['game_dir']).resolve():
        raise RuntimeError('游戏路径与保护快照不匹配。')
    return root, plan


def process_details():
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        "@(Get-CimInstance Win32_Process -Filter \"Name = 'PBZ.exe' OR Name = 'ProjectPBZ.exe' OR Name = 'PBZ-Win64-Shipping.exe' OR Name = 'ProjectPBZ-Win64-Shipping.exe'\" | Select-Object ProcessId,ExecutablePath,CommandLine) | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding='utf8', errors='replace', check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    data = json.loads(result.stdout or '[]')
    return data if isinstance(data, list) else [data]


def windows_arguments(command_line):
    """Parse the actual Unicode Windows command line, including whole-arg quotes."""
    if not command_line or len(command_line) > 32767:
        return []
    parse = ctypes.WinDLL('shell32').CommandLineToArgvW
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    free = ctypes.WinDLL('kernel32').LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    values = parse(command_line, ctypes.byref(count))
    if not values:
        return []
    try:
        return [values[i] for i in range(count.value)]
    finally:
        free(ctypes.cast(values, ctypes.c_void_p))


def isolated_main(process, plan):
    executable = Path(plan['game_dir']) / 'platform/steam/PBZ.exe'
    if str(process.get('ExecutablePath', '')).casefold() != str(executable).casefold():
        return False
    values = [arg.split('=', 1)[1] for arg in windows_arguments(process.get('CommandLine') or '')
              if arg.casefold().startswith('-savegamesdir=')]
    return len(values) == 1 and bool(values[0]) and Path(values[0]).resolve() == Path(plan['test_root']).resolve()


def steam_path():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as registry:
        steam = Path(winreg.QueryValueEx(registry, 'SteamExe')[0])
    if not steam.is_file():
        raise RuntimeError('未找到正常 Steam 启动入口。')
    return steam


def finish(root, plan):
    game = Path(plan['game_dir'])
    if _game_processes(game):
        raise RuntimeError('游戏仍在运行。请完全退出游戏后再校验，当前未恢复或覆盖任何原档。')
    receipt = read_json(root / 'receipt.json')
    if receipt.get('phase') == 'complete':
        print('此会话已完成，不重复恢复旧快照。历史报告：' + str(root / 'result.json'))
        return read_json(root / 'result.json')
    report = {'session': str(root), 'source_sha256_before': plan['source_sha256']}
    try:
        current = SaveSession.open(plan['test_save'], game)
        report['inventory'] = assess(current, plan, receipt)
    except Exception as exc:
        report['inventory'] = {'status': 'ERROR', 'error': str(exc)}
    # A never-launched or completed session must never roll back later normal play.
    original_state = guard.verify(root)
    progress = original_state['groups']['progress']
    progress_edits = any([p for p in progress.get(field, []) if Path(p).name != 'steam_autocloud.vdf']
                         for field in ('changed', 'missing', 'new', 'mtime_changed'))
    if receipt['phase'] == 'prepared':
        report['original_files'] = guard.verify(root)
        report['recovery'] = 'not_launched_no_restore'
    elif receipt.get('unexpected_game_detected') or progress_edits:
        report['original_files'] = guard.verify(root)
        report['recovery'] = 'BLOCKED_ORIGINAL_PROGRESS_OR_UNKNOWN_GAME_SESSION'
        report['note'] = '检测到原进度变化或非本次隔离进程。为避免覆盖正常游玩的新进度，未自动恢复；请保留全部快照。'
    elif original_state['identical']:
        report['original_files'] = original_state
        report['recovery'] = 'verified_no_restore_needed'
    else:
        # Audit failures do not prevent restoring the user's protected files.
        save_json(root / 'result.json', report)
        guard.restore(root)
        report['original_files'] = guard.verify(root)
        report['recovery'] = 'restored_and_verified'
    report['source_sha256_after'] = guard.digest(plan['source'])
    report['original_main_content_unchanged'] = report['source_sha256_after'] == plan['source_sha256']
    save_json(root / 'result.json', report)
    if report['recovery'] in ('restored_and_verified', 'verified_no_restore_needed'):
        receipt['phase'] = 'complete'
        save_json(root / 'receipt.json', receipt)
    lines = ['物品持久性：' + report['inventory']['status'],
             '原始存档与设置一致：' + str(report['original_files']['identical']),
             '原始主存档内容一致：' + str(report['original_main_content_unchanged']),
             '报告：' + str(root / 'result.json')]
    if report.get('note'):
        lines.append(report['note'])
    (root / 'result.txt').write_text('\n'.join(lines), encoding='utf8')
    print('\n'.join(lines))
    return report


def run(root, plan):
    game = Path(plan['game_dir'])
    receipt = read_json(root / 'receipt.json')
    if receipt['phase'] != 'prepared':
        raise RuntimeError('这个会话已启动过。请用“Check-PBZ-Test.cmd”收尾，重新测试需准备新副本。')
    if _game_processes(game):
        raise RuntimeError('请先退出正常游戏，再启动测试。')
    if not guard.verify(root)['identical']:
        raise RuntimeError('准备副本后原始文件已变化，已取消启动。请让 Codex 按最新原档重新准备，不能使用旧快照覆盖新进度。')
    if guard.digest(plan['test_save']) != plan['staged_sha256']:
        raise RuntimeError('测试副本已变化，已取消启动。')
    if guard.digest(root / 'staged-before-game.sav') != plan['staged_sha256']:
        raise RuntimeError('测试前基线损坏，已取消启动。')
    # Recheck the supported binary before relying on its native SaveGamesDir route.
    SaveSession.open(plan['test_save'], game)
    probe = Path(plan['test_root']) / '.write-probe'
    probe.write_bytes(b'isolation writable')
    probe.unlink()
    steam = steam_path()
    receipt.update(phase='launching', launched_at=dt.datetime.now().isoformat())
    save_json(root / 'receipt.json', receipt)
    print('隔离监控已开始。等待“隔离已确认”后才点击继续，请勿重复启动测试入口。', flush=True)
    subprocess.Popen([str(steam), '-applaunch', '4115450', '-SaveGamesDir=' + plan['test_root']],
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 120
    seen = False
    known = set()
    while True:
        pids = set(_game_processes(game))
        if pids:
            seen = True
            if pids - known:
                details = process_details()
                for proc in details:
                    if isolated_main(proc, plan):
                        if proc['ProcessId'] not in {p['ProcessId'] for p in receipt['verified_main_processes']}:
                            receipt['verified_main_processes'].append(proc)
                            receipt['phase'] = 'running'
                            print('隔离已确认。可以点击“继续游戏”，按操作说明完成后退出。', flush=True)
                    elif str(proc.get('ExecutablePath', '')).casefold() == str(game / 'platform/steam/PBZ.exe').casefold():
                        receipt['unexpected_game_detected'] = True
                        print('未确认隔离！不要点击继续，请退出游戏。本次不会标记通过。', flush=True)
                known.update(pids)
                save_json(root / 'receipt.json', receipt)
        elif seen or time.monotonic() > deadline:
            break
        time.sleep(1)
    # Steam may still be updating AutoCloud marker timestamps just after exit.
    time.sleep(3)
    print('游戏已退出，开始自动验收与恢复。', flush=True)
    return finish(root, plan)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'finish', 'status'])
    parser.add_argument('--game-dir', type=Path)
    parser.add_argument('--save', type=Path)
    parser.add_argument('--medicine-quantity', type=int, help='Explicit test-copy count; does not change the persistence criteria')
    parser.add_argument('--session', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            if not args.game_dir or not args.save:
                parser.error('prepare requires --game-dir and --save')
            with operation_lock():
                prepare(args.game_dir, args.save, args.medicine_quantity)
        else:
            root, plan = load_plan(args.session)
            if args.action == 'run':
                with operation_lock():
                    run(root, plan)
            elif args.action == 'finish':
                with operation_lock():
                    finish(root, plan)
            else:
                print(json.dumps({'session': str(root), 'receipt': read_json(root / 'receipt.json'),
                                  'original_files': guard.verify(root)}, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print('监控已中断。请先完全退出测试游戏，再双击 Check-PBZ-Test.cmd 校验并恢复。')
        return 2
    except Exception as exc:
        print('未完成：' + str(exc))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
