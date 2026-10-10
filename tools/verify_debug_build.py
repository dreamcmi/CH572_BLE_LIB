# -*- coding: utf-8 -*-
"""verify_debug_build.py —— 确认调试版固件里该有的内容都在。

只检查"用户能不能看到关键信息"，不依赖符号表（-Os 下静态函数常被内联）。
"""
import os, sys

sys.path.insert(0, 'tools')
sys.stdout.reconfigure(encoding='utf-8')
from hex_probe import load_hex


def blob_of(path):
    m, _ = load_hex(path)
    return bytes(m.get(a, 0xFF) for a in range(min(m), max(m) + 1)), min(m), max(m)


CHECKS = [
    ('BLEInit 追踪点',        '[BLEInit'.encode()),
    ('highcode 自检提示',     'highcode'.encode()),
    ('Flash 读探测提示',      'Flash '.encode()),
    ('时钟诊断',              '实测系统时钟'.encode()),
    ('版本串校验',            'head file error'.encode()),
]

FILES = [
    ('build/out/ble_uart-rom.hex', 'ROM 方式 (ble_uart-rom.hex)'),
    ('build/out/ble_uart.hex',     '静态库方式 (ble_uart.hex)'),
]

for path, label in FILES:
    if not os.path.exists(path):
        print('  %s : 文件不存在' % label)
        continue
    b, lo, hi = blob_of(path)
    print('%s' % label)
    print('  文件 %d 字节，数据覆盖 0x%06X - 0x%06X' % (os.path.getsize(path), lo, hi))
    for name, pat in CHECKS:
        print('    %-18s %s' % (name, '有' if pat in b else '—'))
    print()
