import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

from pbz_editor.core import EditorError, LosslessJSON, PBZSContainer, SaveSession, edit_inventory, sha256


def fixture_raw():
    # Deliberately non-canonical numeric literals outside the changed records.
    return b'{"PBZGameSystem":{"CycleCount":0,"DateTimeString":"2000.01.01-12.00.00","CurrentLevelName":"PBZ_Pangzhen_WP","HasGameTime":12345},"Inventory":{"InventoryInfos":{"Material":{"CurrentAmount":3,"MaxAmount":7}},"MainWeaponInfos":{"IronBlade":{"WeaponLevel":14,"AlreadySnapSoul":0}},"ShaChiWeaponInfo":{"ShachiHook":{"ShaChiWeaponLevel":7}},"AccessoryInfos":{"AC_Test":{"CurrentLevel":0,"UnrelatedNumber":0.10000000000000001}},"MedicineInfos":{"Hulu":{"CurrentLevel":0,"CurrentTime":3,"MaxTime":2,"BuffRemainTime":0}},"AccessoryIDs":["AC_Test","None"],"MainWeapons":["IronBlade","None"],"ShaqiWeapons":["ShachiHook","None"],"CurrentMainWeaponID":"IronBlade","TopMedicine":"Hulu","BottomMedicine":"None"},"Unknown":{"position":-12345.678900000001,"exponent":1.23400e-9,"long":99999999999999999,"text":"a,b:[]{}"}}'


def entries_fixture(container):
    entries = []
    for name, raw in [("__Advanced__", b'{"LastContinueSlot":"AutoSave0"}'), ("AutoSave0", fixture_raw()), ("ManualSave1", fixture_raw()), ("__Achievements__", b'{"UnlockedIds":[]}')]:
        entries.append({"name": name, "algorithm": 1, "flag": 1, "raw": raw,
                        "json": json.loads(raw), "block": container.seal(zlib.compress(raw))})
    return entries


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.container = PBZSContainer(bytes(range(32)))
        self.entries = entries_fixture(self.container)
        self.source = self.container.encode(self.entries, {})

    def test_full_roundtrip_preserves_slot_ciphertext(self):
        loaded = self.container.decode(self.source)
        self.assertEqual([(e['name'], e['raw'], e['block']) for e in loaded], [(e['name'], e['raw'], e['block']) for e in self.entries])

    def test_target_only_rewrite_for_raw_and_zlib(self):
        raw = fixture_raw().replace(b'12345', b'12346', 1)
        for compression in ('none', 'zlib'):
            encoded = self.container.encode(self.entries, {'AutoSave0': raw}, compression)
            loaded = self.container.decode(encoded)
            for before, after in zip(self.entries, loaded):
                if before['name'] == 'AutoSave0':
                    self.assertEqual(after['raw'], raw)
                else:
                    self.assertEqual(after['block'], before['block'])
                    self.assertEqual(after['raw'], before['raw'])

    def test_wrong_key_and_tampering_rejected(self):
        with self.assertRaises(EditorError):
            PBZSContainer(bytes(reversed(range(32)))).decode(self.source)
        for offset in (8, 24, 50, len(self.source)-1):
            corrupt = bytearray(self.source)
            corrupt[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(EditorError):
                self.container.decode(corrupt)

    def test_truncation_rejected(self):
        for length in (0, 4, 12, 40, len(self.source)-1):
            with self.subTest(length=length), self.assertRaises(EditorError):
                self.container.decode(self.source[:length])


class LosslessTests(unittest.TestCase):
    def test_object_delete_all_positions_with_whitespace(self):
        for key in ('a','b','c'):
            editor = LosslessJSON(b'{ "a": 1, "b": {"nested":3}, "c": 1.2300e-8 }')
            editor.remove([key])
            expected = {'a':1,'b':{'nested':3},'c':1.2300e-8}
            del expected[key]
            self.assertEqual(json.loads(editor.bytes()), expected)
            if key != 'c':
                self.assertIn(b'1.2300e-8', editor.bytes())
        editor = LosslessJSON(b'{"a":1}')
        editor.remove(['a'])
        self.assertEqual(editor.bytes(), b'{}')

    def test_insert_update_nested_and_array_preserves_other_literals(self):
        editor = LosslessJSON(b'{"x":{"a":1.2300e-9},"arr":["a","b"]}')
        editor.set(['x','b'], {'value':2})
        editor.set(['arr',1], 'c')
        self.assertIn(b'1.2300e-9', editor.bytes())
        self.assertEqual(editor.value['arr'], ['a','c'])
        editor.remove(['x','b'])
        self.assertEqual(editor.bytes(), b'{"x":{"a":1.2300e-9},"arr":["a","c"]}')

    def test_duplicate_keys_rejected(self):
        with self.assertRaises(EditorError):
            LosslessJSON(b'{"Inventory":{},"Inventory":{}}')

    def test_all_category_edits_and_unicode(self):
        ops = [
            {'action':'add','id':'NewMaterial','category':'goods','quantity':17},
            {'action':'add','id':'NewWeapon','category':'weapon','quantity':1,'level':12},
            {'action':'add','id':'NewPhantom','category':'phantom','quantity':1,'level':7},
            {'action':'add','id':'AC_ResistRanged','category':'accessory','quantity':1,'level':0,'name':'避箭护符'},
            {'action':'add','id':'NewMedicine','category':'medicine','quantity':3,'level':1,'base_capacity':2,'equip_slot':'bottom'},
        ]
        raw, changes = edit_inventory(fixture_raw(), ops)
        obj = json.loads(raw)
        self.assertEqual(len(changes),5)
        self.assertEqual(obj['Inventory']['InventoryInfos']['NewMaterial']['CurrentAmount'],17)
        self.assertEqual(obj['Inventory']['MainWeaponInfos']['NewWeapon']['WeaponLevel'],13)
        self.assertEqual(obj['Inventory']['ShaChiWeaponInfo']['NewPhantom']['ShaChiWeaponLevel'],8)
        self.assertEqual(obj['Inventory']['AccessoryInfos']['AC_ResistRanged']['CurrentLevel'],0)
        self.assertEqual(obj['Inventory']['MedicineInfos']['NewMedicine']['CurrentTime'],3)
        original_unknown = fixture_raw()[fixture_raw().index(b'"Unknown"'):]
        self.assertTrue(raw.endswith(original_unknown))
        self.assertIn(b'"UnrelatedNumber":0.10000000000000001',raw)

    def test_add_set_remove_and_unequip_accessory(self):
        raw,_=edit_inventory(fixture_raw(),[{'action':'add','id':'Material','category':'goods','quantity':4}])
        self.assertEqual(json.loads(raw)['Inventory']['InventoryInfos']['Material']['CurrentAmount'],7)
        raw,_=edit_inventory(raw,[{'action':'set','id':'Material','category':'goods','quantity':50}])
        self.assertEqual(json.loads(raw)['Inventory']['InventoryInfos']['Material']['CurrentAmount'],50)
        raw,_=edit_inventory(raw,[{'action':'remove','id':'AC_Test','category':'accessory'}])
        self.assertNotIn('AC_Test',json.loads(raw)['Inventory']['AccessoryInfos'])
        self.assertEqual(json.loads(raw)['Inventory']['AccessoryIDs'][0],'None')

    def test_invalid_values_and_equipped_deletion_are_rejected(self):
        bad = [
            {'category':'goods','id':'x','quantity':-1},
            {'category':'goods','id':'x','quantity':True},
            {'category':'goods','id':'x','quantity':2**31},
            {'category':'weapon','id':'x','quantity':10},
            {'category':'weapon','id':'x','level':5,'max_level':2},
            {'category':'appearance','id':'x'},
            {'action':'remove','category':'weapon','id':'IronBlade'},
            {'action':'remove','category':'phantom','id':'ShachiHook'},
            {'action':'remove','category':'medicine','id':'Hulu'},
        ]
        for operation in bad:
            with self.subTest(operation=operation), self.assertRaises(EditorError):
                edit_inventory(fixture_raw(),[operation])

    def test_add_existing_unique_does_not_downgrade_but_set_can(self):
        for category,item_id,field,level_field in [('weapon','IronBlade','MainWeaponInfos','WeaponLevel'),('phantom','ShachiHook','ShaChiWeaponInfo','ShaChiWeaponLevel')]:
            raw,_=edit_inventory(fixture_raw(),[{'action':'add','id':item_id,'category':category,'level':0}])
            old=json.loads(fixture_raw())['Inventory'][field][item_id][level_field]
            self.assertEqual(json.loads(raw)['Inventory'][field][item_id][level_field],old)
            raw,_=edit_inventory(raw,[{'action':'set','id':item_id,'category':category,'level':0}])
            self.assertEqual(json.loads(raw)['Inventory'][field][item_id][level_field],1)
        source=fixture_raw().replace(b'"AC_Test":{"CurrentLevel":0',b'"AC_Test":{"CurrentLevel":1').replace(b'"Hulu":{"CurrentLevel":0',b'"Hulu":{"CurrentLevel":2')
        for category,item_id,field in [('accessory','AC_Test','AccessoryInfos'),('medicine','Hulu','MedicineInfos')]:
            raw,_=edit_inventory(source,[{'action':'add','id':item_id,'category':category,'level':0}])
            self.assertGreater(json.loads(raw)['Inventory'][field][item_id]['CurrentLevel'],0)

    def test_casefold_identifier_reuses_existing_fname_key(self):
        raw,_=edit_inventory(fixture_raw(),[{'action':'add','id':'MATERIAL','category':'goods','quantity':2}])
        records=json.loads(raw)['Inventory']['InventoryInfos']
        self.assertEqual(records['Material']['CurrentAmount'],5)
        self.assertNotIn('MATERIAL',records)

    def test_new_medicine_uses_native_base_capacity_and_existing_capacity_survives(self):
        raw,_=edit_inventory(fixture_raw(),[{'action':'add','id':'KuYe','category':'medicine','quantity':1,'equip_slot':'bottom'}])
        record=json.loads(raw)['Inventory']['MedicineInfos']['KuYe']
        self.assertEqual(record['MaxTime'],3)
        self.assertEqual(record['CurrentTime'],1)
        raw,_=edit_inventory(raw,[{'action':'add','id':'Hulu','category':'medicine','quantity':1}])
        record=json.loads(raw)['Inventory']['MedicineInfos']['Hulu']
        self.assertEqual(record['MaxTime'],2)
        self.assertEqual(record['CurrentTime'],4)
        with self.assertRaises(EditorError):
            edit_inventory(raw,[{'action':'add','id':'UnknownMedicine','category':'medicine','quantity':1}])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source_path = self.directory/'PBZSaveGame.sav'
        self.container = PBZSContainer(bytes(range(32)))
        self.entries = entries_fixture(self.container)
        self.source = self.container.encode(self.entries,{})
        self.source_path.write_bytes(self.source)
        self.session = SaveSession(self.source_path,self.directory,self.container,self.source,self.directory/'private')
        self.ops = [{'action':'add','id':'AC_ResistRanged','category':'accessory','level':0}]

    def tearDown(self):
        self.temp.cleanup()

    def test_slot_and_inventory_contract(self):
        self.assertEqual(self.session.current_slot,'AutoSave0')
        self.assertEqual(len(self.session.slots),2)
        self.assertEqual(self.session.slots[0]['playtime'],12345)
        self.assertTrue(any(i['id']=='IronBlade' and i['category']=='weapon' for i in self.session.inventory('AutoSave0')))

    def test_export_does_not_modify_source_and_no_overwrite(self):
        result = self.session.export('AutoSave0',self.ops,self.directory/'export.sav')
        self.assertTrue(result['file_verified'])
        self.assertEqual(self.source_path.read_bytes(),self.source)
        target = self.directory/'export.sav'
        target_bytes = target.read_bytes()
        with self.assertRaises(EditorError):
            self.session.export('AutoSave0',self.ops,target)
        self.assertEqual(target.read_bytes(),target_bytes)
        with self.assertRaises(EditorError):
            self.session.export('AutoSave0',self.ops,self.source_path)

    @patch('pbz_editor.core._game_processes',return_value=[])
    def test_apply_backup_and_restore_on_disposable_copy(self, _):
        result=self.session.apply('AutoSave0',self.ops)
        backup=Path(result['backup_path'])
        self.assertEqual(backup.read_bytes(),self.source)
        loaded=self.container.decode(self.source_path.read_bytes())
        self.assertIn('AC_ResistRanged',next(e for e in loaded if e['name']=='AutoSave0')['json']['Inventory']['AccessoryInfos'])
        restored=self.session.restore(backup)
        self.assertEqual(self.source_path.read_bytes(),self.source)
        self.assertTrue(Path(restored['backup_path']).is_file())
        self.assertFalse(list(self.directory.glob('*.writing')))

    @patch('pbz_editor.core._game_processes',return_value=[])
    def test_concurrent_source_change_prevents_apply(self, _):
        self.source_path.write_bytes(b'new progress')
        with self.assertRaises(EditorError):
            self.session.apply('AutoSave0',self.ops)
        self.assertEqual(self.source_path.read_bytes(),b'new progress')

    @patch('pbz_editor.core._game_processes',return_value=[123])
    def test_running_game_prevents_live_write_but_allows_export(self, _):
        with self.assertRaises(EditorError):
            self.session.apply('AutoSave0',self.ops)
        self.session.export('AutoSave0',self.ops,self.directory/'export.sav')
        self.assertEqual(self.source_path.read_bytes(),self.source)

    @patch('pbz_editor.core._game_processes',return_value=[])
    def test_corrupt_backup_rejected(self, _):
        result=self.session.apply('AutoSave0',self.ops)
        backup=Path(result['backup_path'])
        backup.write_bytes(b'corrupt')
        current=self.source_path.read_bytes()
        with self.assertRaises(EditorError):
            self.session.restore(backup)
        self.assertEqual(self.source_path.read_bytes(),current)

    def test_atomic_create_never_clobbers_existing_file(self):
        target=self.directory/'other.sav'
        target.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            SaveSession._atomic_write(target,b'replacement')
        self.assertEqual(target.read_bytes(),b'existing')
        self.assertFalse(list(self.directory.glob('*.writing')))

    def test_global_outfit_plan_modifies_only_global_slot(self):
        ops=[{'action':'add','id':'Hun_OutFit_FengXue','category':'appearance','quantity':1}]
        preview=self.session.preview('AutoSave0',ops)
        self.assertTrue(preview['has_global_changes'])
        self.assertEqual(preview['affected_slots'],['__Advanced__'])
        out=self.directory/'outfit.sav'
        self.session.export('AutoSave0',ops,out)
        decoded=self.container.decode(out.read_bytes())
        for old,new in zip(self.entries,decoded):
            if old['name']=='__Advanced__':
                self.assertEqual(new['json']['PBZAppearanceComponent']['Hun']['Outfits']['Hun_OutFit_FengXue'],{'HasRedPoint':True})
                self.assertEqual(new['json']['LastContinueSlot'],'AutoSave0')
            else:
                self.assertEqual(old['block'],new['block'])

    def test_mixed_outfit_and_inventory_changes_and_component_rejection(self):
        ops=self.ops+[{'action':'add','id':'Hun_OutFit_FengXue','category':'appearance'}]
        candidate,_=self.session._candidate('AutoSave0',ops)
        decoded=self.container.decode(candidate)
        self.assertIn('AC_ResistRanged',next(e for e in decoded if e['name']=='AutoSave0')['json']['Inventory']['AccessoryInfos'])
        advanced=next(e for e in decoded if e['name']=='__Advanced__')['json']
        self.assertIn('Hun_OutFit_FengXue',advanced['PBZAppearanceComponent']['Hun']['Outfits'])
        with self.assertRaises(EditorError):
            self.session.preview('AutoSave0',[{'action':'add','id':'HairStyle_Climax','category':'appearance'}])

    def test_unknown_executable_version_rejected_before_key_lookup(self):
        executable=self.directory/'game/platform/steam/PBZ.exe'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'MZ-not-the-supported-build')
        with self.assertRaisesRegex(EditorError,'26266538'):
            SaveSession.open(self.source_path,self.directory/'game')

    def test_outfit_remove_preserves_other_global_fields(self):
        from pbz_editor.core import edit_appearance
        raw=b'{"PBZAppearanceComponent":{"Hun":{"Outfits":{"Hun_OutFit_FengXue":{},"Hun_OutFit_Climax":{"HasRedPoint":false}},"CurrentOutfit":"Hun_OutFit_Climax"}},"UnknownFloat":0.10000000000000001}'
        modified,changes=edit_appearance(raw,[{'action':'remove','id':'Hun_OutFit_FengXue','category':'appearance'}])
        obj=json.loads(modified)
        self.assertNotIn('Hun_OutFit_FengXue',obj['PBZAppearanceComponent']['Hun']['Outfits'])
        self.assertEqual(obj['PBZAppearanceComponent']['Hun']['CurrentOutfit'],'Hun_OutFit_Climax')
        self.assertIn(b'0.10000000000000001',modified)


if __name__=='__main__':
    unittest.main()
