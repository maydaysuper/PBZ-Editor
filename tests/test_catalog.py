import json
from pathlib import Path
import pytest
from pbz_editor.catalog import Catalog

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def catalog():
    path = ROOT/'.local/reference-data/catalog.json'
    if not path.is_file():
        pytest.skip('Optional local game integration data not installed')
    return Catalog.load(path)

def test_real_catalog_has_every_parsed_table_category(catalog):
    assert catalog.metadata['categories']=={'goods':640,'weapon':33,'phantom':27,'accessory':73,'medicine':17,'appearance':54}
    assert len(catalog.items)==844
    assert sum(x['parsed_rows'] for x in catalog.metadata['sources'])==845
    assert all(x['declared_rows']==x['parsed_rows'] for x in catalog.metadata['sources'])

def test_native_item_ids_are_not_table_row_names(catalog):
    arrow=catalog.get('AC_ResistRanged','accessory')
    assert arrow['name']=='避箭护符'
    assert arrow['row_name']=='ResistRanged'
    assert arrow['evidence']['name_key']=='Item_Accessory_025'
    assert catalog.get('I_02002')['name']=='飞贼的秘籍'
    assert catalog.get('Weapon_Material_10')['name']=='溶剂'
    assert catalog.get('IronBlade','weapon')['name']=='残钢刃'
    assert catalog.get('BrutalAxe','phantom')['name']=='巨凶斧'
    assert catalog.get('Hulu','medicine')['name']=='药葫芦'

def test_search_supports_chinese_english_ids_and_categories(catalog):
    assert catalog.search('避箭')[0]['id']=='AC_ResistRanged'
    assert catalog.search('arrowward')[0]['id']=='AC_ResistRanged'
    assert catalog.search('Weapon_Material_10','goods')[0]['name']=='溶剂'
    assert not catalog.search('避箭','weapon')

def test_unique_ids_preserve_duplicate_native_variant(catalog):
    assert len({(x['category'],x['id']) for x in catalog.items})==len(catalog.items)
    assert catalog.get('HuoLiuXing','phantom')['variants']

def test_no_empty_placeholder_or_asset_path_ids(catalog):
    assert all(x['id'] not in ('','None') and '/' not in x['id'] for x in catalog.items)
    assert all(x['source'].endswith('.uasset') and x['verification'] in ('catalog_verified','native_schema_verified_runtime_untested') for x in catalog.items)
    outfits=[x for x in catalog.items if x['category']=='appearance' and x['supported']]
    parts=[x for x in catalog.items if x['category']=='appearance' and not x['supported']]
    assert len(outfits)==9 and all(x['scope']=='global' for x in outfits)
    assert len(parts)==45 and all(x['experimental'] and x['unsupported_reason'] for x in parts)
    assert catalog.metadata['editable_entries']==799

def test_level_evidence_is_explicit(catalog):
    assert catalog.get('AC_ResistRanged')['max_level']==1
    assert catalog.get('AC_PerfectFrame')['max_level']==2
    assert catalog.get('Hulu')['max_level']==6
    assert catalog.get('Hulu')['base_capacity']==2
    assert catalog.get('RongQi')['base_capacity']==1
    assert catalog.get('kuye')['base_capacity']==3
    assert catalog.get('Tang')['max_level'] is None

def test_names_are_single_line_plain_titles(catalog):
    assert all('\n' not in x['name'] and '<' not in x['name'] for x in catalog.items)
    assert all('\n' not in x['name_en'] and '<' not in x['name_en'] for x in catalog.items)
    mirrors=catalog.search('将军铜镜')
    assert mirrors and mirrors[0]['name']=='将军铜镜'
    assert '古铜' in mirrors[0]['description']
    assert catalog.get('R_03001')['name']=='【墨玑子绝笔信】其一'
    assert '今怪面之属' in catalog.get('R_03001')['description']

def test_native_table_extraction_matches_committed_metadata_if_cache_present(catalog):
    import importlib.util
    spec=importlib.util.spec_from_file_location('build_catalog',ROOT/'tools/build_catalog.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    seen=0
    for source in catalog.metadata['sources']:
        chunk=ROOT/'.local/cache'/f'{source["table"]}.chunk'
        if not chunk.exists():continue
        rows,count=mod.table_rows(chunk.read_bytes(),source['category'])
        assert count==source['declared_rows']
        assert len(rows)==count
        seen+=1
    if not seen:pytest.skip('Private extracted game table cache not installed')
