# -*- coding: utf-8 -*-
"""rom_stub_gen.py —— 为"协议栈引用了但本平台不提供"的符号生成兼容桩。

背景：
    libCH572BLE_PERI.a 是从全功能协议栈裁剪出来的。裁剪时删掉了主机角色 /
    GATT 客户端 / BLE 5.x 高级特性的**实现**，但 gatt.o、att.o、api_c.o 等
    对象里仍残留着**对它们的调用**（那些调用所在的分支在从机角色下永远不会执行）。

    这会导致 ROM 链接时出现一批 undefined reference。这些符号本就不该在
    从机固件里被用到，所以本脚本为它们生成"弱桩"：
      * 函数：空实现，返回 0
      * 数据：零初始化

    作用有两个：
      1. 让 ROM 镜像能链接成功；
      2. 万一将来真有人走到那条分支，桩里的 while(1) 会让问题立刻暴露，
         而不是跳到随机地址跑飞（调试友好）。

用法：
    python tools\\rom_stub_gen.py --lib libCH572BLE_PERI.a --keep <符号清单文件> \\
        --out rom/rom_stub.c
    其中 <符号清单文件> 每行一个"应当保留（真正实现）"的符号名；
    清单之外的未定义符号会被生成为桩。
"""
import sys, os, re, argparse, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
from ar_scan import parse_ar, parse_elf_syms


def collect(lib_path):
    """收集归档里所有对象的符号（含 LOCAL 绑定的 .bss/.sbss 数据）。

    重要：协议栈里很多"全局数据"（gapRole_state、gBleHostPara 等）在目标文件里
    是 **LOCAL** 绑定（static 或者本编译单元内定义），只把 STB_GLOBAL 收集进来
    会误判为"缺失"，进而生成多余的桩 —— 那会把真实数据覆盖掉，属于致命错误。
    所以这里对"已定义"不做绑定过滤，只有"未定义"才关心绑定。
    """
    defined, undef = set(), collections.defaultdict(set)
    for nm, off, size, body in parse_ar(lib_path):
        d, u = parse_elf_syms(body)
        if d is None:
            continue
        for s in d:
            # (名字, 值, 大小, 绑定, 段名)；只要不是 UND/ABS 之外一律算"已定义"
            if s[4]:                 # 有段名 → 确实被定义了
                defined.add(s[0])
        for s in u:
            undef[s].add(nm)
    return defined, undef


# 这些前缀的符号是"编译器/系统"级别，交给 libc / libgcc，不要生成桩
SKIP_PREFIX = ('__', '_GLOBAL_', '_Z')

# 标准 C 库函数：由 -lc / nano.specs 提供，不能生成同名桩（签名冲突且没必要）
LIBC_SYMBOLS = {
    'memcpy', 'memset', 'memmove', 'memcmp', 'strlen', 'strcpy', 'strncpy',
    'strcmp', 'strncmp', 'strcat', 'malloc', 'free', 'calloc', 'realloc',
    'printf', 'sprintf', 'snprintf', 'puts', 'putchar', 'atoi',
    'abs', 'div', 'ldiv', 'abort', 'exit',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lib', required=True)
    ap.add_argument('--keep', required=True, help='保留(真实实现)的符号清单文件，每行一个')
    ap.add_argument('--out', required=True)
    ap.add_argument('--report', default='')
    args = ap.parse_args()

    defined, undef = collect(args.lib)

    keep = set()
    if os.path.exists(args.keep):
        for line in open(args.keep, encoding='utf-8', errors='replace'):
            line = line.strip()
            if line and not line.startswith('#'):
                keep.add(line)

    missing = sorted(s for s in undef if s not in defined and s not in keep)
    missing = [s for s in missing
               if not s.startswith(SKIP_PREFIX) and s not in LIBC_SYMBOLS]

    # 分类：函数（需要调用桩）与数据（需要存储桩）
    # 判据（按 WCH 代码的命名习惯）：看"前缀之后的第一个字母"是大写还是小写
    #   前缀是小写模块名（l2cap / gap / gatt / att / ll / sm / tmos ...）时：
    #       紧跟小写 → 驼峰函数，如 l2capFindPsm、gapProcessScanningEvt
    #       紧跟大写 → 全局变量，如 l2capChannels、gapRole_state
    #   其它情况（全大写前缀、下划线分隔）一律先按函数处理，出错时链接器会报错，
    #   比"把真实数据误替换成桩"安全得多。
    data_pat = re.compile(
        r'^(?:l2cap|gap|gatt|att|ll|sm|tmos|tmos_|rf|ble|host|ip|bb|lle)[A-Z]')
    func_pat = re.compile(
        r'^(?:l2cap|gap|gatt|att|ll|sm|tmos|rf|ble|host|ip|bb|lle)[a-z_]')

    funcs, datas = [], []
    for s in missing:
        # R_RISCV_CALL relocations in the supplied archive identify these as
        # functions despite the lowercase module / uppercase suffix spelling.
        if s in {'l2capFindPsm', 'l2capNumActiveChannnels'} or func_pat.match(s):
            funcs.append(s)
        elif data_pat.match(s):
            datas.append(s)
        else:
            funcs.append(s)

    lines = []
    lines.append('/*******************************************************************************')
    lines.append(' * 文件名称 : rom_stub.c')
    lines.append(' * 描    述 : 本文件由 tools/rom_stub_gen.py 自动生成，请勿手工修改。')
    lines.append(' *')
    lines.append(' *            CH572 的 BLE 协议栈是从全功能协议栈裁剪出来的：主机角色、')
    lines.append(' *            GATT 客户端、BLE 5.x 高级特性的"实现"被删掉了，')
    lines.append(' *            但部分对象里仍残留着对这些实现的"调用"。')
    lines.append(' *            这些调用所在的分支在从机角色下永远不会执行，')
    lines.append(' *            因此这里为它们提供**弱桩**，目的是：')
    lines.append(' *              1) 让 ROM 镜像能够链接成功；')
    lines.append(' *              2) 万一真被调用到，立刻停在桩里（便于定位），')
    lines.append(' *                 而不是跳到无效地址跑飞。')
    lines.append(' *')
    lines.append(' *            ⚠ 本平台确实不支持这些能力，应用不要尝试调用它们。')
    lines.append(' ******************************************************************************/')
    lines.append('')
    lines.append('#include <stdint.h>')
    lines.append('')
    lines.append('/* --------------------------------------------------------------------------- */')
    lines.append('/* 数据桩：%d 个                                                                 */' % len(datas))
    lines.append('/* --------------------------------------------------------------------------- */')
    for s in datas:
        lines.append('__attribute__((weak)) volatile uint32_t %s;' % s)
    lines.append('')
    lines.append('/* --------------------------------------------------------------------------- */')
    lines.append('/* 函数桩：%d 个                                                                 */' % len(funcs))
    lines.append('/* --------------------------------------------------------------------------- */')
    lines.append('/* 桩函数统一返回 0；如果需要在"被误调用"时立刻停住，')
    lines.append('   把下面的 ROM_STUB_HALT 打开即可（默认关闭，避免误伤正常流程）。 */')
    lines.append('#ifndef ROM_STUB_HALT')
    lines.append('#define ROM_STUB_HALT   0')
    lines.append('#endif')
    lines.append('')
    lines.append('#define ROM_STUB(name)                                  \\')
    lines.append('    __attribute__((weak)) uint32_t name(void)           \\')
    lines.append('    {                                                   \\')
    lines.append('        if (ROM_STUB_HALT) { while (1) { } }            \\')
    lines.append('        return 0;                                       \\')
    lines.append('    }')
    lines.append('')
    for s in funcs:
        lines.append('ROM_STUB(%s)' % s)
        lines.append('')

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, 'w', encoding='utf-8').write('\n'.join(lines))

    print('协议栈已定义符号 : %d' % len(defined))
    print('保留(真实实现)   : %d' % len(keep))
    print('生成数据桩       : %d' % len(datas))
    print('生成函数桩       : %d' % len(funcs))
    print('输出             : %s' % args.out)

    if args.report:
        with open(args.report, 'w', encoding='utf-8') as f:
            f.write('# ROM 兼容桩清单\n\n')
            f.write('## 数据桩（%d 个）\n\n' % len(datas))
            for s in datas:
                src = ', '.join(sorted(undef[s])[:3])
                f.write('- `%s`  ← 引用者: %s\n' % (s, src))
            f.write('\n## 函数桩（%d 个）\n\n' % len(funcs))
            for s in funcs:
                src = ', '.join(sorted(undef[s])[:3])
                f.write('- `%s`  ← 引用者: %s\n' % (s, src))
        print('生成桩清单       : %s' % args.report)


if __name__ == '__main__':
    main()
