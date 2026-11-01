"""Build local catalog metadata directly from verified native rows."""
import re, struct, hashlib
from collections import Counter
from .resources_reader import Reader, name_batch

TABLES={
    'DT_InventoryData':('goods','ProjectPBZ/Content/PBZContent/DataTable/Item/DT_InventoryData.uasset'),
    'DT_WeaponData':('weapon','ProjectPBZ/Content/PBZContent/DataTable/UI/DT_WeaponData.uasset'),
    'DT_ShaqiWeaponData':('phantom','ProjectPBZ/Content/PBZContent/DataTable/UI/DT_ShaqiWeaponData.uasset'),
    'DT_Accessory':('accessory','ProjectPBZ/Content/PBZContent/DataTable/UI/DT_Accessory.uasset'),
    'DT_MedicineTable':('medicine','ProjectPBZ/Content/PBZContent/DataTable/UI/DT_MedicineTable.uasset'),
    'DT_PBZAppearanceTable':('appearance','ProjectPBZ/Content/PBZContent/DataTable/UI/DT_PBZAppearanceTable.uasset'),
}


def plain_text(text):
    text=re.sub(r'<dtv\b[^>]*/>', '〔等级数值〕',text)
    text=re.sub(r'<InputAction\b[^>]*/>', '〔操作键〕',text)
    return re.sub(r'<[^>]*>','',text).strip()


def row_schema(row):
    from .region_builder import header
    properties, zero, after = header(row['raw'], 8)
    if after >= len(row['raw']) or not properties or max(properties) > 1024:
        raise ValueError('Invalid item property header')
    return properties


def build_catalog(store, zh, en, expected, layouts=None, tolerant=False):
    lookup = {path.casefold(): path for path in store.files}
    items, coverage, unavailable = {}, [], {}
    for table, (category, source) in TABLES.items():
        try:
            data = store.read_file(lookup[source.casefold()])
            checksum = hashlib.sha256(data).hexdigest()
            reference = checksum == expected.get(source)
            if not reference and not tolerant:
                raise ValueError('物品表与已适配版本不一致：' + table)
            rows, count = table_rows(data, category)
            if any(row['id'] not in ('None', '') and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,255}', row['id']) for row in rows):
                raise ValueError('Unrecognized item identifier layout')
            if not reference:
                accepted = (layouts or {}).get(category, [])
                if not accepted or any(row_schema(row) not in accepted for row in rows):
                    raise ValueError('Item property schema changed')
            # Validate the entire category before accepting any of its rows.
            parsed = [item_metadata(row, category, source, zh, en)
                      for row in rows if row['id'] not in ('None', '')]
        except (ValueError, KeyError, IndexError, struct.error, OSError) as error:
            if not tolerant:
                raise
            unavailable[category] = str(error)
            continue
        omitted, duplicates = [], []
        parsed = iter(parsed)
        for row in rows:
            if row['id'] in ('None', ''):
                omitted.append(row['row_name'])
                continue
            item = next(parsed)
            key = category, item['id'].casefold()
            if key in items:
                duplicates.append({'id': item['id'], 'row_name': item['row_name']})
                items[key].setdefault('variants', []).append({k: item[k] for k in
                    ('row_name', 'name', 'source', 'experimental', 'evidence')})
            else:
                items[key] = item
        coverage.append({'table': table, 'source': source, 'category': category,
            'declared_rows': count, 'parsed_rows': len(rows), 'sha256': checksum,
            'compatibility': 'reference' if reference else 'structural',
            'omitted_empty_ids': omitted, 'duplicate_variants': duplicates})
    return {'schema_version': 1, 'game': 'Phantom Blade Zero', 'reference_build': '26266538',
        'verification': 'catalog_verified; game-runtime NOT_TESTED',
        'categories': dict(Counter(i['category'] for i in items.values())),
        'editable_entries': sum(i['supported'] for i in items.values()),
        'non_item_assets': sum(not i['supported'] for i in items.values()),
        'sources': coverage, 'items': list(items.values()),
        'available_categories': [category for category, _ in TABLES.values() if category not in unavailable],
        'unavailable_categories': unavailable}


def normalize_name(raw,fallback=''):
    title_match=re.search(r'<LetterBold\b[^>]*>(.*?)</>',raw,re.S|re.I)
    if title_match:
        return plain_text(title_match.group(1)).replace('\n',' ').strip(),plain_text(raw[title_match.end():])
    text=plain_text(raw)
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if not lines:return fallback,''
    return lines[0],'\n'.join(lines[1:])


def table_rows(data,category):
    names,_=name_batch(data)
    header=struct.unpack_from('<I',data,4)[0]
    declared=struct.unpack_from('<I',data,header+10)[0]
    if not 1 <= declared <= 50000:
        raise ValueError('Invalid table row count')
    start=header+14
    if category in ('weapon','phantom'):
        tag='Weapon.Slot.Main' if category=='weapon' else 'Weapon.Slot.ShaChi'
        sig=struct.pack('<II',names.index(tag),0)+bytes.fromhex('800301800301')
        ends=[m.end() for m in re.finditer(re.escape(sig),data)]
        starts=[start]+ends[:-1]
        if not ends or ends[-1]!=len(data): raise ValueError('Weapon table terminal mismatch')
    else:
        schema=data[start+8:start+10]
        starts=[]
        for p in range(start,len(data)-12):
            index,num=struct.unpack_from('<II',data,p)
            if index<len(names) and num==0 and data[p+8:p+10]==schema: starts.append(p)
        ends=starts[1:]+[len(data)]
    if len(starts)!=declared: raise ValueError(f'Table row count mismatch {len(starts)} != {declared}')
    rows=[]
    for p,end in zip(starts,ends):
        name_index,number=struct.unpack_from('<II',data,p)
        rowname=names[name_index]+(f'_{number-1}' if number else '')
        id_offset=end-(24 if category in ('weapon','phantom') else 17)
        id_index,id_number=struct.unpack_from('<II',data,id_offset)
        # Some goods include a nonempty trailing event/tag property (seven
        # additional bytes). The primary item ID remains before its 800301
        # inherited-property header. Never substitute the trailing event tag.
        if category=='goods' and (id_index>=len(names) or id_number>1_000_000):
            id_offset=end-24;id_index,id_number=struct.unpack_from('<II',data,id_offset)
        if id_index>=len(names) or id_number>1_000_000: raise ValueError(f'Invalid ID field in {rowname}')
        item_id=names[id_index]+(f'_{id_number-1}' if id_number else '')
        raw=data[p:end]
        text_keys=[]
        marker=b'\x08\0\0\0Default\0'
        for match in re.finditer(re.escape(marker),raw):
            try:
                key=Reader(raw,match.end()).fstring()
                if key and key not in text_keys:text_keys.append(key)
            except (ValueError,UnicodeError):pass
        rows.append({'row_name':rowname,'id':item_id,'raw':raw,'start':p,'end':end,'id_offset':id_offset,'text_keys':text_keys})
    return rows,declared


def item_metadata(row,category,source,zh,en):
    keys=row['text_keys']
    name_key=next((k for k in keys if k in zh),None)
    raw_name=zh.get(name_key,row['row_name'])
    display,name_body=normalize_name(raw_name,row['row_name'])
    english_name,english_body=normalize_name(en.get(name_key,''))
    # Several reading records omit a separate localized title and their first
    # FText is the entire letter. Its native table row is the actual title.
    # Do not put the first paragraph of a letter into the item-name column.
    if name_key and ('Des' in name_key or 'Function' in name_key) and '<LetterBold' not in raw_name:
        display=row['row_name'];name_body=plain_text(raw_name);english_name='';english_body=plain_text(en.get(name_key,''))
    # Localized title is the first FText property. Other localized values are
    # description/effect fields, retained in serialization order.
    descriptions=[]
    for key in keys[1:]:
        text=zh.get(key,'')
        if text and text!=display and text not in descriptions and ('Des' in key or 'Func' in key or 'description' in key.casefold()): descriptions.append(text)
    effect_descriptions=[zh[k] for k in keys if k in zh and ('FunctionDes' in k or 'Func_' in k)]
    experimental=bool(re.search(r'test|debug|placeholder|horsecart|prologue|测试|占位|废弃|马车战|序章|大魂用',row['row_name']+' '+row['id'],re.I))
    max_level=None
    if category=='accessory':
        upgrades=[int(m.group(1)) for key in keys if (m:=re.fullmatch(r'Item_Accessory_\d+_(\d+)',key))]
        max_level=max(upgrades,default=0)
    if category=='medicine':
        # The native GetMedicineMaxLevel implementation at 0x146956980
        # explicitly clamps the table's level count to six. The shipped UI
        # calls that helper rather than deriving a limit from localized text.
        max_level=6
    subcategory=''
    if category=='goods':
        ident=row['id']
        if ident.startswith(('Weapon_Material','Medi_Material','Ac_Material')):subcategory='强化材料'
        elif ident.startswith('K_'):subcategory='钥匙与机关'
        elif ident.startswith('I_'):subcategory='任务道具'
        elif ident.startswith('Page_'):subcategory='书页'
        elif ident.startswith('T_'):subcategory='宝藏'
        elif ident.startswith(('F_','FA_','R_')):subcategory='收集与记录'
        elif ident.startswith('Item_Piece_'):subcategory='碎片'
        elif ident.startswith('Return_'):subcategory='返还道具'
        else:subcategory='其他物品'
    outfit=category=='appearance' and row['id'].startswith('Hun_OutFit_')
    part=category=='appearance' and not outfit
    if category=='appearance':subcategory='完整套装' if outfit else '外观组成资产（非可解锁物品）'
    description=plain_text('\n\n'.join(descriptions[:2])) or name_body
    english_descriptions=[]
    for key in keys[1:]:
        text=en.get(key,'')
        if text and text!=english_name and text not in english_descriptions and ('Des' in key or 'Func' in key or 'description' in key.casefold()):
            english_descriptions.append(text)
    description_en=plain_text('\n\n'.join(english_descriptions[:2])) or english_body
    item={'id':row['id'],'name':display,'name_en':english_name,'raw_name':raw_name,'category':category,'subcategory':subcategory,'description':description,'effects':[plain_text(x) for x in effect_descriptions[:8]],'max_level':max_level,'source':source,'row_name':row['row_name'],'experimental':experimental or part,'verification':'native_schema_verified_runtime_untested' if outfit else 'catalog_verified','supported':not part,'scope':'global' if outfit else 'slot','unsupported_reason':'模型、头发等外观组成资产，不是可解锁套装' if part else '', 'evidence':{'row_start':row['start'],'row_end':row['end'],'id_offset':row['id_offset'],'name_key':name_key}}
    item['description_en']=description_en
    if outfit:
        from .core import KNOWN_OUTFITS
        if row['id'] not in KNOWN_OUTFITS:
            item['supported'] = False
            item['unsupported_reason'] = '该服装尚未验证解锁规则，仅供浏览。'
    if category=='accessory':
        item['base_max_level']=max_level
        item['max_level_note']='基础升级上限；高周目额外打磨由游戏按周目计算，尚未统一解析'
    if category=='medicine':
        raw=row['raw']
        candidates=[q for q in range(len(raw)-200,len(raw)-150) if raw[q-5:q]==bytes.fromhex('ff00000000') and 1<=struct.unpack_from('<I',raw,q)[0]<=10 and raw[q+4] in (1,3)]
        if len(candidates)!=1:raise ValueError(f'Medicine base usage field ambiguous: {row["id"]}')
        q=candidates[0]
        item['base_capacity']=struct.unpack_from('<I',raw,q)[0]
        item['evidence']['base_capacity_offset']=row['start']+q
        item['evidence']['base_capacity_native']='PushMedicineToInventory @0x146968ab6: row+0x150 initializes current/base usage'
        item['evidence']['max_level_native']='GetMedicineMaxLevel @0x146956980: min(table level count, 6)'
    return item


