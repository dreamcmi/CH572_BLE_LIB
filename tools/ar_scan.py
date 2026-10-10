# -*- coding: utf-8 -*-
"""ar_scan.py —— 解析 GNU ar 静态库(.a)，输出成员列表与符号表概要（支持 ELF32/ELF64，RISC-V）。

用于对比 CH592 的 libCH59xBLE.a 与 CH572 的 libCH572BLE_PERI.a 的组成与规模。
"""
import sys, struct, os, io

sys.stdout.reconfigure(encoding='utf-8')


def parse_elf_syms(data):
    """解析 .symtab，返回 (defined, undef)。defined 元素为 (名, 值, 大小, 绑定, 段名)。"""
    if data[:4] != b'\x7fELF':
        return None, None
    is64 = data[4] == 2
    if is64:
        e_shoff = struct.unpack_from('<Q', data, 0x28)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', data, 0x3A)
        sh_fmt, sh_size = '<IIQQQQIIQQ', 64
    else:
        e_shoff = struct.unpack_from('<I', data, 0x20)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', data, 0x2E)
        sh_fmt, sh_size = '<IIIIIIIIII', 40

    secs = []
    for i in range(e_shnum):
        o = e_shoff + i * e_shentsize
        v = struct.unpack_from(sh_fmt, data, o)
        secs.append(dict(name=v[0], type=v[1], off=v[4], size=v[5], link=v[6]))

    strtab_off = secs[e_shstrndx]['off'] if e_shstrndx < len(secs) else 0

    def secname(idx):
        if idx >= len(secs):
            return ''
        s = secs[idx]['name']
        end = data.index(b'\0', strtab_off + s)
        return data[strtab_off + s:end].decode('utf-8', 'replace')

    defined, undef = [], set()
    sym_size = 24 if is64 else 16
    for sec in secs:
        if sec['type'] != 2:            # SHT_SYMTAB
            continue
        st = secs[sec['link']]['off']
        for i in range(sec['size'] // sym_size):
            o = sec['off'] + i * sym_size
            if is64:
                st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from('<IBBHQQ', data, o)
            else:
                st_name, st_value, st_size, st_info, st_other, st_shndx = struct.unpack_from('<IIIBBH', data, o)
            if st_name == 0:
                continue
            end = data.index(b'\0', st + st_name)
            nm = data[st + st_name:end].decode('utf-8', 'replace')
            if st_shndx == 0:
                undef.add(nm)
            else:
                defined.append((nm, st_value, st_size, st_info & 0xF, secname(st_shndx)))
    return defined, undef


def parse_ar(path):
    """解析 ar 归档，返回 [(名, 偏移, 大小, 内容)]，支持 GNU 长文件名表 '//'。"""
    with open(path, 'rb') as f:
        blob = f.read()
    if blob[:8] != b'!<arch>\n':
        raise ValueError('不是 ar 归档: ' + path)
    pos, longnames, members = 8, b'', []
    while pos + 60 <= len(blob):
        hdr = blob[pos:pos + 60]
        name = hdr[0:16].decode('latin-1').rstrip()
        try:
            size = int(hdr[48:58].decode('latin-1').strip())
        except ValueError:
            break
        body_off = pos + 60
        body = blob[body_off:body_off + size]
        if name == '//':
            longnames = body
        elif name not in ('/', '/SYM64/', '__.SYMDEF'):
            real = name
            if name.startswith('/') and name[1:].isdigit():
                off = int(name[1:])
                end = longnames.find(b'/\n', off)
                if end < 0:
                    end = longnames.find(b'\0', off)
                real = longnames[off:end].decode('utf-8', 'replace').rstrip('/')
            elif name.endswith('/'):
                real = name[:-1]
            members.append((real, body_off, size, body))
        pos = body_off + size + (size & 1)
    return members


def collect(path):
    """收集归档中全部已定义/未定义符号。"""
    members = parse_ar(path)
    defined, undef, per_member = [], set(), []
    for nm, off, size, body in members:
        d, u = parse_elf_syms(body)
        if d is None:
            per_member.append((nm, size, None, None))
            continue
        per_member.append((nm, size, d, u))
        defined.extend(d)
        undef |= u
    return members, defined, undef, per_member


def report(path, top=60):
    members, defined, undef, per_member = collect(path)
    print('=' * 100)
    print('归档: %s   大小: %d 字节   成员数: %d' % (path, os.path.getsize(path), len(members)))
    print()
    print('%-40s %10s %9s %9s %6s %10s' % ('成员(.o)', '文件大小', '定义符号', '未定义', '有代码', '.text大小'))
    print('-' * 100)
    for nm, size, d, u in per_member:
        if d is None:
            print('%-40s %10d %9s %9s %6s %10s' % (nm[:40], size, '-', '-', '-', '-'))
            continue
        text = [s for s in d if s[4] == '.text' and s[3] == 2]
        print('%-40s %10d %9d %9d %6s %10d' % (nm[:40], size, len(d), len(u),
                                               'Y' if text else 'n', sum(s[2] for s in text)))
    print()
    text = [s for s in defined if s[4] == '.text']
    bss = [s for s in defined if s[4] == '.bss']
    data = [s for s in defined if s[4] == '.data']
    print('合计: 定义符号 %d, 未定义符号 %d, .text %d 字节, .data %d 字节, .bss %d 字节'
          % (len(defined), len(undef), sum(s[2] for s in text), sum(s[2] for s in data), sum(s[2] for s in bss)))
    print()
    print('--- .text 中最大的 %d 个符号 ---' % top)
    for s in sorted(text, key=lambda x: -x[2])[:top]:
        print('   %-58s %7d  (绑定=%d)' % (s[0][:58], s[2], s[3]))
    print()
    return members, defined, undef


if __name__ == '__main__':
    for p in sys.argv[1:]:
        report(p)
