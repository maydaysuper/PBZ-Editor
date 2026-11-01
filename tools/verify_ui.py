"""Exercise the real Qt UI with an installed game and export a private save copy."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from pbz_editor.paths import project_root
from pbz_editor.ui import EditorWindow, configure_application
from pbz_editor.draft import signature


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", required=True)
    parser.add_argument("--save", required=True)
    args = parser.parse_args()
    source = Path(args.save).resolve()
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    output = project_root() / '.local/verification' / ('backpack-' + time.strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True, exist_ok=True)
    save = output / 'PBZSaveGame.sav'
    baseline = source.read_bytes()
    save.write_bytes(baseline)
    app = QApplication([])
    configure_application(app)
    errors = []
    previous_hook = sys.excepthook
    def record_exception(kind, value, traceback):
        errors.append(f'{kind.__name__}: {value}')
        previous_hook(kind, value, traceback)
    sys.excepthook = record_exception
    QMessageBox.warning = lambda _parent, _title, text, *_a: errors.append(text)
    started = time.monotonic()
    window = EditorWindow(game_dir=args.game_dir, save_path=save)
    window.show()
    deadline = time.monotonic() + 30
    while not window.session and not errors and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    if not window.session or errors:
        raise RuntimeError(f"UI failed to open the real save: {errors}")
    assert len(window.items) > 700
    report = {'catalog_count': len(window.items), 'load_seconds': time.monotonic() - started,
              'review_save': str(save), 'source_path': str(source)}
    report['icon_count'] = len(window.icon_index['items']) if window.icon_index else 0
    report['icon_cache_loaded'] = window.icon_index is not None
    window.session.local_dir = output
    window.session.backup_dir = output / 'backups'
    before_entries = [(e['name'], e['block']) for e in window.session.entries]
    window.grab().save(str(output / 'initial.png'))
    def add(ident):
        window.search.setText(ident)
        index = next(i for i, item in enumerate(window.visible_items) if item['id'] == ident)
        window.item_table.selectRow(index)
        QTest.mouseClick(window.add_button, Qt.MouseButton.LeftButton)
        app.processEvents()
    add('Weapon_Material_01')
    assert window.bag_selected == ('goods', 'weapon_material_01')
    initial_count = window.quantity_spin.value()
    window.quantity_spin.setValue(initial_count + 2)
    window.undo()
    assert window.quantity_spin.value() == initial_count
    window.quantity_spin.setValue(initial_count + 2)
    add('HuoLiuXing')
    level = window.level_spin.value()
    window.level_spin.setValue(0 if level else 1)
    add('Medicine_Recover')
    window.medicine_slot.setCurrentIndex(window.medicine_slot.findData('top'))
    window.quantity_spin.setValue(2)
    assert save.read_bytes() == baseline and not window.session.backup_dir.exists()
    report['preview_only_until_save'] = True
    report['undo_verified'] = True
    expected = {k: signature(v) for k, v in window.owned.items()}
    slot = window.slot_name()
    report["preview"] = window.session.preview(slot, window.pending)
    report['operations'] = window.draft.validated_operations()
    destination = output / f"ui-export-{time.time_ns()}.sav"
    report['export'] = window.session.export(slot, report['operations'], destination)
    window.search.clear()
    window.refresh_items()
    app.processEvents()
    window.grab().save(str(output / "editor.png"))
    report["screenshot"] = str(output / "editor.png")
    QTest.mouseClick(window.apply_button, Qt.MouseButton.LeftButton)
    deadline = time.monotonic() + 30
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not window.busy and not window.pending and not errors, errors
    assert {k: signature(v) for k, v in window.owned.items()} == expected
    assert len(list(window.session.backup_dir.glob('*.sav'))) == 1
    affected = report['preview']['affected_slots']
    for (name, block), entry in zip(before_entries, window.session.entries):
        if name not in affected:
            assert block == entry['block']
    report['saved_backpack_matches_preview'] = True
    report['single_backup_verified'] = True
    report['unrelated_slots_preserved'] = True
    report['source_unchanged'] = hashlib.sha256(source.read_bytes()).hexdigest() == original
    assert report['source_unchanged']
    report["game_runtime_verified"] = False
    window.pool.waitForDone(10000)
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    app.quit()
    sys.excepthook = previous_hook
    assert not errors, errors
    # Keep the review copy identical to its input after testing; only this new
    # private path is restored, never the supplied input or any game save.
    save.write_bytes(baseline)
    report['review_copy_reset'] = True
    (output / 'ui-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf8')
    print(json.dumps({k: report[k] for k in ('review_save', 'load_seconds', 'preview_only_until_save',
        'saved_backpack_matches_preview', 'single_backup_verified', 'source_unchanged', 'screenshot')}, ensure_ascii=False))


if __name__ == "__main__":
    main()
