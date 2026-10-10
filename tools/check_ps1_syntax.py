# -*- coding: utf-8 -*-
"""check_ps1_syntax.py —— 用 PowerShell 解析器检查 .ps1 是否有语法错误。

为什么要它：本工程的脚本里有大量中文注释，一旦 BOM 丢失，PowerShell 5.1
会按 GBK 解析而报出莫名其妙的语法错误（见 tools/fix_ps1_encoding.py）。
改完脚本后跑一下这个，比等构建失败再回头查要快。

用法：
    python tools\\check_ps1_syntax.py [文件...]
    不给参数时检查 scripts\\ 下所有 .ps1。
退出码 0 = 全部通过。
"""
import base64
import glob
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PS = r'''
$errors = $null
$tokens = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile(
    '{path}', [ref]$tokens, [ref]$errors)
if ($errors -and $errors.Count -gt 0) {
    $errors | Select-Object -First 8 | ForEach-Object {
        "  line $($_.Extent.StartLineNumber): $($_.Message)"
    }
    exit 1
}
exit 0
'''


def check(path):
    # 路径拼进脚本正文再整体编码成 -EncodedCommand：
    # 避免 -Command + 位置参数、以及路径里的空格/中文带来的引号问题。
    ps = PS.replace('{path}', path.replace("'", "''"))
    encoded = base64.b64encode(ps.encode('utf-16-le')).decode('ascii')
    proc = subprocess.run(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
         '-EncodedCommand', encoded],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    ok = (proc.returncode == 0)
    print('  [%s] %s' % ('OK' if ok else '语法错误', os.path.basename(path)))
    if not ok:
        sys.stdout.write(proc.stdout)
        sys.stdout.write(proc.stderr)
    return ok


def main():
    files = sys.argv[1:]
    if not files:
        files = sorted(glob.glob(os.path.join(ROOT, 'scripts', '*.ps1')))
    bad = 0
    for f in files:
        if not check(f):
            bad += 1
    print()
    print('共检查 %d 个文件，异常 %d 个' % (len(files), bad))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
