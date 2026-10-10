# -*- coding: utf-8 -*-
"""hex_probe.py —— 解析 Intel HEX，输出地址跨度、空洞、填充值统计。"""
import sys
sys.stdout.reconfigure(encoding='utf-8')


def load_hex(path):
    mem = {}
    ext = 0
    recs = 0
    for line in open(path, 'r', errors='replace'):
        line = line.strip()
        if not line.startswith(':'):
            continue
        try:
            b = bytes.fromhex(line[1:])
        except ValueError:
            continue
        n, off, typ = b[0], (b[1] << 8) | b[2], b[3]
        if typ == 0:
            base = ext + off
            for i in range(n):
                mem[base + i] = b[4 + i]
            recs += 1
        elif typ == 4:
            ext = ((b[4] << 8) | b[5]) << 16
        elif typ == 2:
            ext = ((b[4] << 8) | b[5]) << 4
    return mem, recs


for path in sys.argv[1:]:
    mem, recs = load_hex(path)
    if not mem:
        print(path, '-> 空')
        continue
    lo, hi = min(mem), max(mem) + 1
    total = len(mem)
    print('=' * 88)
    print(path)
    print('  数据记录: %d   地址范围: 0x%08X - 0x%08X' % (recs, lo, hi))
    print('  实际字节: %d   跨度: %d   空洞: %d' % (total, hi - lo, (hi - lo) - total))
    from collections import Counter
    c = Counter(mem.values())
    print('  出现最多的字节值:', ', '.join('0x%02X=%d' % (v, n) for v, n in c.most_common(5)))
    print('  填充率(0xFF): %.1f%%' % (100.0 * c.get(0xFF, 0) / total))
    # 每 16K 分块统计
    print('  分区段统计:')
    blk = {}
    for a, v in mem.items():
        k = a >> 14
        blk.setdefault(k, [0, 0])
        blk[k][0] += 1
        if v != 0xFF:
            blk[k][1] += 1
    for k in sorted(blk):
        print('    0x%06X - 0x%06X : 有数据 %6d 字节, 非0xFF %6d 字节'
              % (k << 14, ((k + 1) << 14) - 1, blk[k][0], blk[k][1]))
    print()
