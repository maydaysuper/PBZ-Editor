"""Regression cases grounded in build 26266538's actual JSON loader/serializer."""
import json

import pytest

from pbz_editor.core import EditorError, LosslessJSON, edit_inventory, inventory_level, medicine_slots


def source():
    return b'{"PBZGameSystem":{"CycleCount":0},"Inventory":{"MainWeaponInfos":{"Sword":{}},"ShaChiWeaponInfo":{"HuoLiuXing":{}},"MedicineInfos":{"Hulu":{"CurrentTime":3,"MaxTime":2,"CurrentLevel":0,"Extra":0.10000000000000001}},"TopMedicine":"Hulu","BottomMedicine":"None"},"Unknown":1.23400e-9}'


@pytest.mark.parametrize('category,field', [('weapon', 'WeaponLevel'), ('phantom', 'ShaChiWeaponLevel')])
def test_native_omitted_grade_one_is_initial_strengthening(category, field):
    assert inventory_level(category, {}) == 0
    assert inventory_level(category, {field: 1}) == 0
    assert inventory_level(category, {field: 2}) == 1


@pytest.mark.parametrize('category,item_id,map_name,field', [
    ('weapon', 'Sword', 'MainWeaponInfos', 'WeaponLevel'),
    ('phantom', 'HuoLiuXing', 'ShaChiWeaponInfo', 'ShaChiWeaponLevel')])
def test_requested_first_upgrade_writes_native_grade_two(category, item_id, map_name, field):
    edited, _ = edit_inventory(source(), [{'id': item_id, 'category': category, 'level': 1}])
    assert json.loads(edited)['Inventory'][map_name][item_id][field] == 2
    assert inventory_level(category, json.loads(edited)['Inventory'][map_name][item_id]) == 1


def test_unlocking_unequipped_medicine_defaults_to_zero_without_touching_shortcuts():
    edited, _ = edit_inventory(source(), [{'id': 'Medicine_Recover', 'category': 'medicine'}])
    inv = json.loads(edited)['Inventory']
    assert inv['MedicineInfos']['Medicine_Recover']['CurrentTime'] == 0
    assert inv['MedicineInfos']['Medicine_Recover']['MaxTime'] == 1
    assert (inv['TopMedicine'], inv['BottomMedicine']) == ('Hulu', 'None')
    assert inv['MedicineInfos']['Hulu']['CurrentTime'] == 3


def test_positive_count_requires_explicit_slot_and_is_never_silently_discarded():
    with pytest.raises(EditorError, match='未装备'):
        edit_inventory(source(), [{'id': 'Medicine_Recover', 'category': 'medicine', 'quantity': 1}])


def test_equipping_to_unused_slot_retains_existing_equipped_medicine():
    edited, changes = edit_inventory(source(), [{'id': 'Medicine_Recover', 'category': 'medicine',
                                               'quantity': 1, 'equip_slot': 'bottom'}])
    inv = json.loads(edited)['Inventory']
    assert (inv['TopMedicine'], inv['BottomMedicine']) == ('Hulu', 'Medicine_Recover')
    assert inv['MedicineInfos']['Medicine_Recover']['CurrentTime'] == 1
    assert inv['MedicineInfos']['Hulu']['CurrentTime'] == 3
    assert changes[0]['equipment_after']['BottomMedicine'] == 'Medicine_Recover'


def test_replacing_slot_previews_native_count_reset_and_preserves_other_number_literals():
    edited, changes = edit_inventory(source(), [{'id': 'Medicine_Recover', 'category': 'medicine',
                                               'quantity': 1, 'equip_slot': 'top'}])
    inv = json.loads(edited)['Inventory']
    assert inv['MedicineInfos']['Hulu']['CurrentTime'] == 0
    displaced = next(c for c in changes if c['id'] == 'Hulu')
    assert displaced['before']['CurrentTime'] == 3
    assert displaced['after']['CurrentTime'] == 0
    assert 'note' in displaced
    assert b'0.10000000000000001' in edited
    assert b'1.23400e-9' in edited


def test_moving_medicine_clears_duplicate_shortcut_case_insensitively():
    edited, _ = edit_inventory(source(), [{'id': 'hULu', 'category': 'medicine', 'quantity': 0,
                                          'equip_slot': 'bottom'}])
    inv = json.loads(edited)['Inventory']
    assert inv['TopMedicine'] == 'None'
    assert inv['BottomMedicine'] == 'Hulu'
    assert inv['MedicineInfos']['Hulu']['CurrentTime'] == 3
    assert medicine_slots(inv, 'HULU') == ['bottom']


def test_final_batch_cannot_promise_positive_counts_for_two_replacements_of_same_slot():
    with pytest.raises(EditorError, match='未装备'):
        edit_inventory(source(), [
            {'id': 'Medicine_Recover', 'category': 'medicine', 'quantity': 1, 'equip_slot': 'bottom'},
            {'id': 'NingFengDan', 'category': 'medicine', 'quantity': 1, 'equip_slot': 'bottom'}])


def test_explicit_unequip_zeroes_current_usage_in_preview():
    edited, _ = edit_inventory(source(), [{'action': 'set', 'id': 'Hulu', 'category': 'medicine',
                                          'quantity': 0, 'equip_slot': 'none'}])
    assert json.loads(edited)['Inventory']['TopMedicine'] == 'None'
    assert json.loads(edited)['Inventory']['MedicineInfos']['Hulu']['CurrentTime'] == 0


def test_slot_option_is_not_accepted_for_another_category():
    with pytest.raises(EditorError, match='只适用于药品'):
        edit_inventory(source(), [{'id': 'Sword', 'category': 'weapon', 'equip_slot': 'top'}])


def test_positive_medicine_count_preview_does_not_promise_exact_runtime_persistence():
    edited, changes = edit_inventory(source(), [{'id': 'Medicine_Recover', 'category': 'medicine',
                                               'quantity': 1, 'equip_slot': 'bottom'}])
    assert json.loads(edited)['Inventory']['MedicineInfos']['Medicine_Recover']['CurrentTime'] == 1
    assert '不能保证精确保持' in changes[0]['note']
