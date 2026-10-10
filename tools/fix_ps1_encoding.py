# -*- coding: utf-8 -*-
"""fix_ps1_encoding.py —— 修正 .ps1 文件的编码：确保「有且仅有一个」UTF-8 BOM。

为什么必须做这件事：
    Windows PowerShell 5.1 读取 .ps1 时，如果没有 BOM，会按系统 ANSI（中文系统是 GBK）
    解析。本工程的脚本里有大量中文注释与中文提示，按 GBK 解析会直接语法报错。
    但如果**多**了一个 BOM（重复的 U+FEFF），同样会报
    "意外的属性 CmdletBinding" 这类莫名其妙的语法错误。

本脚本对每个文件：
    1. 用 UTF-8 解码（保留任何内嵌的 U+FEFF 字符）；
    2. 去掉正文开头出现的所有 U+FEFF；
    3. 以「UTF-8 with BOM」重新写回。

用法：
    python tools\\fix_ps1_encoding.py [文件...]
    不给参数时处理 scripts\\ 下所有 .ps1。
"""
import sys, os, glob
sys.stdout.reconfigure(encoding='utf-8')

BOM_CHAR = '\ufeff'


def fix(path):
    raw = open(path, 'rb').read()
    # 先用 UTF-8 解码；解不开就说明本来就是 GBK，按 UTF-8 重写后再处理
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('gbk', 'replace')
        note = '（原为 GBK，已转 UTF-8）'
    else:
        note = ''

    # 统计并剥掉正文开头多余的 BOM 字符
    stripped = text.lstrip(BOM_CHAR)
    n_bom = len(text) - len(stripped)
    text = stripped

    with open(path, 'wb') as f:
        f.write(b'\xef\xbb\xbf')            # 恰好一个 UTF-8 BOM
        f.write(text.encode('utf-8'))

    # 复验
    chk = open(path, 'rb').read(3)
    ok = (chk == b'\xef\xbb\xbf')
    return n_bom, ok, note


def main():
    files = sys.argv[1:]
    if not files:
        here = os.path.dirname(os.path.abspath(__file__))
        files = sorted(glob.glob(os.path.join(os.path.dirname(here), 'scripts', '*.ps1')))

    bad = 0
    for f in files:
        n_bom, ok, note = fix(f)
        status = 'OK' if ok else 'FAIL'
        if not ok:
            bad += 1
        extra = ('  去掉多余 BOM %d 个' % n_bom) if n_bom else ''
        print('  [%s] %-28s%s%s' % (status, os.path.basename(f), extra, note))

    print()
    print('共处理 %d 个文件，异常 %d 个' % (len(files), bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
