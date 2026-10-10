# -*- coding: utf-8 -*-
"""交叉验证：43 个"本平台无此 API"到底是
   ① 库归档里真的没有这些符号（WCH 裁掉了代码），还是
   ② 符号在归档里、只是跳转表不给槽位（代码在但不可调用）。
同时统计"可用但示例未用"的 API 里，有多少符号其实已经在镜像里。
"""
import io
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH572_BLE_LIB'
BIN = (r'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
       r'\components\WCH\Toolchain\RISC-V Embedded GCC12\bin')
NM = os.path.join(BIN, 'riscv-wch-elf-nm.exe')
LIB = os.path.join(ROOT, 'lib', 'libCH572BLE_PERI.a')


def archive_symbols():
    out = subprocess.run([NM, '--defined-only', LIB],
                         capture_output=True, text=True, errors='replace').stdout
    syms = set()
    for line in out.split('\n'):
        m = re.match(r'^[0-9a-fA-F]{8}\s+\S\s+(\S+)$', line.strip())
        if m:
            syms.add(m.group(1))
    return syms


def jt_rows():
    rows = []
    for line in io.open(os.path.join(ROOT, 'lib', 'CH572BLE_ROM_JT.md'), encoding='utf-8'):
        if not line.startswith('|'):
            continue
        parts = [p.strip() for p in line.strip('|').split('|')]
        parts = [p for p in parts if p != '']
        if len(parts) != 4 or not parts[0].isdigit():
            continue
        rows.append((int(parts[0]), parts[1].strip('`'), parts[2], parts[3]))
    return rows


def main():
    syms = archive_symbols()
    rows = jt_rows()

    missing, present_only_hidden = [], []
    for idx, name, status, addr in rows:
        if status.startswith('可用') or '版本' in status or '预留' in name:
            continue
        if name in syms:
            present_only_hidden.append((idx, name))
        else:
            missing.append((idx, name))

    out = []
    W = out.append
    W('# "本平台无此 API" 的真相核查')
    W('')
    W('归档 `libCH572BLE_PERI.a` 共 %d 个已定义符号。' % len(syms))
    W('跳转表里标记为"本平台无此 API"的项：%d 个。' % (len(missing) + len(present_only_hidden)))
    W('')
    W('| 情况 | 数量 | 含义 |')
    W('|---|---:|---|')
    W('| ① 归档里**根本没有**该符号 | %d | WCH 确实没把代码给 CH572 |' % len(missing))
    W('| ② 归档里有符号，只是跳转表不给槽 | %d | 代码在库里，但 API 被封锁 |' % len(present_only_hidden))
    W('')
    if present_only_hidden:
        W('### ② 类明细（代码在、API 被封）')
        W('')
        for idx, name in present_only_hidden:
            W('- JT(%d) `%s`' % (idx, name))
        W('')
    if missing:
        W('### ① 类明细（库里确实没有）')
        W('')
        for idx, name in missing:
            W('- JT(%d) `%s`' % (idx, name))
        W('')
    io.open(os.path.join(ROOT, 'build', 'missing_api_check.md'), 'w',
            encoding='utf-8').write('\n'.join(out))
    print('① 库里没有: %d    ② 库里有但封了 API: %d' % (len(missing), len(present_only_hidden)))


if __name__ == '__main__':
    main()
