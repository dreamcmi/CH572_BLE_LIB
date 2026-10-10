# -*- coding: utf-8 -*-
"""生成 CH572 BLE 协议栈 API 对照表（peripheral-only 视角）。

三个层次：
  1. 跳转表暴露的 API（164 项）→ 哪些可用、哪些本平台没有
  2. 示例应用实际调用了哪些
  3. 归档里有、但跳转表不暴露的内部符号（不可用）
"""
import io
import os
import re
import subprocess
import sys
from collections import OrderedDict

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH572_BLE_LIB'
JT_MD = os.path.join(ROOT, 'lib', 'CH572BLE_ROM_JT.md')
REF_HDR = r'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH592EVT-1.7\EVT\EXAM\BLE\LIB\CH59xBLE_ROM.h'
BIN = (r'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
       r'\components\WCH\Toolchain\RISC-V Embedded GCC12\bin')
NM = os.path.join(BIN, 'riscv-wch-elf-nm.exe')

# API 家族归类（按名字前缀）
FAMILIES = [
    ('TMOS 内核 / 内存工具', lambda n: n.startswith('tmos_') or n.startswith('TMOS_')),
    ('LL 链路层',            lambda n: n.startswith('LL_') or n.startswith('ll_')),
    ('BLE 库管理',           lambda n: n.startswith('BLE_') or n.startswith('CH572BLE_')),
    ('linkDB 连接数据库',    lambda n: n.startswith('linkDB_')),
    ('ATT 属性协议',         lambda n: n.startswith('ATT_')),
    ('GATT',                 lambda n: n.startswith('GATT_') or n.startswith('gatt')),
    ('GAP 通用访问',         lambda n: n.startswith('GAP_')),
    ('GAPRole 角色',         lambda n: n.startswith('GAPRole_')),
    ('GAPBondMgr 绑定',      lambda n: n.startswith('GAPBondMgr_')),
    ('GATTServApp 服务端',   lambda n: n.startswith('GATTServApp_')),
    ('SM 安全管理',          lambda n: n.startswith('SM_')),
    ('RF 射频',              lambda n: n.startswith('RF_')),
    ('Flash / ISP',          lambda n: 'FLASH' in n or 'EEPROM' in n),
]


def family(name):
    for label, pred in FAMILIES:
        if pred(name):
            return label
    return '其他'


def parse_jt_md(path):
    """返回 [(idx, name, 可用?, addr)]"""
    rows = []
    dbg = []
    for line in io.open(path, encoding='utf-8'):
        if not line.startswith('|'):
            continue
        # 注意：line.strip('|') 会留下一个空串（表格行首尾各有一个 |），
        # 所以 parts 会多出一格，必须先滤掉空串再判断列数。
        parts = [p.strip() for p in line.strip('|').split('|')]
        parts = [p for p in parts if p != '']
        dbg.append((len(parts), parts[:2]))
        if len(parts) != 4 or not parts[0].isdigit():
            continue
        idx = int(parts[0])
        raw = parts[1].strip('`')
        ok = parts[2].startswith('可用') or '版本' in parts[2]
        rows.append((idx, raw, ok, parts[3]))
    if '--debug' in sys.argv:
        print('  首 3 个以 | 开头的行: %r' % (dbg[:3],))
        print('  以 | 开头的行总数: %d' % len(dbg))
    return rows


def parse_ref_hdr_apis(path):
    """从 ABI 参考头文件取全部 API 名字（宽松匹配，允许全角括号）。"""
    txt = io.open(path, encoding='utf-8', errors='replace').read()
    names = {}
    for m in re.finditer(r'#define\s+(\w+)\s*\([^)]*\)?\s*[^\n]*?BLE_LIB_JT\((\d+)\)', txt):
        names[int(m.group(2))] = m.group(1)
    return names


def scan_app_calls(srcdirs, symbols):
    """扫描应用源码，统计每个 API 被调用的次数和位置。"""
    hits = OrderedDict()
    for d in srcdirs:
        for dirpath, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x != '__pycache__']
            for f in files:
                if not f.endswith(('.c', '.h')):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    txt = io.open(p, encoding='utf-8', errors='replace').read()
                except OSError:
                    continue
                rel = os.path.relpath(p, ROOT)
                for s in symbols:
                    # 只认"函数调用"形式：名字后紧跟 (
                    for m in re.finditer(r'(?<![\w.])' + re.escape(s) + r'\s*\(', txt):
                        ln = txt[:m.start()].count('\n') + 1
                        hits.setdefault(s, []).append('%s:%d' % (rel, ln))
    return hits


def main():
    jt = parse_jt_md(JT_MD)
    ref = parse_ref_hdr_apis(REF_HDR)

    if '--debug' in sys.argv:
        print('JT_MD       = %s' % JT_MD)
        print('rows parsed = %d' % len(jt))
        print('usable rows = %d' % len([r for r in jt if r[2]]))
        print('ref header  = %d apis' % len(ref))
        for r in jt[:6]:
            print('   %r' % (r,))
        return 0

    # 补齐"本平台无此 API"的名字：md 里有名字，直接用
    api_names = [r[1] for r in jt if r[1] and '预留' not in r[1]]

    # 应用源码目录
    appdirs = [os.path.join(ROOT, 'examples'), os.path.join(ROOT, 'hal'),
               os.path.join(ROOT, 'debug')]
    # 只关心跳转表暴露的 API 里应用真正调用的
    usable = [r[1] for r in jt if r[2] and r[1] not in ('VER_LIB',)]
    calls = scan_app_calls(appdirs, usable)

    out = []
    W = out.append

    # ---------- 表 A：按家族汇总 ----------
    fam_stat = OrderedDict()
    for idx, name, ok, addr in jt:
        if '预留' in name:
            continue
        f = family(name)
        a = fam_stat.setdefault(f, {'ok': 0, 'no': 0, 'used': 0, 'names_ok': [], 'names_no': []})
        if ok:
            a['ok'] += 1
            a['names_ok'].append(name)
            if name in calls:
                a['used'] += 1
        else:
            a['no'] += 1
            a['names_no'].append(name)

    W('# CH572 BLE 协议栈 API 对照表（peripheral-only 视角）')
    W('')
    W('数据来源：`lib/CH572BLE_ROM_JT.md`（164 项跳转表）+ 应用源码静态扫描 + `libCH572BLE_PERI.a` 归档。')
    W('')
    W('## A. 按功能家族汇总')
    W('')
    W('| 功能家族 | 可用 API | 本平台没有 | 示例已用 | 示例未用 |')
    W('|---|---:|---:|---:|---:|')
    tot_ok = tot_no = tot_used = 0
    for f, a in sorted(fam_stat.items(), key=lambda kv: -(kv[1]['ok'] + kv[1]['no'])):
        tot_ok += a['ok']
        tot_no += a['no']
        tot_used += a['used']
        W('| %s | %d | %d | %d | %d |' % (f, a['ok'], a['no'], a['used'], a['ok'] - a['used']))
    W('| **合计** | **%d** | **%d** | **%d** | **%d** |' % (
        tot_ok, tot_no, tot_used, tot_ok - tot_used))
    W('')

    # ---------- 表 B：可用 API 逐条 ----------
    W('## B. 可用 API 逐条（%d 个）' % tot_ok)
    W('')
    W('"示例已用" 指 ble_uart 示例（examples/ + hal/ + debug/）里真的调了。')
    W('')
    W('| JT | API | 家族 | 示例已用 | 调用位置 |')
    W('|---:|---|---|---|---|')
    for idx, name, ok, addr in jt:
        if not ok or name == 'VER_LIB':
            continue
        loc = calls.get(name)
        W('| %d | `%s` | %s | %s | %s |' % (
            idx, name, family(name),
            '✅' if loc else '',
            (', '.join(loc[:2]) + ('…' if len(loc) > 2 else '')) if loc else ''))
    W('')

    # ---------- 表 C：本平台没有的 API ----------
    W('## C. 本平台没有的 API（%d 个，跳转表槽为 0）' % tot_no)
    W('')
    W('这些名字在 `CH572BLE_ROM.h` 里**完全没有宏** → 误用是编译/链接期报错。')
    W('')
    W('| JT | API | 家族 |')
    W('|---:|---|---|')
    for idx, name, ok, addr in jt:
        if ok or '预留' in name:
            continue
        W('| %d | `%s` | %s |' % (idx, name, family(name)))
    W('')

    # ---------- 表 D：应用实际调用清单 ----------
    W('## D. 示例应用实际调用的协议栈 API（%d 个）' % len(calls))
    W('')
    W('| API | 家族 | 调用位置 |')
    W('|---|---|---|')
    for name, locs in sorted(calls.items()):
        W('| `%s` | %s | %s |' % (name, family(name), ', '.join(locs[:3])))
    W('')

    io.open(os.path.join(ROOT, 'build', 'api_table.md'), 'w', encoding='utf-8').write('\n'.join(out))
    print('可用 %d / 没有 %d / 示例已用 %d' % (tot_ok, tot_no, tot_used))
    print('written build/api_table.md')


if __name__ == '__main__':
    main()
