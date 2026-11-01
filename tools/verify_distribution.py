"""Verify generated data and write/restore ONLY a newly created disposable copy."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtGui import QImage
from pbz_editor.bootstrap import prepare_data
from pbz_editor.catalog import Catalog
from pbz_editor.core import SaveSession, PBZSContainer, OodleCodec, game_key, sha256
from pbz_editor.resources_reader import IoStore
from pbz_editor.pak_reader import PakReader


def contains_reference(actual, reference):
    """Permit new localization fields, but require every original value unchanged."""
    if isinstance(reference, dict):
        return isinstance(actual, dict) and all(k in actual and contains_reference(actual[k], v) for k, v in reference.items())
    if isinstance(reference, list):
        return isinstance(actual, list) and len(actual) == len(reference) and all(contains_reference(a, b) for a, b in zip(actual, reference))
    return actual == reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepared-root', type=Path, required=True)
    parser.add_argument('--reference-data', type=Path)
    parser.add_argument('--reference-icons', type=Path)
    parser.add_argument('--oracle-dll', type=Path, help='Optional locally installed independent decoder, never copied')
    args = parser.parse_args()
    game, source, output = args.game_dir.resolve(), args.source.resolve(), args.output.resolve()
    if output.is_relative_to(game) or source.is_relative_to(output):
        raise SystemExit('Output must be separate from the game and source')
    output.mkdir(parents=True, exist_ok=False)
    before, stat = source.read_bytes(), source.stat()
    catalog_path = prepare_data(game, args.prepared_root)
    catalog = Catalog.load(catalog_path)
    report = {'catalog_entries': len(catalog.items), 'editable_entries': sum(i.get('supported', True) for i in catalog.items),
              'game_runtime_verification': 'NOT_TESTED'}
    if args.reference_data:
        for name in ('catalog.json', 'regions.json'):
            reference = json.loads((args.reference_data/name).read_text(encoding='utf8'))
            if 'steam_build' in reference:
                reference['reference_build'] = reference.pop('steam_build')
            assert contains_reference(json.loads((catalog_path.parent/name).read_text(encoding='utf8')), reference)
        report['catalog_and_regions_equal_reference'] = True
    index = json.loads((args.prepared_root/'icons/index.json').read_text(encoding='utf8'))
    report['icons'], report['unique_textures'] = len(index['items']), index['texture_count']
    if args.reference_icons:
        reference = json.loads((args.reference_icons/'index.json').read_text(encoding='utf8'))
        assert index['items'].keys() == reference['items'].keys()
        checked = set()
        for ident, record in index['items'].items():
            previous = reference['items'][ident]
            assert {k: v for k, v in record.items() if k != 'png_sha256'} == {k: v for k, v in previous.items() if k != 'png_sha256'}
            if record['file'] not in checked:
                a, b = QImage(str(args.prepared_root/'icons'/record['file'])), QImage(str(args.reference_icons/previous['file']))
                assert not a.isNull() and a == b
                checked.add(record['file'])
        report['icon_pixels_and_references_equal'] = len(checked)
    disposable = output/'PBZSaveGame.sav'
    shutil.copy2(source, disposable)
    session = SaveSession.open(disposable, game, local_dir=output/'user-data')
    assert getattr(session.container.codec, 'backend', None) == 'bundled-open'
    originals = session.entries.copy()
    slot = session.current_slot
    old = next((i.get('quantity', 0) for i in session.inventory(slot) if i['id'] == 'Weapon_Material_01'), 0)
    operations = [{'action': 'set', 'category': 'goods', 'id': 'Weapon_Material_01', 'quantity': 3 if old != 3 else 4}]
    session.preview(slot, operations)
    assert disposable.read_bytes() == before
    result = session.apply(slot, operations)
    assert result['operation_count'] == 1 and Path(result['backup_path']).read_bytes() == before
    assert all(a['block'] == b['block'] for a, b in zip(originals, session.entries) if a['name'] != slot)
    exported = output/'export.sav'
    session.export(slot, [dict(operations[0], quantity=5)], exported)
    SaveSession.open(exported, game, local_dir=output/'user-data')
    report['write_export_backup_untouched_slots'] = True
    if args.oracle_dll:
        oracle = OodleCodec(args.oracle_dll, game)
        alternative = PBZSContainer(game_key(game), oracle)
        for raw in (before, disposable.read_bytes(), exported.read_bytes()):
            assert [e['raw'] for e in alternative.decode(raw)] == [e['raw'] for e in session.container.decode(raw)]
        report['save_slots_equal_independent_decoder'] = len(originals)
        utoc = game/'ProjectPBZ/Content/Paks/ProjectPBZ-Windows.utoc'
        a, b = IoStore(utoc, session.container.codec), IoStore(utoc, oracle)
        assets = set(json.loads((ROOT/'data/profile.json').read_text(encoding='utf8'))['resources']) | {i['texture'] for i in index['items'].values()}
        lookup = {name.casefold(): name for name in a.files}
        for name in sorted(assets):
            name = lookup[name.casefold()]
            assert a.read_file(name) == b.read_file(name)
        report['native_resources_equal_independent_decoder'] = len(assets)
        pak = game/'ProjectPBZ/Content/Paks/ProjectPBZ-Windows.pak'
        a, b = PakReader(pak, session.container.codec), PakReader(pak, oracle)
        members = ['ProjectPBZ/Content/Localization/Game/'+lang+'/Game.locres' for lang in ('zh-Hans', 'en-US')]
        members.append('ProjectPBZ/Content/JavaScript/Library/MapAreaGroups.js')
        for member in members:
            assert a.read_file(member) == b.read_file(member)
        report['pak_members_equal_independent_decoder'] = len(members)
    session.restore(result['backup_path'])
    assert disposable.read_bytes() == before
    assert source.read_bytes() == before and source.stat().st_mtime_ns == stat.st_mtime_ns
    report.update({'backup_restore_exact': True, 'source_hash': sha256(before), 'source_unchanged': True, 'ok': True})
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__': main()
