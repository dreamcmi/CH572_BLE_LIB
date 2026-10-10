# -*- coding: utf-8 -*-
"""统计跳转表里空槽（0）的实际情况，并做"连续排列"的推演。

回答的问题：如果把 43 个空槽去掉、让 API 连续排列，会怎样？
"""
import io
import os
import re
import struct
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH572_BLE_LIB'
BIN = os.path.join(ROOT, 'lib', 'CH572BLE_ROM.bin')
JT_MD = os.path.join(ROOT, 'lib', 'CH572BLE_ROM_JT.md')
BASE = 0x20000
JT_OFF = 0x34


def jt_rows():
    rows = []
    for line in io.open(JT_MD, encoding='utf-8'):
        if not line.startswith('|'):
            continue
        p = [x.strip() for x in line.strip('|').split('|')]
        p = [x for x in p if x != '']
        if len(p) != 4 or not p[0].isdigit():
            continue
        rows.append((int(p[0]), p[1].strip('`'), p[2], p[3]))
    return rows


def main():
    blob = open(BIN, 'rb').read()
    rows = jt_rows()
    n = len(rows)
    assert n == 164, n

    slots = [struct.unpack_from('<I', blob, JT_OFF + i * 4)[0] for i in range(n)]

    out = []
    W = out.append
    W('# 跳转表空槽分析')
    W('')
    W('镜像 %s，%d 字节；跳转表 %d 项 × 4 B = %d 字节（+0x34 .. +0x%X）'
      % (os.path.basename(BIN), len(blob), n, n * 4, JT_OFF + n * 4))
    W('')

    zero = [i for i, v in enumerate(slots) if v == 0]
    nonzero = [i for i, v in enumerate(slots) if v != 0]
    W('| 项目 | 数量 |')
    W('|---|---:|')
    W('| 非 0 槽（真实地址） | %d |' % len(nonzero))
    W('| 0 槽（空） | %d |' % len(zero))
    W('| 其中"本平台无此 API" | %d |' % len([i for i, _n, s, _a in rows if not s.startswith('可用') and '版本' not in s and '预留' not in _n]))
    W('| 其中"纯预留/对齐" | %d |' % len([i for i, _n, _s, _a in rows if '预留' in _n]))
    W('| **空槽占用的 Flash** | **%d 字节** |' % (len(zero) * 4))
    W('')

    # 空槽在表里的分布（连续段）
    segs = []
    if zero:
        s = zero[0]
        p = zero[0]
        for z in zero[1:]:
            if z == p + 1:
                p = z
                continue
            segs.append((s, p))
            s = p = z
        segs.append((s, p))
    W('空槽分布（连续段）：')
    W('')
    W('| 段 | 下标范围 | 长度 | 前一个可用 API | 后一个可用 API |')
    W('|---|---|---:|---|---|')
    for a, b in segs:
        before = next((nm for i, nm, st, _ in reversed(rows)
                       if i < a and st.startswith('可用')), '—')
        after = next((nm for i, nm, st, _ in rows
                      if i > b and st.startswith('可用')), '—')
        W('| | %d – %d | %d | %s | %s |' % (a, b, b - a + 1, before, after))
    W('')

    # ---- 连续排列推演 ----
    W('## 推演：若把空槽删掉、API 连续排列会怎样')
    W('')
    W('做法：保持"可用 API 的相对顺序"，把它们的下标重新压成 0..99。')
    W('')
    W('| API | 现在下标 | 压紧后下标 | 变化 |')
    W('|---|---:|---:|---:|')
    shifts = []
    # slot 0 是 VER_LIB，从可用的第一个开始压
    newidx = 0
    for i, nm, st, ad in rows:
        if '预留' in nm:
            continue
        if not (st.startswith('可用') or '版本' in st):
            continue
        shifts.append((nm, i, newidx))
        newidx += 1
    for nm, old, new in shifts[:6] + [('…', '', '')] + shifts[-6:]:
        if nm == '…':
            W('| … | … | … | … |')
            continue
        W('| `%s` | %d | %d | %+d |' % (nm, old, new, new - old))
    W('')
    moved = [s for s in shifts if s[1] != s[2]]
    W('**%d 个 API 里，%d 个的下标会变**（只有下标 0 附近少数几个不动）。' % (len(shifts), len(moved)))
    W('')
    W('代价：')
    W('')
    W('1. `include/CH572BLE_ROM.h` 里每个 `BLE_LIB_JT(n)` 都要重写 —— 也就是')
    W('   放弃 WCH 官方 `CH59xBLE_ROM.h` 的下标契约，不再能沿用官方头文件。')
    W('2. 应用层所有已编译的代码（含第三方示例、旧固件）全部失效：')
    W('   同一个 `BLE_LIB_JT(48)` 从"无此 API"变成别的函数，**静默调用错函数**。')
    W('3. 省下的 Flash = 空槽字节数 = **%d 字节**（占总镜像 %.3f%%）。'
      % (len(zero) * 4, 100.0 * len(zero) * 4 / len(blob)))
    W('')

    io.open(os.path.join(ROOT, 'build', 'jt_gap_analysis.md'), 'w',
            encoding='utf-8').write('\n'.join(out))
    print('空槽 %d 个 = %d 字节；压紧后 %d 个 API 里有 %d 个下标会变'
          % (len(zero), len(zero) * 4, len(shifts), len(moved)))


if __name__ == '__main__':
    main()
