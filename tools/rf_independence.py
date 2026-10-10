# -*- coding: utf-8 -*-
"""判定 libCH57xRF.a 能否脱离 libCH572BLE_PERI.a 独立使用。

核心问题：RF 库缺的那 4 个全局变量（gptrBBReg / gptrLLEReg / gptrRFENDReg /
gStatus）以及 gTuneFilter* 到底由谁提供 —— 是 RF 库自己有，还是必须来自 BLE 库？
"""
import io
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'C:\Users\DarrenCheng\Desktop\CH570StaicBle'
BIN = (r'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
       r'\components\WCH\Toolchain\RISC-V Embedded GCC12\bin')
NM = os.path.join(BIN, 'riscv-wch-elf-nm.exe')
AR = os.path.join(BIN, 'riscv-wch-elf-ar.exe')

RF_LIB = os.path.join(ROOT, 'CH572EVT-1.4', 'EVT', 'EXAM', 'RF', 'LIB', 'libCH57xRF.a')
BLE_LIB = os.path.join(ROOT, 'CH572_BLE_LIB', 'lib', 'libCH572BLE_PERI.a')

NEEDED = ['gptrBBReg', 'gptrLLEReg', 'gptrRFENDReg', 'gStatus',
          'gTuneFilter', 'gTuneFilter2M']


def symbols(lib, mode):
    out = subprocess.run([NM, mode, lib], capture_output=True, text=True,
                         errors='replace').stdout
    defined, undef = {}, set()
    members = []
    for line in out.split('\n'):
        line = line.strip()
        m = re.match(r'^([0-9a-fA-F]{8})\s+(\S)\s+(\S+)$', line)
        if m:
            defined[m.group(3)] = (m.group(1), m.group(2))
            continue
        m = re.match(r'^U\s+(\S+)$', line)
        if m:
            undef.add(m.group(1))
            continue
        if line.endswith('.o:'):
            members.append(line[:-1])
    return defined, undef, members


def main():
    rf_def, rf_undef, rf_members = symbols(RF_LIB, '--defined-only')
    ble_def, ble_undef, ble_members = symbols(BLE_LIB, '--defined-only')

    # RF 库"全部"符号（含未定义）需要再跑一次不带 --defined-only
    allout = subprocess.run([NM, RF_LIB], capture_output=True, text=True,
                            errors='replace').stdout

    out = []
    W = out.append
    W('# libCH57xRF.a 独立性判定')
    W('')
    W('| 库 | 对象数 | 已定义符号 |')
    W('|---|---:|---:|')
    W('| `libCH57xRF.a`（纯 RF） | %d | %d |' % (len(rf_members), len(rf_def)))
    W('| `libCH572BLE_PERI.a`（BLE 协议栈） | %d | %d |' % (len(ble_members), len(ble_def)))
    W('')
    W('对象清单（RF 库）：%s' % ', '.join(rf_members))
    W('')

    W('## 1. RF 库引用的关键全局变量的归属')
    W('')
    W('| 符号 | RF 库里已定义? | BLE 库里已定义? | 结论 |')
    W('|---|---|---|---|')
    for s in NEEDED:
        in_rf = s in rf_def
        in_ble = s in ble_def
        if in_rf:
            concl = 'RF 库自带 ✓ 可独立'
        elif in_ble:
            concl = '★ 只在 BLE 库里 → RF 库依赖 BLE 库'
        else:
            concl = '两边都没有 → 需应用提供'
        W('| `%s` | %s | %s | %s |' % (
            s, '✓' if in_rf else '—', '✓' if in_ble else '—', concl))
    W('')

    # 真正 unresolved：既不在 RF 库、也不在 BLE 库
    rf_all_defined, rf_all_undef, _ = symbols(RF_LIB, '')
    still = sorted(u for u in rf_all_undef
                   if u not in rf_all_defined and u not in ble_def)
    W('## 2. 两个库都提供不了的符号（必须应用/HAL 补）')
    W('')
    if still:
        for s in still:
            W('- `%s`' % s)
    else:
        W('无 —— 只需要 `libCH57xRF.a`（若上面第 1 节没有 ★ 行）')
    W('')

    W('## 3. RF 库导出的全部 API（应用可调用）')
    W('')
    W('| 符号 | 类型 |')
    W('|---|---|')
    for n in sorted(k for k, v in rf_def.items() if v[1] in 'Tt'):
        W('| `%s` | 函数 |' % n)
    W('')
    for n in sorted(k for k, v in rf_def.items() if v[1] in 'DdBbRr'):
        W('| `%s` | 数据(0x%s) |' % (n, rf_def[n][0]))
    W('')

    io.open(os.path.join(ROOT, 'CH572_BLE_LIB', 'build', 'rf_independence.md'), 'w',
            encoding='utf-8').write('\n'.join(out))
    print('RF 库定义 %d 个符号；两库都缺的符号 %d 个' % (len(rf_def), len(still)))
    for s in still:
        print('   缺: %s' % s)


if __name__ == '__main__':
    main()
