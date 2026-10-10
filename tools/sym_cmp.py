# -*- coding: utf-8 -*-
"""sym_cmp.py —— 对比两个静态库的符号集合，并按模块分类给出缺失清单。

另外统计库的实际二进制体积（按节类型汇总），用于评估 flash/RAM 占用。
"""
import sys, collections
sys.stdout.reconfigure(encoding='utf-8')
from ar_scan import parse_ar, parse_elf_syms


def sections_of(data):
    """返回 {节名: (类型, 大小)}（含重定位，便于估算代码体积）。"""
    import struct
    if data[:4] != b'\x7fELF':
        return {}
    is64 = data[4] == 2
    if is64:
        e_shoff = struct.unpack_from('<Q', data, 0x28)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', data, 0x3A)
        fmt = '<IIQQQQIIQQ'
    else:
        e_shoff = struct.unpack_from('<I', data, 0x20)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', data, 0x2E)
        fmt = '<IIIIIIIIII'
    raw = []
    for i in range(e_shnum):
        v = struct.unpack_from(fmt, data, e_shoff + i * e_shentsize)
        raw.append(v)
    stro = raw[e_shstrndx][4]

    def nm(i):
        s = raw[i][0]
        e = data.index(b'\0', stro + s)
        return data[stro + s:e].decode('utf-8', 'replace')

    out = {}
    for i, v in enumerate(raw):
        out[nm(i)] = (v[1], v[5])
    return out


def load(path):
    syms, undef = set(), set()
    size_by_sec = collections.Counter()
    sectype_by_sec = {}
    for name, off, size, body in parse_ar(path):
        d, u = parse_elf_syms(body)
        if d is None:
            continue
        for s in d:
            if s[3] == 2:      # STB_GLOBAL
                syms.add(s[0])
        undef |= u
        for sec, (typ, sz) in sections_of(body).items():
            if typ != 8:       # 排除 NOBITS(.bss)，另算
                size_by_sec[sec] += sz
            else:
                sectype_by_sec[sec] = sz
    return syms, undef, size_by_sec, sectype_by_sec


def report(path, tag):
    syms, undef, sizes, nobits = load(path)
    print('=' * 96)
    print('%s : %s' % (tag, path))
    print('  全局定义符号: %d   未定义符号: %d' % (len(syms), len(undef)))
    codenames = [s for s in sizes if s.startswith('.text') or s.startswith('.rodata') or s.startswith('.srodata')]
    code = sum(sizes[s] for s in codenames)
    datanames = [s for s in sizes if s.startswith('.data') and 'rel' not in s and 'ro' not in s]
    data = sum(sizes[s] for s in datanames)
    bss = sum(nobits.values())
    print('  .text+.rodata 合计: %d 字节 (%.1f KB)' % (code, code / 1024))
    print('  .data  合计: %d 字节   .bss 合计: %d 字节' % (data, bss))
    top = sorted(((s, v) for s, v in sizes.items()
                  if s.startswith(('.text', '.rodata', '.srodata', '.data'))), key=lambda x: -x[1])[:12]
    print('  主要节:')
    for s, v in top:
        print('     %-28s %8d' % (s, v))
    return syms, undef


a_syms, a_und = report(sys.argv[1], '库A')
b_syms, b_und = report(sys.argv[2], '库B')

print()
print('=' * 96)
print('符号集合差异 (仅比较全局定义符号)')
print('  库A 独有: %d' % len(a_syms - b_syms))
print('  库B 独有: %d' % len(b_syms - a_syms))
common = a_syms & b_syms
print('  共有: %d' % len(common))

PREFIX = ['GAPRole_Central', 'GAPRole_Observer', 'GAPRole_Broadcaster', 'GAPRole_Peripheral',
          'GAPRole_Sync', 'GAPBondMgr', 'GATT_', 'GATTServApp', 'GATTClientApp', 'LL_', 'HCI_',
          'LLC_', 'SM_', 'RF_', 'LLE_', 'BB_', 'TMOS_', 'tmos', 'ATT_', 'L2CAP_', 'L2CAP',
          'll_', 'lle_', 'rf_', 'bb_', 'ip_', 'aes', 'AES', 'hal', 'HAL', 'R8_', 'pfn']

groups = collections.defaultdict(lambda: [0, 0])
for s in sorted(b_syms):
    for p in PREFIX:
        if s.startswith(p):
            groups[p][0] += 1
            if s in a_syms:
                groups[p][1] += 1
            break
print()
print('%-24s %10s %10s %10s' % ('符号前缀', '库B总数', '库A也有', '库B独有'))
print('-' * 60)
for p, (tot, ina) in sorted(groups.items(), key=lambda x: -(x[1][0] - x[1][1])):
    print('%-24s %10d %10d %10d' % (p, tot, ina, tot - ina))
