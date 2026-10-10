"""Check ROM HEX structure using the generated jump-table and RAM metadata."""
import argparse
import re
import struct
from pathlib import Path


def load_hex(path):
    memory, ext, eof = {}, 0, False
    for lineno, line in enumerate(Path(path).read_text(encoding='ascii').splitlines(), 1):
        if not line:
            continue
        if eof or not line.startswith(':'):
            raise ValueError(f'line {lineno}: invalid HEX record')
        raw = bytes.fromhex(line[1:])
        if len(raw) < 5 or len(raw) != raw[0] + 5 or sum(raw) & 255:
            raise ValueError(f'line {lineno}: invalid length/checksum')
        count, offset, kind = raw[0], int.from_bytes(raw[1:3], 'big'), raw[3]
        data = raw[4:4 + count]
        if kind == 0:
            for i, value in enumerate(data):
                address = ext + offset + i
                if address in memory and memory[address] != value:
                    raise ValueError(f'conflicting data at 0x{address:X}')
                memory[address] = value
        elif kind == 1:
            if count or offset:
                raise ValueError('invalid EOF')
            eof = True
        elif kind in (2, 4):
            if count != 2 or offset:
                raise ValueError('invalid extended address')
            ext = int.from_bytes(data, 'big') << (4 if kind == 2 else 16)
        elif kind not in (3, 5):
            raise ValueError(f'unsupported HEX record: {kind}')
    if not memory or not eof:
        raise ValueError('empty HEX or missing EOF')
    return memory


def u32(memory, address):
    return struct.unpack('<I', bytes(memory[address + i] for i in range(4)))[0]


def main():
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('hex')
    p.add_argument('base', nargs='?', type=lambda s: int(s, 0))
    p.add_argument('entries', nargs='?', type=int)
    p.add_argument('--header', default=str(root / 'include/CH572BLE_ROM.h'))
    p.add_argument('--ram-header', default=str(root / 'rom/rom_ram_info.h'))
    p.add_argument('--quiet', action='store_true')
    a = p.parse_args()
    header = Path(a.header).read_text(encoding='utf-8')
    ram = Path(a.ram_header).read_text(encoding='utf-8')

    def macro(text, name):
        match = re.search(r'^#define\s+' + name + r'\s+(0x[\dA-Fa-f]+|\d+)', text, re.M)
        if not match:
            raise ValueError(f'missing generated macro: {name}')
        return int(match[1], 0)

    base = a.base if a.base is not None else macro(header, 'LIB_FLASH_BASE_ADDRESSS')
    entries = a.entries if a.entries is not None else macro(header, 'ROM_JT_ENTRY_COUNT')
    ram_start = macro(ram, 'ROM_RAM_HIGHCODE_VMA')
    ram_end = macro(ram, 'ROM_RAM_HIGHCODE_END')
    memory = load_hex(a.hex)
    end = base + macro(header, 'LIB_FLASH_SIZE')
    if entries <= 0 or entries > 1024 or base + 0x34 + entries * 4 > end:
        raise ValueError('invalid jump-table length')
    if min(memory) != base or max(memory) + 1 != end:
        raise ValueError('HEX range differs from generated image metadata')
    if u32(memory, base) != 0x43483537:
        raise ValueError('ROM magic mismatch')
    uuid = struct.unpack('<22H', bytes(memory[base + 8 + i] for i in range(44)))
    expected = (0x1800, 0x1801, 0x2800, 0x2801, 0x2802, 0x2803,
                0x2900, 0x2901, 0x2902, 0x2903, 0x2904, 0x2905,
                0x2906, 0x2907, 0x2908, 0x2A00, 0x2A01, 0x2A02,
                0x2A03, 0x2A04, 0x2A05, 0x2A06)
    if uuid != expected:
        raise ValueError('UUID table mismatch')
    flash = copied = zero = 0
    for i in range(entries):
        value = u32(memory, base + 0x34 + 4 * i)
        if value == 0:
            zero += 1
        elif base <= value < end:
            flash += 1
        elif ram_start <= value < ram_end:
            copied += 1
        else:
            raise ValueError(f'JT[{i}] outside Flash/copied RAM: 0x{value:08X}')
    print(f'PASS: magic/22 UUIDs/{entries} JT entries; Flash={flash}, copied RAM={copied}, reserved={zero}')
    if not a.quiet:
        print(f'Flash: 0x{base:08X}-0x{end - 1:08X}; RAM code: 0x{ram_start:08X}-0x{ram_end - 1:08X}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError) as error:
        print(f'FAIL: {error}')
        raise SystemExit(1)
