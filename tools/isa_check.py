# -*- coding: utf-8 -*-
"""isa_check.py —— 校验编译产物只使用了 CH572（青稞 RISC-V3C）支持的指令。

为什么需要它：
    本工程允许用通用 RISC-V 工具链（rv32imac）做交叉验证编译，而 CH572 内核是
    RV32IMBC（无 A 扩展、无浮点）。如果工具链生成了原子指令（amo*/lr.w/sc.w）、
    浮点指令，或者 WCH 私人自定义指令，烧进 CH572 就会直接跑飞。
    本脚本在产物层面把这几个风险点查一遍。

做法：
    1. 从符号表取出所有 FUNC 类型符号，按地址排序；
    2. 只对这些"真实存在的函数区间"做线性扫描解码，避免把 .text 里的
       字符串 / 跳转表当成指令（那种做法会产生大量假阳性）；
    3. 按 RISC-V 规范判断 opcode 归属，报告 A 扩展 / 浮点 / 未知 opcode。

用法：
    python tools\\isa_check.py <elf文件> [更多elf...]
"""
import sys, struct, collections
sys.stdout.reconfigure(encoding='utf-8')


def read_elf(path):
    """解析 ELF32，返回 (可执行节列表, 函数符号列表)。"""
    data = open(path, 'rb').read()
    if data[:4] != b'\x7fELF' or data[4] != 1:
        raise ValueError('只支持 ELF32 文件')

    e_shoff = struct.unpack_from('<I', data, 0x20)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', data, 0x2E)

    secs = []
    for i in range(e_shnum):
        v = struct.unpack_from('<IIIIIIIIII', data, e_shoff + i * e_shentsize)
        secs.append(dict(nameoff=v[0], type=v[1], flags=v[2], addr=v[3],
                         off=v[4], size=v[5], link=v[6], entsize=v[9]))

    shstr = secs[e_shstrndx]['off']

    def nm(off, base=None):
        base = shstr if base is None else base
        e = data.index(b'\0', base + off)
        return data[base + off:e].decode('utf-8', 'replace')

    exec_secs, funcs = [], []
    for s in secs:
        name = nm(s['nameoff'])
        # SHF_EXECINSTR = 0x4
        if (s['flags'] & 0x4) and s['size'] > 0:
            exec_secs.append((name, s['addr'], data[s['off']:s['off'] + s['size']]))
        # SHT_SYMTAB = 2
        if s['type'] == 2:
            strtab = secs[s['link']]['off']
            n = s['size'] // 16
            for i in range(n):
                o = s['off'] + i * 16
                st_name, st_value, st_size, st_info, st_other, st_shndx = \
                    struct.unpack_from('<IIIBBH', data, o)
                if st_name == 0 or st_shndx == 0:
                    continue
                if (st_info & 0xF) != 2:      # STT_FUNC = 2
                    continue
                if st_size == 0:
                    continue
                funcs.append((st_value, st_size, nm(st_name, strtab)))

    return exec_secs, funcs


OPCODE_MAP = {
    0x37: 'lui',       0x17: 'auipc',   0x6F: 'jal',     0x67: 'jalr',
    0x63: 'branch',    0x03: 'load',    0x23: 'store',   0x13: 'op-imm',
    0x33: 'op',        0x0F: 'fence',   0x73: 'system',
    0x2F: 'ATOMIC(A扩展-不支持)',
    0x07: 'LOAD-FP(浮点-不支持)', 0x27: 'STORE-FP(浮点-不支持)',
    0x43: 'MADD-FP(浮点-不支持)', 0x47: 'MSUB-FP(浮点-不支持)',
    0x4B: 'NMSUB-FP(浮点-不支持)', 0x4F: 'NMADD-FP(浮点-不支持)',
    0x53: 'OP-FP(浮点-不支持)',
}
BAD = ('不支持',)


def scan(path, verbose=True):
    exec_secs, funcs = read_elf(path)

    # 建立 "地址 -> 节内容" 映射，便于按函数地址取码
    def fetch(addr, size):
        for name, base, blob in exec_secs:
            if base <= addr < base + len(blob):
                off = addr - base
                return blob[off:off + size], name
        return None, None

    if verbose:
        print('=' * 88)
        print('文件:', path)
        for name, addr, blob in exec_secs:
            print('  可执行节 %-18s 地址 0x%08X 大小 %6d' % (name, addr, len(blob)))
        print('  函数符号: %d 个（只扫描这些真实代码区间）' % len(funcs))
        print()

    counter = collections.Counter()
    bad_hits = []
    custom_hits = []
    total = 0

    # 去重：同一地址可能因弱符号重复出现
    seen = set()
    for addr, size, name in sorted(funcs):
        key = (addr, size)
        if key in seen:
            continue
        seen.add(key)

        blob, secname = fetch(addr, size)
        if blob is None:
            continue

        off = 0
        while off + 2 <= len(blob):
            half = struct.unpack_from('<H', blob, off)[0]
            if (half & 0x3) != 0x3:
                # 16 位压缩指令
                counter['compressed(16bit)'] += 1
                total += 1
                off += 2
                continue
            if off + 4 > len(blob):
                break
            w = struct.unpack_from('<I', blob, off)[0]
            total += 1
            opcode = w & 0x7F
            kind = OPCODE_MAP.get(opcode, 'UNKNOWN-opcode-0x%02X' % opcode)
            counter[kind] += 1
            if any(b in kind for b in BAD):
                bad_hits.append((addr + off, secname, name, w, kind))
            if kind.startswith('UNKNOWN'):
                custom_hits.append((addr + off, secname, name, w, kind))
            off += 4

    if verbose:
        print('  解码指令数: %d' % total)
        print('  指令类别统计:')
        for k, v in counter.most_common():
            mark = '   <== 不兼容！' if any(b in k for b in BAD) else ''
            print('     %-26s %6d%s' % (k, v, mark))
        print()

        if bad_hits:
            print('  !! CH572 不支持的指令 %d 条：' % len(bad_hits))
            for a, sec, fn, w, kind in bad_hits[:15]:
                print('     0x%08X [%s] %s  0x%08X  %s' % (a, sec, fn, w, kind))
        else:
            print('  [通过] 无 A 扩展原子指令、无浮点指令。')

        if custom_hits:
            print('  !! 无法识别的 opcode %d 条（需人工确认）：' % len(custom_hits))
            agg = collections.Counter(k for _, _, _, _, k in custom_hits)
            for k, v in agg.most_common(10):
                print('     %-26s %6d 条' % (k, v))
            for a, sec, fn, w, kind in custom_hits[:8]:
                print('       例: 0x%08X [%s] %s  0x%08X' % (a, sec, fn, w))
        else:
            print('  [通过] 无标准 RISC-V 之外的 opcode。')

    return (not bad_hits) and (not custom_hits)


if __name__ == '__main__':
    ok = True
    for p in sys.argv[1:]:
        if not scan(p):
            ok = False
    print()
    print('结论:', '[通过] 产物指令集与 CH572 兼容' if ok else '[需人工确认] 存在可疑指令')
    sys.exit(0 if ok else 2)
