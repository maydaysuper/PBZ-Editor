import json
from pathlib import Path
import struct

import pytest
from PySide6.QtGui import QImage, QColor
from pbz_editor.catalog import Catalog
from pbz_editor.icons import (SCHEMA, UTOC, catalog_digest, digest, load_index,
                              soft_references, texture_mip, thumbnail)


def texture_fixture():
    names = ['None', 'PF_B8G8R8A8', 'Test']
    strings = b''.join(n.encode() for n in names)
    batch = struct.pack('<IIQ', len(names), len(strings), 0) + bytes(len(names)*8)
    batch += b''.join(struct.pack('>H', len(n)) for n in names) + strings
    marker = struct.pack('<I', 12) + b'PF_B8G8R8A8\0'
    # Four by four BGRA, with one opaque red pixel at x=2, y=1.
    pixels = bytearray(64)
    pixels[(1*4+2)*4:(1*4+2)*4+4] = bytes((0, 0, 255, 255))
    prefix = struct.pack('<III', 4, 4, 1) + marker + struct.pack('<III', 0, 1, 0)
    body = prefix + pixels + struct.pack('<III', 4, 4, 1) + bytes(12)
    header = bytearray(256)
    struct.pack_into('<II', header, 0, 0, len(header))
    header[60:60+len(batch)] = batch
    pos = 60 + len(batch)
    struct.pack_into('<QQ', header, pos, 0, 32)
    bulk_offset = pos + 16
    struct.pack_into('<QQQIB3s', header, bulk_offset, len(prefix), 0xffffffffffffffff,
                     len(pixels), 0x48, 0, bytes(3))
    struct.pack_into('<I', header, 24, bulk_offset + 32)
    return bytes(header) + body, bulk_offset


def test_texture_header_bulk_length_and_bgra_are_verified():
    data, _ = texture_fixture()
    fmt, w, h, payload = texture_mip(data)
    assert (fmt, w, h, len(payload)) == ('PF_B8G8R8A8', 4, 4, 64)
    image, meta = thumbnail(data)
    assert (image.width(), image.height()) == (128, 128)
    assert meta['crop'] == [2, 1, 3, 2]
    assert image.pixelColor(64, 64) == QColor(255, 0, 0, 255)
    assert image.pixelColor(0, 0).alpha() == 0


@pytest.mark.parametrize('damage', ['length', 'offset', 'flags', 'dimensions', 'truncated'])
def test_invalid_bulk_boundaries_never_reach_native_decoder(damage):
    data, offset = texture_fixture()
    raw = bytearray(data)
    if damage == 'length':
        struct.pack_into('<Q', raw, offset + 16, 63)
    elif damage == 'offset':
        struct.pack_into('<Q', raw, offset, 1)
    elif damage == 'flags':
        struct.pack_into('<I', raw, offset + 24, 0)
    elif damage == 'dimensions':
        struct.pack_into('<I', raw, 256, 8193)
    else:
        raw = raw[:-25]
    with pytest.raises(ValueError):
        texture_mip(bytes(raw))


def test_soft_references_are_confined_to_row_and_require_matching_names():
    names = ['/Game/UI/T_One', 'T_One', '/Game/UI/T_Two', 'WrongName']
    raw = bytes(8) + struct.pack('<5I', 0, 0, 1, 0, 0) + struct.pack('<5I', 2, 0, 3, 0, 0)
    assert soft_references(raw, names, 0, len(raw)) == [{'asset': names[0], 'offset': 8}]
    assert soft_references(raw, names, 28, len(raw)) == []
    with pytest.raises(ValueError):
        soft_references(raw, names, 0, len(raw)+1)


@pytest.fixture
def icon_cache(tmp_path):
    catalog = Catalog([{'id': 'Item', 'category': 'goods', 'name': '物品'}])
    game, root = tmp_path / 'game', tmp_path / 'icons'
    utoc = game / UTOC
    utoc.parent.mkdir(parents=True)
    utoc.write_bytes(b'game-index')
    root.mkdir()
    name = 'a'*64 + '.png'
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(QColor('red'))
    image.save(str(root / name), 'PNG')
    index = {'schema': SCHEMA, 'utoc_sha256': digest(utoc.read_bytes()),
             'catalog_sha256': catalog_digest(catalog), 'total': 1,
             'items': {'goods:item': {'file': name, 'png_sha256': digest((root/name).read_bytes())}}}
    (root/'index.json').write_text(json.dumps(index), encoding='utf8')
    return game, root, catalog, index


def test_verified_cache_reloads_without_game_extraction(icon_cache):
    game, root, catalog, index = icon_cache
    assert load_index(root, game, catalog) == index


@pytest.mark.parametrize('damage', ['game', 'catalog', 'png', 'path'])
def test_stale_or_tampered_cache_is_rejected(icon_cache, damage):
    game, root, catalog, index = icon_cache
    if damage == 'game':
        (game/UTOC).write_bytes(b'new-game-version')
    elif damage == 'catalog':
        catalog.items[0]['evidence'] = {'row_start': 4}
    elif damage == 'png':
        (root/index['items']['goods:item']['file']).write_bytes(b'broken image')
    else:
        index['items']['goods:item']['file'] = '../outside.png'
        (root/'index.json').write_text(json.dumps(index), encoding='utf8')
    assert load_index(root, game, catalog) is None


def test_local_icon_index_links_to_exact_native_row_references_if_available():
    root = Path(__file__).resolve().parents[1]
    index_file = root/'.local/icons/index.json'
    if not index_file.exists():
        pytest.skip('Private icon cache not installed')
    index = json.loads(index_file.read_text(encoding='utf8'))
    tables = {}
    for entry in index['items'].values():
        path = root/'.local/cache'/Path(entry['table']).name.replace('.uasset', '.chunk')
        if not path.exists():
            pytest.skip('Private native table cache not installed')
        if path not in tables:
            from pbz_editor.resources_reader import name_batch
            raw = path.read_bytes()
            tables[path] = raw, name_batch(raw)[0]
        raw, names = tables[path]
        assert digest(raw) == entry['table_sha256']
        a, number, b, other, subpath = struct.unpack_from('<5I', raw, entry['reference_offset'])
        assert number == other == subpath == 0
        assert names[a] == entry['reference'] and names[b] == names[a].rsplit('/', 1)[-1]
        png = QImage(str(root/'.local/icons'/entry['file']))
        assert not png.isNull() and png.width() == png.height() == 128
