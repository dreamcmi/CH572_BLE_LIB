# -*- coding: utf-8 -*-
"""rom_hdr_extract.py —— 从 CH572BLEPeri_LIB.h 抽取"纯类型部分"，供 ROM 头文件复用。

ROM 方式下应用**不能**再包含 CH572BLEPeri_LIB.h —— 那会把 API 声明成普通函数
符号，和 ROM 头文件里的 BLE_LIB_JT 宏冲突。但协议栈的 typedef / struct / enum /
常量宏仍然需要，所以要把它们抽出来单独成文。

实现思路（刻意做得简单直白，便于核对）：
    逐行扫描，用几个明确的状态处理
      1. 多行注释块的进入/退出（整块丢弃）
      2. #define 的多行续行（整块保留）
      3. typedef / struct / enum / union 块（从关键字行开始，到"花括号配平且遇 ;"结束）
    其余行（extern、函数原型、include、include guard）一律丢弃。

用法：
    python tools\\rom_hdr_extract.py <CH572BLEPeri_LIB.h> --out <types文件>
"""
import sys, os, re, argparse
sys.stdout.reconfigure(encoding='utf-8')

GUARD = '__CH572BLE_LIB_PERI_H'
KEEP_TAG = re.compile(r'^\s*(typedef\s+)?(struct|enum|union)\b')
KEEP_TYPEDEF = re.compile(r'^\s*typedef\b')


def extract(src_path):
    # 统一换行符并去掉行尾的 \r。
    # 源文件是 Windows 换行；如果只按 '\n' 切分，每行末尾会残留 '\r'，
    # 拼回去时就会在续行宏（#define ... \）中间插入空行，把宏拆断。
    raw_text = open(src_path, 'rb').read().decode('gbk', 'replace')
    lines = raw_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    kept = []
    stats = {'comment': 0, 'define': 0, 'typedef': 0, 'tagdef': 0,
             'extern': 0, 'proto': 0, 'other': 0}

    i, n = 0, len(lines)
    in_comment = False

    while i < n:
        raw = lines[i]
        s = raw.strip()

        # ---------- 1. 多行注释 ----------
        if in_comment:
            if '*/' in raw:
                in_comment = False
            i += 1
            continue
        if s.startswith('/*'):
            if '*/' not in s:
                in_comment = True
            stats['comment'] += 1
            i += 1
            continue
        if s.startswith('//'):
            i += 1
            continue
        if not s:
            i += 1
            continue

        # ---------- 2. #define（含续行） ----------
        if s.startswith('#define'):
            if GUARD in s:
                i += 1
                continue
            buf = [raw]
            while buf[-1].rstrip().endswith('\\') and i + 1 < n:
                i += 1
                buf.append(lines[i])
            kept.append('\n'.join(buf).rstrip())
            stats['define'] += 1
            i += 1
            continue

        # ---------- 3. 其它预处理指令一律丢弃（含 include / ifndef guard） ----------
        if s.startswith('#'):
            # #if / #endif 这类结构性指令：丢弃。
            # 代价是丢掉条件编译，但本头文件里的条件编译只服务于 include guard，
            # 对我们抽取类型没有影响。
            i += 1
            continue

        # ---------- 4. extern 声明：丢弃（可能跨行） ----------
        if s.startswith('extern'):
            while i < n and ';' not in lines[i]:
                i += 1
            i += 1
            stats['extern'] += 1
            continue

        # ---------- 5. typedef / struct / enum / union 块：保留 ----------
        if KEEP_TYPEDEF.match(s) or KEEP_TAG.match(s):
            buf, depth, started = [], 0, False
            j = i
            while j < n:
                l = lines[j]
                buf.append(l)
                code = re.sub(r'//.*', '', l)
                code = re.sub(r'/\*.*?\*/', '', code)
                if '{' in code:
                    started = True
                depth += code.count('{') - code.count('}')
                t = l.strip()
                j += 1
                if started and depth <= 0:
                    break
                if not started and t.endswith(';'):
                    break
            kept.append('\n'.join(buf).rstrip())
            if KEEP_TYPEDEF.match(s):
                stats['typedef'] += 1
            else:
                stats['tagdef'] += 1
            i = j
            continue

        # ---------- 6. 函数原型 / 普通声明：丢弃 ----------
        if s.endswith(';'):
            if '(' in s:
                stats['proto'] += 1
            else:
                stats['other'] += 1
            i += 1
            continue

        stats['other'] += 1
        i += 1

    return kept, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    kept, stats = extract(args.src)

    out = []
    out.append('/*******************************************************************************')
    out.append(' * 文件名称 : CH572BLE_ROM_types.h')
    out.append(' * 描    述 : 由 tools/rom_hdr_extract.py 自动生成，请勿手工修改。')
    out.append(' *')
    out.append(' *            内容：从 CH572BLEPeri_LIB.h 抽取出来的"纯类型与常量"部分 ——')
    out.append(' *            协议栈全部 typedef / struct / enum / #define 常量，')
    out.append(' *            不含任何 extern 变量与函数原型。')
    out.append(' *')
    out.append(' *            为什么需要它：ROM 方式下应用只包含 CH572BLE_ROM.h，由它再')
    out.append(' *            包含本文件。应用**不能**直接包含 CH572BLEPeri_LIB.h，')
    out.append(' *            否则 API 会被声明成普通函数符号，与 BLE_LIB_JT 宏冲突。')
    out.append(' ******************************************************************************/')
    out.append('#ifndef __CH572BLE_ROM_TYPES_H')
    out.append('#define __CH572BLE_ROM_TYPES_H')
    out.append('')
    out.append('#ifdef __cplusplus')
    out.append('extern "C" {')
    out.append('#endif')
    out.append('')
    out.append('#include "stdint.h"')
    out.append('')
    out.extend(kept)
    out.append('')
    out.append('#ifdef __cplusplus')
    out.append('}')
    out.append('#endif')
    out.append('')
    out.append('#endif /* __CH572BLE_ROM_TYPES_H */')
    out.append('')

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, 'w', encoding='utf-8').write('\n'.join(out))

    print('抽取统计：')
    for k in ('typedef', 'tagdef', 'define', 'extern', 'proto', 'comment', 'other'):
        print('    %-10s 保留/丢弃 %d' % (k, stats[k]))
    print('保留块数 : %d' % len(kept))
    print('输出行数 : %d' % len(out))
    print('输出     : %s' % args.out)


if __name__ == '__main__':
    main()
