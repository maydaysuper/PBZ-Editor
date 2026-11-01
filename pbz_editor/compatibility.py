"""Automatic, read-only discovery of PBZS key material and save capabilities.

A changed executable is not rejected by its build number. Candidate keys are
accepted only after authenticating the save directory. This establishes file
format compatibility, not in-game behavior of an untested game build.
"""
from __future__ import annotations

import hashlib
import json
import mmap
from pathlib import Path
import struct
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_ANCHOR = 'sealed but block is PLAINTEXT'.encode('utf-16-le') + b'\0\0'
MAX_EXECUTABLE = 2 * 1024 ** 3
MAX_CANDIDATES = 128


def profile_path():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))/'data/profile.json'


def load_profile():
    return json.loads(profile_path().read_text(encoding='utf8'))


def executable_path(game):
    from .core import EditorError
    path = Path(game).resolve()/'platform/steam/PBZ.exe'
    if not path.is_file():
        raise EditorError('请选择含 platform/steam/PBZ.exe 的游戏根目录。')
    return path


def pe_sections(data):
    """Return bounded file ranges of an x64 PE; never execute or load the image."""
    if not 64 <= len(data) <= MAX_EXECUTABLE or data[:2] != b'MZ':
        raise ValueError('Invalid PE image')
    pe = struct.unpack_from('<I', data, 60)[0]
    if not 64 <= pe <= min(len(data)-24, 1024*1024) or data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Invalid PE header')
    machine, count = struct.unpack_from('<HH', data, pe+4)
    optional = struct.unpack_from('<H', data, pe+20)[0]
    table = pe+24+optional
    if machine != 0x8664 or not 1 <= count <= 96 or optional < 112 or table+40*count > len(data):
        raise ValueError('Unsupported PE layout')
    if struct.unpack_from('<H', data, pe+24)[0] != 0x20b:
        raise ValueError('Expected a PE32+ image')
    ranges = []
    for pos in range(table, table+40*count, 40):
        size, start = struct.unpack_from('<II', data, pos+16)
        if start+size > len(data):
            raise ValueError('PE section outside file')
        if size:
            if any(start < end and start+size > begin for begin, end in ranges):
                raise ValueError('Overlapping PE sections')
            ranges.append((start, start+size))
    return ranges


def authenticated_directory(key, source):
    """Use the small authenticated directory, before any decompression or edits."""
    from .core import EditorError, MAX_FILE_BYTES
    if not 12 <= len(source) <= MAX_FILE_BYTES or source[:4] != b'PBZS':
        raise EditorError('这不是支持的 PBZS 存档文件。')
    size = struct.unpack_from('<I', source, 8)[0]
    if not 36 <= size <= min(len(source)-12, 32*1024*1024):
        raise EditorError('存档目录长度无效。')
    block = source[12:12+size]
    if block[:8] != b'PBZS\x01\x01\0\0':
        raise EditorError('存档加密块头无效。')
    try:
        directory = AESGCM(key).decrypt(block[8:20], block[36:]+block[20:36], None)
    except InvalidTag:
        return False
    return directory[:12] == source[:12]


def resolve_key(game, source=None):
    """Locate relocated/changed adjacent XOR shares, authenticated against a save.

    Without a save, only fingerprint-matching reference material is returned.
    No candidate, key or game bytes are persisted to settings or reports.
    """
    from .core import EditorError
    path = executable_path(game)
    profile = load_profile()
    locator = profile['save_key']
    try:
        if not 64 <= path.stat().st_size <= MAX_EXECUTABLE:
            raise ValueError('Executable size out of bounds')
        with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            ranges = pe_sections(data)
            seen = set()
            attempts = 0
            def candidate(offset, method):
                nonlocal attempts
                if not any(start <= offset and offset+64 <= end for start, end in ranges):
                    return None
                material = data[offset:offset+64]
                key = bytes(a ^ b for a, b in zip(material[:32], material[32:]))
                if key in seen:
                    return None
                seen.add(key)
                attempts += 1
                if attempts > MAX_CANDIDATES:
                    raise ValueError('Too many key candidates')
                known_material = hashlib.sha256(material).hexdigest() == locator['material_sha256']
                if (source is None and known_material) or (source is not None and authenticated_directory(key, source)):
                    digest = hashlib.sha256(data).hexdigest()
                    return key, {'executable': 'reference' if digest == profile['exe_sha256'] else 'changed',
                                 'executable_sha256': digest, 'key_locator': method,
                                 'key_authenticated': source is not None,
                                 'reference_build': profile['build']}
                return None
            found = candidate(locator['reference_offset'], 'offset')
            if found:
                return found
            for start, end in ranges:
                cursor = start
                while True:
                    match = data.find(KEY_ANCHOR, cursor, end)
                    if match < 0:
                        break
                    cursor = match+len(KEY_ANCHOR)
                    for padding in range(16):
                        found = candidate(cursor+padding, 'anchor')
                        if found:
                            return found
    except (OSError, ValueError, struct.error) as error:
        if isinstance(error, EditorError):
            raise
        raise EditorError('无法识别此游戏的存档密钥布局，未进行写入。') from error
    raise EditorError('无法验证存档密钥：文件可能损坏，或游戏的加密方式已变化。未进行写入。')


def slot_category_issue(inventory, category, reference=False):
    """Check only fields used by that adapter, preserving unrelated fields."""
    from .core import CATEGORY_FIELDS
    if not isinstance(inventory, dict):
        return '所选进度的背包结构无法识别，仅可查看。'
    if category == 'appearance':
        return None
    field = CATEGORY_FIELDS[category]
    if field not in inventory:
        return None if reference else '该类别的存档字段缺失，仅可查看。'
    records = inventory[field]
    if not isinstance(records, dict):
        return '该类别的存档结构已变化，仅可查看。'
    required = {'goods': ('CurrentAmount', 'MaxAmount'), 'weapon': ('WeaponLevel',),
                'phantom': ('ShaChiWeaponLevel',), 'accessory': ('CurrentLevel',),
                'medicine': ('CurrentLevel', 'CurrentTime', 'MaxTime')}[category]
    for ident, record in records.items():
        if not isinstance(ident, str) or not ident or not isinstance(record, dict):
            return '该类别的存档结构已变化，仅可查看。'
        for name in required:
            value = record.get(name)
            if (value is None and reference and name not in record):
                continue
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
                return '该类别的数值结构已变化，仅可查看。'
    if category in ('weapon', 'phantom', 'accessory'):
        field = {'weapon': 'MainWeapons', 'phantom': 'ShaqiWeapons', 'accessory': 'AccessoryIDs'}[category]
        value = inventory.get(field, [])
        if not isinstance(value, list) or any(not isinstance(i, str) for i in value):
            return '该类别的装备栏结构已变化，仅可查看。'
    if category == 'medicine' and any(not isinstance(inventory.get(f, 'None'), str) for f in ('TopMedicine', 'BottomMedicine')):
        return '该类别的装备栏结构已变化，仅可查看。'
    return None
