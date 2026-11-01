"""Bundled open decoder and standard Oodle stored-block framing."""
import ctypes
import hashlib
import json
from pathlib import Path
import sys

MAX_BYTES = 64 * 1024 * 1024


class OpenOodleCodec:
    def __init__(self, directory=None):
        from .core import EditorError
        if directory is None:
            base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
            directory = base/'native' if getattr(sys, 'frozen', False) else base/'.local/native'
        directory = Path(directory)
        try:
            metadata = json.loads((directory/'manifest.json').read_text(encoding='utf-8-sig'))
            if metadata.get('abi') != 1 or metadata.get('library') != 'pbz_codec.dll':
                raise ValueError('Codec ABI mismatch')
            self.path = directory/'pbz_codec.dll'
            if hashlib.sha256(self.path.read_bytes()).hexdigest() != metadata['sha256']:
                raise ValueError('Codec checksum mismatch')
            self.dll = ctypes.CDLL(str(self.path.resolve()))
            self.dll.pbz_codec_version.restype = ctypes.c_uint32
            if self.dll.pbz_codec_version() != 1:
                raise ValueError('Codec ABI mismatch')
            self.fn = self.dll.pbz_decode
            self.fn.restype = ctypes.c_ssize_t
            self.fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        except (OSError, ValueError, KeyError) as error:
            raise EditorError('内置解码组件缺失或损坏，请重新解压完整的运行包。') from error
        self.backend = 'bundled-open'

    def decompress(self, data, size):
        from .core import EditorError
        if not isinstance(size, int) or not 0 <= size <= MAX_BYTES or len(data) > MAX_BYTES + 65536:
            raise EditorError('压缩数据大小超出支持范围。')
        if size == 0:
            if data:
                raise EditorError('空数据块包含了多余内容。')
            return b''
        output = ctypes.create_string_buffer(size)
        n = self.fn(data, len(data), output, size)
        if n != size:
            raise EditorError('压缩数据未通过内置解码校验，文件未写入。')
        return output.raw

    def compress(self, data):
        from .core import EditorError
        if len(data) > MAX_BYTES:
            raise EditorError('压缩数据大小超出支持范围。')
        # Oodle block header: restart + uncompressed + standard large Kraken
        # block. Keeps container algorithm=2; no proprietary encoder is used.
        result = b''.join(b'\xcc\x06' + data[p:p+0x40000] for p in range(0, len(data), 0x40000))
        if self.decompress(result, len(data)) != data:
            raise EditorError('存储块未通过往返校验。')
        return result
