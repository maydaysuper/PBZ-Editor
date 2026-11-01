"""Read-only PBZ IoStore and localization metadata extraction.

The IoStore field layout follows Epic's public format as implemented in retoc.
This module deliberately does not parse the game's custom ContainerHeader.
It reads the indexed file/chunk and compression block tables directly.
"""
from __future__ import annotations
import ctypes
import hashlib
import struct
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAK_KEY = bytes.fromhex('4BC8A51E5381325073F2BC7D4E3089CD557C4E9B7D453901C2C795518867E01D')

class Reader:
    def __init__(self, data: bytes, offset: int = 0): self.data, self.p = data, offset
    def take(self, size: int) -> bytes:
        if size < 0 or self.p + size > len(self.data): raise ValueError('Truncated resource')
        result = self.data[self.p:self.p+size]; self.p += size; return result
    def u32(self): return struct.unpack('<I', self.take(4))[0]
    def i32(self): return struct.unpack('<i', self.take(4))[0]
    def fstring(self):
        length = self.i32()
        if abs(length) > 16_000_000: raise ValueError('Invalid FString length')
        if length == 0: return ''
        return self.take(abs(length)*(2 if length < 0 else 1)).decode('utf-16le' if length < 0 else 'utf8').rstrip('\0')

def aes_decrypt(data, key=PAK_KEY):
    if len(data) % 16: raise ValueError('AES block alignment')
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return dec.update(data)+dec.finalize()

class OodleReader:
    """Use a caller-selected installed codec. No proprietary library is shipped."""
    def __init__(self, dll_path):
        self.dll = ctypes.WinDLL(str(Path(dll_path).resolve()))
        self.fn = self.dll.OodleLZ_Decompress
        self.fn.restype = ctypes.c_ssize_t
        self.fn.argtypes = [ctypes.c_void_p,ctypes.c_ssize_t,ctypes.c_void_p,ctypes.c_ssize_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_ssize_t,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ssize_t,ctypes.c_int]
    def decompress(self, data, size):
        out = ctypes.create_string_buffer(size)
        n = self.fn(data,len(data),out,size,1,0,0,None,0,None,None,None,0,3)
        if n != size: raise ValueError(f'Oodle decompression failed: {n} != {size}')
        return out.raw

class IoStore:
    def __init__(self, utoc_path, codec):
        self.path, self.codec = Path(utoc_path), codec
        self.data = b = self.path.read_bytes()
        if b[:16] != b'-==--==--==--==-': raise ValueError('Unknown IoStore magic')
        self.version = b[16]
        vals = struct.unpack_from('<9I', b,20)
        header_size, self.count, block_count, block_entry_size, method_count, method_length, self.block_size, dir_size, self.partitions = vals
        if header_size != 144 or block_entry_size != 12: raise ValueError('Unsupported IoStore header')
        self.flags = b[80]
        seeds = struct.unpack_from('<I',b,84)[0]
        self.partition_size = struct.unpack_from('<Q',b,88)[0]
        overflow = struct.unpack_from('<I',b,96)[0]
        self.offsets_start = header_size + self.count*12
        self.blocks_start = self.offsets_start + self.count*10 + seeds*4 + overflow*4
        methods_start = self.blocks_start + block_count*12
        self.methods = ['None']+[b[methods_start+i*method_length:methods_start+(i+1)*method_length].split(b'\0')[0].decode() for i in range(method_count)]
        directory_start = methods_start+method_count*method_length
        if self.flags & 4:
            sig_size = struct.unpack_from('<I',b,directory_start)[0]
            directory_start += 4+2*sig_size+block_count*20
        directory = b[directory_start:directory_start+dir_size]
        if self.flags & 2: directory = aes_decrypt(directory)
        r = Reader(directory)
        self.mount = r.fstring()
        dirs = [struct.unpack('<4I',r.take(16)) for _ in range(r.u32())]
        files = [struct.unpack('<3I',r.take(12)) for _ in range(r.u32())]
        names = [r.fstring() for _ in range(r.u32())]
        self.files = {}
        absent = 0xffffffff
        def walk(index, parent):
            name, child, sibling, file_index = dirs[index]
            current = parent+([names[name]] if name != absent else [])
            while file_index != absent:
                fname, file_index, chunk = files[file_index]
                self.files['/'.join(current+[names[fname]])] = chunk
            while child != absent:
                walk(child,current); child=dirs[child][2]
        if dirs: walk(0,[])
    def read_chunk(self, index):
        if not 0 <= index < self.count: raise ValueError('Invalid chunk index')
        p = self.offsets_start+index*10
        offset = int.from_bytes(self.data[p:p+5],'big')
        length = int.from_bytes(self.data[p+5:p+10],'big')
        first, last = offset//self.block_size, (offset+length-1)//self.block_size
        result = bytearray()
        for ix in range(first,last+1):
            p=self.blocks_start+ix*12; entry=self.data[p:p+12]
            disk = int.from_bytes(entry[:5],'little'); compressed=int.from_bytes(entry[5:8],'little'); raw=int.from_bytes(entry[8:11],'little'); method=entry[11]
            part, local = divmod(disk,self.partition_size)
            chunk_file=self.path.with_suffix('.ucas') if part==0 else self.path.with_name(self.path.stem+f'_s{part}.ucas')
            with chunk_file.open('rb') as f:
                f.seek(local); data=f.read((compressed+15)//16*16 if self.flags&2 else compressed)
            if self.flags&2: data=aes_decrypt(data)
            data=data[:compressed]
            if method:
                if self.methods[method] != 'Oodle': raise ValueError(f'Unsupported compression {self.methods[method]}')
                data=self.codec.decompress(data,raw)
            result.extend(data)
        start=offset-first*self.block_size
        return bytes(result[start:start+length])
    def read_file(self, path): return self.read_chunk(self.files[path])

def localization(data):
    r=Reader(data)
    if r.take(16) != bytes.fromhex('0e147475674a03fc4a15909dc3377f1b'): raise ValueError('Unknown locres magic')
    version=r.take(1)[0]
    if version != 3: raise ValueError(f'Unsupported locres version {version}')
    strings_offset=struct.unpack('<q',r.take(8))[0]
    r.u32()  # total key count
    entries=[]
    for _ in range(r.u32()):
        r.u32(); namespace=r.fstring()
        for _ in range(r.u32()):
            r.u32(); key=r.fstring(); source_hash=r.u32(); index=r.i32()
            entries.append((namespace,key,source_hash,index))
    s=Reader(data,strings_offset)
    strings=[]
    for _ in range(s.u32()): strings.append(s.fstring());s.i32()
    return {key:strings[index] for namespace,key,source_hash,index in entries if 0<=index<len(strings)}

def name_batch(data):
    """PBZ UE5.6 Zen summary has its serialized package name batch at byte 60."""
    count,string_length=struct.unpack_from('<II',data,60)
    if not 0<count<200000: raise ValueError('Unsupported name batch')
    headers=76+count*8; pos=headers+count*2; start=pos; names=[]
    for i in range(count):
        header=struct.unpack_from('>H',data,headers+i*2)[0]
        size=(header&0x7fff)*(2 if header&0x8000 else 1)
        names.append(data[pos:pos+size].decode('utf-16le' if header&0x8000 else 'utf8'))
        pos+=size
    if pos-start != string_length: raise ValueError('Name batch size mismatch')
    return names,pos
