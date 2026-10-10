# -*- coding: utf-8 -*-
"""compare_flash_funcs.py —— 对比两种构建方式里 Flash 操作函数的反汇编。

背景：实机自检确认
    * .highcode 搬运正确
    * FLASH_CMD_GET_ROM_INFO 在 RAM (0x200006F2) 正确
    * 但调用后卡死在 FLASH_ROM_IN 的忙等待循环
      （轮询 R8_FLASH_CTRL 0x40001806 的 bit7，一直不清）

既然代码在 RAM、地址对、搬运对，那差异只能在**代码本身的链接结果**上。
静态库方式是上板验证过能跑的，所以直接逐条对比同一个函数。
"""
import re, subprocess, sys

sys.stdout.reconfigure(encoding='utf-8')

OBJDUMP = (r'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
           r'\components\WCH\Toolchain\RISC-V Embedded GCC12\bin'
           r'\riscv-wch-elf-objdump.exe')

ELFS = [
    ('ROM 方式(卡死)', 'build/out/ble_uart-rom.elf'),
    ('静态库方式(能跑)', 'build/out/ble_uart.elf'),
]


def load(elf):
    out = subprocess.run([OBJDUMP, '-d', elf], capture_output=True,
                         text=True, errors='replace').stdout
    return out.split('\n')


def func(lines, name):
    s = None
    for i, l in enumerate(lines):
        if re.match(r'^[0-9a-f]+ <%s>:' % re.escape(name), l.strip()):
            s = i
            break
    if s is None:
        return None
    e = s + 1
    while e < len(lines) and not re.match(r'^[0-9a-f]{8} <', lines[e]):
        e += 1
    return lines[s:e]


FUNCS = ['FLASH_START', 'FLASH_ROM_ADDR', 'FLASH_ROM_BEG', 'FLASH_ROM_IN']

data = {}
for label, elf in ELFS:
    try:
        data[label] = load(elf)
    except Exception as ex:
        print('读取 %s 失败: %s' % (elf, ex))

# 先看 0x1f000 那里到底是什么
print('=' * 78)
print('0x1F000 处的代码是什么？（两个反汇编里的 "jr -> 1f000" 都指向它）')
print('=' * 78)
for label, lines in data.items():
    for i, l in enumerate(lines):
        if re.match(r'^\s*1f000:', l):
            print('  [%s]' % label)
            for x in lines[i:i + 5]:
                print('    ' + x.rstrip())
            break
    else:
        print('  [%s] 0x1F000 处没有代码' % label)
print()

for fn in FUNCS:
    print('=' * 78)
    print('%s' % fn)
    print('=' * 78)
    for label, lines in data.items():
        b = func(lines, fn)
        print('  --- %s ---' % label)
        if not b:
            print('    (未找到)')
            continue
        for l in b:
            if l.strip():
                # 只留助记符部分，便于对比
                print('    ' + l.rstrip())
    print()
