"""Verify the two independently linked images and create a sparse combined HEX."""
import argparse
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path
from elf_image import ElfImage
from rom_inspect import load_hex


def write_hex(memory, output):
    def record(addr, kind, data):
        raw = bytes([len(data), addr >> 8, addr & 255, kind]) + bytes(data)
        return ':' + (raw + bytes([(-sum(raw)) & 255])).hex().upper()
    lines, upper = [], None
    addresses = sorted(memory)
    i = 0
    while i < len(addresses):
        start = addresses[i]
        if start >> 16 != upper:
            upper = start >> 16
            lines.append(record(0, 4, [upper >> 8, upper & 255]))
        data = []
        while i < len(addresses) and len(data) < 16 and addresses[i] == start + len(data) and addresses[i] >> 16 == upper:
            data.append(memory[addresses[i]])
            i += 1
        lines.append(record(start & 65535, 0, data))
    lines.append(':00000001FF')
    Path(output).write_text('\n'.join(lines) + '\n', encoding='ascii')


def verify(rom_path, app_path, ram_header, objdump, output=None, check_hex=None):
    rom, app = ElfImage(rom_path), ElfImage(app_path)
    ram = {k: int(v, 16) for k, v in re.findall(r'^#define\s+(ROM_RAM_\w+)\s+0x([\dA-Fa-f]+)', Path(ram_header).read_text(encoding='utf-8'), re.M)}
    base = rom.symbols['ROM_BASE']
    memory = rom.flash_bytes()
    if struct.unpack('<I', bytes(memory[base + i] for i in range(4)))[0] != 0x43483537:
        raise ValueError('ROM magic mismatch')
    # Verify every named relocation in the actual jump table, including extra APIs.
    asm = Path(rom_path).parent / 'rom_jt.S'
    slots = re.findall(r'^\s*\.word\s+(\w+)', asm.read_text(encoding='utf-8'), re.M)
    if len(slots) * 4 != rom.sections['.rom_jt']['size']:
        raise ValueError('JT assembly/ELF size mismatch')
    for i, name in enumerate(slots):
        ptr = struct.unpack('<I', bytes(memory[base + 0x34 + 4 * i + j] for j in range(4)))[0]
        expected = 0 if name == '0' else rom.symbols[name]
        if ptr != expected:
            raise ValueError(f'JT[{i}] {name}: 0x{ptr:X} != 0x{expected:X}')
        if ptr and not (base <= ptr < rom.symbols['_rom_end_addr'] or 0x20000000 <= ptr < ram['ROM_RAM_HIGHCODE_END']):
            raise ValueError(f'JT[{i}] points outside image code')
    for name, key in (('.highcode', 'HIGHCODE'), ('.data', 'DATA'), ('.bss', 'BSS')):
        s = rom.sections[name]
        if s['vma'] != ram['ROM_RAM_' + key + '_VMA'] or s['size'] != ram['ROM_RAM_' + key + '_SIZE']:
            raise ValueError(f'{name}: stale RAM metadata')
        if key != 'BSS' and rom.lma(name) != ram['ROM_RAM_' + key + '_LMA']:
            raise ValueError(f'{name}: stale RAM copy source')
    reserve = ram['ROM_RAM_RESERVE_END']
    for name, s in app.sections.items():
        if s['flags'] & 2 and s['size'] and s['vma'] >= 0x20000000:
            if s['vma'] < reserve or s['vma'] + s['size'] > 0x20003000:
                raise ValueError(f'application {name} overlaps ROM RAM or exceeds SRAM')
    stack = 0x20003000 - app.symbols['_ebss']
    if stack < 1024:
        raise ValueError(f'insufficient stack: {stack} bytes')
    # Simulate the reset copy from Flash to application highcode.
    af = app.flash_bytes()
    start, end, load = (app.symbols[k] for k in ('_highcode_vma_start', '_highcode_vma_end', '_highcode_lma'))
    for name in ('.highcode',):
        s = app.sections[name]
        for i in range(s['size']):
            if af.get(load + s['vma'] - start + i) != app.raw[s['offset'] + i]:
                raise ValueError(f'reset copy does not match {name}')
    if app.symbols['_vector_base'] & 1023:
        raise ValueError('invalid vector base/copy start')
    if end < app.sections['.highcode']['vma'] + app.sections['.highcode']['size']:
        raise ValueError('reset copy omits highcode')
    if 'BLE_LibInit' in app.symbols or 'FLASH_CMD_ROM_WRITE' in app.symbols:
        raise ValueError('application duplicates stack/ISP implementation')
    reset = subprocess.run([objdump, '-d', '--disassemble=handle_reset', str(app.path)], check=True, capture_output=True, text=True).stdout
    if not re.search(r'\bfence\.i\b', reset):
        raise ValueError('reset copy lacks instruction synchronization')
    vector_write = reset.find('mtvec,')
    status_write = reset.find('mstatus,t0')
    if vector_write < 0 or status_write <= vector_write:
        raise ValueError('reset configures global status before the vector table')
    # Interrupt entries must return with mret; ordinary library bodies use ret.
    for image, names in ((rom, ('BB_IRQHandler', 'LLE_IRQHandler')),
                         (app, ('BB_IRQHandler', 'LLE_IRQHandler', 'SysTick_Handler', 'RTC_IRQHandler'))):
        for name in names:
            dis = subprocess.run([objdump, '-d', '--disassemble=' + name, str(image.path)], check=True, capture_output=True, text=True).stdout
            if not re.search(r'\bmret\b', dis):
                raise ValueError(f'{image.path}: {name} lacks interrupt return')
    dis = subprocess.run([objdump, '-d', str(rom.path)], check=True, capture_output=True, text=True).stdout
    if re.search(r'\bgp\b', dis):
        raise ValueError('independent ROM image uses application gp')
    # Every low-level flash operation must execute within the copied RAM code.
    for name, value in rom.symbols.items():
        if name.startswith(('FLASH_CMD_', 'FLASH_ROM_', 'FLASH_START')) and not ram['ROM_RAM_HIGHCODE_VMA'] <= value < ram['ROM_RAM_HIGHCODE_END']:
            raise ValueError(f'{name}: flash operation not in RAM')
    snv, snv_size = app.symbols['SNV_ADDR'], app.symbols['SNV_SIZE']
    vector = app.sections['.vectors']
    for irq, name in ((20, 'BB_IRQHandler'), (21, 'LLE_IRQHandler'), (12, 'SysTick_Handler'), (28, 'RTC_IRQHandler')):
        pointer = struct.unpack_from('<I', app.raw, vector['offset'] + irq * 4)[0]
        if pointer != app.symbols[name]:
            raise ValueError(f'vector {irq} does not resolve to {name}')
        if not start <= pointer < end:
            raise ValueError(f'vector {irq} target {name} is outside copied application RAM code')
    if snv & 4095 or snv_size < 4096 or snv + snv_size > base:
        raise ValueError('invalid physical SNV erase partition')
    for addr, value in af.items():
        if addr >= snv or addr in memory:
            raise ValueError(f'application Flash overlaps SNV/ROM at 0x{addr:X}')
        memory[addr] = value
    if any(snv <= addr < snv + snv_size for addr in memory):
        raise ValueError('combined image overwrites SNV')
    if output:
        write_hex(memory, output)
        if load_hex(output) != memory:
            raise ValueError('combined HEX export mismatch')
        manifest = {
            'chip': 'CH572', 'validation': 'software checks passed; board test pending',
            'rom_base': hex(base), 'rom_end_exclusive': hex(rom.symbols['_rom_end_addr']),
            'rom_flash_bytes': len(rom.flash_bytes()), 'app_flash_bytes': len(af),
            'jump_table_entries': len(slots), 'rom_ram_bytes': ram['ROM_RAM_RESERVE_SIZE'],
            'app_highcode_bytes': app.sections['.highcode']['size'],
            'app_data_bytes': app.sections['.data']['size'], 'app_bss_bytes': app.sections['.bss']['size'],
            'stack_reserve_bytes': stack, 'snv_address': hex(snv), 'snv_sector_bytes': snv_size,
            'combined_hex_sha256': hashlib.sha256(Path(output).read_bytes()).hexdigest(),
            'rom_elf_sha256': hashlib.sha256(rom.raw).hexdigest(),
            'app_elf_sha256': hashlib.sha256(app.raw).hexdigest(),
        }
        Path(output).with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if check_hex and load_hex(check_hex) != memory:
        raise ValueError('saved combined HEX differs from current ROM/app ELF')
    print(f'PASS: {len(slots)} JT entries, ROM RAM {ram["ROM_RAM_RESERVE_SIZE"]} bytes, stack {stack} bytes; copies/IRQs/ISP/partitions verified')
    return dict(rom_ram=ram['ROM_RAM_RESERVE_SIZE'], stack=stack, flash_bytes=len(memory))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom', required=True)
    p.add_argument('--app', required=True)
    p.add_argument('--ram-header', required=True)
    p.add_argument('--objdump', required=True)
    p.add_argument('--out')
    p.add_argument('--check-hex')
    a = p.parse_args()
    verify(a.rom, a.app, a.ram_header, a.objdump, a.out, a.check_hex)
