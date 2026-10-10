#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mem_layout.py —— 解析 ELF，输出 Flash(LMA) 与 RAM(VMA) 的精确内存布局。

为什么需要这个工具：
    `size` 命令只给 text/data/bss 三个汇总数字，看不出"哪块占了多少、
    放在 Flash 还是 RAM、上电后搬不搬"。而做 OTA 规划必须知道
    Flash 里到底有哪些段、各自占多少、起始地址在哪。

用法：
    python tools/mem_layout.py <elf>              # 布局报告
    python tools/mem_layout.py <elf> --list       # 列出所有 ALLOC 段原始信息
    python tools/mem_layout.py <elf> --json x.json

说明：
    Flash 占用按 PT_LOAD 的 p_paddr/p_filesz 统计（就是烧录器要写进去的字节）；
    RAM 占用按段的 VMA 统计。链接脚本里写了 `AT>FLASH` 的段，
    其 VMA 在 RAM、LMA 在 Flash —— 这就是"上电搬到 RAM"的段。
"""
import sys, json, struct
from collections import namedtuple

sys.stdout.reconfigure(encoding='utf-8')

SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 0x1, 0x2, 0x4
PT_LOAD = 1

RAM_LO, RAM_HI = 0x20000000, 0x20003000     # CH572: 12 KB SRAM（0x20000000 起，共 0x3000）
FLASH_LO, FLASH_HI = 0x00000000, 0x0003C000  # CH572: 240 KB 用户 Flash

Sec = namedtuple('Sec', 'name typ flags addr offset size')


def _u(d, off, n):
    """按 ELF32 小端读 n 个 32 位字。"""
    return struct.unpack_from('<' + 'I' * n, d, off)


def parse_elf(path):
    """返回 (段列表, 程序头列表)。"""
    d = open(path, 'rb').read()
    if d[:4] != b'\x7fELF':
        raise ValueError('不是 ELF 文件：%s' % path)
    if d[4] != 1:
        raise ValueError('只支持 32 位 ELF')

    (e_shoff,) = _u(d, 0x20, 1)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', d, 0x2E)

    raw = []
    for i in range(e_shnum):
        o = e_shoff + i * e_shentsize
        name, typ, flags, addr, off, size = _u(d, o, 6)
        raw.append((name, typ, flags, addr, off, size))

    # 段名字符串表
    so = e_shoff + e_shstrndx * e_shentsize
    _, _, _, _, str_off, str_size = _u(d, so, 6)
    strtab = d[str_off:str_off + str_size]

    def sname(n):
        if n == 0:
            return ''
        e = strtab.find(b'\0', n)
        return strtab[n:e].decode('utf-8', 'replace')

    secs = [Sec(sname(n), t, f, a, o, s) for (n, t, f, a, o, s) in raw]

    # 程序头
    (e_phoff,) = _u(d, 0x1C, 1)
    e_phentsize, e_phnum = struct.unpack_from('<HH', d, 0x2A)
    phdrs = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz = _u(d, o, 6)
        phdrs.append(dict(type=p_type, off=p_offset, vaddr=p_vaddr,
                          paddr=p_paddr, filesz=p_filesz, memsz=p_memsz))
    return secs, phdrs


def analyze(path):
    secs, phdrs = parse_elf(path)

    # ---------------- Flash：按 PT_LOAD 的物理地址统计 ----------------
    loads = [p for p in phdrs if p['type'] == PT_LOAD and p['filesz'] > 0]
    loads.sort(key=lambda p: p['paddr'])

    # 给每个 PT_LOAD 找出它覆盖的段名（按 VMA 区间相交判断）
    def covered(ld):
        out = []
        for s in secs:
            if s.size == 0 or not (s.flags & SHF_ALLOC):
                continue
            if s.addr < ld['vaddr'] + ld['memsz'] and s.addr + s.size > ld['vaddr']:
                out.append(s)
        return out

    flash = []
    for ld in loads:
        cov = covered(ld)
        flash.append(dict(paddr=ld['paddr'], vaddr=ld['vaddr'],
                          size=ld['filesz'], sections=[s.name for s in cov]))

    # ---------------- RAM：ALLOC 且 VMA 落在 RAM 区间的段 ----------------
    ram = sorted([s for s in secs
                  if (s.flags & SHF_ALLOC) and s.size > 0
                  and RAM_LO <= s.addr < RAM_HI],
                 key=lambda s: s.addr)

    return dict(elf=path, secs=secs, flash=flash, ram=ram)


def fmt(n):
    return '{0:,}'.format(n)


def report(L):
    print('=' * 80)
    print('内存布局分析  %s' % L['elf'])
    print('=' * 80)

    # ---------- Flash ----------
    print()
    print('【Flash 占用】按 LMA 统计（= 烧录器要写进芯片的字节）')
    print('-' * 80)
    print('  %-32s %-12s %-12s %9s' % ('内容', 'Flash 地址', 'RAM 地址', '大小'))
    tot = 0
    for f in L['flash']:
        names = f['sections'] or ['(未命名)']
        label = names[0] if len(names) == 1 else '%s 等%d段' % (names[0], len(names))
        vad = ('0x%08X' % f['vaddr']) if f['vaddr'] else '—'
        moved = '' if f['vaddr'] in (0, f['paddr']) else '   ← 上电搬到 RAM'
        print('  %-32s 0x%08X   %-12s %9s%s'
              % (label[:32], f['paddr'], vad, fmt(f['size']), moved))
        tot += f['size']
    print('  %-32s %-25s %9s' % ('Flash 合计', '', fmt(tot)))

    # ---------- RAM ----------
    print()
    print('【RAM 占用】按 VMA 统计（运行期实际地址范围）')
    print('-' * 80)
    if not L['ram']:
        print('  （无 RAM 段）')
        return tot, 0, 0

    lo = min(s.addr for s in L['ram'])
    hi = max(s.addr + s.size for s in L['ram'])
    for s in L['ram']:
        attr = ''.join(c for c, fl in (('W', SHF_WRITE), ('X', SHF_EXECINSTR))
                       if s.flags & fl) or 'R'
        print('  0x%08X - 0x%08X  %8s  %-16s %s'
              % (s.addr, s.addr + s.size, fmt(s.size), s.name, attr))
    span = hi - lo
    # 栈：RAM 顶端 - RAM 段结束
    stack = RAM_HI - hi
    print('  %-38s %8s  （已占用）' % ('', fmt(span)))
    print('  0x%08X - 0x%08X  %8s  %s'
          % (hi, RAM_HI, fmt(stack), '.stack （可用栈空间）'))
    print('  %-38s %8s  （RAM 总量 12 KB = 12,288 字节）' % ('', fmt(RAM_HI - RAM_LO)))
    return tot, span, stack


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return 1
    path = args[0]

    if '--list' in sys.argv:
        secs, _ = parse_elf(path)
        print('%-26s %-12s %10s  %s' % ('段名', '地址', '大小', '标志'))
        print('-' * 62)
        for s in secs:
            if (s.flags & SHF_ALLOC) and s.size > 0:
                attr = ''.join(c for c, fl in (('W', SHF_WRITE), ('X', SHF_EXECINSTR))
                               if s.flags & fl) or 'R'
                print('%-26s 0x%08X %10s  %s' % (s.name, s.addr, fmt(s.size), attr))
        return 0

    L = analyze(path)
    flash_tot, ram_used, stack = report(L)

    if '--json' in sys.argv:
        i = sys.argv.index('--json')
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else 'mem_layout.json'
        json.dump(dict(elf=L['elf'], flash_total=flash_tot, ram_used=ram_used,
                       stack=stack, flash=L['flash'],
                       ram=[dict(name=s.name, addr='0x%08X' % s.addr, size=s.size)
                            for s in L['ram']]),
                  open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print('\nJSON 已写入 %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
