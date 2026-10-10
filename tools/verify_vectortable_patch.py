# -*- coding: utf-8 -*-
"""verify_vectortable_patch.py —— 确认"运行时装配向量表"的代码真的进了固件。

检查点：
  1. 固件里有 "向量表装配" 这个调试串（说明代码被编译进来）
  2. InstallRomIrqHandlers 附近有向 0x2000xxxx+0x50 / +0x54 写值的指令
  3. 打印出这两条 store 的目标地址，便于人工核对是不是向量表
"""
import os, re, subprocess, sys

sys.stdout.reconfigure(encoding='utf-8')

BIN = (r'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
       r'\components\WCH\Toolchain\RISC-V Embedded GCC12\bin')
OBJDUMP = BIN + r'\riscv-wch-elf-objdump.exe'
NM = BIN + r'\riscv-wch-elf-nm.exe'

ELF = 'build/out/ble_uart-rom.hex'
ELF = 'build/out/ble_uart-rom-debug.elf' if os.path.exists('build/out/ble_uart-rom-debug.elf') \
      else 'build/out/ble_uart-rom.elf'

print('检查对象: %s' % ELF)
print()

# ---- 1) 符号 ----
out = subprocess.run([NM, ELF], capture_output=True, text=True,
                     errors='replace').stdout
print('相关符号:')
for l in out.split('\n'):
    if '_vector_base' in l or 'IQLibHandlerLocation' in l:
        print('  ' + l.strip())
print()

# ---- 2) 找 InstallRomIrqHandlers 并看它做了什么 ----
dis = subprocess.run([OBJDUMP, '-d', ELF], capture_output=True, text=True,
                     errors='replace').stdout
lines = dis.split('\n')

s = None
for i, l in enumerate(lines):
    if re.match(r'^[0-9a-f]+ <InstallRomIrqHandlers>:', l.strip()):
        s = i
        break

if s is None:
    print('未找到 InstallRomIrqHandlers（可能被内联）')
    # 退而求其次：全局搜写入向量表的模式
    body = lines
else:
    e = s + 1
    while e < len(lines) and not re.match(r'^[0-9a-f]{8} <', lines[e]):
        e += 1
    body = lines[s:e]
    print('InstallRomIrqHandlers: %d 行' % (e - s))
    print()

print('函数体里的 store 指令:')
n = 0
for l in body:
    if re.search(r'\b(sw|sb)\b', l) and re.search(r'#\s*2000', l):
        print('  ' + l.strip())
        n += 1
print('  （共 %d 条）' % n)
print()

# ---- 3) 从固件里找调试串 ----
hexf = 'build/out/ble_uart-rom.hex'
if os.path.exists(hexf):
    sys.path.insert(0, 'tools')
    from hex_probe import load_hex
    m, _ = load_hex(hexf)
    blob = bytes(m.get(a, 0xFF) for a in range(min(m), max(m) + 1))
    print('固件里的关键串:')
    for pat in ['向量表装配', '向量表探测', '[BLEInit', 'ROM RAM']:
        p = pat.encode()
        print('  %-14s %s' % (pat, 'OK' if p in blob else '缺失'))
