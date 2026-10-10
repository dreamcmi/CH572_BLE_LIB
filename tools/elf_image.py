"""Minimal ELF32 little-endian reader for CH572 build validation (no dependencies)."""
import struct
from pathlib import Path


class ElfImage:
    def __init__(self, path):
        self.path = Path(path)
        self.raw = self.path.read_bytes()
        if self.raw[:7] != b'\x7fELF\x01\x01\x01':
            raise ValueError(f'{path}: expected little-endian ELF32')
        h = struct.unpack_from('<16sHHIIIIIHHHHHH', self.raw)
        self.segments = []
        for i in range(h[10]):
            p = struct.unpack_from('<8I', self.raw, h[5] + i * h[9])
            if p[0] == 1:
                self.segments.append(dict(offset=p[1], vma=p[2], lma=p[3],
                                          filesz=p[4], memsz=p[5], flags=p[6]))
        sections = [struct.unpack_from('<10I', self.raw, h[6] + i * h[11])
                    for i in range(h[12])]
        strings = sections[h[13]]
        names = self.raw[strings[4]:strings[4] + strings[5]]
        self.sections = {}
        self.symbols = {}
        for s in sections:
            name = names[s[0]:].split(b'\0', 1)[0].decode('ascii')
            self.sections[name] = dict(type=s[1], flags=s[2], vma=s[3],
                                       offset=s[4], size=s[5], align=s[8])
            if s[1] == 2:
                st = sections[s[6]]
                table = self.raw[st[4]:st[4] + st[5]]
                for off in range(s[4], s[4] + s[5], s[9]):
                    n, value, size, info, other, index = struct.unpack_from('<IIIBBH', self.raw, off)
                    sym = table[n:].split(b'\0', 1)[0].decode('ascii', errors='replace')
                    if sym and index:
                        self.symbols[sym] = value

    def lma(self, name):
        s = self.sections[name]
        for p in self.segments:
            if p['vma'] <= s['vma'] and s['vma'] + s['size'] <= p['vma'] + p['filesz']:
                return p['lma'] + s['vma'] - p['vma']
        raise ValueError(f'{self.path}: section {name} has no Flash load image')

    def flash_bytes(self):
        result = {}
        for p in self.segments:
            if not p['filesz']:
                continue
            if p['lma'] + p['filesz'] > 0x3C000:
                raise ValueError(f'{self.path}: load segment outside user Flash')
            for i, byte in enumerate(self.raw[p['offset']:p['offset'] + p['filesz']]):
                addr = p['lma'] + i
                if addr in result and result[addr] != byte:
                    raise ValueError(f'{self.path}: conflicting load segments')
                result[addr] = byte
        return result
