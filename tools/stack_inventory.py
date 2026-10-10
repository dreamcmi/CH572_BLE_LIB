# -*- coding: utf-8 -*-
"""把 libCH572BLE_PERI.a 的 54 个对象按"模块职责"归类，并统计每个模块的
代码量 + 其中有多少个函数最终进了 ROM 镜像。

数据来源：
  * build/rom/stage/*.o            —— 归档解包出来的协议栈对象
  * build/rom/CH572BLE_ROM.map     —— 最终镜像的链接 map（含每个 .text.* 的来源对象）
"""
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = r'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH572_BLE_LIB'
BIN = (r'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
       r'\components\WCH\Toolchain\RISC-V Embedded GCC12\bin')
SIZE = os.path.join(BIN, 'riscv-wch-elf-size.exe')
NM = os.path.join(BIN, 'riscv-wch-elf-nm.exe')

STAGE = os.path.join(ROOT, 'build', 'rom', 'stage')
MAP = os.path.join(ROOT, 'build', 'rom', 'CH572BLE_ROM.map')

# 模块归类：按对象名前缀/关键字判断职责
MODULES = [
    ('TMOS 内核',        ('tmos', 'task_scheduler')),
    ('HAL / 时钟',       ('hal', 'clock', 'mcu')),
    ('RF 射频前端',      ('rfend', 'rf_', 'rf.o', 'phy')),
    ('LL 链路层-公共',   ('lle', 'll_', 'll.o', 'linklayer')),
    ('LL 链路层-从机',   ('ll_slave', 'll_peri')),
    ('LL 链路层-主机',   ('ll_master', 'll_central', 'll_cent')),
    ('LL 加密',          ('ll_enc', 'll_aes')),
    ('控制流程输入',     ('ctl_input', 'ctl_')),
    ('HCI / 主机接口',   ('hci', 'host_input', 'host_out')),
    ('GAP 通用访问',     ('gap.o', 'gap_', 'gaprole')),
    ('GAP 从机角色',     ('gap_peripheral', 'gaprole_peri', 'peripheral')),
    ('GAP 主机角色',     ('gap_central', 'gaprole_cent', 'central')),
    ('GAP 广播/观察',    ('gap_broadcaster', 'gap_observer', 'broadcaster', 'observer')),
    ('GAP 绑定管理',     ('gapbondmgr', 'gapbond')),
    ('SM 安全管理',      ('sm.o', 'sm_', 'security')),
    ('GATT 服务端',      ('gattservapp', 'gatt_server', 'gattserv')),
    ('GATT 客户端',      ('gatt_client', 'gattclient', 'gatt.o')),
    ('ATT 属性协议',     ('att',)),
    ('L2CAP',            ('l2cap',)),
    ('IPC / 消息',       ('ipc', 'api_c', 'api.o')),
    ('内存/工具库',      ('memory', 'mem.o', 'util')),
    ('AES 硬件',         ('aes',)),
    ('ISP / Flash',      ('isp', 'flash')),
]


def classify(objname):
    n = objname.lower()
    for label, keys in MODULES:
        for k in keys:
            if k in n:
                return label
    return '其他'


def main():
    objs = sorted(f for f in os.listdir(STAGE) if f.endswith('.o'))

    # 每个对象的代码量
    sizes = {}
    for o in objs:
        p = os.path.join(STAGE, o)
        out = subprocess.run([SIZE, p], capture_output=True, text=True, errors='replace').stdout
        m = re.search(r'^\s*(\d+)\s+(\d+)\s+(\d+)', out, re.M)
        if m:
            sizes[o] = int(m.group(1))

    # map 里每个进入镜像的 .text.<sym> 属于哪个对象
    used_by_obj = {}
    txt = open(MAP, encoding='utf-8', errors='replace').read()
    # 形如:  .text.Foo   0x...  0x...  path\to\obj.o
    for m in re.finditer(r'^\s+\.text(?:\.\S+)?\s+0x[0-9a-f]+\s+0x([0-9a-f]+)\s+(\S+\.o)\s*$',
                         txt, re.M):
        sz = int(m.group(1), 16)
        o = os.path.basename(m.group(2))
        if sz == 0:
            continue
        used_by_obj[o] = used_by_obj.get(o, 0) + sz

    # 汇总到模块
    agg = {}
    for o in objs:
        mod = classify(o)
        a = agg.setdefault(mod, {'n': 0, 'size': 0, 'used': 0, 'objs': []})
        a['n'] += 1
        a['size'] += sizes.get(o, 0)
        a['used'] += used_by_obj.get(o, 0)
        a['objs'].append(o)

    lines = []
    lines.append('# libCH572BLE_PERI.a 模块构成（%d 个对象）' % len(objs))
    lines.append('')
    lines.append('| 模块 | 对象数 | 归档代码 | 进入镜像 | 裁掉(G C) | 对象文件 |')
    lines.append('|---|---:|---:|---:|---:|---|')
    tot_s = tot_u = 0
    for mod, a in sorted(agg.items(), key=lambda kv: -kv[1]['size']):
        tot_s += a['size']
        tot_u += a['used']
        lines.append('| %s | %d | %s | %s | %s | %s |' % (
            mod, a['n'], '{:,}'.format(a['size']), '{:,}'.format(a['used']),
            '{:,}'.format(max(0, a['size'] - a['used'])),
            ', '.join(sorted(a['objs'])[:6]) + ('…' if len(a['objs']) > 6 else '')))
    lines.append('| **合计** | **%d** | **%s** | **%s** | **%s** | |' % (
        len(objs), '{:,}'.format(tot_s), '{:,}'.format(tot_u),
        '{:,}'.format(tot_s - tot_u)))
    lines.append('')
    lines.append('注：')
    lines.append('- "归档代码" = 对象 .text 大小之和，不含 .data/.bss。')
    lines.append('- "进入镜像" = 该对象里被 --gc-sections 保留下来的 .text 字节数。')
    lines.append('- "裁掉" 是估算上界：被删的可能是主机角色等死代码，也可能是内联后失效的函数。')

    open(os.path.join(ROOT, 'build', 'stack_modules.md'), 'w', encoding='utf-8').write('\n'.join(lines))
    print('written stack_modules.md')


if __name__ == '__main__':
    main()
