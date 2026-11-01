"""Evidence-backed map collection regions, separate from the item catalog."""
from __future__ import annotations

import json
from pathlib import Path


class RegionIndex:
    def __init__(self, data=None):
        data = data or {}
        self.regions = list(data.get('regions', []))
        self.by_id = {}
        for region in self.regions:
            ident, name = region.get('id'), region.get('name')
            if not isinstance(ident, str) or not ident or ident in self.by_id:
                raise ValueError('区域编号无效或重复。')
            if not isinstance(name, str) or not name:
                raise ValueError('区域缺少显示名称。')
            self.by_id[ident] = region
        self.regions.sort(key=lambda value: (value.get('order', 999), value['name']))
        self.items = {}
        for item in data.get('items', []):
            category, ident = item.get('category'), item.get('id')
            if not isinstance(ident, str) or not ident or not isinstance(category, str):
                raise ValueError('区域索引包含无效物品。')
            key = category, ident.casefold()
            if key in self.items:
                raise ValueError('区域索引包含重复物品。')
            regions = list(dict.fromkeys(item.get('regions', [])))
            status = item.get('status', 'verified')
            if status not in ('verified', 'common') or not item.get('evidence'):
                raise ValueError('区域归属缺少依据。')
            if any(region not in self.by_id for region in regions) or (status == 'verified' and not regions):
                raise ValueError('区域索引引用了未知地图。')
            self.items[key] = {**item, 'regions': regions, 'status': status}

    @classmethod
    def load(cls, path: Path):
        if not path.is_file():
            return cls()
        return cls(json.loads(path.read_text(encoding='utf-8-sig')))

    def annotate(self, item):
        result = dict(item)
        entry = self.items.get((item['category'], item['id'].casefold()))
        raw_ids = list(entry['regions']) if entry else []
        result['region_ids'] = [ident for ident in raw_ids if self.by_id[ident].get('name_verified', True)]
        result['unresolved_region_ids'] = [ident for ident in raw_ids if ident not in result['region_ids']]
        result['region_names'] = [self.by_id[ident]['name'] for ident in result['region_ids']]
        result['region_names_en'] = [self.by_id[ident].get('name_en') or self.by_id[ident]['name'] for ident in result['region_ids']]
        result['region_status'] = entry['status'] if entry and (result['region_ids'] or entry['status'] == 'common') else 'unknown'
        result['region_evidence'] = list(entry['evidence']) if entry else []
        source_kinds = {e.get('source_kind') for e in result['region_evidence'] if isinstance(e, dict)}
        result['region_association_only'] = 'debug_region_list' in source_kinds and 'map_collection' not in source_kinds
        return result


def matches_region(item, selection):
    if selection in (None, '', 'all'):
        return True
    if selection == 'unknown':
        return item.get('region_status', 'unknown') == 'unknown'
    if selection == 'common':
        return item.get('region_status') == 'common'
    if selection == 'multi':
        return len(item.get('region_ids', [])) > 1
    return selection in item.get('region_ids', [])
