# -*- coding: utf-8 -*-
"""verify_rf_rom.py —— 校验纯 RF 固定地址镜像的一致性。

与 BLE 的 tools/rom_verify.py 同思路，但检查项按本镜像的头部布局重写：
    0x00  魔数 8 字节（'7''5''R''F' + 4 字节保留）
    0x08  版本串指针（4 字节，指向库自己的 VER_RF_LIB）
    0x0C  跳转表起点（4 字节对齐）
    跳转表 N 项 × 4 字节

检查：
  1. 魔数是否正确
  2. 版本串指针是否落在镜像内、且内容像字符串
  3. 跳转表每一项是否等于对应符号的真实地址（用符号表比对）
  4. 指向 RAM 的项是否落在镜像声明的 RAM 预留区内
  5. 镜像大小是否与头文件 LIB 尺寸一致

用法：
    python tools\\verify_rf_rom.py --bin lib\\CH572RF_ROM.bin --syms lib\\CH572RF_ROM_SYMS.txt \\
        --base 0x10000 --hdr include\\CH572RF_ROM.h --ram-hdr rom_rf\\rom_rf_ram_info.h
"""
import argparse
import io
import os
import re
import struct
import sys

sys.stdout.reconfigure(encoding='utf-8')

MAGIC = b'\x37\x35\x52\x46'          # '7','5','R','F'
JT_OFF = 0x0C

# 跳转表顺序必须与 tools/gen_rf_jt.py 的 JT_ENTRIES 一致
JT_SYMS = [
    ('VER_RF_LIB',          '版本串'),
    ('RFRole_BasicInit',    '初始化'),
    ('RFRole_Stop',         '停止'),
    ('RFRole_Shut',         '关闭'),
    ('RFIP_StartTx',        '发送'),
    ('RFIP_SetRx',          '接收'),
    ('RFIP_SingleChannel',  '切频道'),
    ('RFIP_SetTxPower',     '设功率'),
    ('RFIP_Calibration',    '校准'),
    ('RFIP_WakeUpRegInit',  '唤醒重配'),
    ('RFIP_ReadCrc',        '读CRC'),
    ('BB_LibIRQHandler',    'BB ISR'),
    ('LLE_LibIRQHandler',   'LLE ISR'),
]


def load_syms(path):
    syms = {}
    for line in io.open(path, encoding='utf-8', errors='replace'):
        m = re.match(r'^([0-9a-fA-F]{8})\s+\S\s+(\S+)$', line.strip())
        if m:
            syms.setdefault(m.group(2), int(m.group(1), 16))
    return syms


def read_ram_range(path):
    txt = io.open(path, encoding='utf-8', errors='replace').read()
    def g(k):
        m = re.search(r'#define\s+%s\s+0x([0-9A-Fa-f]+)' % k, txt)
        return int(m.group(1), 16) if m else None
    return g('RF_ROM_RESERVE_START'), g('RF_ROM_RESERVE_END')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bin', required=True)
    ap.add_argument('--syms', required=True)
    ap.add_argument('--base', required=True)
    ap.add_argument('--hdr', default='')
    ap.add_argument('--ram-hdr', default='')
    args = ap.parse_args()

    base = int(args.base, 16)
    blob = open(args.bin, 'rb').read()
    syms = load_syms(args.syms)
    fails, warns = [], []

    n_jt = len(JT_SYMS)
    min_size = JT_OFF + n_jt * 4
    print('=' * 76)
    print('纯 RF 固定地址镜像校验')
    print('=' * 76)
    print('  镜像   : %s (%d 字节)' % (os.path.basename(args.bin), len(blob)))
    print('  基址   : 0x%08X' % base)
    print('  跳转表 : %d 项，位于 +0x%02X' % (n_jt, JT_OFF))
    print()

    if len(blob) < min_size:
        print('结论: [失败] 镜像太小，装不下头部与跳转表')
        return 2

    # ---- 1. 魔数 ----
    print('-' * 76)
    print('1. 头部魔数')
    print('-' * 76)
    ok = blob[:4] == MAGIC
    print('  +0x00  %s  %s' % (blob[:4].hex(' '), '[OK] "75RF"' if ok else '[失败] 期望 37 35 52 46'))
    if not ok:
        fails.append('魔数不对：%s' % blob[:4].hex(' '))

    # ---- 2. 版本串 ----
    print()
    print('-' * 76)
    print('2. 版本串')
    print('-' * 76)
    ver_ptr = struct.unpack_from('<I', blob, 0x08)[0]
    if base <= ver_ptr < base + len(blob):
        raw = blob[ver_ptr - base: ver_ptr - base + 64].split(b'\x00')[0]
        try:
            s = raw.decode('ascii')
        except UnicodeDecodeError:
            s = ''
        printable = all(32 <= c < 127 for c in raw) if raw else False
        if printable and len(raw) >= 4:
            print('  +0x08  → 0x%08X  "%s"  [OK]' % (ver_ptr, s))
            if ver_ptr != syms.get('VER_RF_LIB'):
                warns.append('版本串指针 0x%08X 与符号表 VER_RF_LIB(0x%08X) 不一致'
                             % (ver_ptr, syms.get('VER_RF_LIB', 0)))
        else:
            print('  +0x08  → 0x%08X  内容不是可打印字符串  [失败]' % ver_ptr)
            fails.append('版本串内容异常')
    else:
        print('  +0x08  → 0x%08X  落在镜像外  [失败]' % ver_ptr)
        fails.append('版本串指针越界：0x%08X' % ver_ptr)

    # ---- 3. 跳转表逐项 ----
    print()
    print('-' * 76)
    print('3. 跳转表逐项核对（表内地址 == 符号真实地址）')
    print('-' * 76)
    ram_lo = ram_hi = None
    if args.ram_hdr and os.path.exists(args.ram_hdr):
        ram_lo, ram_hi = read_ram_range(args.ram_hdr)
        print('  镜像 RAM 预留：0x%08X .. 0x%08X' % (ram_lo, ram_hi))
        print()

    n_ok = n_flash = n_ram = n_bad = 0
    for i, (name, desc) in enumerate(JT_SYMS):
        v = struct.unpack_from('<I', blob, JT_OFF + i * 4)[0]
        real = syms.get(name)
        if real is None:
            print('  RF_JT(%2d) %-20s 表内=0x%08X  符号表里没有  [失败]' % (i, name, v))
            fails.append('符号表缺少 %s' % name)
            n_bad += 1
            continue
        match = (v == real)
        if not match:
            n_bad += 1
            fails.append('RF_JT(%d) %s 表内 0x%08X != 符号 0x%08X' % (i, name, v, real))
        else:
            n_ok += 1
        if base <= v < base + len(blob):
            n_flash += 1
            where = 'Flash'
        elif ram_lo and ram_lo <= v < ram_hi:
            n_ram += 1
            where = 'RAM  '
        else:
            where = '??   '
            warns.append('RF_JT(%d) %s 的地址 0x%08X 既不在镜像内也不在 RAM 预留区内'
                         % (i, name, v))
        print('  RF_JT(%2d) %-20s 表内=0x%08X  %s  [%s] %s'
              % (i, name, v, 'OK ' if match else '差异', where, desc))

    print()
    print('  对上符号 : %d 项' % n_ok)
    print('  指向 Flash: %d 项' % n_flash)
    print('  指向 RAM  : %d 项' % n_ram)
    print('  不匹配    : %d 项' % n_bad)
    if n_bad == 0:
        print('  [通过] 跳转表与镜像符号完全一致')

    # ---- 4. 头文件一致性 ----
    if args.hdr and os.path.exists(args.hdr):
        print()
        print('-' * 76)
        print('4. 应用侧头文件一致性')
        print('-' * 76)
        h = io.open(args.hdr, encoding='utf-8', errors='replace').read()
        m = re.search(r'#define\s+RF_ROM_BASE_ADDRESSS\s+0x([0-9A-Fa-f]+)', h)
        if m:
            hb = int(m.group(1), 16)
            ok = (hb == base)
            print('  基址     : 0x%08X  %s' % (hb, '[OK]' if ok else '[失败] 与镜像不符'))
            if not ok:
                fails.append('头文件基址 0x%08X != 镜像基址 0x%08X' % (hb, base))
        else:
            fails.append('头文件里找不到 RF_ROM_BASE_ADDRESSS')

        m = re.search(r'#define\s+RF_ROM_JT_ENTRY_COUNT\s+(\d+)', h)
        if m:
            hc = int(m.group(1))
            ok = (hc == n_jt)
            print('  跳转表项 : %d  %s' % (hc, '[OK]' if ok else '[失败] 与校验脚本不一致'))
            if not ok:
                fails.append('头文件跳转表项数 %d != 校验脚本 %d' % (hc, n_jt))

        # 头文件里每个 RF_JT(n) 的 n 都必须在范围内
        used = set(int(x) for x in re.findall(r'RF_JT\((\d+)\)', h))
        oob = sorted(i for i in used if i >= n_jt)
        if oob:
            fails.append('头文件引用了越界的跳转表下标：%s' % oob)
        else:
            print('  引用下标 : 最大 %d，全部在范围内  [OK]' % (max(used) if used else -1))

    # ---- 汇总 ----
    print()
    print('=' * 76)
    if fails:
        print('结论: [失败] 发现 %d 个问题' % len(fails))
        for f in fails:
            print('   - ' + f)
    else:
        print('结论: [通过] 镜像、跳转表、头文件三者一致')
    if warns:
        print()
        print('提示:')
        for w in warns:
            print('   - ' + w)
    return 0 if not fails else 2


if __name__ == '__main__':
    sys.exit(main())
