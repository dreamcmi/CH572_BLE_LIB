# -*- coding: utf-8 -*-
"""rom_jt_extract.py —— 从 WCH 的 CH59xBLE_ROM.h 里抽取「跳转表下标 -> API」映射。

WCH 的 ROM 头文件把每个 API 定义成：
    #define  函数名  ((返回类型 (*)(参数...))  BLE_LIB_JT(n))
以及固定数据结构：
    #define  名称    ((const uint8_t*)  BLE_LIB_UUID(n))
本脚本用正则把这些 n 抽出来，得到一个稳定的"跳转表 ABI 清单"，
用于给 CH572 生成同款头文件（保持下标一致，方便两平台代码互移植）。

用法：
    python tools\\rom_jt_extract.py <CH59xBLE_ROM.h> [输出json]
"""
import sys, re, json
sys.stdout.reconfigure(encoding='utf-8')

path = sys.argv[1]
raw = open(path, 'rb').read().decode('gbk', 'replace')

# 关键：先把续行折叠成单行，再去匹配。
# CH59xBLE_ROM.h 里不少函数宏的原型是跨行的（行尾用 '\' 续行），
# 不折叠的话参数串里会混进反斜杠和换行，生成的头文件直接语法错误。
flat = re.sub(r'\\\s*\n\s*', ' ', raw)

funcs = {}
uuids = {}

# 函数：#define  名字  (  (  返回类型  (*)  (参数)  )  BLE_LIB_JT(n)  )
re_func = re.compile(
    r'#define\s+([A-Za-z_]\w*)\s+\(\s*\(\s*([^()]*(?:\([^()]*\))?[^()]*?)\s*\(\s*\*\s*\)\s*\(([^)]*)\)\s*\)\s*BLE_LIB_JT\(\s*(\d+)\s*\)',
    re.S)
for m in re_func.finditer(flat):
    name, ret, args, idx = m.group(1), ' '.join(m.group(2).split()), ' '.join(m.group(3).split()), int(m.group(4))
    funcs[name] = dict(index=idx, ret=ret, args=args)

# UUID / 数据：#define 名字 ((const uint8_t*) BLE_LIB_UUID(n))
re_uuid = re.compile(
    r'#define\s+([A-Za-z_]\w*)\s+\(\s*\(\s*const\s+uint8_t\s*\*\s*\)\s*BLE_LIB_UUID\(\s*(\d+)\s*\)\s*\)')
for m in re_uuid.finditer(flat):
    uuids[m.group(1)] = int(m.group(2))

# 版本字符串
re_ver = re.search(r'#define\s+VER_LIB\s+\(\(\s*const\s+uint8_t\s*\*\s*\)\s*BLE_LIB_JT\(\s*(\d+)\s*\)\s*\)', flat)
ver_idx = int(re_ver.group(1)) if re_ver else None

# ---- RAM 跳转表项（BLE_LIB_RAM_JT） ----
# WCH 的 ROM 方案里，有 3 个入口不走 Flash 跳转表，而是放在 RAM 固定地址，
# 由 ROM 在初始化时把真实地址写进去：
#       *(uint32_t*)(0x20000000 + n*4)
#   n=0  TMOS_SystemProcess     （应用主循环调用）
#   n=1  BB_IRQLibHandler       （基带中断，装进应用的向量表）
#   n=2  LLE_IRQLibHandler      （链路层中断，装进应用的向量表）
# 本工程生成的 ROM 头文件需要单独处理这几项（见 rom_gen.py），
# 所以这里把它们抽出来告诉下游。
ram_funcs = {}
re_ram = re.compile(
    r'#define\s+([A-Za-z_]\w*)\s+\(\s*\(\s*([^()]*(?:\([^()]*\))?[^()]*?)\s*\(\s*\*\s*\)\s*\(([^)]*)\)\s*\)\s*BLE_LIB_RAM_JT\(\s*(\d+)\s*\)',
    re.S)
for m in re_ram.finditer(flat):
    ram_funcs[m.group(1)] = dict(index=int(m.group(4)),
                                 ret=' '.join(m.group(2).split()),
                                 args=' '.join(m.group(3).split()))
# 把 RAM 项从 Flash 跳转表里剔除，避免重复定义
for name in ram_funcs:
    funcs.pop(name, None)

print('=' * 90)
print('源文件:', path)
print('函数项: %d 个   数据项(UUID): %d 个   VER_LIB 下标: %s'
      % (len(funcs), len(uuids), ver_idx))
print('RAM 跳转表项: %d 个  %s' % (len(ram_funcs), ', '.join('%s(RAM_JT%d)' % (n, v['index']) for n, v in sorted(ram_funcs.items(), key=lambda kv: kv[1]['index']))))
print()

maxidx = max([v['index'] for v in funcs.values()] + list(uuids.values()) + ([ver_idx] if ver_idx is not None else []))
print('跳转表最大下标: %d' % maxidx)
print()

print('-' * 90)
print('跳转表清单（按下标排序）')
print('-' * 90)
by_idx = sorted(funcs.items(), key=lambda kv: kv[1]['index'])
for name, info in by_idx:
    print('  JT(%3d)  %-42s %s (%s)' % (info['index'], name, info['ret'], info['args'][:60]))

print()
print('-' * 90)
print('UUID 清单（按下标排序）')
print('-' * 90)
for name, idx in sorted(uuids.items(), key=lambda kv: kv[1]):
    print('  UUID(%2d)  %s' % (idx, name))

# 检查下标是否连续
used = sorted(set([v['index'] for v in funcs.values()] + list(uuids.values())))
gaps = [i for i in range(used[0], used[-1] + 1) if i not in used]
print()
print('下标是否连续: %s%s' % ('是' if not gaps else '否', '' if not gaps else ('，空缺: ' + str(gaps))))

if len(sys.argv) > 2:
    out = dict(ver_lib=ver_idx, functions=funcs, uuids=uuids, ram_functions=ram_funcs)
    json.dump(out, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print()
    print('已写出:', sys.argv[2])
