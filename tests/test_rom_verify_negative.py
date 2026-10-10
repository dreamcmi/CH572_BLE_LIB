# -*- coding: utf-8 -*-
"""验证 rom_verify.py 真的能抓到跳转表错位（负向测试）。

做法：把 ROM bin 复制一份，故意改坏一个槽位，再用同一个工具去验，
      要求它必须报"地址不匹配"。抓不到就说明这个校验是摆设。

为什么需要它：make_rom.ps1 现在会在生成镜像后调用 rom_verify.py。
如果 rom_verify.py 本身失灵（比如遍历范围写错、判定条件太宽），
构建照样"通过"，跳转表错位就会一路烧进芯片。这个测试就是防这个。

用法（在工程根目录）：
    python tests\\test_rom_verify_negative.py
退出码 0 = 四类错位都能抓到；1 = 有漏报。
"""
import io, os, re, shutil, struct, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from rom_verify import load_syms, classify_slots   # noqa: E402

BASE = 0x20000
SRC = os.path.join(ROOT, 'lib', 'CH572BLE_ROM.bin')
SYMS = os.path.join(ROOT, 'lib', 'CH572BLE_ROM_SYMS.txt')
JT = os.path.join(ROOT, 'build', 'rom', 'jt_map.json')
TMP = os.path.join(ROOT, 'build', 'rom_verify_negative.bin')

blob = bytearray(open(SRC, 'rb').read())
syms = load_syms(SYMS)

import json                                                   # noqa: E402
jt = json.load(open(JT, encoding='utf-8'))
idx2name = dict((v['index'], k) for k, v in jt['functions'].items())
ver_idx = jt.get('ver_lib', 0)
ver_str = syms.get('_rom_version_str')

print('=' * 74)
print('rom_verify.py 负向测试（故意改坏一个槽位）')
print('=' * 74)

ok = True

# --- 基线：原图必须全过 ---
cnt, mm = classify_slots(bytes(blob), BASE, 164, idx2name, syms, ver_idx, ver_str)
print('  基线           : 对上符号 %d 项，不匹配 %d 项  %s'
      % (cnt['ok'], len(mm), '[期望]' if not mm else '[异常]'))
ok &= (len(mm) == 0)

# --- 用例 1：把一个真实 API 槽改成别的地址 -> 必须报不匹配 ---
slot_idx = 28                       # JT(28) BLE_LibInit
off = 0x34 + slot_idx * 4
orig = struct.unpack_from('<I', blob, off)[0]
struct.pack_into('<I', blob, off, orig + 4)     # 故意偏 4 字节
cnt, mm = classify_slots(bytes(blob), BASE, 164, idx2name, syms, ver_idx, ver_str)
hit = any(i == slot_idx for i, _n, _g, _e in mm)
print('  用例1 地址偏移4 : 不匹配 %d 项，命中 JT(%d)=%s  %s'
      % (len(mm), slot_idx, hit, '[期望]' if hit else '[漏报!]'))
ok &= hit
struct.pack_into('<I', blob, off, orig)          # 还原

# --- 用例 2：符号存在但槽被清 0 -> 必须报"调用即跳 0" ---
struct.pack_into('<I', blob, off, 0)
cnt, mm = classify_slots(bytes(blob), BASE, 164, idx2name, syms, ver_idx, ver_str)
hit = any(i == slot_idx and '跳 0' in e for i, _n, _g, e in mm)
print('  用例2 槽清0     : 不匹配 %d 项，命中"跳 0"  %s'
      % (len(mm), '[期望]' if hit else '[漏报!]'))
ok &= hit
struct.pack_into('<I', blob, off, orig)

# --- 用例 3：槽指向镜像外的野地址 -> 必须报不匹配 ---
struct.pack_into('<I', blob, off, 0xDEADBEEF)
cnt, mm = classify_slots(bytes(blob), BASE, 164, idx2name, syms, ver_idx, ver_str)
hit = any(i == slot_idx for i, _n, _g, _e in mm)
print('  用例3 野地址    : 不匹配 %d 项，命中野地址  %s'
      % (len(mm), '[期望]' if hit else '[漏报!]'))
ok &= hit

# --- 用例 4：头文件把空槽暴露成宏 -> 必须报失败 ---
hdr = io.open(os.path.join(ROOT, 'include', 'CH572BLE_ROM.h'),
              encoding='utf-8', errors='replace').read()
bad_hdr = os.path.join(ROOT, 'build', 'rom_verify_negative.h')
io.open(bad_hdr, 'w', encoding='utf-8').write(
    hdr + '\n#define  GATT_ReadCharValue  (( bStatus_t (*)(void) ) BLE_LIB_JT(48))\n')
proc = subprocess.run([sys.executable,
                       os.path.join(ROOT, 'tools', 'rom_verify.py'),
                       '--bin', SRC, '--syms', SYMS, '--base', '0x20000',
                       '--jt-json', JT, '--hdr', bad_hdr],
                      capture_output=True, text=True, encoding='utf-8', errors='replace')
hit = ('[失败]' in proc.stdout) and ('空槽' in proc.stdout) and (proc.returncode != 0)
print('  用例4 空槽暴露  : 退出码 %d，报"空槽"  %s'
      % (proc.returncode, '[期望]' if hit else '[漏报!]'))
if not hit:
    print('        debug: rc=%r  失败字样=%r  空槽字样=%r'
          % (proc.returncode, '[失败]' in proc.stdout, '空槽' in proc.stdout))
    print('        debug stdout tail: %r' % proc.stdout[-200:])
ok = bool(ok) and bool(hit)

if os.path.exists(bad_hdr):
    os.remove(bad_hdr)

print()
print('结论: %s' % ('[通过] rom_verify.py 能抓到这四类错位' if ok else '[失败] 有漏报，校验不可信'))
sys.exit(0 if ok else 1)
