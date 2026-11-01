"""Authenticated PBZS containers and category-aware, lossless inventory edits.

The installed game supplies version-bound key material. No proprietary library
or private save is distributed with this module.
"""
from __future__ import annotations

import copy
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
from typing import Any
import uuid
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EditorError(ValueError):
    """A user-readable failure which leaves the input save unchanged."""


SUPPORTED_EXE_SHA256 = "7b4fcd286a350780faf0824d73aa150b21cbf1bccde883e6a96386cbe09973b4"
SUPPORTED_BUILD = "26266538"
MAX_SLOT_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
CATEGORY_FIELDS = {
    "goods": "InventoryInfos",
    "weapon": "MainWeaponInfos",
    "phantom": "ShaChiWeaponInfo",
    "accessory": "AccessoryInfos",
    "medicine": "MedicineInfos",
}
KNOWN_OUTFITS = {
    'Hun_OutFit_Default', 'Hun_OutFit_Climax', 'Hun_OutFit_MoTian',
    'Hun_OutFit_YaYu', 'Hun_OutFit_FengXue', 'Hun_OutFit_ZhuiMing',
    'Hun_OutFit_GuYou', 'Hun_OutFit_JiGuanMoQu', 'Hun_OutFit_Climax_Hat',
}
MEDICINE_BASE_CAPACITY = {
    'Hulu': 2, 'GuiShen': 2, 'RongQi': 1, 'NingFengDan': 2,
    'Medicine_Duration': 4, 'Medicine_Recover': 1, 'Medicine_ResistanceElement': 3,
    'Medicine_Interrupt': 4, 'Medicine_ShachiAttack': 2, 'Medicine_AdditionalRecover': 3,
    'KuYe': 3, 'FeiJiu': 2, 'Medicine_Ghost': 2, 'Medicine_Element': 2,
    'Medicine_UltimateCharge': 1, 'JinJiHeJi': 1, 'Medicine_Attack': 2,
}


def project_local_dir() -> Path:
    from .paths import local_dir
    return local_dir()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return dt.datetime.now().strftime("%Y.%m.%d-%H.%M.%S")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class OodleCodec:
    """Use the bundled open codec unless an external library is explicit."""
    def __new__(cls, path=None, game_dir=None):
        if path is None and not os.environ.get('PBZ_OODLE_PATH'):
            from .compression import OpenOodleCodec
            return OpenOodleCodec()
        return super().__new__(cls)

    def __init__(self, path: str | Path | None = None, game_dir: str | Path | None = None):
        choices: list[Path] = []
        if path:
            choices.append(Path(path))
        if os.environ.get("PBZ_OODLE_PATH"):
            choices.append(Path(os.environ["PBZ_OODLE_PATH"]))
        if game_dir:
            game = Path(game_dir)
            choices.extend(game.glob("oo2core_*_win64.dll"))
            choices.extend((game / "Binaries/Win64").glob("oo2core_*_win64.dll"))
            common = game.parent
            choices.extend([
                common / "Planet Zoo/oo2core_8_win64.dll",
                common / "ELDEN RING/Game/oo2core_6_win64.dll",
                common / "Red Dead Redemption 2/oo2core_5_win64.dll",
                common / "Grand Theft Auto V Enhanced/oo2core_5_win64.dll",
            ])
        self.path: Path | None = None
        self.dll = None
        errors = []
        for candidate in choices:
            if not candidate.is_file():
                continue
            try:
                dll = ctypes.WinDLL(str(candidate.resolve()))
                decoder = dll.OodleLZ_Decompress
                decoder.restype = ctypes.c_ssize_t
                decoder.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_ssize_t,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t,
                                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
                self.dll, self.decoder, self.path = dll, decoder, candidate.resolve()
                return
            except (OSError, AttributeError) as exc:
                errors.append(str(exc))
        raise EditorError("未找到可用的 Oodle 解码库。请在设置中选择本机已有的 oo2core_*_win64.dll。" + (" " + errors[-1] if errors else ""))

    def decompress(self, data: bytes, size: int) -> bytes:
        if not 0 <= size <= MAX_SLOT_BYTES:
            raise EditorError("存档槽的解压大小超出支持范围。")
        out = ctypes.create_string_buffer(max(1, size))
        n = self.decoder(data, len(data), out, size, 1, 0, 0, None, 0, None, None, None, 0, 3)
        if n != size:
            raise EditorError("Oodle 解压失败，原存档未修改。")
        return out.raw[:size]

    def compress(self, data: bytes) -> bytes:
        try:
            encoder = self.dll.OodleLZ_Compress
        except AttributeError as exc:
            raise EditorError("当前 Oodle 库只有解码能力，请选择另一库或使用 zlib 导出。") from exc
        encoder.restype = ctypes.c_ssize_t
        encoder.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_int,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        out = ctypes.create_string_buffer(len(data) + 65536)
        n = encoder(9, data, len(data), out, 2, None, None, None, None, 0)
        if not 0 < n <= len(out):
            raise EditorError("Oodle 压缩失败。")
        result = out.raw[:n]
        if self.decompress(result, len(data)) != data:
            raise EditorError("压缩后的数据未通过往返校验。")
        return result


class PBZSContainer:
    """Strict parser for the observed version 3, sealed-directory container."""
    def __init__(self, key: bytes, codec: OodleCodec | None = None):
        if len(key) != 32:
            raise EditorError("存档密钥长度错误。")
        self.key, self.codec = key, codec

    def unseal(self, block: bytes) -> bytes:
        if len(block) < 36 or block[:8] != b"PBZS\x01\x01\x00\x00":
            raise EditorError("存档加密块头无效。")
        try:
            return AESGCM(self.key).decrypt(block[8:20], block[36:] + block[20:36], None)
        except Exception as exc:
            raise EditorError("存档完整性校验失败：文件损坏、版本不匹配或密钥不正确。") from exc

    def seal(self, clear: bytes) -> bytes:
        nonce = os.urandom(12)
        result = AESGCM(self.key).encrypt(nonce, clear, None)
        return b"PBZS\x01\x01\x00\x00" + nonce + result[-16:] + result[:-16]

    def _decompress(self, data: bytes, algorithm: int, size: int) -> bytes:
        if algorithm == 0:
            raw = data
        elif algorithm == 1:
            try:
                dec = zlib.decompressobj()
                raw = dec.decompress(data, size + 1)
                if len(raw) > size or dec.unconsumed_tail or not dec.eof or dec.unused_data:
                    raise ValueError("zlib length/stream boundary mismatch")
            except zlib.error as exc:
                raise EditorError("zlib 解压失败。") from exc
        elif algorithm == 2:
            if self.codec is None:
                raise EditorError("此存档需要 Oodle 解码库。")
            raw = self.codec.decompress(data, size)
        else:
            raise EditorError(f"未知压缩算法：{algorithm}。")
        if len(raw) != size:
            raise EditorError("存档槽的实际大小与目录不符。")
        return raw

    def decode(self, data: bytes) -> list[dict]:
        if not 12 <= len(data) <= MAX_FILE_BYTES or data[:4] != b"PBZS":
            raise EditorError("这不是支持的 PBZS 存档文件。")
        version, flags, count, directory_size = struct.unpack_from("<BBHI", data, 4)
        if version != 3 or flags != 1 or not 1 <= count <= 4096:
            raise EditorError("暂不支持此 PBZS 容器版本或标志。")
        if not 36 <= directory_size <= len(data) - 12:
            raise EditorError("存档目录长度无效。")
        directory = self.unseal(data[12:12 + directory_size])
        if directory[:12] != data[:12]:
            raise EditorError("存档头副本校验失败。")
        cursor, end, names, entries = 12, 0, set(), []
        for _ in range(count):
            if cursor + 2 > len(directory):
                raise EditorError("存档目录截断。")
            name_size = struct.unpack_from("<H", directory, cursor)[0]
            cursor += 2
            if not 1 <= name_size <= 4096 or cursor + name_size + 14 > len(directory):
                raise EditorError("存档槽名或目录边界无效。")
            try:
                name = directory[cursor:cursor + name_size].decode("utf8")
            except UnicodeDecodeError as exc:
                raise EditorError("存档槽名编码无效。") from exc
            cursor += name_size
            algorithm, flag, size, offset, block_size = struct.unpack_from("<BBIII", directory, cursor)
            cursor += 14
            if name in names or algorithm not in (0, 1, 2) or flag != 1:
                raise EditorError("重复存档槽或不支持的压缩/加密标志。")
            if size > MAX_SLOT_BYTES or offset != end or block_size < 36 or 12 + directory_size + offset + block_size > len(data):
                raise EditorError("存档槽范围或解压大小无效。")
            block = data[12 + directory_size + offset:12 + directory_size + offset + block_size]
            raw = self._decompress(self.unseal(block), algorithm, size)
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise EditorError(f"存档槽 {name} 中的 JSON 无效。") from exc
            if not isinstance(value, dict):
                raise EditorError("存档槽必须包含 JSON 对象。")
            names.add(name)
            entries.append({"name": name, "algorithm": algorithm, "flag": flag, "raw": raw, "json": value, "block": block})
            end = offset + block_size
        if cursor != len(directory) or 12 + directory_size + end != len(data):
            raise EditorError("存档目录或数据存在未使用的尾部字节。")
        return entries

    def encode(self, entries: list[dict], replacements: dict[str, bytes], compression: str = "preserve") -> bytes:
        if compression not in ("preserve", "zlib", "none"):
            raise EditorError("未知压缩选项。")
        if set(replacements) - {e["name"] for e in entries}:
            raise EditorError("修改包含不存在的存档槽。")
        rows, chunks, offset = [], [], 0
        for entry in entries:
            name = entry["name"].encode("utf8")
            algorithm, raw, block = entry["algorithm"], entry["raw"], entry["block"]
            if entry["name"] in replacements:
                raw = replacements[entry["name"]]
                if len(raw) > MAX_SLOT_BYTES:
                    raise EditorError("修改后的存档槽过大。")
                json.loads(raw)
                if compression == "zlib":
                    algorithm = 1
                elif compression == "none":
                    algorithm = 0
                if algorithm == 0:
                    compressed = raw
                elif algorithm == 1:
                    compressed = zlib.compress(raw)
                else:
                    if self.codec is None:
                        raise EditorError("重新压缩需要 Oodle 库。")
                    compressed = self.codec.compress(raw)
                block = self.seal(compressed)
            rows.append(struct.pack("<H", len(name)) + name + struct.pack("<BBIII", algorithm, 1, len(raw), offset, len(block)))
            chunks.append(block)
            offset += len(block)
        directory_size = 12 + sum(map(len, rows)) + 36
        header = b"PBZS" + struct.pack("<BBHI", 3, 1, len(entries), directory_size)
        result = header + self.seal(header + b"".join(rows)) + b"".join(chunks)
        if len(result) > MAX_FILE_BYTES:
            raise EditorError("修改后的存档过大。")
        return result


class _Node:
    def __init__(self, start: int, end: int, value: Any, children=None):
        self.start, self.end, self.value, self.children = start, end, value, children


class LosslessJSON:
    """Patch selected JSON members while keeping every unrelated literal intact."""
    def __init__(self, raw: bytes):
        self.text = raw.decode("utf8")
        self.decoder = json.JSONDecoder()
        self._parse()

    def _space(self, i):
        while i < len(self.text) and self.text[i] in " \r\n\t":
            i += 1
        return i

    def _value(self, i):
        i = self._space(i)
        start = i
        if self.text[i] == "{":
            i += 1
            children, value = {}, {}
            i = self._space(i)
            while self.text[i] != "}":
                key_start = i
                key, i = self.decoder.raw_decode(self.text, i)
                if not isinstance(key, str) or key in children:
                    raise EditorError("JSON 含重复或无效对象键。")
                i = self._space(i)
                if self.text[i] != ":":
                    raise EditorError("JSON 对象缺少冒号。")
                child, i = self._value(i + 1)
                children[key] = (key_start, child)
                value[key] = child.value
                i = self._space(i)
                if self.text[i] == "}":
                    break
                if self.text[i] != ",":
                    raise EditorError("JSON 对象分隔符无效。")
                i = self._space(i + 1)
            return _Node(start, i + 1, value, children), i + 1
        if self.text[i] == "[":
            children, value = [], []
            i = self._space(i + 1)
            while self.text[i] != "]":
                child, i = self._value(i)
                children.append(child)
                value.append(child.value)
                i = self._space(i)
                if self.text[i] == "]":
                    break
                if self.text[i] != ",":
                    raise EditorError("JSON 数组分隔符无效。")
                i = self._space(i + 1)
            return _Node(start, i + 1, value, children), i + 1
        value, end = self.decoder.raw_decode(self.text, i)
        return _Node(start, end, value), end

    def _parse(self):
        try:
            self.root, end = self._value(0)
            if self._space(end) != len(self.text):
                raise EditorError("JSON 末尾包含额外数据。")
        except (IndexError, json.JSONDecodeError) as exc:
            raise EditorError("存档 JSON 结构无效。") from exc

    @property
    def value(self):
        return self.root.value

    def node(self, path):
        node = self.root
        for key in path:
            if isinstance(node.children, dict):
                node = node.children[key][1]
            else:
                node = node.children[key]
        return node

    def _patch(self, start, end, text):
        self.text = self.text[:start] + text + self.text[end:]
        self._parse()

    def set(self, path, value):
        parent = self.node(path[:-1])
        key = path[-1]
        if isinstance(parent.children, dict) and key not in parent.children:
            prefix = "," if parent.children else ""
            self._patch(parent.end - 1, parent.end - 1, prefix + _json(key) + ":" + _json(value))
        else:
            target = self.node(path)
            if target.value != value:
                self._patch(target.start, target.end, _json(value))

    def set_raw(self, path, raw_text):
        """Set a subtree using valid JSON text without normalizing its literals."""
        json.loads(raw_text)
        parent = self.node(path[:-1])
        key = path[-1]
        if isinstance(parent.children, dict) and key not in parent.children:
            prefix = "," if parent.children else ""
            self._patch(parent.end - 1, parent.end - 1, prefix + _json(key) + ":" + raw_text)
        else:
            target = self.node(path)
            if self.text[target.start:target.end] != raw_text:
                self._patch(target.start, target.end, raw_text)

    def remove(self, path):
        parent = self.node(path[:-1])
        key = path[-1]
        if not isinstance(parent.children, dict) or key not in parent.children:
            return
        keys = list(parent.children)
        index = keys.index(key)
        key_start, target = parent.children[key]
        if index + 1 < len(keys):
            end = parent.children[keys[index + 1]][0]
            start = key_start
        elif index:
            previous = parent.children[keys[index - 1]][1]
            start, end = previous.end, target.end
        else:
            start, end = key_start, target.end
        self._patch(start, end, "")

    def bytes(self):
        return self.text.encode("utf8")


def _int(value, label, minimum=0, maximum=2_147_483_647):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EditorError(f"{label} 必须是 {minimum} 到 {maximum} 的整数。")
    return value


def inventory_level(category: str, record: dict) -> int:
    """Public strengthening counts are zero based; native weapon grades start at 1."""
    if category in ('weapon', 'phantom'):
        field = 'WeaponLevel' if category == 'weapon' else 'ShaChiWeaponLevel'
        return max(1, record.get(field, 1)) - 1
    return record.get('CurrentLevel', 0)


def game_key(game_dir):
    game = Path(game_dir).resolve()
    executable = game / 'platform/steam/PBZ.exe'
    if not executable.is_file():
        raise EditorError('请选择含 platform/steam/PBZ.exe 的游戏根目录。')
    hasher = hashlib.sha256()
    with executable.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            hasher.update(block)
    if hasher.hexdigest() != SUPPORTED_EXE_SHA256:
        raise EditorError(f'当前游戏版本尚未适配。本版支持 Steam build {SUPPORTED_BUILD}，文件未修改。')
    with executable.open('rb') as stream:
        stream.seek(0xc1c7b28)
        left = stream.read(32)
        stream.seek(0xc1c7b48)
        right = stream.read(32)
    return bytes(a ^ b for a, b in zip(left, right))


def medicine_slots(inventory: dict, item_id: str) -> list[str]:
    return [slot for slot, field in (('top', 'TopMedicine'), ('bottom', 'BottomMedicine'))
            if str(inventory.get(field, 'None')).casefold() == item_id.casefold()]


def _edit_inventory_small(raw: bytes, operations: list[dict]) -> tuple[bytes, list[dict]]:
    editor = LosslessJSON(raw)
    if not isinstance(operations, list) or len(operations) > 5000:
        raise EditorError("操作清单无效或超过 5000 条。")
    if not isinstance(editor.value.get("Inventory"), dict):
        raise EditorError("所选存档槽没有可编辑背包。")
    cycle = editor.value.get("PBZGameSystem", {}).get("CycleCount", 0)
    changes = []
    for operation in operations:
        op = dict(operation)
        action, item_id, category = op.get("action", "add"), op.get("id", ""), op.get("category", "")
        if action not in ("add", "set", "remove"):
            raise EditorError("操作必须是 add、set 或 remove。")
        if not isinstance(item_id, str) or not item_id or len(item_id) > 256 or any(ord(c) < 32 for c in item_id):
            raise EditorError("物品 ID 无效。")
        if category not in CATEGORY_FIELDS:
            raise EditorError(f"当前未验证类别 {category} 的存档写入规则，不能将其记为添加成功。")
        field = CATEGORY_FIELDS[category]
        path = ["Inventory", field, item_id]
        records = editor.value["Inventory"].get(field)
        if records is None:
            if action != "remove":
                editor.set(["Inventory", field], {})
            records = {}
        if not isinstance(records, dict):
            raise EditorError(f"{field} 结构异常。")
        before = copy.deepcopy(records.get(item_id))
        record = copy.deepcopy(before) if isinstance(before, dict) else {}
        timestamp = _now()
        if action == "remove":
            inventory = editor.value["Inventory"]
            if category == "weapon" and (item_id in inventory.get("MainWeapons", []) or item_id == inventory.get("CurrentMainWeaponID")):
                raise EditorError("该主武器正在装备。请先在游戏内卸下或更换，再删除。")
            if category == "phantom" and item_id in inventory.get("ShaqiWeapons", []):
                raise EditorError("该影之武装正在装备。请先更换，再删除。")
            if category == "medicine" and item_id in (inventory.get("TopMedicine"), inventory.get("BottomMedicine")):
                raise EditorError("该药品正在装备。请先更换，再删除。")
            if category == "accessory":
                for index, equipped in enumerate(inventory.get("AccessoryIDs", [])):
                    if equipped == item_id:
                        editor.set(["Inventory", "AccessoryIDs", index], "None")
            editor.remove(path)
            changes.append({"action": action, "id": item_id, "category": category, "before": before, "after": None, "name": op.get("name", item_id)})
            continue
        quantity = _int(op.get("quantity", 1), "数量")
        if category in ("weapon", "phantom", "accessory") and quantity != 1:
            raise EditorError("武器、影之武装和饰品按唯一记录保存，数量应为 1。")
        level = _int(op.get("level", inventory_level(category, record)), "等级", maximum=999)
        if op.get("max_level") is not None and level > _int(op["max_level"], "物品等级上限", maximum=999):
            raise EditorError(f"{item_id} 的等级超过物品表上限。")
        if action == "add" and category != "goods" and before:
            existing_level = inventory_level(category, record)
            level = max(_int(existing_level, "已有等级", maximum=999), level)
        if category == "goods":
            current = _int(record.get("CurrentAmount", 0), "已有数量")
            amount = current + quantity if action == "add" else quantity
            _int(amount, "最终数量")
            record.update(CurrentAmount=amount, MaxAmount=max(record.get("MaxAmount", 0), amount), DirtyTime=timestamp)
            record.setdefault("FirstAddTime", timestamp)
            record.setdefault("AcquiredCycle", cycle)
        elif category == "weapon":
            record.update(WeaponLevel=level + 1, HasUIRedPoint=True)
            record.setdefault("DateTime", timestamp)
            record.setdefault("AlreadySnapSoul", _int(op.get("soul_level", 0), "炼魂等级", maximum=99))
            record.setdefault("AcquiredCycle", cycle)
            record.setdefault("FirstAcquiredCycle", cycle)
        elif category == "phantom":
            record.update(ShaChiWeaponLevel=level + 1, HasUIRedPoint=True, DirtyTime=timestamp)
            record.setdefault("AlreadySnapSoul", bool(op.get("soul_tempered", False)))
            record.setdefault("AcquiredCycle", cycle)
            record.setdefault("FirstAcquiredCycle", cycle)
        elif category == "accessory":
            record.update(CurrentLevel=level, HasUIRedPoint=True, DirtyTime=timestamp)
            record.setdefault("AcquiredCycle", cycle)
        elif category == "medicine":
            equip_slot = op.get('equip_slot', 'keep')
            if equip_slot not in ('keep', 'top', 'bottom', 'none'):
                raise EditorError('药品装备栏必须是 keep、top、bottom 或 none。')
            if equip_slot != 'keep':
                for slot, equipped_field in (('top', 'TopMedicine'), ('bottom', 'BottomMedicine')):
                    current = editor.value['Inventory'].get(equipped_field, 'None')
                    if slot == equip_slot:
                        editor.set(['Inventory', equipped_field], item_id)
                    elif str(current).casefold() == item_id.casefold():
                        editor.set(['Inventory', equipped_field], 'None')
            record.update(CurrentLevel=level, AlreadyHasUIRedPoint=True, DirtyTime=timestamp)
            capacity = next((value for key,value in MEDICINE_BASE_CAPACITY.items() if key.casefold()==item_id.casefold()),None)
            capacity = op.get('base_capacity',op.get('max_time',capacity))
            if capacity is None and before is None:
                raise EditorError('未知药品 ID 缺少已验证的基础容量；请在高级清单提供 base_capacity。')
            capacity = _int(capacity if capacity is not None else record.get('MaxTime',0), '药品基础容量')
            record.setdefault("CurrentTime", capacity if medicine_slots(editor.value['Inventory'], item_id) else 0)
            record.setdefault("MaxTime", capacity)
            record.setdefault("BuffRemainTime", 0)
            record.setdefault("AcquiredCycle", cycle)
            if "quantity" in op:
                record["CurrentTime"] = (before.get('CurrentTime',0) if before else 0) + quantity if action == "add" else quantity
                _int(record["CurrentTime"], "药品当前次数")
        if before is None:
            editor.set(path, record)
        else:
            for key, value in record.items():
                if key not in before or before[key] != value:
                    editor.set(path + [key], value)
        change = {"action": action, "id": item_id, "category": category, "before": before, "after": record, "name": op.get("name", item_id)}
        if category == 'medicine' and record.get('CurrentTime', 0) > 0:
            change['note'] = '写入指定的当前次数；游戏载入时可能按自身规则补满，不能保证精确保持该次数。'
        changes.append(change)
    return editor.bytes(), changes


def edit_inventory(raw: bytes, operations: list[dict]) -> tuple[bytes, list[dict]]:
    """Batch edits are O(source bytes + record bytes), not O(source * fields).

    Each operation runs the same category adapter on a tiny record snapshot.
    Existing record text is retained, including unknown numeric literals. Each
    affected category map is reconstructed once; unrelated categories and all
    values outside the affected maps remain byte-identical.
    """
    if not isinstance(operations, list) or len(operations) > 5000:
        raise EditorError("操作清单无效或超过 5000 条。")
    editor = LosslessJSON(raw)
    inventory = editor.value.get("Inventory")
    if not isinstance(inventory, dict):
        raise EditorError("所选存档槽没有可编辑背包。")
    cycle = editor.value.get("PBZGameSystem", {}).get("CycleCount", 0)
    equipment = {key: copy.deepcopy(inventory[key]) for key in ("AccessoryIDs", "MainWeapons", "ShaqiWeapons", "CurrentMainWeaponID", "TopMedicine", "BottomMedicine") if key in inventory}
    maps: dict[str, dict] = {}
    changes = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise EditorError("操作清单中的条目必须是对象。")
        op = dict(operation)
        category = op.get("category", "")
        if category not in CATEGORY_FIELDS:
            raise EditorError(f"当前未验证类别 {category} 的存档写入规则，不能将其记为添加成功。")
        if category != 'medicine' and 'equip_slot' in op:
            raise EditorError('equip_slot 只适用于药品。')
        field = CATEGORY_FIELDS[category]
        if field not in maps:
            values = inventory.get(field, {})
            if not isinstance(values, dict):
                raise EditorError(f"{field} 结构异常。")
            node = editor.node(["Inventory", field]) if field in inventory else None
            records = {}
            keys = {}
            if node:
                for key, (key_start, value_node) in node.children.items():
                    records[key] = editor.text[value_node.start:value_node.end]
                    keys[key] = editor.text[key_start:value_node.start]
            maps[field] = {"records": records, "keys": keys, "original_order": list(values), "existed": field in inventory}
        state = maps[field]
        item_id = op.get("id", "")
        if not isinstance(item_id, str):
            raise EditorError("物品 ID 无效。")
        # FName identifiers are case-insensitive; preserve an existing key's
        # spelling rather than creating a second entry for Kuye / KuYe.
        matches = [key for key in state['records'] if key.casefold() == item_id.casefold()]
        if len(matches) > 1:
            raise EditorError(f"存档中存在大小写冲突的物品 ID：{item_id}。")
        if matches:
            item_id = op['id'] = matches[0]
        record = state['records'].get(item_id)
        mini_map = '{}' if record is None else '{' + _json(item_id) + ':' + record + '}'
        mini_members = [_json(field) + ':' + mini_map] + [_json(k) + ':' + _json(v) for k,v in equipment.items()]
        mini_text = '{"PBZGameSystem":{"CycleCount":' + _json(cycle) + '},"Inventory":{' + ','.join(mini_members) + '}}'
        modified, single_changes = _edit_inventory_small(mini_text.encode('utf8'), [op])
        result = LosslessJSON(modified)
        result_map = result.value['Inventory'].get(field, {})
        if item_id in result_map:
            node = result.node(['Inventory',field,item_id])
            state['records'][item_id] = result.text[node.start:node.end]
        else:
            state['records'].pop(item_id, None)
        if category == 'accessory' and 'AccessoryIDs' in equipment:
            equipment['AccessoryIDs'] = result.value['Inventory']['AccessoryIDs']
        if category == 'medicine':
            for equipped_field in ('TopMedicine', 'BottomMedicine'):
                if equipped_field in result.value['Inventory']:
                    equipment[equipped_field] = result.value['Inventory'][equipped_field]
        changes.extend(single_changes)
    for field, state in maps.items():
        keys = [key for key in state['original_order'] if key in state['records']]
        keys.extend(key for key in state['records'] if key not in state['original_order'])
        members = [state['keys'].get(key, _json(key) + ':') + state['records'][key] for key in keys]
        if state['existed'] or members:
            editor.set_raw(['Inventory',field], '{' + ','.join(members) + '}')
    for equipped_field in ('AccessoryIDs', 'TopMedicine', 'BottomMedicine'):
        if equipped_field in equipment and equipment[equipped_field] != inventory.get(equipped_field):
            editor.set(['Inventory', equipped_field], equipment[equipped_field])
    # Check the final batch layout, not an intermediate state that another operation may replace.
    requested = {str(op.get('id', '')).casefold() for op in operations
                 if op.get('category') == 'medicine' and op.get('action', 'add') != 'remove'}
    medicines = editor.value['Inventory'].get('MedicineInfos', {})
    for item_id, record in list(medicines.items()):
        if not isinstance(record, dict) or medicine_slots(editor.value['Inventory'], item_id):
            continue
        if item_id.casefold() in requested and record.get('CurrentTime', 0) > 0:
            raise EditorError(f'{item_id} 未装备，游戏读档会将次数清零。请明确选择上栏/下栏，或将使用次数设为 0。')
        was_equipped = medicine_slots(inventory, item_id)
        if was_equipped and record.get('CurrentTime', 0) > 0:
            before = copy.deepcopy(record)
            editor.set(['Inventory', 'MedicineInfos', item_id, 'CurrentTime'], 0)
            changes.append({'action': 'normalize', 'id': item_id, 'category': 'medicine',
                'before': before, 'after': copy.deepcopy(editor.value['Inventory']['MedicineInfos'][item_id]),
                'name': item_id, 'note': '更换快捷栏后该药品不再装备；其次数在游戏下次读档时会归零，本次同步为 0。'})
    if any(equipment.get(field) != inventory.get(field) for field in ('TopMedicine', 'BottomMedicine')):
        for change in changes:
            if change['category'] == 'medicine':
                change['equipment_before'] = {field: inventory.get(field, 'None') for field in ('TopMedicine', 'BottomMedicine')}
                change['equipment_after'] = {field: equipment.get(field, 'None') for field in ('TopMedicine', 'BottomMedicine')}
                change.setdefault('note', '明确更换药品快捷栏；未装备药品的剩余次数不会跨读档保留。')
    return editor.bytes(), changes


def edit_appearance(raw: bytes, operations: list[dict]) -> tuple[bytes, list[dict]]:
    """Own complete outfits in the account wardrobe, as the native serializer does.

    Component meshes/hair/hat table rows are deliberately not invented as
    inventory records: native Load only accepts AppearanceType == Outfit (4).
    """
    editor = LosslessJSON(raw)
    changes = []
    for operation in operations:
        action = operation.get('action', 'add')
        item_id = operation.get('id', '')
        if action not in ('add','set','remove'):
            raise EditorError('外观操作必须是 add、set 或 remove。')
        canonical = next((key for key in KNOWN_OUTFITS if key.casefold() == str(item_id).casefold()), None)
        if canonical is None:
            raise EditorError('该外观不是本版本已确认的完整服装套装；头发、帽子或身体网格不能作为独立套装添加。')
        if operation.get('quantity',1) != 1:
            raise EditorError('服装按唯一拥有记录保存，数量应为 1。')
        root_path = ['PBZAppearanceComponent','Hun','Outfits']
        parent = editor.value
        for index,key in enumerate(root_path):
            if key not in parent:
                if action == 'remove':
                    break
                editor.set(root_path[:index+1], {})
                parent = editor.node(root_path[:index+1]).value
            else:
                parent = parent[key]
                if not isinstance(parent, dict):
                    raise EditorError('账号衣柜的保存结构异常。')
        try:
            records = editor.node(root_path).value
        except KeyError:
            records = {}
        key = next((key for key in records if key.casefold() == canonical.casefold()),canonical)
        before = copy.deepcopy(records.get(key))
        path = root_path + [key]
        if action == 'remove':
            if key in records:
                editor.remove(path)
            after = None
        else:
            if before is not None and not isinstance(before,dict):
                raise EditorError('已有服装记录的结构异常。')
            after = copy.deepcopy(before) if before else {}
            after['HasRedPoint'] = True
            if before is None:
                editor.set(path,after)
            else:
                editor.set(path+['HasRedPoint'],True)
        changes.append({'action':action,'id':key,'category':'appearance','name':operation.get('name',key),
                        'before':before,'after':after,'scope':'global','slot':'__Advanced__',
                        'path':'.'.join(path),'note':'账号衣柜拥有记录；实际可用性还受游戏的套装类型、剧情及权益检查影响。'})
    return editor.bytes(),changes


def _game_processes(game_dir: Path) -> list[int]:
    """Read process names using Toolhelp; inaccessible/failed enumeration fails closed."""
    if os.name != "nt":
        return []
    from ctypes import wintypes
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise EditorError("无法检查游戏进程，请稍后重试。")
    result = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not ok:
            raise EditorError("无法枚举进程，已取消写入。")
        while ok:
            if re.fullmatch(r"(?:PBZ|ProjectPBZ|PBZ-Win64-Shipping|ProjectPBZ-Win64-Shipping)\.exe", entry.szExeFile, re.I):
                result.append(entry.th32ProcessID)
            ok = kernel.Process32NextW(handle, ctypes.byref(entry))
        return result
    finally:
        kernel.CloseHandle(handle)


class SaveSession:
    def __init__(self, save_path: Path, game_dir: Path, container: PBZSContainer, source: bytes, local_dir: Path | None = None):
        self.save_path, self.game_dir = save_path.resolve(), game_dir.resolve()
        self.container, self.source = container, source
        self.source_sha256 = sha256(source)
        self.entries = container.decode(source)
        self.local_dir = (local_dir or project_local_dir()).resolve()
        self.backup_dir = self.local_dir / "backups"
        self._refresh_metadata()

    def _refresh_metadata(self):
        self.current_slot = next((e["json"].get("LastContinueSlot", "") for e in self.entries if e["name"] == "__Advanced__"), "")
        self.slots = []
        for entry in self.entries:
            if not isinstance(entry["json"].get("Inventory"), dict):
                continue
            data = entry["json"].get("PBZGameSystem", {})
            self.slots.append({"name": entry["name"], "date": data.get("DateTimeString", ""),
                               "level": data.get("CurrentLevelName", ""), "playtime": data.get("HasGameTime", 0),
                               "current": entry["name"] == self.current_slot, "cycle": data.get("CycleCount", 0)})
        if self.current_slot not in {s["name"] for s in self.slots}:
            self.current_slot = self.slots[0]["name"] if self.slots else ""

    def refresh(self):
        """Discard the in-memory snapshot and reopen the current source bytes."""
        data = self.save_path.read_bytes()
        entries = self.container.decode(data)
        self.source, self.source_sha256, self.entries = data, sha256(data), entries
        self._refresh_metadata()
        return self

    @classmethod
    def open(cls, save_path: str | Path, game_dir: str | Path, oodle_path: str | Path | None = None, local_dir: str | Path | None = None):
        game, save = Path(game_dir).resolve(), Path(save_path).resolve()
        key = game_key(game)
        source = save.read_bytes()
        codec = OodleCodec(oodle_path, game)
        return cls(save, game, PBZSContainer(key, codec), source, Path(local_dir) if local_dir else None)

    def _entry(self, slot):
        entry = next((e for e in self.entries if e["name"] == slot and "Inventory" in e["json"]), None)
        if entry is None:
            raise EditorError("所选存档槽不存在或没有背包。")
        return entry

    def inventory(self, slot: str) -> list[dict]:
        inventory = self._entry(slot)["json"]["Inventory"]
        items = []
        for category, field in CATEGORY_FIELDS.items():
            for item_id, record in inventory.get(field, {}).items():
                if not isinstance(record, dict):
                    continue
                quantity = record.get("CurrentAmount", 0) if category == "goods" else record.get("CurrentTime", 0) if category == "medicine" else 1
                level = inventory_level(category, record)
                item = {"id": item_id, "category": category, "quantity": quantity, "level": level, "record": copy.deepcopy(record), 'scope':'slot'}
                if category == 'medicine':
                    item['equipped_slots'] = medicine_slots(inventory, item_id)
                items.append(item)
        advanced = next((e['json'] for e in self.entries if e['name']=='__Advanced__'),{})
        for item_id,record in advanced.get('PBZAppearanceComponent',{}).get('Hun',{}).get('Outfits',{}).items():
            items.append({'id':item_id,'category':'appearance','quantity':1,'level':0,'record':copy.deepcopy(record),'scope':'global'})
        return items

    def _edit_plan(self, slot: str, operations: list[dict]):
        if not isinstance(operations,list) or len(operations)>5000 or any(not isinstance(op,dict) for op in operations):
            raise EditorError('操作清单无效或超过 5000 条。')
        entry = self._entry(slot)
        local_ops = [op for op in operations if op.get('category')!='appearance']
        global_ops = [op for op in operations if op.get('category')=='appearance']
        replacements,changes = {},[]
        if local_ops:
            modified,local_changes = edit_inventory(entry['raw'],local_ops)
            if modified != entry['raw']:
                replacements[slot] = modified
            for change in local_changes:
                change.update(scope='slot',slot=slot)
            changes.extend(local_changes)
        if global_ops:
            advanced = next((e for e in self.entries if e['name']=='__Advanced__'),None)
            if advanced is None:
                raise EditorError('存档中没有账号全局槽，不能写入全局衣柜。')
            modified,global_changes = edit_appearance(advanced['raw'],global_ops)
            if modified != advanced['raw']:
                replacements['__Advanced__'] = modified
            changes.extend(global_changes)
        return replacements,changes

    def _candidate(self, slot: str, operations: list[dict], compression="preserve"):
        replacements,changes = self._edit_plan(slot,operations)
        candidate = self.container.encode(self.entries, replacements, compression) if replacements else self.source
        checked = self.container.decode(candidate)
        if len(checked) != len(self.entries):
            raise EditorError("生成后的存档槽数量变化，已取消写入。")
        for before, after in zip(self.entries, checked):
            if before["name"] != after["name"] or (before["name"] in replacements and after["raw"] != replacements[before['name']]):
                raise EditorError("写出校验失败。")
            if before["name"] not in replacements and (before["raw"] != after["raw"] or before["block"] != after["block"]):
                raise EditorError("非目标存档槽发生变化，已取消写入。")
        return candidate, changes

    def preview(self, slot: str, operations: list[dict]) -> dict:
        replacements,changes = self._edit_plan(slot,operations)
        return {"slot": slot, "changes": changes, "operation_count": len(changes), "changed": bool(replacements),
                'affected_slots':list(replacements), 'has_global_changes':'__Advanced__' in replacements,
                "original_sha256": self.source_sha256, "unrelated_slots_preserved": True,
                "note": "普通物品编辑所选进度槽，完整服装编辑账号全局衣柜；不会同步剧情、NPC 状态或成就。"}

    def project(self, slot: str, operations: list[dict]):
        """Return a validated in-memory view using the same adapters as save.

        This never compresses, encrypts, backs up or writes any file. Entries
        not affected by the edits are shared read-only with this snapshot.
        """
        replacements, _ = self._edit_plan(slot, operations)
        view = copy.copy(self)
        view.entries = [dict(entry, raw=replacements[entry['name']],
                             json=json.loads(replacements[entry['name']]))
                        if entry['name'] in replacements else entry for entry in self.entries]
        return view

    def _check_source(self):
        if not self.save_path.is_file() or sha256(self.save_path.read_bytes()) != self.source_sha256:
            raise EditorError("存档已被游戏或其他程序更新。请重新打开存档，再应用修改。")

    @staticmethod
    def _atomic_write(destination: Path, data: bytes, expected: str | None = None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix="." + destination.name + ".", suffix=".writing", dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.read_bytes() != data:
                raise EditorError("写出后的文件校验失败。")
            if expected is not None and (not destination.is_file() or sha256(destination.read_bytes()) != expected):
                raise EditorError("存档在写入前发生变化，已取消覆盖。")
            if expected is None:
                # Atomic create-without-replacement: an export must not clobber
                # a file another program created after the file-picker check.
                os.link(temporary, destination)
                temporary.unlink()
                temporary = None
            else:
                os.replace(temporary, destination)
                temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _backup(self, data: bytes, purpose: str, output_hash: str) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        filename = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + purpose + "-" + uuid.uuid4().hex[:6] + ".sav"
        path = self.backup_dir / filename
        self._atomic_write(path, data)
        metadata = {"source_path": str(self.save_path), "sha256": sha256(data), "expected_current_sha256": output_hash,
                    "purpose": purpose, "created_at": _now()}
        self._atomic_write(path.with_suffix(".json"), json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf8"))
        return path

    def export(self, slot: str, operations: list[dict], destination: str | Path, compression="preserve") -> dict:
        dest = Path(destination).resolve()
        if dest == self.save_path:
            raise EditorError("另存为不能覆盖正在编辑的源存档，请使用“写入存档”。")
        candidate, changes = self._candidate(slot, operations, compression)
        if dest.exists():
            raise EditorError("目标文件已存在，请选择新文件名。")
        self._atomic_write(dest, candidate)
        return {"destination": str(dest), "backup_path": None, "sha256": sha256(candidate), "slot": slot,
                "operation_count": len(changes), 'affected_slots':list(dict.fromkeys(c['slot'] for c in changes)), "file_verified": True, "game_runtime_verified": False}

    def apply(self, slot: str, operations: list[dict], compression="preserve") -> dict:
        if _game_processes(self.game_dir):
            raise EditorError("游戏仍在运行。请退出游戏后写入，避免自动保存覆盖修改。")
        self._check_source()
        candidate, changes = self._candidate(slot, operations, compression)
        if candidate == self.source:
            return {"destination": str(self.save_path), "backup_path": None, "sha256": self.source_sha256,
                    "slot": slot, "operation_count": 0, "file_verified": True, "game_runtime_verified": False}
        output_hash = sha256(candidate)
        backup = self._backup(self.source, "before-edit", output_hash)
        self._check_source()
        if _game_processes(self.game_dir):
            raise EditorError("游戏在验证期间启动，已取消写入；备份已保留。")
        self._atomic_write(self.save_path, candidate, self.source_sha256)
        self.source, self.source_sha256 = candidate, output_hash
        self.entries = self.container.decode(candidate)
        self._refresh_metadata()
        return {"destination": str(self.save_path), "backup_path": str(backup), "sha256": output_hash,
                "slot": slot, "operation_count": len(changes), 'affected_slots':list(dict.fromkeys(c['slot'] for c in changes)), "file_verified": True, "game_runtime_verified": False}

    def restore(self, backup_path: str | Path, allow_newer: bool = False) -> dict:
        if _game_processes(self.game_dir):
            raise EditorError("请先退出游戏再恢复备份。")
        backup = Path(backup_path).resolve()
        try:
            metadata = json.loads(backup.with_suffix(".json").read_text(encoding="utf8"))
        except (OSError, ValueError) as exc:
            raise EditorError("未找到对应备份记录，无法验证恢复来源。") from exc
        data = backup.read_bytes()
        if sha256(data) != metadata.get("sha256") or Path(metadata.get("source_path", "")).resolve() != self.save_path:
            raise EditorError("备份内容或目标路径与备份记录不符。")
        self.container.decode(data)
        self._check_source()
        if self.source_sha256 != metadata.get("expected_current_sha256") and not allow_newer:
            raise EditorError("此备份之后已有新进度。默认不会覆盖；请明确选择允许放弃新进度。")
        displaced = self._backup(self.source, "before-restore", sha256(data))
        if _game_processes(self.game_dir):
            raise EditorError("游戏在验证期间启动，已取消恢复。")
        self._atomic_write(self.save_path, data, self.source_sha256)
        self.source, self.source_sha256 = data, sha256(data)
        self.entries = self.container.decode(data)
        self._refresh_metadata()
        return {"destination": str(self.save_path), "backup_path": str(displaced), "sha256": self.source_sha256,
                "file_verified": True, "game_runtime_verified": False}
