# -*- coding: utf-8 -*-
"""verify_diagnostics.py —— 确认诊断功能都编进了固件。

检查：
  1. 心跳字符串（[心跳 N]）
  2. HardFault 现场打印字符串
  3. HardFault_Handler 是强符号（覆盖启动文件的弱定义）
  4. BLE 中断计数包装 Debug_BbIrqHook 在
"""
import os, re, subprocess, sys

sys.stdout.reconfigure(encoding='utf-8')

BIN = (r'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
       r'\components\WCH\Toolchain\RISC-V Embedded GCC12\bin')
NM = BIN + r'\riscv-wch-elf-nm.exe'

sys.path.insert(0, 'tools')
from hex_probe import load_hex

CASES = [
    ('ROM 方式', 'build/out/ble_uart-rom-debug.elf', 'build/out/ble_uart-rom-debug.hex'),
    ('静态库', 'build/out/ble_uart-static-debug.elf', 'build/out/ble_uart-static-debug.hex'),
]

STRINGS = [
    ('心跳', '[心跳'),
    ('HardFault 标题', 'HardFault'),
    ('mcause', 'mcause'),
    ('向量表检查', '向量表['),
]

for label, elf, hx in CASES:
    print('=' * 72)
    print(label)
    print('=' * 72)

    if os.path.exists(hx):
        m, _ = load_hex(hx)
        blob = bytes(m.get(a, 0xFF) for a in range(min(m), max(m) + 1))
        for name, pat in STRINGS:
            p = pat.encode()
            print('  %-16s %s' % (name, 'OK' if p in blob else '缺失'))
    else:
        print('  (hex 不存在)')

    if os.path.exists(elf):
        out = subprocess.run([NM, elf], capture_output=True, text=True,
                             errors='replace').stdout
        print('  符号:')
        for pat in ('HardFault_Handler', 'Debug_PrintSnapshot',
                    'Debug_BbIrqHook', 'g_dbgBleIrqCount', 'g_dbgHeartbeat'):
            hit = None
            for l in out.split('\n'):
                if re.search(r'\b%s\b' % pat, l):
                    hit = l.strip()
                    break
            print('    %-22s %s' % (pat, hit if hit else '缺失'))
    print()
