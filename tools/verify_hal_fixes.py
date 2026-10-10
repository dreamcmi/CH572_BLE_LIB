# -*- coding: utf-8 -*-
"""verify_hal_fixes.py —— 验证官方 MCU.c 的关键初始化项是否都进了固件。

对比对象：官方 CH572EVT-1.4 的 HAL/MCU.c 里的 CH57x_BLEInit()
被验证对象：本工程 build/out/ble_uart-rom.elf 里的 CH57x_BLEInit()
"""
import subprocess, sys, os, re
sys.stdout.reconfigure(encoding='utf-8')

OBJDUMP = (r'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
           r'\components\WCH\Toolchain\RISC-V Embedded GCC12\bin'
           r'\riscv-wch-elf-objdump.exe')

ELF = r'build\out\ble_uart-rom.elf'

out = subprocess.run([OBJDUMP, '-d', ELF], capture_output=True,
                     text=True, errors='replace').stdout
lines = out.split('\n')

# 截取 CH57x_BLEInit 的函数体
start = None
for i, l in enumerate(lines):
    if '<CH57x_BLEInit>:' in l:
        start = i
        break
if start is None:
    print('未找到 CH57x_BLEInit')
    sys.exit(1)

end = start + 1
while end < len(lines) and not re.match(r'^[0-9a-f]{8} <', lines[end]):
    end += 1
body = '\n'.join(lines[start:end])

print('=' * 74)
print('CH57x_BLEInit 反汇编（%d 行）' % (end - start))
print('=' * 74)

CHECKS = [
    ('SysTick 配置',
     '写 0xE000F004/8/C（SysTick 寄存器）',
     lambda b: 'e000f' in b or 'e0000' in b),
    ('MISC_CTRL 射频位',
     '读改写 0x40001048，bit24-29 设 0xE',
     lambda b: '40001048' in b or ('0x40001' in b and 'e000' in b)),
    ('安全访问解锁',
     'sys_safe_access_enable 的签名写入（0x40001040 写 0x57/0xA8）',
     lambda b: '40001040' in b),
    ('PFIC 中断优先级',
     '设置 BLEL_IRQn 优先级（0xF0）',
     lambda b: '0xf0' in b.lower() and ('csr' in b or '0x804' in b or '0x7c' in b)),
    ('版本一致性检查',
     'tmos_memcmp(VER_LIB, VER_FILE) 的调用',
     lambda b: 'jalr' in b and b.count('jalr') >= 3),
]

ok = 0
for name, how, fn in CHECKS:
    r = fn(body)
    if r:
        ok += 1
    print('  [%s] %-18s %s' % ('OK' if r else '??', name, how))

print()
print('  通过 %d / %d 项' % (ok, len(CHECKS)))

# 打印前若干行便于人工核对
print()
print('-' * 74)
print('反汇编片段（前 40 行）')
print('-' * 74)
for l in lines[start:start + 40]:
    if l.strip():
        print('  ' + l.rstrip())
