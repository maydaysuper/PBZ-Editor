import pytest
import json
from pathlib import Path
import struct

from pbz_editor.regions import RegionIndex, matches_region


def manifest():
    return {'regions': [{'id': 'village', 'name': '陆家村', 'order': 1},
                        {'id': 'town', 'name': '庞镇', 'order': 2}],
            'items': [{'id': 'AC_Test', 'category': 'accessory', 'regions': ['village', 'town'],
                       'evidence': [{'source': 'test-map-collection-table'}]}]}


def test_region_mapping_is_case_insensitive_and_requires_matching_category():
    index = RegionIndex(manifest())
    item = index.annotate({'id': 'ac_test', 'category': 'accessory'})
    assert item['region_names'] == ['陆家村', '庞镇']
    assert matches_region(item, 'village') and matches_region(item, 'town')
    assert matches_region(item, 'multi')
    assert index.annotate({'id': 'AC_Test', 'category': 'goods'})['region_status'] == 'unknown'


def test_missing_region_index_never_guesses_from_name_or_number(tmp_path):
    index = RegionIndex.load(tmp_path / 'missing.json')
    item = index.annotate({'id': 'I_02002', 'category': 'goods', 'name': '陆家村的物品'})
    assert item['region_ids'] == []
    assert matches_region(item, 'unknown')
    assert not matches_region(item, 'village')


def test_region_assignments_without_evidence_or_known_map_are_rejected():
    data = manifest()
    data['items'][0]['evidence'] = []
    with pytest.raises(ValueError, match='依据'):
        RegionIndex(data)
    data = manifest()
    data['items'][0]['regions'] = ['unverified-map']
    with pytest.raises(ValueError, match='未知地图'):
        RegionIndex(data)


def test_unknown_common_and_multi_are_distinct():
    data = manifest()
    data['items'].append({'id': 'Shared', 'category': 'goods', 'regions': [], 'status': 'common',
                          'evidence': [{'source': 'test-explicit-global-rule'}]})
    index = RegionIndex(data)
    common = index.annotate({'id': 'Shared', 'category': 'goods'})
    assert matches_region(common, 'common')
    assert not matches_region(common, 'unknown')
    assert not matches_region(common, 'multi')
    assert matches_region(common, 'all')


def test_debug_list_association_does_not_claim_verified_acquisition():
    data = manifest()
    data['items'][0]['evidence'] = [{'source': 'native-region-list', 'source_kind': 'debug_region_list'}]
    item = RegionIndex(data).annotate({'id': 'AC_Test', 'category': 'accessory'})
    assert item['region_association_only']


def test_unlocalized_map_id_is_preserved_but_not_shown_as_a_guessed_region():
    data = manifest()
    data['regions'][0]['name_verified'] = False
    data['items'][0]['regions'] = ['village']
    item = RegionIndex(data).annotate({'id': 'AC_Test', 'category': 'accessory'})
    assert item['region_ids'] == []
    assert item['unresolved_region_ids'] == ['village']
    assert matches_region(item, 'unknown')


def test_real_native_region_examples_and_association_boundary():
    from pbz_editor.catalog import Catalog
    root = Path(__file__).resolve().parents[1]
    path = root / '.local/reference-data/catalog.json'
    if not path.is_file():
        pytest.skip('Optional local game integration data not installed')
    catalog = Catalog.load(path)
    for ident, region in [('AC_ResistRanged', 'LuJiaCun'), ('HuoLiuXing', 'PangZhen'),
                          ('SlitheringSword', 'ZhengDaoDaYing'), ('Medicine_Recover', 'PangZhen')]:
        item = catalog.get(ident)
        assert region in item['region_ids']
        assert any(e['source_kind'] == 'map_collection' for e in item['region_evidence'])
    book = catalog.get('I_02002')
    assert book['region_ids'] == ['FuJiZhiLin']
    assert book['region_association_only']
    assert len([i for i in catalog.items if i.get('supported', True) and i['region_ids']]) == 666


def test_region_evidence_ids_match_actual_cached_fname_bytes_if_available():
    from pbz_editor.resources_reader import name_batch
    root = Path(__file__).resolve().parents[1]
    chunk = root / '.local/region-investigation/assets/DT_MapItemData.chunk'
    if not chunk.exists():
        pytest.skip('Private map table cache not installed')
    raw = chunk.read_bytes()
    names, _ = name_batch(raw)
    index = json.loads((root / '.local/reference-data/regions.json').read_text(encoding='utf8'))
    checked = 0
    for item in index['items']:
        for evidence in item['evidence']:
            ordinal, number = struct.unpack_from('<II', raw, evidence['offset'])
            value = names[ordinal] + (f'_{number - 1}' if number else '')
            assert value.casefold() == item['id'].casefold()
            checked += 1
    assert checked == 938
