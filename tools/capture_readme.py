"""Capture the real editor with a generated demo inventory, never a player save.

Requires locally prepared catalog/regions and icon cache. Only screenshots are
published; the synthetic save and its backup live in a temporary directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
import zlib

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox
from pbz_editor import ui
from pbz_editor.catalog import Catalog
from pbz_editor.core import PBZSContainer, SaveSession


def demo_session(root):
    inventory = {
        'InventoryInfos': {
            'Weapon_Material_01': {'CurrentAmount': 10, 'MaxAmount': 10},
            'Weapon_Material_02': {'CurrentAmount': 5, 'MaxAmount': 5},
        },
        'MainWeaponInfos': {
            'IronBlade': {'WeaponLevel': 2},
            'SlitheringSword': {'WeaponLevel': 1},
        },
        'ShaChiWeaponInfo': {
            'HuoLiuXing': {'ShaChiWeaponLevel': 1},
            'BrutalAxe': {'ShaChiWeaponLevel': 1},
        },
        'AccessoryInfos': {'AC_ResistFire': {'CurrentLevel': 0}},
        'MedicineInfos': {'Hulu': {'CurrentLevel': 0, 'CurrentTime': 2, 'MaxTime': 2}},
        'MainWeapons': ['IronBlade', 'None'],
        'ShaqiWeapons': ['HuoLiuXing', 'None'],
        'AccessoryIDs': ['AC_ResistFire', 'None'],
        'CurrentMainWeaponID': 'IronBlade',
        'TopMedicine': 'Hulu', 'BottomMedicine': 'None',
    }
    slot = {'PBZGameSystem': {'DateTimeString': 'DEMO', 'HasGameTime': 0},
            'Inventory': inventory}
    container = PBZSContainer(bytes(range(32)))
    entries = []
    for name, data in [('__Advanced__', {'LastContinueSlot': 'ManualSave0'}),
                       ('ManualSave0', slot)]:
        raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf8')
        entries.append({'name': name, 'raw': raw, 'json': data, 'algorithm': 1,
                        'flag': 1, 'block': container.seal(zlib.compress(raw))})
    source = root/'PBZ-Demo.sav'
    source.write_bytes(container.encode(entries, {}))
    return SaveSession(source, root/'not-a-game-installation', container,
                       source.read_bytes(), root/'demo-data')


def settle(app):
    for _ in range(4):
        app.processEvents()
        QTest.qWait(50)


def select_item(window, ident):
    row = next(r for r, item in enumerate(window.visible_items) if item['id'] == ident)
    window.item_table.selectRow(row)
    window.item_table.scrollToItem(window.item_table.item(row, 0))


def capture(app, window, path):
    settle(app)
    # Fail closed if a visible label ever starts displaying an absolute path.
    visible = [w.text() for kind in (QLabel, QLineEdit)
               for w in window.findChildren(kind) if w.isVisible()]
    assert not any(':/Users/' in t or ':\\Users\\' in t or str(ROOT) in t for t in visible)
    assert window.session.save_path.name == 'PBZ-Demo.sav'
    assert not window.welcome.isVisible()
    assert all(s['date'] == 'DEMO' and s['playtime'] == 0 for s in window.session.slots)
    assert window.grab().save(str(path), 'PNG')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--icons', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/screenshots')
    args = parser.parse_args()
    catalog = args.catalog.resolve()
    icons = args.icons.resolve()
    assert icons.name == 'icons', 'Use a prepared icons directory'
    index = json.loads((icons/'index.json').read_text(encoding='utf8'))
    assert len(Catalog.load(catalog).items) >= 700
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    private = ROOT/'.local'
    private.mkdir(exist_ok=True)
    app = QApplication.instance() or QApplication([])
    ui.configure_application(app)
    errors = []
    with tempfile.TemporaryDirectory(prefix='readme-demo-', dir=private) as temporary, \
            patch.object(ui, 'load_settings', side_effect=lambda: {}), \
            patch.object(ui, 'save_settings'), \
            patch.object(ui, 'find_game', return_value=None), \
            patch.object(ui, 'find_saves', return_value=[]), \
            patch.object(ui, 'local_dir', return_value=icons.parent), \
            patch.object(QMessageBox, 'warning', side_effect=lambda *a: errors.append(a[2])):
        for locale in ('zh-CN', 'en-US'):
            root = Path(temporary)/locale
            root.mkdir()
            session = demo_session(root)
            original = session.source
            window = ui.EditorWindow(catalog=catalog, language_code=locale)
            window.show()
            window.accept_session(session)
            window._apply_icons(index)
            window.category_list.doItemsLayout()
            select_item(window, 'IronBlade')
            capture(app, window, output/f'01-browse.{locale}.png')

            window.category_filter.setCurrentIndex(window.category_filter.findData('accessory'))
            window.region_filter.setCurrentIndex(window.region_filter.findData('LuJiaCun'))
            select_item(window, 'AC_ResistRanged')
            window.add_selected()
            window.level_spin.setValue(1)
            assert session.save_path.read_bytes() == original
            assert window.apply_button.isEnabled() and window.pending
            capture(app, window, output/f'02-edit.{locale}.png')

            window.category_filter.setCurrentIndex(window.category_filter.findData('goods'))
            window.region_filter.setCurrentIndex(window.region_filter.findData('all'))
            select_item(window, 'Weapon_Material_01')
            window.bag_selected = ('goods', 'weapon_material_01')
            window.refresh_bag()
            window.quantity_spin.setValue(20)
            assert session.save_path.read_bytes() == original
            window.apply_save()
            deadline = time.monotonic() + 10
            while window.busy and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(10)
            assert not errors, errors
            assert not window.busy and not window.pending
            assert len(list(session.backup_dir.glob('*.sav'))) == 1
            assert next(i for i in session.inventory('ManualSave0')
                        if i['id'] == 'Weapon_Material_01')['quantity'] == 20
            assert session.save_path.read_bytes() != original
            capture(app, window, output/f'03-saved.{locale}.png')
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    print('Captured 6 UI screenshots from generated DEMO inventories; no player save was opened.')


if __name__ == '__main__':
    main()
