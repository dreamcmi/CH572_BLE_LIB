# -*- coding: utf-8 -*-
"""hdr_diff.py —— 提取两份 BLE LIB 头文件中的函数声明并做差异对比。"""
import sys, re
sys.stdout.reconfigure(encoding='utf-8')

FUNC_RE = re.compile(
    r'^\s*(?:extern\s+)?([A-Za-z_][A-Za-z0-9_\s\*]*?)\s+'
    r'([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{]*)\)\s*;', re.M)


def funcs(path):
    raw = open(path, 'rb').read().decode('gbk', 'replace')
    out = {}
    for m in FUNC_RE.finditer(raw):
        ret, name, args = m.group(1).strip(), m.group(2), ' '.join(m.group(3).split())
        if name in ('if', 'while', 'for', 'switch', 'return', 'sizeof', 'typedef'):
            continue
        out[name] = (ret, args)
    return out


a = funcs(sys.argv[1])
b = funcs(sys.argv[2])
na, nb = set(a), set(b)
print('CH572 header 函数: %d' % len(na))
print('CH592 header 函数: %d' % len(nb))
print()
print('=== CH592 有、CH572 没有的 (%d 个) ===' % len(nb - na))
for n in sorted(nb - na):
    print('   %-42s %s' % (n, b[n][0]))
print()
print('=== CH572 有、CH592 没有的 (%d 个) ===' % len(na - nb))
for n in sorted(na - nb):
    print('   %-42s %s' % (n, a[n][0]))
print()
print('=== 同名但原型不同的 (%d 个) ===' % sum(1 for n in na & nb if a[n] != b[n]))
for n in sorted(na & nb):
    if a[n] != b[n]:
        print('   %s' % n)
        print('      CH572: %s %s(%s)' % (a[n][0], n, a[n][1]))
        print('      CH592: %s %s(%s)' % (b[n][0], n, b[n][1]))
