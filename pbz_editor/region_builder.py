"""Build local region associations from native assets, without developer caches."""
import struct,json,re,hashlib
from .resources_reader import Reader,name_batch

def u32(b, p): return struct.unpack_from('<I', b, p)[0]


def fname(b, p, names):
    index, number = struct.unpack_from('<II', b, p)
    if index >= len(names) or number > 1000000: raise ValueError('FName invalid')
    return names[index] + (f'_{number - 1}' if number else ''), p + 8


def header(b, p):
    props, masked, index = [], [], 0
    for _ in range(10):
        frag = struct.unpack_from('<H', b, p)[0]; p += 2
        skip, count = frag & 0x7f, frag >> 9
        index += skip
        for i in range(count):
            props.append(index + i)
            if frag & 0x80: masked.append(index + i)
        index += count
        if frag & 0x100: break
    else: raise ValueError('unterminated header')
    bits = len(masked)
    size = 1 if bits <= 8 else 2 if bits <= 16 else ((bits + 31) // 32) * 4
    zero = set()
    if bits:
        mask = int.from_bytes(b[p:p + size], 'little'); p += size
        zero = {prop for bit, prop in enumerate(masked) if mask & (1 << bit)}
    return props, zero, p


def debug_map(b, p, names):
    if u32(b, p) != 0: raise ValueError('removed keys unsupported')
    count = u32(b, p + 4); p += 8
    if not 1 <= count <= 30: raise ValueError('count')
    result = []
    for _ in range(count):
        start = p; mapid, p = fname(b, p, names)
        props, zero, p = header(b, p)
        if props != [0] or zero: raise ValueError('debug value schema')
        length = u32(b, p); p += 4
        if not 0 <= length <= 1000: raise ValueError('array length')
        values = []
        for _ in range(length):
            off = p; ident, p = fname(b, p, names); values.append((ident, off))
        result.append((mapid, start, values))
    return result, p


def build_regions(assets, loc, catalog_data, group_script_text, english=None):
    b = assets['DT_MapItemData']; names, _ = name_batch(b)
    if hashlib.sha256(b).hexdigest() != '64010746ef71ac987d9261c4251d733bacc4780feb44b5dbbe55c33bbe5624d1':
        raise ValueError('Unadapted map table: verify the new layout before building region metadata.')
    body = u32(b, 4)
    props, zero, p = header(b, body)
    assert props == [0, 1] and not zero
    assert u32(b, p) == 0
    map_count = u32(b, p + 4); map_start = p + 8
    # Identify the second property structurally: fully valid TMap to exact export end.
    tails = []
    for off in range(map_start, len(b) - 20):
        if b[off:off + 4] != b'\0\0\0\0' or not 1 <= u32(b, off + 4) <= 30: continue
        try:
            entries, end = debug_map(b, off, names)
            if end == len(b) - 4 and b[end:] == b'\0\0\0\0': tails.append((off, entries))
        except (ValueError, struct.error, IndexError): pass
    assert len(tails) == 1
    debug_start, debug = tails[0]
    starts = []
    for off in range(map_start, debug_start - 10):
        if b[off + 8:off + 10] != b'\0\x1b': continue
        if u32(b, off + 4) != 0: continue
        try:
            ident, pos = fname(b, off, names)
            if not re.fullmatch(r'[A-Za-z][A-Za-z_0-9]{1,40}', ident): continue
            fields, zero, after = header(b, pos)
            if fields == list(range(13)) and not zero: starts.append((off, ident, after))
        except (ValueError, struct.error, IndexError): pass
    assert len(starts) == map_count and starts[0][0] == map_start
    native_ids = {name for _, name, _ in starts}

    # MapData rows: FName row key, 19-property header, FName MapID, MapName FText.
    mb = assets['DT_MapData']; mn, _ = name_batch(mb)
    mh = u32(mb, 4); declared = u32(mb, mh + 10)
    labels = {}
    marker = b'\x08\0\0\0Default\0'
    rows = []
    for match in re.finditer(re.escape(marker), mb[mh:]):
        text_off = mh + match.start(); key = Reader(mb, mh + match.end()).fstring()
        found = []
        for distance in (20, 24):
            row = text_off - distance
            try:
                if u32(mb, row + 4) != 0: continue
                rowid, hp = fname(mb, row, mn); fields, zeros, pp = header(mb, hp)
                if not re.fullmatch(r'[A-Za-z][A-Za-z_0-9]{1,40}', rowid): continue
                if fields != list(range(19)) or 0 in zeros or 1 in zeros: continue
                valueid, pp = fname(mb, pp, mn)
                if pp + 2 != text_off: continue
                found.append((rowid, valueid, row))
            except (ValueError, struct.error, IndexError): pass
        assert len(found) == 1, (text_off, found)
        rowid, valueid, row = found[0]
        rows.append(rowid)
        labels[rowid.casefold()] = {'name': loc.get(key, rowid), 'order': len(rows),
            'evidence': {'source': 'ProjectPBZ/Content/PBZContent/DataTable/UI/DT_MapData.uasset',
                         'offset': text_off, 'field': 'MapName.Key', 'localization_key': key,
                         'row_name': rowid, 'map_id_field': valueid, 'order_source': 'table_row_sequence'}}
    assert len(rows) == declared

    # Official big-map/sub-map relation; merge only if names are also equal.
    ab = assets['DA_MapIDAsset']; an, _ = name_batch(ab)
    _, _, ap = header(ab, u32(ab, 4)); assert u32(ab, ap) == 0
    n = u32(ab, ap + 4); ap += 8; groups = {}
    for _ in range(n):
        parent, ap = fname(ab, ap, an); fields, zeros, ap = header(ab, ap)
        assert fields == [0] and not zeros
        length = u32(ab, ap); ap += 4; children = []
        for _ in range(length):
            child, ap = fname(ab, ap, an); children.append(child)
        groups[parent] = children
    assert ap == len(ab) - 4
    canonical = {ident: ident for ident in native_ids}
    for parent, children in groups.items():
        parent_native = next((i for i in native_ids if i.casefold() == parent.casefold()), None)
        parent_label = labels.get(parent.casefold())
        if not parent_native or not parent_label: continue
        for child in children:
            actual = next((i for i in native_ids if i.casefold() == child.casefold()), None)
            child_label = labels.get(child.casefold())
            if actual and child_label and child_label['name'] == parent_label['name']:
                canonical[actual] = parent_native
    # MapData.MapID explicitly redirects aliases (HuanShuShi -> MeiGuHu).
    for ident in native_ids:
        info = labels.get(ident.casefold())
        if not info: continue
        target = info['evidence']['map_id_field']
        actual = next((i for i in native_ids if i.casefold() == target.casefold()), None)
        parent_info = labels.get(target.casefold())
        if actual and actual != ident and parent_info and parent_info['name'] == info['name']:
            canonical[ident] = actual
        elif actual and actual != ident and not parent_info:
            # Explicit native MapID redirect; inherit the row's own localized label.
            canonical[ident] = actual
    group_script = group_script_text
    group_literal = re.search(r'const MAP_MERGE_GROUPS = (\{.*?\});', group_script, re.S).group(1)
    merge_groups = json.loads(re.sub(r',\s*}', '}', group_literal))
    for parent, children in merge_groups.items():
        actual_parent = next((i for i in native_ids if i.casefold() == parent), None)
        info = labels.get(parent)
        if not actual_parent or not info: continue
        for child in children:
            actual_child = next((i for i in native_ids if i.casefold() == child), None)
            child_info = labels.get(child)
            if actual_child and (not child_info or child_info['name'] == info['name']):
                canonical[actual_child] = actual_parent
    for ident in canonical:
        seen = set(); current = ident
        while canonical[current] != current:
            if current in seen: raise ValueError('region alias cycle')
            seen.add(current); current = canonical[current]
        canonical[ident] = current
    definitions = []
    for ident in sorted(set(canonical.values())):
        if ident.casefold() == 'test': continue
        info = labels.get(ident.casefold())
        # Unlocalized native IDs are honestly retained; never invented Chinese names.
        definitions.append({'id': ident, 'name': info['name'] if info else ident,
            'name_verified': bool(info),
            'order': info['order'] if info else 999,
            'map_ids': sorted(k for k, v in canonical.items() if v == ident),
            'evidence': ([info['evidence']] if info else [{'source': 'ProjectPBZ/Content/PBZContent/Maps/DT_MapItemData.uasset', 'field': 'MapItemDatas', 'name_status': 'native_id_without_localized_label'}])})

    if english:
        for definition in definitions:
            key = next((e.get('localization_key') for e in definition['evidence'] if e.get('localization_key')), None)
            definition['name_en'] = english.get(key, '')
    catalog = catalog_data['items']
    lookup = {i['id'].casefold(): i for i in catalog}
    if len(lookup) != len(catalog):
        raise ValueError('Ambiguous item ID across categories; verify the native schema before indexing.')
    result = {}
    def add(ident, mapid, evidence):
        item = lookup.get(ident.casefold())
        if not item or mapid.casefold() == 'test': return
        k = item['category'], item['id']
        entry = result.setdefault(k, {'id': item['id'], 'category': item['category'], 'regions': [], 'status': 'verified', 'evidence': []})
        region = canonical.get(mapid, mapid)
        if region not in entry['regions']: entry['regions'].append(region)
        evidence = dict(evidence, raw_map_id=mapid)
        if evidence not in entry['evidence']: entry['evidence'].append(evidence)

    for mapid, off, entries in debug:
        for ident, itemoff in entries:
            add(ident, mapid, {'source': 'ProjectPBZ/Content/PBZContent/Maps/DT_MapItemData.uasset',
                'field': 'DebugAllItems.ItemIds', 'offset': itemoff, 'source_kind': 'debug_region_list',
                'relation': 'native_region_association_not_first_or_unique_acquisition'})
    for ix, (start, mapid, after) in enumerate(starts):
        end = starts[ix + 1][0] if ix + 1 < len(starts) else debug_start
        if mapid.casefold() == 'test': continue
        for off in range(after, end - 8):
            try: ident, _ = fname(b, off, names)
            except (ValueError, struct.error): continue
            item = lookup.get(ident.casefold())
            if not item or item['category'] not in ('weapon', 'phantom', 'accessory', 'medicine'): continue
            # These four native point records have 8 properties with their ID first.
            # Verify the complete first-property header; plain aligned-byte scans are insufficient.
            matched = False
            for hp in (off - 2, off - 3):
                try:
                    fields, zeros, pp = header(b, hp)
                    if fields == list(range(8)) and 0 not in zeros and pp == off:
                        matched = True
                except (ValueError, struct.error): pass
            if matched:
                add(ident, mapid, {'source': 'ProjectPBZ/Content/PBZContent/Maps/DT_MapItemData.uasset',
                    'field': 'MapItemDatas.native_collection_point.ID', 'offset': off,
                    'source_kind': 'map_collection', 'native_record_properties': 8})
    output = {'schema_version': 1, 'reference_build': '26266538', 'regions': definitions, 'items': sorted(result.values(), key=lambda x: (x['category'], x['id'])),
              'provenance': {'map_asset_sha256': hashlib.sha256(b).hexdigest(), 'map_entries': map_count,
                'debug_region_count': len(debug), 'debug_references': sum(len(x[2]) for x in debug),
                'map_data_rows': declared, 'map_id_asset_groups': groups,
                'native_merge_groups': merge_groups,
                'native_debug_field_name_evidence': {'source': 'platform/steam/PBZ.exe', 'string_file_offset': 200769792,
                    'names': ['MapItemDatas', 'DebugAllItems', 'DebugMapItemIdList', 'ItemIds']},
                'limitations': ['Native DebugAllItems indicates regional association, not first/unique acquisition.',
                               'Not referenced does not mean unavailable.',
                               'Appearance entries without native region metadata remain unknown.',
                               'Only equal localized names with official DA_MapIDAsset relation are merged.']}}
    return output
