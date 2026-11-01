"""Two-pane workspace: preview and undo in memory, commit only on Save."""
import json
import os
import zlib
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from pbz_editor import ui
from pbz_editor.core import PBZSContainer, SaveSession, EditorError
from pbz_editor.draft import InventoryDraft

ITEMS = [
    {'id': 'AC_ResistRanged', 'name': '避箭护符', 'name_en': 'Arrowward', 'category': 'accessory', 'max_level': 1},
    {'id': 'Material', 'name': '强化材料', 'category': 'goods'},
    {'id': 'IronBlade', 'name': '长刀', 'category': 'weapon', 'max_level': 20},
    {'id': 'HuoLiuXing', 'name': '火流星', 'category': 'phantom', 'max_level': 3},
    {'id': 'Medicine_Recover', 'name': '不尽水', 'category': 'medicine', 'max_level': 6},
    {'id': 'Hulu', 'name': '葫芦', 'category': 'medicine', 'max_level': 6},
    {'id': 'Hun_OutFit_FengXue', 'name': '风雪服装', 'category': 'appearance'},
    {'id': 'Hair', 'name': '头发组件', 'category': 'appearance', 'supported': False},
]

@pytest.fixture(scope='session')
def application():
    app = QApplication.instance() or QApplication([])
    ui.configure_application(app)
    return app

@pytest.fixture
def session(tmp_path, monkeypatch):
    container = PBZSContainer(bytes(range(32)))
    raw = b'{"PBZGameSystem":{"HasGameTime":10},"Inventory":{"InventoryInfos":{"Material":{"CurrentAmount":3,"MaxAmount":3}},"MainWeaponInfos":{"IronBlade":{"WeaponLevel":3}},"MainWeapons":["IronBlade"],"MedicineInfos":{"Hulu":{"CurrentLevel":0,"CurrentTime":3,"MaxTime":2}},"TopMedicine":"Hulu","BottomMedicine":"None"},"Unknown":0.10000000000000001}'
    entries = []
    for name, data in [('__Advanced__', b'{"LastContinueSlot":"AutoSave0"}'), ('AutoSave0', raw), ('ManualSave1', raw), ('__Achievements__', b'{"Unknown":[1,2]}')]:
        entries.append({'name': name, 'raw': data, 'json': json.loads(data), 'algorithm': 1,
                        'flag': 1, 'block': container.seal(zlib.compress(data))})
    source = tmp_path / 'disposable.sav'
    source.write_bytes(container.encode(entries, {}))
    monkeypatch.setattr('pbz_editor.core._game_processes', lambda _: [])
    return SaveSession(source, tmp_path / 'game', container, source.read_bytes(), tmp_path / 'private')

@pytest.fixture
def window(application, tmp_path, monkeypatch):
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'items': ITEMS}), encoding='utf8')
    monkeypatch.setattr(ui, 'load_settings', lambda: {})
    monkeypatch.setattr(ui, 'save_settings', lambda _: None)
    monkeypatch.setattr(ui, 'find_game', lambda: None)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_: QMessageBox.StandardButton.Ok)
    w = ui.EditorWindow(catalog=catalog)
    w.show()
    application.processEvents()
    yield w
    w.busy = False
    if w.draft:
        w.discard()
    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()

@pytest.fixture
def opened(window, session):
    window.accept_session(session)
    return window

def choose(window, ident):
    window.search.setText(ident)
    window.refresh_items()
    row = next(i for i, item in enumerate(window.visible_items) if item['id'] == ident)
    window.item_table.selectRow(row)

def select_bag(window, category, ident):
    window.bag_search.clear()
    window.bag_table.selectRow(window.bag_rows.index((category, ident.casefold())))

def wait(window):
    for _ in range(400):
        QApplication.processEvents()
        if not window.busy:
            return
        QTest.qWait(10)
    pytest.fail('save task did not finish')

def test_default_library_has_no_per_row_inputs_and_requires_save(window):
    assert window.item_table.rowCount() == 7
    assert not window.add_button.isEnabled() and not window.apply_button.isEnabled()
    assert window.editor.isHidden()
    assert all(window.item_table.cellWidget(r, c) is None for r in range(7) for c in range(3))


def test_sidebar_click_and_keyboard_filter_the_catalog(window):
    row = next(r for r in range(window.category_list.count())
               if window.category_list.item(r).data(Qt.ItemDataRole.UserRole) == 'phantom')
    rect = window.category_list.visualItemRect(window.category_list.item(row))
    QTest.mouseClick(window.category_list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert window.category_filter.currentData() == 'phantom'
    assert window.catalog_heading.text() == '影之武装'
    assert [i['id'] for i in window.visible_items] == ['HuoLiuXing']
    assert window.category_filter.isHidden()
    QTest.keyClick(window.category_list, Qt.Key.Key_Down)
    assert window.category_filter.currentData() == 'accessory'
    assert [i['id'] for i in window.visible_items] == ['AC_ResistRanged']


def test_sidebar_counts_track_supported_and_internal_entries(window):
    row = window.category_filter.findData('appearance')
    assert window.category_list.item(row).text().endswith('1')
    assert window.category_list.item(0).text().endswith('7')
    window.internal_action.setChecked(True)
    assert window.category_list.item(row).text().endswith('2')
    assert window.category_list.item(0).text().endswith('8')


def test_sidebar_navigation_keeps_pending_backpack_and_source(opened):
    original = opened.session.save_path.read_bytes()
    choose(opened, 'Material')
    opened.add_selected()
    opened.search.clear()
    opened.category_list.setCurrentRow(opened.category_filter.findData('medicine'))
    assert {i['id'] for i in opened.visible_items} == {'Medicine_Recover', 'Hulu'}
    assert opened.owned[('goods', 'material')]['quantity'] == 4
    assert len(opened.pending) == 1 and opened.session.save_path.read_bytes() == original


def test_busy_task_disables_sidebar_and_backup_restore_entry(opened):
    import threading
    release = threading.Event()
    opened.run_task('工作中', lambda: release.wait(3), lambda _: None)
    assert not opened.sidebar.isEnabled() and not opened.backups_button.isEnabled()
    release.set()
    wait(opened)
    assert opened.sidebar.isEnabled() and opened.backups_button.isEnabled()

def test_first_run_without_game_shows_direct_setup(window):
    assert not window.welcome.isHidden()
    assert window.choose_game_button.isEnabled()
    window.load_save('test-copy.sav')
    assert '选择游戏位置' in window.status.text()
    assert window.session is None and not window.busy


def test_startup_multiple_accounts_requests_selection_without_loading(application, tmp_path, monkeypatch):
    catalog = tmp_path/'catalog.json'
    catalog.write_text(json.dumps({'items': ITEMS}), encoding='utf8')
    saves = [tmp_path/'111/PBZSaveGame.sav', tmp_path/'222/PBZSaveGame.sav']
    selected, loaded = [], []
    monkeypatch.setattr(ui, 'load_settings', lambda: {'game_dir': str(tmp_path/'game')})
    monkeypatch.setattr(ui, 'find_saves', lambda _: saves)
    monkeypatch.setattr(ui.EditorWindow, 'choose_detected_save', lambda _, values: selected.append(values))
    monkeypatch.setattr(ui.EditorWindow, 'load_save', lambda _, value: loaded.append(value))
    w = ui.EditorWindow(catalog=catalog)
    QTest.qWait(150)
    assert selected == [saves] and loaded == [] and w.session is None
    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_preparation_can_cancel_and_retry(window, session, monkeypatch):
    import threading
    from pbz_editor import bootstrap
    window.settings['game_dir'] = str(session.game_dir)
    window._custom_catalog = False
    entered = threading.Event()
    def prepare(game, root, progress, cancel):
        entered.set()
        if not cancel.wait(3):
            raise RuntimeError('Cancellation was not signalled')
        raise InterruptedError('cancelled')
    monkeypatch.setattr(bootstrap, 'prepare_data', prepare)
    window.load_save(str(session.save_path))
    assert entered.wait(3)
    window.cancel_preparation()
    wait(window)
    assert window.session is None
    assert not window.retry_button.isHidden()
    assert '已取消' in window.status.text()
    monkeypatch.setattr(bootstrap, 'prepare_data', lambda *_: None)
    monkeypatch.setattr(SaveSession, 'open', lambda *_: session)
    window.retry_preparation()
    wait(window)
    assert window.session is session
    assert window.welcome.isHidden()
    assert window.slot_combo.itemText(0).startswith('自动存档')

@pytest.mark.parametrize('query', ['Arrowward', '避箭', 'AC_ResistRanged'])
def test_search_finds_localized_names_and_ids(window, query):
    window.search.setText(query)
    assert [i['id'] for i in window.visible_items] == ['AC_ResistRanged']

def test_unsupported_items_are_browse_only(opened):
    opened.internal_action.setChecked(True)
    choose(opened, 'Hair')
    assert not opened.add_button.isEnabled()
    assert not opened.bag_table.isColumnHidden(3)

def test_add_selects_and_highlights_backpack_without_disk_write(opened):
    original = opened.session.save_path.read_bytes()
    choose(opened, 'AC_ResistRanged')
    QTest.mouseClick(opened.add_button, Qt.MouseButton.LeftButton)
    assert opened.bag_selected == ('accessory', 'ac_resistranged')
    assert opened.bag_name.text() == '避箭护符'
    assert opened.changes_label.text() == '有 1 处修改'
    assert opened.apply_button.isEnabled()
    assert opened.session.save_path.read_bytes() == original
    assert not opened.session.backup_dir.exists()
    assert opened.bag_table.item(opened.bag_table.currentRow(), 2).text() == '新增'

def test_already_owned_unique_is_viewed_without_resetting_level(opened):
    choose(opened, 'IronBlade')
    assert opened.add_button.text() == '在背包中查看'
    opened.add_selected()
    assert opened.level_spin.value() == 2 and not opened.pending

def test_editing_targets_stages_both_and_undoes(opened):
    original = opened.session.save_path.read_bytes()
    choose(opened, 'Material')
    opened.add_selected()
    assert opened.quantity_spin.value() == 4
    opened.quantity_spin.setValue(20)
    assert opened.owned[('goods', 'material')]['quantity'] == 20
    select_bag(opened, 'weapon', 'IronBlade')
    opened.level_spin.setValue(1)
    assert opened.changes_label.text() == '有 2 处修改'
    opened.undo()
    assert opened.owned[('weapon', 'ironblade')]['level'] == 2
    opened.undo()
    assert opened.owned[('goods', 'material')]['quantity'] == 4
    opened.undo()
    assert not opened.pending and opened.session.save_path.read_bytes() == original

def test_setting_back_to_original_clears_dirty_count(opened):
    select_bag(opened, 'goods', 'Material')
    opened.quantity_spin.setValue(10)
    opened.quantity_spin.setValue(3)
    assert not opened.pending and not opened.apply_button.isEnabled()


def test_save_commits_typed_number_even_before_enter(opened):
    select_bag(opened, 'goods', 'Material')
    opened.quantity_spin.setFocus()
    assert opened.quantity_spin.lineEdit().width() > 40
    opened.quantity_spin.lineEdit().selectAll()
    QTest.keyClicks(opened.quantity_spin, '25')
    # Keyboard tracking is off: Save must finish the active numeric edit.
    assert opened.apply_button.isEnabled()
    QTest.mouseClick(opened.apply_button, Qt.MouseButton.LeftButton)
    wait(opened)
    assert opened.owned[('goods', 'material')]['quantity'] == 25
    assert not opened.pending

def test_save_commits_mixed_backpack_once_and_preserves_other_slots(opened, monkeypatch):
    session, original = opened.session, opened.session.source
    information = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: information.append(args))
    for ident in ('Material', 'AC_ResistRanged', 'HuoLiuXing', 'Hun_OutFit_FengXue'):
        choose(opened, ident)
        opened.add_selected()
    select_bag(opened, 'goods', 'Material')
    opened.quantity_spin.setValue(12)
    select_bag(opened, 'phantom', 'HuoLiuXing')
    opened.level_spin.setValue(1)
    assert session.save_path.read_bytes() == original
    QTest.mouseClick(opened.apply_button, Qt.MouseButton.LeftButton)
    assert opened.busy and not opened.add_button.isEnabled()
    wait(opened)
    assert session.source != original
    actual = {(i['category'], i['id']): i for i in session.inventory('AutoSave0')}
    assert actual[('goods', 'Material')]['quantity'] == 12
    assert actual[('phantom', 'HuoLiuXing')]['level'] == 1
    assert ('appearance', 'Hun_OutFit_FengXue') in actual
    assert ('accessory', 'AC_ResistRanged') in actual
    for old, new in zip(session.container.decode(original), session.entries):
        if old['name'] not in ('AutoSave0', '__Advanced__'):
            assert old['block'] == new['block']
    assert b'0.10000000000000001' in session._entry('AutoSave0')['raw']
    backups = list(session.backup_dir.glob('*.sav'))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert not opened.pending and not opened.undo_button.isEnabled()
    assert '已自动备份' in opened.status.text() and information == []

@pytest.mark.parametrize('cause', ['running', 'external'])
def test_refused_save_preserves_draft_and_file(opened, monkeypatch, cause):
    choose(opened, 'Material')
    opened.add_selected()
    session, original = opened.session, opened.session.source
    if cause == 'running':
        monkeypatch.setattr('pbz_editor.core._game_processes', lambda _: [123])
    else:
        session.save_path.write_bytes(b'external progress')
    expected = session.save_path.read_bytes()
    opened.apply_save()
    wait(opened)
    assert session.save_path.read_bytes() == expected and session.source == original
    assert opened.pending and opened.apply_button.isEnabled()
    assert not session.backup_dir.exists()

def test_new_medicine_replacement_is_visible_and_undoable(opened):
    choose(opened, 'Medicine_Recover')
    opened.add_selected()
    assert opened.quantity_spin.value() == 0 and not opened.quantity_spin.isEnabled()
    opened.medicine_slot.setCurrentIndex(opened.medicine_slot.findData('top'))
    assert opened.quantity_spin.value() == 1
    assert opened.owned[('medicine', 'hulu')]['quantity'] == 0
    assert opened.owned[('medicine', 'medicine_recover')]['equipped_slots'] == ['top']
    assert len(opened.pending) == 2
    opened.draft.validated_operations()
    opened.undo()
    assert opened.owned[('medicine', 'hulu')]['quantity'] == 3
    assert opened.owned[('medicine', 'hulu')]['equipped_slots'] == ['top']

def test_medicine_count_and_slot_survive_save(opened):
    choose(opened, 'Medicine_Recover')
    opened.add_selected()
    opened.medicine_slot.setCurrentIndex(opened.medicine_slot.findData('bottom'))
    opened.quantity_spin.setValue(2)
    opened.apply_save()
    wait(opened)
    assert opened.owned[('medicine', 'medicine_recover')]['quantity'] == 2
    assert opened.owned[('medicine', 'medicine_recover')]['equipped_slots'] == ['bottom']

def test_remove_equipped_weapon_is_rejected(opened):
    select_bag(opened, 'weapon', 'IronBlade')
    opened.remove_selected()
    assert ('weapon', 'ironblade') in opened.owned and not opened.pending

def test_remove_then_undo_restores_item(opened):
    select_bag(opened, 'goods', 'Material')
    opened.remove_selected()
    assert ('goods', 'material') not in opened.owned
    assert opened.bag_table.item(opened.bag_table.currentRow(), 2).text() == '已移出'
    opened.undo()
    assert opened.owned[('goods', 'material')]['quantity'] == 3

def test_add_reveals_result_despite_backpack_search(opened):
    opened.bag_search.setText('不存在')
    choose(opened, 'AC_ResistRanged')
    opened.add_selected()
    assert not opened.bag_search.text() and opened.bag_selected == ('accessory', 'ac_resistranged')

def test_library_filters_preserve_backpack_edits(opened):
    choose(opened, 'Material')
    opened.add_selected()
    opened.search.setText('不存在')
    assert not opened.visible_items
    assert opened.owned[('goods', 'material')]['quantity'] == 4 and len(opened.pending) == 1

@pytest.mark.parametrize('answer', [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Save])
def test_switching_progress_handles_unsaved_edits(opened, monkeypatch, answer):
    choose(opened, 'Material')
    opened.add_selected()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: answer)
    opened.slot_combo.setCurrentIndex(opened.slot_combo.findData('ManualSave1'))
    wait(opened)
    if answer == QMessageBox.StandardButton.Cancel:
        assert opened.slot_name() == 'AutoSave0' and opened.pending
        assert opened.slot_combo.currentData() == 'AutoSave0'
    else:
        assert opened.slot_name() == 'ManualSave1' and not opened.pending
        amount = next(i['quantity'] for i in opened.session.inventory('AutoSave0') if i['id'] == 'Material')
        assert amount == (4 if answer == QMessageBox.StandardButton.Save else 3)

def test_export_does_not_write_source_or_clear_draft(opened, tmp_path, monkeypatch):
    source = opened.session.save_path.read_bytes()
    choose(opened, 'Material')
    opened.add_selected()
    dest = tmp_path / 'export.sav'
    monkeypatch.setattr(ui.QFileDialog, 'getSaveFileName', lambda *_: (str(dest), ''))
    opened.export_save()
    wait(opened)
    assert dest.is_file() and opened.pending and opened.session.save_path.read_bytes() == source

@pytest.mark.parametrize('answer', [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Save])
def test_close_handles_unsaved_edits(opened, monkeypatch, answer):
    original = opened.session.save_path.read_bytes()
    choose(opened, 'Material')
    opened.add_selected()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: answer)
    opened.close()
    wait(opened)
    assert opened.isVisible() == (answer == QMessageBox.StandardButton.Cancel)
    assert (opened.session.save_path.read_bytes() != original) == (answer == QMessageBox.StandardButton.Save)

def test_region_and_category_filters_combine(opened, tmp_path):
    (tmp_path / 'regions.json').write_text(json.dumps({'regions': [
        {'id': 'village', 'name': '陆家村', 'order': 1}, {'id': 'town', 'name': '庞镇', 'order': 2}],
        'items': [{'id': 'AC_ResistRanged', 'category': 'accessory', 'regions': ['village'], 'evidence': [{'source': 'test'}]},
                  {'id': 'IronBlade', 'category': 'weapon', 'regions': ['town'], 'evidence': [{'source': 'test'}]}]}), encoding='utf8')
    opened.load_catalog(tmp_path / 'catalog.json')
    opened.region_filter.setCurrentIndex(opened.region_filter.findData('village'))
    assert [i['id'] for i in opened.visible_items] == ['AC_ResistRanged']
    opened.category_filter.setCurrentIndex(opened.category_filter.findData('weapon'))
    assert not opened.visible_items
    opened.region_filter.setCurrentIndex(opened.region_filter.findData('town'))
    assert [i['id'] for i in opened.visible_items] == ['IronBlade'] and not opened.pending

def test_startup_prefers_last_test_copy(application, window, tmp_path, monkeypatch):
    path = tmp_path / 'review-copy.sav'
    path.write_bytes(b'copy')
    calls = []
    monkeypatch.setattr(ui, 'load_settings', lambda: {'last_save': str(path), 'game_dir': 'test-game'})
    monkeypatch.setattr(ui, 'find_saves', lambda _: pytest.fail('must prefer last copy'))
    monkeypatch.setattr(ui.EditorWindow, 'load_save', lambda _self, value: calls.append(value))
    other = ui.EditorWindow(catalog=tmp_path / 'catalog.json')
    try:
        for _ in range(100):
            QApplication.processEvents()
            if calls:
                break
            QTest.qWait(10)
        assert calls == [str(path)]
    finally:
        other.close()
        other.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

def test_draft_projection_is_read_only_and_folds_repeated_edits(session):
    source, entries = session.source, [(e['raw'], e['json'], e['block']) for e in session.entries]
    draft = InventoryDraft(session, 'AutoSave0', ITEMS)
    for amount in (4, 20, 8):
        draft.stage({'id': 'Material', 'category': 'goods', 'action': 'set', 'quantity': amount})
    assert len(draft.operations()) == 1 and draft.operations()[0]['quantity'] == 8
    assert entries == [(e['raw'], e['json'], e['block']) for e in session.entries]
    assert session.save_path.read_bytes() == source and not session.backup_dir.exists()
    draft.validated_operations()

def test_draft_equipment_replace_then_remove_uses_final_state(session):
    draft = InventoryDraft(session, 'AutoSave0', ITEMS)
    draft.stage({'id': 'Medicine_Recover', 'category': 'medicine', 'action': 'add', 'quantity': 1, 'equip_slot': 'top'})
    draft.stage({'id': 'Hulu', 'category': 'medicine', 'action': 'remove'})
    ops = draft.validated_operations()
    assert any(op['id'] == 'Medicine_Recover' for op in ops[:-1])
    assert ops[-1]['action'] == 'remove'
    session.apply('AutoSave0', ops)
    assert not any(i['id'] == 'Hulu' for i in session.inventory('AutoSave0'))

def test_invalid_stage_is_atomic(session):
    draft = InventoryDraft(session, 'AutoSave0', ITEMS)
    with pytest.raises(EditorError):
        draft.stage({'id': 'Medicine_Recover', 'category': 'medicine', 'action': 'add', 'quantity': 1})
    assert not draft.changed_keys and not draft.history


def test_unequip_then_remove_medicine_preserves_final_equipment(session):
    draft = InventoryDraft(session, 'AutoSave0', ITEMS)
    draft.stage({'id': 'Hulu', 'category': 'medicine', 'action': 'set', 'quantity': 0, 'equip_slot': 'none'})
    draft.stage({'id': 'Hulu', 'category': 'medicine', 'action': 'remove'})
    session.apply('AutoSave0', draft.validated_operations())
    assert session._entry('AutoSave0')['json']['Inventory']['TopMedicine'] == 'None'
    assert not any(i['id'] == 'Hulu' for i in session.inventory('AutoSave0'))


def test_close_detects_uncommitted_typed_number(opened, monkeypatch):
    select_bag(opened, 'goods', 'Material')
    opened.quantity_spin.lineEdit().setText('18')
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Cancel)
    opened.close()
    assert opened.isVisible() and opened.owned[('goods', 'material')]['quantity'] == 18

def test_add_then_remove_new_item_is_not_dirty(session):
    draft = InventoryDraft(session, 'AutoSave0', ITEMS)
    draft.stage({'id': 'AC_ResistRanged', 'category': 'accessory', 'action': 'add'})
    draft.stage({'id': 'AC_ResistRanged', 'category': 'accessory', 'action': 'remove'})
    assert not draft.changed_keys and draft.validated_operations() == []


def test_icon_decoration_preserves_draft_selection_and_typed_value(opened, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage, QColor
    monkeypatch.setattr(ui, 'local_dir', lambda: tmp_path)
    icons = tmp_path/'icons'
    icons.mkdir()
    name = 'a'*64 + '.png'
    image = QImage(128, 128, QImage.Format.Format_ARGB32)
    image.fill(QColor('red'))
    image.save(str(icons/name), 'PNG')
    choose(opened, 'Material')
    opened.add_selected()
    opened.quantity_spin.setFocus()
    opened.quantity_spin.lineEdit().selectAll()
    QTest.keyClicks(opened.quantity_spin, '27')
    before = opened.session.save_path.read_bytes()
    pending = opened.pending
    opened._apply_icons({'items': {'goods:material': {'file': name}}, 'total': 1})
    assert opened.pending == pending
    assert opened.bag_selected == ('goods', 'material')
    assert opened.quantity_spin.lineEdit().text() == '27'
    assert not opened.item_table.item(0, 0).icon().isNull()
    assert not opened.bag_table.item(opened.bag_table.currentRow(), 0).icon().isNull()
    assert not opened.library_preview.isHidden() and not opened.backpack_preview.isHidden()
    assert opened.session.save_path.read_bytes() == before


def test_background_icon_preparation_does_not_block_or_clear_draft(opened, tmp_path, monkeypatch):
    from pbz_editor.icons import UTOC
    game = tmp_path/'installed'
    (game/UTOC).parent.mkdir(parents=True)
    (game/UTOC).write_bytes(b'fake index')
    opened.settings['game_dir'] = str(game)
    monkeypatch.setattr(ui, 'local_dir', lambda: tmp_path)
    monkeypatch.setattr('pbz_editor.icons.load_index', lambda *_: None)
    def prepare(*args, **kwargs):
        kwargs['progress'](1, 1)
        return {'items': {}, 'total': 1}
    monkeypatch.setattr('pbz_editor.icons.build_icons', prepare)
    choose(opened, 'Material')
    opened.add_selected()
    pending = opened.pending
    opened._ensure_icons()
    assert not opened.busy
    for _ in range(200):
        QApplication.processEvents()
        if not opened.icons_loading:
            break
        QTest.qWait(10)
    assert not opened.icons_loading and opened.pending == pending
    assert opened.icon_status.text() == '图标 0/1'


def test_language_switch_preserves_typed_edit_filters_history_and_source(opened):
    original = opened.session.save_path.read_bytes()
    choose(opened, 'Material')
    opened.add_selected()
    draft = opened.draft
    opened.quantity_spin.lineEdit().setText('27')
    opened.language_combo.setCurrentIndex(opened.language_combo.findData('en-US'))
    QApplication.processEvents()
    assert opened.open_button.text() == 'Open save'
    assert opened.draft is draft and opened.owned[('goods', 'material')]['quantity'] == 27
    assert opened.search.text() == 'Material' and opened.bag_selected == ('goods', 'material')
    assert opened.session.save_path.read_bytes() == original
    assert opened.settings['language'] == 'en-US'
    choose(opened, 'AC_ResistRanged')
    assert opened.selected_item['name'] == 'Arrowward'
    opened.search.setText('避箭')
    assert any(i['id'] == 'AC_ResistRanged' for i in opened.visible_items)
    opened.language_combo.setCurrentIndex(opened.language_combo.findData('zh-CN'))
    assert opened.open_button.text() == '打开存档'
    assert opened.draft is draft and opened.owned[('goods', 'material')]['quantity'] == 27
    opened.undo()
    assert opened.owned[('goods', 'material')]['quantity'] == 4
    opened.language_combo.setCurrentIndex(opened.language_combo.findData('en-US'))
    opened.apply_save()
    wait(opened)
    assert not opened.pending
    assert next(i for i in opened.session.inventory('AutoSave0') if i['id'] == 'Material')['quantity'] == 4


def test_persisted_language_used_at_startup(application, tmp_path, monkeypatch):
    catalog = tmp_path/'catalog.json'
    catalog.write_text(json.dumps({'items': ITEMS}), encoding='utf8')
    monkeypatch.setattr(ui, 'load_settings', lambda: {'language': 'en-US'})
    monkeypatch.setattr(ui, 'find_game', lambda: None)
    w = ui.EditorWindow(catalog=catalog)
    try:
        assert w.open_button.text() == 'Open save'
        assert w.language_combo.currentData() == 'en-US'
        assert w.item_lookup[('accessory', 'ac_resistranged')]['name'] == 'Arrowward'
    finally:
        w.close()
        w.deleteLater()
        ui.set_language('zh-CN')
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
