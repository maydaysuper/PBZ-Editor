"""Searchable, provenance-bearing inventory catalog."""
from __future__ import annotations
import json
from pathlib import Path
from .regions import RegionIndex

CATEGORIES = {'goods':'普通物品','weapon':'主武器','phantom':'影之武装','accessory':'饰品','medicine':'药品','appearance':'外观'}

class Catalog:
    def __init__(self, items, metadata=None):
        self.items = list(items)
        self.metadata = metadata or {}
        self._index = {}
        for item in self.items:
            if item.get('category') not in CATEGORIES: raise ValueError(f'Unknown catalog category: {item.get("category")}')
            key = item['category'], item['id'].casefold()
            if key in self._index: raise ValueError(f'Duplicate catalog item: {key}')
            self._index[key] = item
    @classmethod
    def load(cls, path):
        data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        index=RegionIndex.load(Path(path).with_name('regions.json'))
        items=data if isinstance(data,list) else data['items']
        metadata={} if isinstance(data,list) else {k:v for k,v in data.items() if k!='items'}
        metadata['region_definitions']=index.regions
        return cls([index.annotate(item) for item in items],metadata)
    def get(self,item_id,category=None):
        if category: return self._index.get((category,item_id.casefold()))
        return next((item for item in self.items if item['id'].casefold()==item_id.casefold()),None)
    def search(self,query='',category=None):
        terms=query.casefold().split()
        return [item for item in self.items if (not category or item['category']==category) and all(term in ' '.join(str(item.get(k,'')) for k in ['id','name','name_en','description','description_en','subcategory','row_name','region_names','region_names_en']).casefold() for term in terms)]
