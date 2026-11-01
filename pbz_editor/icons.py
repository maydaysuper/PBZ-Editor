"""Read native item texture references and cache local-only thumbnails.

No save, game asset or installed library is modified. Only the observed PBZ
Zen Texture2D inline bulk layout is accepted. Unsupported assets stay blank.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import threading
import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from .resources_reader import IoStore, name_batch
from .core import OodleCodec

SCHEMA = 1
UTOC = 'ProjectPBZ/Content/Paks/ProjectPBZ-Windows.utoc'
FORMATS = {'PF_BC7': 16, 'PF_DXT5': 16, 'PF_DXT1': 8, 'PF_B8G8R8A8': 4, 'PF_G8': 1}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def key(item):
    return item['category'] + ':' + item['id'].casefold()


def catalog_digest(catalog):
    return digest(json.dumps({'sources': catalog.metadata.get('sources', []),
        'items': [(key(i), i.get('source'), i.get('evidence')) for i in catalog.items]},
        sort_keys=True, ensure_ascii=False).encode('utf8'))


def load_index(root, game_dir, catalog):
    """A stale, partial, damaged or path-traversing cache is never trusted."""
    root = Path(root)
    try:
        index = json.loads((root / 'index.json').read_text(encoding='utf8'))
        if index.get('schema') != SCHEMA or index.get('catalog_sha256') != catalog_digest(catalog):
            return None
        if index.get('utoc_sha256') != digest((Path(game_dir) / UTOC).read_bytes()):
            return None
        if not isinstance(index.get('items'), dict):
            return None
        if index.get('total') != sum(i.get('supported', True) for i in catalog.items):
            return None
        for entry in index['items'].values():
            name = entry.get('file', '')
            if not re.fullmatch(r'[a-f0-9]{64}\.png', name):
                return None
            data = (root / name).read_bytes()
            if digest(data) != entry.get('png_sha256') or not data.startswith(b'\x89PNG\r\n\x1a\n'):
                return None
        return index
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def soft_references(data, names, start, end):
    """Only complete FSoftObjectPath package/asset FName pairs from this row."""
    if not 0 <= start < end <= len(data):
        raise ValueError('Invalid catalog row bounds')
    paths = {i: n for i, n in enumerate(names) if n.startswith('/Game/')}
    refs = []
    seen = set()
    for pos in range(start + 8, end - 19):
        a, number, b, other, subpath = struct.unpack_from('<5I', data, pos)
        if a not in paths or number or other or subpath or b >= len(names):
            continue
        package = paths[a]
        if package.rsplit('/', 1)[-1] != names[b] or package in seen:
            continue
        refs.append({'asset': package, 'offset': pos})
        seen.add(package)
    return refs


def ranked_references(category, refs):
    def rank(ref):
        path = ref['asset'].casefold()
        leaf = path.rsplit('/', 1)[-1]
        if '/ui/' not in path or any(t in path for t in ('glow', 'lightmask', '/curve/', '/system/', '/white/needitem/')):
            return 100
        if category == 'weapon':
            return 0 if '/temp_render/t_icon_weapon_' in path else 2 if '/icon_hud_weapon_' in path else 1 if '/temp_render/t_weapon_' in path else 100
        if category == 'phantom':
            return 0 if '/temp_render/t_icon_shaqiweapon_' in path else 2 if '/icon_hud_shaqiweapon_' in path else 1 if '/temp_render/t_shaqiweapon_' in path else 100
        if category == 'medicine':
            return 0 if leaf.startswith(('t_icon_medicine_', 't_icon_bloodbottle_')) else 2 if leaf.startswith('t_icon_hud_') else 1 if leaf.startswith(('t_medicine_', 't_bloodbottle_')) else 100
        if category == 'appearance':
            return 0 if leaf.startswith('t_icon_appearance_') else 100
        if leaf.endswith('_w') or '/white/' in path:
            return 20
        if leaf.startswith(('t_fx_', 'iconlight', 't_attr_')):
            return 100
        if leaf.startswith('t_accessories_s_'):
            return 10
        return 0 if '/uinew/' in path else 5
    return sorted([r for r in refs if rank(r) < 100], key=lambda r: (rank(r), -r['offset']))


def texture_mip(data):
    """Validate Zen bulk map and platform data before exposing a BCn payload.

    Layout references: CUE4Parse FZenPackageSummary, FBulkDataMapEntry,
    FTexturePlatformData and FTexture2DMipMap. This is an independent narrow
    parser, not a general Unreal asset deserializer.
    """
    if len(data) < 128 or len(data) > 64 * 1024 * 1024:
        raise ValueError('Unsupported texture size')
    versioned, header = struct.unpack_from('<II', data)
    if versioned or not 60 < header < len(data):
        raise ValueError('Unsupported Zen texture summary')
    names, pos = name_batch(data)
    pad = struct.unpack_from('<Q', data, pos)[0]
    if pad > 7:
        raise ValueError('Unsupported Zen bulk alignment')
    pos += 8 + pad
    bulk_size = struct.unpack_from('<Q', data, pos)[0]
    pos += 8
    hashes_offset = struct.unpack_from('<I', data, 24)[0]
    if not bulk_size or bulk_size % 32 or bulk_size > 32 * 32 or pos + bulk_size != hashes_offset or hashes_offset > header:
        raise ValueError('Unsupported texture bulk map')
    bulk = [struct.unpack_from('<QQQI', data, p) for p in range(pos, pos + bulk_size, 32)]
    candidates = []
    for fmt in FORMATS:
        if fmt not in names:
            continue
        encoded = fmt.encode() + b'\0'
        marker = struct.pack('<I', len(encoded)) + encoded
        cursor = header
        while True:
            found = data.find(marker, cursor, min(header + 4096, len(data)))
            if found < 0:
                break
            cursor = found + 1
            try:
                width, height, packed = struct.unpack_from('<III', data, found - 12)
                if not 1 <= width <= 8192 or not 1 <= height <= 8192 or packed != 1:
                    continue
                p = found + len(marker)
                first, count = struct.unpack_from('<II', data, p)
                if first > 16 or not 1 <= count <= 16:
                    continue
                p += 8
                mips = []
                for _ in range(count):
                    index = struct.unpack_from('<I', data, p)[0]
                    if index >= len(bulk):
                        raise ValueError('Invalid mip bulk index')
                    offset, duplicate, length, flags = bulk[index]
                    pixel_start = header + offset
                    if not flags & 0x40 or flags & 2 or pixel_start != p + 4 or pixel_start + length + 12 > len(data):
                        raise ValueError('Only bounded inline texture payloads are supported')
                    w, h, z = struct.unpack_from('<III', data, pixel_start + length)
                    if not 1 <= w <= width or not 1 <= h <= height or z != 1:
                        raise ValueError('Mip dimensions do not match platform dimensions')
                    expected = w * h * FORMATS[fmt] if fmt in ('PF_B8G8R8A8', 'PF_G8') else ((w+3)//4)*((h+3)//4)*FORMATS[fmt]
                    if length != expected:
                        raise ValueError('Mip payload length mismatch')
                    mips.append((w, h, data[pixel_start:pixel_start+length]))
                    p = pixel_start + length + 12
                # Use a small native mip where available, preserving readable detail.
                eligible = [m for m in mips if max(m[0], m[1]) >= 128]
                w, h, payload = min(eligible or mips, key=lambda m: m[0]*m[1])
                candidates.append((fmt, w, h, payload))
            except (ValueError, struct.error):
                continue
    if len(candidates) != 1:
        raise ValueError('Texture platform data is absent or ambiguous')
    return candidates[0]


def thumbnail(data):
    import texture2ddecoder
    fmt, width, height, payload = texture_mip(data)
    if fmt == 'PF_B8G8R8A8':
        pixels = payload
    elif fmt == 'PF_G8':
        # UI grayscale textures use intensity as their opacity.
        pixels = b''.join(bytes((255, 255, 255, value)) for value in payload)
    else:
        decoder = {'PF_BC7': texture2ddecoder.decode_bc7, 'PF_DXT5': texture2ddecoder.decode_bc3,
                   'PF_DXT1': texture2ddecoder.decode_bc1}[fmt]
        pixels = decoder(payload, width, height)
    if len(pixels) != width * height * 4:
        raise ValueError('Decoded pixel size mismatch')
    alpha = pixels[3::4].translate(bytes(0 if i <= 8 else i for i in range(256)))
    left, top, right, bottom = width, height, 0, 0
    for y in range(height):
        line = alpha[y*width:(y+1)*width]
        if line.strip(b'\0'):
            left = min(left, width - len(line.lstrip(b'\0')))
            right = max(right, len(line.rstrip(b'\0')))
            top, bottom = min(top, y), y + 1
    if right <= left or bottom <= top:
        raise ValueError('Empty transparent icon')
    image = QImage(pixels, width, height, width*4, QImage.Format.Format_ARGB32).copy(left, top, right-left, bottom-top)
    image = image.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    result = QImage(128, 128, QImage.Format.Format_ARGB32)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawImage((128-image.width())//2, (128-image.height())//2, image)
    painter.end()
    return result, {'format': fmt, 'width': width, 'height': height, 'crop': [left, top, right, bottom]}


def build_icons(game_dir, catalog, root, oodle_path=None, progress=None, cancel=None):
    game, root = Path(game_dir), Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cancel = cancel or threading.Event()
    codec = OodleCodec(oodle_path, game)
    store = IoStore(game / UTOC, codec)
    lookup = {p.casefold(): p for p in store.files}
    table_hashes = {s['source']: s['sha256'] for s in catalog.metadata.get('sources', [])}
    tables, rendered, items, missing = {}, {}, {}, {}
    values = [i for i in catalog.items if i.get('supported', True)]
    for ordinal, item in enumerate(values):
        if cancel.is_set():
            raise InterruptedError('Icon preparation cancelled')
        try:
            source, evidence = item['source'], item['evidence']
            if source not in tables:
                data = store.read_file(lookup[source.casefold()])
                if digest(data) != table_hashes.get(source):
                    raise ValueError('Item table differs from the verified catalog')
                names, _ = name_batch(data)
                tables[source] = data, names
            data, names = tables[source]
            start, end, id_offset = (evidence[k] for k in ('row_start', 'row_end', 'id_offset'))
            if not 0 <= start <= id_offset < end <= len(data):
                raise ValueError('Invalid catalog evidence bounds')
            name, number = struct.unpack_from('<II', data, id_offset)
            actual_id = names[name] + (f'_{number-1}' if number else '')
            if actual_id.casefold() != item['id'].casefold():
                raise ValueError('Row identifier differs from catalog')
            refs = ranked_references(item['category'], soft_references(data, names, start, end))
            failures = []
            for ref in refs:
                asset = 'ProjectPBZ/Content/' + ref['asset'][len('/Game/'):] + '.uasset'
                actual = lookup.get(asset.casefold())
                if actual is None:
                    continue
                if actual not in rendered:
                    try:
                        texture = store.read_file(actual)
                        image, meta = thumbnail(texture)
                        filename = digest(actual.casefold().encode()) + '.png'
                        temporary = root / ('.' + uuid.uuid4().hex + '.png')
                        try:
                            if not image.save(str(temporary), 'PNG'):
                                raise ValueError('Cannot save thumbnail')
                            png_sha = digest(temporary.read_bytes())
                            os.replace(temporary, root / filename)
                        finally:
                            if temporary.exists():
                                temporary.unlink()
                        rendered[actual] = {'file': filename, 'png_sha256': png_sha,
                            'texture_sha256': digest(texture), 'texture': actual, **meta}
                    except (ValueError, OSError, struct.error) as error:
                        rendered[actual] = {'error': str(error)}
                result = rendered[actual]
                if 'error' in result:
                    failures.append(result['error'])
                    continue
                items[key(item)] = {**result, 'id': item['id'], 'category': item['category'],
                    'reference': ref['asset'], 'reference_offset': ref['offset'], 'table': source,
                    'table_sha256': table_hashes[source]}
                break
            if key(item) not in items:
                missing[key(item)] = '; '.join(dict.fromkeys(failures)) or 'No supported texture reference'
        except (ValueError, OSError, KeyError, IndexError, struct.error) as error:
            missing[key(item)] = str(error)
        if progress:
            progress(ordinal + 1, len(values))
    index = {'schema': SCHEMA, 'utoc_sha256': digest(store.data), 'catalog_sha256': catalog_digest(catalog),
             'items': items, 'missing': missing, 'texture_count': len({i['file'] for i in items.values()}),
             'total': len(values), 'game_runtime_verified': False}
    temporary = root / ('.index-' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf8')
        os.replace(temporary, root / 'index.json')
    finally:
        if temporary.exists():
            temporary.unlink()
    return index
