# -*- coding: utf-8 -*-
"""调查 CH572 的 RF 收发能力：能不能脱离 BLE 协议栈单独用。

要回答：
  1. 跳转表里有哪些 RF_* / 相关 API 可用
  2. 厂商头文件里 RF_* 一共声明了多少（对比差集）
  3. 拍发/接收需要的最小初始化链（从 RF_PHY 例程看）
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH572_BLE_LIB'
SDK = r'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH572EVT-1.4'
JT_MD = os.path.join(ROOT, 'lib', 'CH572BLE_ROM_JT.md')
VENDOR_HDR = os.path.join(ROOT, 'include', 'CH572BLEPeri_LIB.h')


def jt_rows():
    rows = []
    for line in io.open(JT_MD, encoding='utf-8'):
        if not line.startswith('|'):
            continue
        c = [x.strip() for x in line.strip('|').split('|')]
        c = [x for x in c if x != '']
        if len(c) != 4 or not c[0].isdigit():
            continue
        rows.append((int(c[0]), c[1].strip('`'), c[2], c[3]))
    return rows


KEY = re.compile(r'(^RF|_RF|Cfo|CFO|RSSI|Frequency|Channel|Tune|PHY|Sensitivity|TxPower|TxPwr)', re.I)


def main():
    rows = jt_rows()
    out = []
    W = out.append

    W('# CH572 RF 收发能力调查')
    W('')

    # 1. 跳转表里 RF 相关的项
    W('## 1. 跳转表里与 RF 直接相关的 API')
    W('')
    W('| JT | API | 状态 | ROM 地址 |')
    W('|---:|---|---|---|')
    rf_ok = rf_no = 0
    for idx, name, st, addr in rows:
        if '预留' in name or name == 'VER_LIB':
            continue
        if not KEY.search(name):
            continue
        if st.startswith('可用'):
            rf_ok += 1
        else:
            rf_no += 1
        W('| %d | `%s` | %s | %s |' % (idx, name, st, addr))
    W('')
    W('可用 %d 个，本平台没有 %d 个。' % (rf_ok, rf_no))
    W('')

    # 2. 厂商头文件里声明的 RF_* 函数
    txt = io.open(VENDOR_HDR, encoding='gbk', errors='replace').read()
    declared = set()
    for m in re.finditer(r'^\s*(?:extern\s+)?[A-Za-z_][\w \*]*?\b(RF_\w+)\s*\(', txt, re.M):
        declared.add(m.group(1))
    jt_names = set(n for _i, n, _s, _a in rows)
    avail = set(n for _i, n, s, _a in rows if s.startswith('可用'))
    W('## 2. 厂商头文件声明的 RF_* 函数 vs 跳转表')
    W('')
    W('| 项目 | 数量 |')
    W('|---|---:|')
    W('| `CH572BLEPeri_LIB.h` 声明的 `RF_*` 函数 | %d |' % len(declared))
    W('| 其中在跳转表里有名字的 | %d |' % len(declared & jt_names))
    W('| 其中跳转表标记"可用"的 | %d |' % len(declared & avail))
    W('| **声明了但跳转表完全没收录** | **%d** |' % len(declared - jt_names))
    W('')
    d = sorted(declared)
    W('声明的 RF_* 函数全清单：')
    W('')
    for n in d:
        mark = '可用' if n in avail else ('无此API' if n in jt_names else '**不在跳转表**')
        W('- `%s` —— %s' % (n, mark))
    W('')

    # 3. 官方 RF_PHY 例程用到的 API
    phy = os.path.join(SDK, 'EVT', 'EXAM', 'RF', 'RF_Basic')
    if not os.path.isdir(phy):
        phy = os.path.join(SDK, 'EVT', 'EXAM', 'RF', 'RF_Extend')
    W('## 3. 官方 RF 例程（%s）里调了什么' % os.path.basename(phy))
    W('')
    srcs = []
    for dirpath, _dirs, files in os.walk(phy):
        for f in files:
            if f.endswith('.c'):
                srcs.append(os.path.join(dirpath, f))
    called = {}
    for p in srcs:
        t = io.open(p, encoding='gbk', errors='replace').read()
        for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', t):
            n = m.group(1)
            if KEY.search(n) or n.startswith(('TMOS_', 'tmos_', 'BLE_', 'HAL_', 'RFRole', 'RFPHY')):
                called.setdefault(n, 0)
                called[n] += 1
    W('| 调用 | 次数 | 跳转表状态 |')
    W('|---|---:|---|')
    for n, c in sorted(called.items(), key=lambda kv: -kv[1]):
        st = '可用' if n in avail else ('无此API' if n in jt_names else '不在跳转表')
        W('| `%s` | %d | %s |' % (n, c, st))
    W('')

    io.open(os.path.join(ROOT, 'build', 'rf_capability.md'), 'w',
            encoding='utf-8').write('\n'.join(out))
    print('RF 可用 %d / 没有 %d；头文件声明 %d，其中不在跳转表 %d'
          % (rf_ok, rf_no, len(declared), len(declared - jt_names)))


if __name__ == '__main__':
    main()
