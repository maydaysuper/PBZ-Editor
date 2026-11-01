"""Bounded, read-only PBZ Pak v12 reader for bootstrap text resources.

Format reference: trumank/repak footer.rs, entry.rs and pak.rs (MIT).
No extraction tools or arbitrary archive paths are executed or materialized.
"""
import hashlib
from pathlib import Path
import struct
import zlib
from .resources_reader import Reader, aes_decrypt

MAX = 64 * 1024 * 1024


def integer(reader, size=8):
    return int.from_bytes(reader.take(size), 'little')


class PakReader:
    def __init__(self, path, codec):
        self.path, self.codec = Path(path), codec
        self.size = self.path.stat().st_size
        with self.path.open('rb') as stream:
            stream.seek(-221, 2)
            footer = stream.read(221)
        magic, version, offset, size = struct.unpack_from('<IIQQ', footer, 17)
        if magic != 0x5a6f12e1 or version != 12 or footer[16] not in (0, 1):
            raise ValueError('Unsupported PBZ Pak footer')
        self.encrypted = bool(footer[16])
        self.methods = ['None'] + [footer[p:p+32].split(b'\0')[0].decode('ascii') for p in range(61, 221, 32)]
        data = self._index(offset, size, footer[41:61])
        r = Reader(data)
        self.mount = r.fstring()
        self.count = r.u32()
        if self.mount != '../../../' or self.count > 1000000:
            raise ValueError('Unsupported Pak index')
        r.take(8)  # path hash seed
        directory = None
        for part in range(2):
            flag = r.u32()
            if flag not in (0, 1):
                raise ValueError('Invalid Pak index flag')
            if flag:
                off, length, checksum = integer(r), integer(r), r.take(20)
                block = self._index(off, length, checksum)
                if part == 1:
                    directory = block
        encoded_size = r.u32()
        self.encoded = r.take(encoded_size)
        if r.u32() != 0:
            raise ValueError('Non-encoded PBZ Pak entries are unsupported')
        if directory is None:
            raise ValueError('Pak directory index is absent')
        d = Reader(directory)
        self.files = {}
        dirs = d.u32()
        if dirs > self.count + 1:
            raise ValueError('Pak directory count exceeds bounds')
        for _ in range(dirs):
            prefix, count = d.fstring(), d.u32()
            if count > self.count:
                raise ValueError('Pak file count exceeds bounds')
            for _ in range(count):
                name, encoded_offset = d.fstring(), d.i32()
                if encoded_offset == -2147483648:
                    continue
                if not 0 <= encoded_offset < len(self.encoded):
                    raise ValueError('Invalid encoded Pak entry')
                full = (prefix.lstrip('/') + name).casefold()
                if full in self.files:
                    raise ValueError('Duplicate Pak path')
                self.files[full] = encoded_offset

    def _read(self, offset, length):
        if not 0 <= length <= MAX or not 0 <= offset <= self.size - length:
            raise ValueError('Pak block is outside the supported bounds')
        with self.path.open('rb') as stream:
            stream.seek(offset)
            data = stream.read(length)
        if len(data) != length:
            raise ValueError('Truncated Pak block')
        return data

    def _index(self, offset, length, checksum):
        data = self._read(offset, length)
        if self.encrypted:
            data = aes_decrypt(data)
        if hashlib.sha1(data).digest() != checksum:
            raise ValueError('Pak index checksum mismatch')
        return data

    def read_file(self, path):
        # The caller supplies a known archive member. No filesystem path is
        # derived from the archive's directory entries.
        r = Reader(self.encoded, self.files[path.casefold()])
        bits = r.u32()
        method, encrypted, count = (bits >> 23) & 0x3f, bool(bits & (1 << 22)), (bits >> 6) & 0xffff
        block_size = bits & 0x3f
        block_size = r.u32() if block_size == 0x3f else block_size << 11
        def var(bit):
            return integer(r, 4 if bits & (1 << bit) else 8)
        offset, uncompressed = var(31), var(30)
        compressed = var(29) if method else uncompressed
        header_size = 53 + (4 + count * 16 if method else 0)
        if uncompressed > MAX or compressed > MAX or method >= len(self.methods):
            raise ValueError('Unsupported Pak member size or compression')
        lengths = ([compressed] if count == 1 and not encrypted else [r.u32() for _ in range(count)]) if method else [compressed]
        on_disk = sum((n+15)//16*16 if encrypted else n for n in lengths)
        data = self._read(offset + header_size, on_disk)
        result, position = bytearray(), 0
        for length in lengths:
            padded = (length+15)//16*16 if encrypted else length
            block = data[position:position+padded]
            position += padded
            if encrypted:
                block = aes_decrypt(block)
            block = block[:length]
            expected = min(block_size or uncompressed, uncompressed - len(result))
            if not method:
                decoded = block
            elif self.methods[method] == 'Oodle':
                decoded = self.codec.decompress(block, expected)
            elif self.methods[method] == 'Zlib':
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(block, expected + 1)
                if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
                    raise ValueError('Invalid compressed Pak block')
            else:
                raise ValueError('Unsupported Pak compression method')
            if len(decoded) != expected:
                raise ValueError('Pak decoded block size mismatch')
            result.extend(decoded)
        if len(result) != uncompressed:
            raise ValueError('Pak decoded member size mismatch')
        return bytes(result)
