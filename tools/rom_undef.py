# -*- coding: utf-8 -*-
"""rom_undef.py —— 计算"把协议栈链接成固定地址 ROM 镜像"时真正缺哪些符号。

思路：
    ROM 镜像 = 协议栈全部 .o + 支撑层 .o 链接到固定地址。
    归档内部的互相引用会自动解决，只有"归档外部"的符号才需要额外提供。
    本脚本模拟链接器的做法：
        1. 收集所有 .o 的 已定义符号 与 未定义符号
        2. 从"种子"出发（协议栈里被外部真正引用的入口，如 BLE_LibInit、
           GAPRole_PeripheralInit 等）做可达性分析，把会被拉进镜像的对象找出来
        3. 汇总这些对象仍未解决的未定义符号 —— 这就是 ROM 需要自己实现
           或从外部取地址的清单

用法：
    python tools\\rom_undef.py <ROM工程构建目录>
"""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
from ar_scan import parse_ar, parse_elf_syms

# 归档成员之间的引用顺序：链接器按命令行顺序 + 归档内部按需拉取。
# 这里简化处理：把所有 .o 都视为"可能被拉进来"，因为 ROM 要提供完整功能。


def collect(objs):
    """objs: [(名字, 内容bytes)] -> (定义表, 每个obj的未定义集合)"""
    defined = {}
    undef_by_obj = {}
    for name, body in objs:
        d, u = parse_elf_syms(body)
        if d is None:
            continue
        for s in d:
            if s[3] == 2:            # STB_GLOBAL
                defined.setdefault(s[0], []).append(name)
        undef_by_obj[name] = set(u)
    return defined, undef_by_obj


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    stack_dir = os.path.join(root, 'stack')

    # ---- 收集协议栈对象 ----
    src_lib = None
    for p in (os.path.join(root, 'libCH572BLE_PERI.a'),
              os.path.join(root, '..', 'lib', 'libCH572BLE_PERI.a')):
        if os.path.exists(p):
            src_lib = p
            break

    objs = []
    if os.path.isdir(stack_dir) and os.listdir(stack_dir):
        for f in sorted(os.listdir(stack_dir)):
            if f.endswith('.o'):
                objs.append((f, open(os.path.join(stack_dir, f), 'rb').read()))
    elif src_lib:
        for nm, off, size, body in parse_ar(src_lib):
            objs.append((nm, body))
    else:
        print('找不到协议栈对象，请先给出 stack 目录或 libCH572BLE_PERI.a')
        sys.exit(1)

    defined, undef_by_obj = collect(objs)
    print('=' * 90)
    print('协议栈对象: %d 个' % len(objs))
    print('归档内已定义全局符号: %d 个' % len(defined))
    print()

    # ---- 汇总所有未定义符号，看归档外部还缺什么 ----
    outside = collections.defaultdict(set)
    for obj, us in undef_by_obj.items():
        for s in us:
            if s not in defined:
                outside[s].add(obj)

    print('-' * 90)
    print('协议栈引用的「归档外部」符号: %d 个' % len(outside))
    print('-' * 90)

    # 分类
    CATS = [
        ('中断处理函数', lambda s: s.endswith('_IRQHandler') or s in (
            'NMI_Handler', 'HardFault_Handler', 'SysTick_Handler', 'SWI_Handler',
            'Ecall_M_Mode_Handler', 'Ecall_U_Mode_Handler', 'Break_Point_Handler')),
        ('编译器运行时(libc/libgcc)', lambda s: s.startswith('__') or s in (
            'memcpy', 'memset', 'memmove', 'memcmp', 'strlen', 'strcpy')),
        ('标准外设驱动', lambda s: any(k in s for k in (
            'FLASH_', 'EEPROM', 'RTC_', 'UART_', 'GPIO', 'SPI_', 'I2C_',
            'PWM', 'TKEY', 'ADC', 'SysTick', 'PFIC_', 'HSECFG', 'SetSysClock',
            'GET_UNIQUE_ID', 'GetMACAddress', 'LSI', 'LowPower', 'GPIOPin', 'GPIOA_'))),
    ]

    seen = set()
    for cat, pred in CATS:
        items = sorted(s for s in outside if s not in seen and pred(s))
        for s in items:
            seen.add(s)
        if items:
            print()
            print('【%s】%d 个' % (cat, len(items)))
            for s in items:
                who = ', '.join(sorted(outside[s])[:3])
                print('    %-34s  <- %s%s' % (s, who, ' ...' if len(outside[s]) > 3 else ''))

    rest = sorted(s for s in outside if s not in seen)
    if rest:
        print()
        print('【其它】%d 个' % len(rest))
        for s in rest:
            who = ', '.join(sorted(outside[s])[:3])
            print('    %-34s  <- %s%s' % (s, who, ' ...' if len(outside[s]) > 3 else ''))

    # 反向：归档定义了多少符号（供生成跳转表用）
    print()
    print('-' * 90)
    print('可用于生成跳转表的符号总数: %d' % len(defined))
    print('-' * 90)


if __name__ == '__main__':
    main()
