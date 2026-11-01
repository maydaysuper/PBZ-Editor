"""Verify real PBZS data using project-local copies. Never applies to a live save."""
from __future__ import annotations
import argparse
import datetime
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pbz_editor.core import SaveSession, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', required=True, type=Path)
    parser.add_argument('--save', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--oodle', type=Path)
    parser.add_argument('--all-catalog',type=Path,help='Export every supported catalog item, including global outfits.')
    args = parser.parse_args()
    project = Path(__file__).resolve().parent.parent
    output = args.output_dir or project / '.local' / 'verification' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True, exist_ok=False)
    original = args.save.read_bytes()
    copied = output / 'source-copy.sav'
    copied.write_bytes(original)
    session = SaveSession.open(copied, args.game_dir, args.oodle, local_dir=output / 'private')
    slot = session.current_slot
    inventory = session.inventory(slot)
    categories = ('goods', 'weapon', 'phantom', 'accessory', 'medicine')
    operations = []
    for category in categories:
        existing = next((item for item in inventory if item['category'] == category), None)
        if existing is None:
            continue
        op = {'action':'set','id':existing['id'],'category':category,'level':existing['level']}
        if category in ('goods','medicine'):
            op['quantity'] = existing['quantity'] + 1
        else:
            op['quantity'] = 1
            op['level'] = existing['level'] + 1
        operations.append(op)
    if args.all_catalog:
        catalog=json.loads(args.all_catalog.read_text(encoding='utf8'))
        operations=[{'action':'add','id':item['id'],'category':item['category'],'quantity':0 if item['category']=='medicine' else 1,'level':0}
                    for item in catalog['items'] if item.get('supported',True)]
    started=time.perf_counter()
    preview=session.preview(slot,operations)
    preview_seconds=time.perf_counter()-started
    affected_slots=set(preview['affected_slots'])
    results = {}
    for compression in ('preserve','zlib','none'):
        destination = output / f'edited-{compression}.sav'
        started=time.perf_counter()
        result = session.export(slot, operations, destination, compression=compression)
        elapsed=time.perf_counter()-started
        decoded = session.container.decode(destination.read_bytes())
        for before, after in zip(session.entries, decoded):
            if before['name'] not in affected_slots:
                assert before['raw'] == after['raw']
                assert before['block'] == after['block']
        results[compression] = {'size': destination.stat().st_size, 'sha256':result['sha256'], 'file_verified':True, 'game_runtime_verified':False,'seconds':elapsed}
    assert copied.read_bytes() == original
    # Exercise the live-write path ONLY against an explicit disposable project-local copy.
    disposable = output / 'disposable-write-test.sav'
    disposable.write_bytes(original)
    disposable_session = SaveSession.open(disposable, args.game_dir, args.oodle, local_dir=output/'private')
    applied = disposable_session.apply(slot, operations)
    restored = disposable_session.restore(applied['backup_path'])
    assert disposable.read_bytes() == original
    source_unchanged = args.save.read_bytes() == original
    report = {
        'source_path':str(args.save.resolve()),'source_sha256':sha256(original),
        'source_unchanged_during_verification':source_unchanged,
        'tested_slot':slot,'slot_count':len(session.entries),'categories_tested':[op['category'] for op in operations],
        'operation_count':len(operations),'preview_seconds':preview_seconds,'affected_slots':list(affected_slots),
        'operations':operations,'exports':results,'disposable_copy_apply_and_restore':True,
        'all_untouched_slot_ciphertexts_preserved':True,'game_runtime_verified':False,
        'oodle_library':str(session.container.codec.path),
        'note':'Only project-local copies were written. zlib and raw modes are supported by inspected game code but have not been loaded in-game.'
    }
    (output/'verification-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print('Report:',output/'verification-report.json')


if __name__ == '__main__':
    main()
