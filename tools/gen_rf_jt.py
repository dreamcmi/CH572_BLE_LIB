# -*- coding: utf-8 -*-
"""gen_rf_jt.py —— 为纯 RF 固定地址镜像生成跳转表汇编 + 应用侧头文件。

★ 与 BLE 版 tools/rom_gen.py 的关键差异（值得记下来）★

  1. **槽位分配方式完全不同**
     BLE 版必须沿用 WCH 官方 CH59xBLE_ROM.h 的**绝对下标**（48 号槽必须是
     GATT_ReadCharValue，哪怕本平台没有），因为那是官方 ABI 契约，且要靠它
     做"空缺槽 = 本平台无此 API"的编译期拦截。

     纯 RF 库**没有官方 ABI 头**可以参考，所以不受任何既有编号约束 ——
     13 个 API 按固定顺序**连续编号**即可，不需要留空槽。
     这是本工程唯一一处"不需要为缺失 API 预留位置"的地方。

  2. **不生成 UUID 表**（BLE 专用）

  3. **版本串直接引用库自己的 VER_RF_LIB**，不自造
     （BLE 那边自造版本串踩过坑：协议栈 VER_FILE 校验会把自造名字当错版本）

  4. **符号存在性是致命检查**：某个 API 万一在库里被改名，
     这里报错退出，而不是静默填 0 —— 因为 RF 库只有 13 个 API，
     缺任何一个都是构建配置出了问题，不该容忍。
"""

import argparse
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

# ---------------------------------------------------------------------------
# 跳转表：固定顺序、连续编号、不留空槽
# 顺序一旦发布就不要改（应用侧头文件是按这个顺序生成的宏）
# ---------------------------------------------------------------------------
JT_ENTRIES = [
    # (符号名, 应用侧宏名, 签名, 说明)
    ('VER_RF_LIB',            None,                       None,
     '版本串地址（不是函数）'),

    ('RFRole_BasicInit',      'RF_RoleInit',
     'bStatus_t (*)(rfRoleConfig_t *)',
     '初始化 RF 角色，注册回调'),
    ('RFRole_Stop',           'RF_RoleStop',
     'bStatus_t (*)(void)', '停止收发'),
    ('RFRole_Shut',           'RF_RoleShut',
     'bStatus_t (*)(void)', '关闭射频（省电）'),

    ('RFIP_StartTx',          'RF_StartTx',
     'bStatus_t (*)(rfipTx_t *)', '按参数发一包'),
    ('RFIP_SetRx',            'RF_SetRx',
     'bStatus_t (*)(rfipRx_t *)', '按参数收一包（一次一收）'),

    ('RFIP_SingleChannel',    'RF_SetChannel',
     'bStatus_t (*)(uint8_t)', '切到单个频道'),
    ('RFIP_SetTxPower',       'RF_SetTxPower',
     'void (*)(uint8_t)', '设发射功率'),

    ('RFIP_Calibration',      'RF_Calibration',
     'void (*)(void)', '射频校准'),
    ('RFIP_WakeUpRegInit',    'RF_WakeUpRegInit',
     'void (*)(void)', '唤醒后重配射频寄存器'),
    ('RFIP_ReadCrc',          'RF_ReadCrc',
     'uint8_t (*)(void)', '读最近一包的 CRC'),

    # 真 ISR：应用不直接"调用"它们，而是把地址装进中断向量表
    ('BB_LibIRQHandler',      'RF_BbIrqHandler',
     'void (*)(void)', '基带真 ISR（装向量表）'),
    ('LLE_LibIRQHandler',     'RF_LleIrqHandler',
     'void (*)(void)', '链路层真 ISR（装向量表）'),
]

JT_COUNT = len(JT_ENTRIES)


def load_syms(path):
    """支持 nm 输出与 'NAME 0xADDR' 两种格式。"""
    syms = {}
    for line in io.open(path, encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^([0-9a-fA-F]{8})\s+([A-Za-z])\s+(\S+)$', line)
        if m:
            syms.setdefault(m.group(3), int(m.group(1), 16))
            continue
        m = re.match(r'^(\S+)\s+0x([0-9a-fA-F]+)$', line)
        if m:
            syms.setdefault(m.group(1), int(m.group(2), 16))
    return syms


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--syms', required=True, help='RF 镜像的符号表（nm 输出）')
    ap.add_argument('--base', required=True, help='镜像基址，如 0x00010000')
    ap.add_argument('--out-asm', required=True, help='输出的跳转表汇编')
    ap.add_argument('--out-hdr', required=True, help='输出的应用侧头文件')
    ap.add_argument('--report', default='', help='可选：对照报告')
    args = ap.parse_args()

    base = int(args.base, 16)
    syms = load_syms(args.syms)

    # ---- 逐个核对符号是否存在（缺任何一个都是配置错误，直接失败）----
    missing = [n for n, _m, _s, _d in JT_ENTRIES if n not in syms]
    if missing:
        sys.stderr.write('错误：以下符号在 RF 镜像里找不到 ——\n')
        for n in missing:
            sys.stderr.write('    %s\n' % n)
        sys.stderr.write('请检查 rom_rf_link.ld 是否把这些段都收进了镜像，'
                         '以及 --gc-sections 是否把它们裁掉了。\n')
        return 2

    # ---- 生成跳转表汇编 ----
    lines = []
    lines.append('/* 本文件由 tools/gen_rf_jt.py 自动生成，请勿手工修改 */')
    lines.append('/* RF 镜像基址 = 0x%08X，跳转表 %d 项，无空槽（连续编号） */'
                 % (base, JT_COUNT))
    lines.append('')
    lines.append('    .section .rom_rf_jt,"a",@progbits')
    lines.append('    .align 2')
    lines.append('    .globl _rom_rf_jump_table')
    lines.append('_rom_rf_jump_table:')
    for i, (name, macro, _sig, desc) in enumerate(JT_ENTRIES):
        lines.append('    .word %-22s /* RF_JT(%2d) %s */' % (name, i, desc))
    lines.append('')
    lines.append('    .globl _rom_rf_jt_end')
    lines.append('_rom_rf_jt_end:')
    lines.append('')
    os.makedirs(os.path.dirname(os.path.abspath(args.out_asm)), exist_ok=True)
    io.open(args.out_asm, 'w', encoding='utf-8').write('\n'.join(lines))

    # ---- 生成应用侧头文件 ----
    rt = syms.get('ROM_RF_RAM_RESERVE_END', 0)
    h = []
    h.append('/*******************************************************************************')
    h.append(' * 文件名称 : CH572RF_ROM.h')
    h.append(' * 描    述 : 纯 RF 固定地址镜像的应用侧头文件（自动生成，请勿手工修改）')
    h.append(' *')
    h.append(' *            应用通过本文件调用镜像里的 RF 函数，**不需要链接 libCH57xRF.a**。')
    h.append(' *')
    h.append(' *            与 BLE 的 CH572BLE_ROM.h 最大的区别：')
    h.append(' *              BLE 那边必须沿用 WCH 官方下标（要留空槽、要对齐 ABI）；')
    h.append(' *              纯 RF 库没有官方 ABI 约束，所以 %d 个 API **连续编号、无空槽**。' % JT_COUNT)
    h.append(' ******************************************************************************/')
    h.append('#ifndef __CH572RF_ROM_H')
    h.append('#define __CH572RF_ROM_H')
    h.append('')
    h.append('/* 数据类型（rfRoleConfig_t / rfipTx_t / rfipRx_t / RF_STATE_*）')
    h.append(' * 必须与库编译时一致，所以直接引用厂商头，不在这里重抄。 */')
    h.append('#include "CH572rf.h"')
    h.append('')
    h.append('/*------------------------------------------------------------')
    h.append(' * 镜像地址空间')
    h.append(' *----------------------------------------------------------*/')
    h.append('#define RF_ROM_BASE_ADDRESSS        0x%08X' % base)
    h.append('#define RF_ROM_JT_ENTRY_COUNT       %d' % JT_COUNT)
    h.append('#define RF_ROM_JT_BASE_ADDRESSS     (RF_ROM_BASE_ADDRESSS + 0x10)')
    h.append('#define RF_ROM_VERSION_ADDRESSS     (RF_ROM_BASE_ADDRESSS + 0x08)')
    h.append('')
    h.append('/* 镜像头部魔数（8 字节，小端读作 0x46523537 = "CH57RF" 的前 4 字节）*/')
    h.append('#define RF_ROM_MAGIC_0              0x37u   /* \'7\' */')
    h.append('#define RF_ROM_MAGIC_1              0x35u   /* \'5\' */')
    h.append('#define RF_ROM_MAGIC_2              0x52u   /* \'R\' */')
    h.append('#define RF_ROM_MAGIC_3              0x46u   /* \'F\' */')
    h.append('')
    h.append('/* 镜像占用的 RAM 区（应用必须避开，并由应用负责初始化）*/')
    h.append('#define RF_ROM_RAM_RESERVE_START    0x20000000u')
    h.append('#define RF_ROM_RAM_RESERVE_END      0x%08Xu' % rt)
    h.append('')
    h.append('/*------------------------------------------------------------')
    h.append(' * 跳转表访问宏 —— 应用调用的唯一入口')
    h.append(' *----------------------------------------------------------*/')
    h.append('#define RF_JT(n)   (*(uint32_t *)(RF_ROM_JT_BASE_ADDRESSS + (n) * 4))')
    h.append('')
    h.append('/* 版本串（库自己的 VER_RF_LIB，不是自造的）*/')
    h.append('#define RF_LibVersion()   ((const char *)RF_JT(0))')
    h.append('')
    h.append('/* 函数指针宏（顺序与跳转表严格一致）*/')
    for i, (name, macro, sig, desc) in enumerate(JT_ENTRIES):
        if macro is None:
            continue
        h.append('/* RF_JT(%2d)  %s */' % (i, desc))
        h.append('#define %-22s ((%s)RF_JT(%d))' % (macro, sig, i))
        h.append('')
    h.append('/*------------------------------------------------------------')
    h.append(' * 自检辅助')
    h.append(' *----------------------------------------------------------*/')
    h.append('/* 校验镜像头 4 字节魔数。返回 1=像是有镜像，0=没有/不对 */')
    h.append('static inline int RF_RomImagePresent(void)')
    h.append('{')
    h.append('    const volatile uint8_t *p = (const volatile uint8_t *)RF_ROM_BASE_ADDRESSS;')
    h.append('    return (p[0] == RF_ROM_MAGIC_0 && p[1] == RF_ROM_MAGIC_1 &&')
    h.append('            p[2] == RF_ROM_MAGIC_2 && p[3] == RF_ROM_MAGIC_3);')
    h.append('}')
    h.append('')
    h.append('#endif /* __CH572RF_ROM_H */')
    h.append('')
    os.makedirs(os.path.dirname(os.path.abspath(args.out_hdr)), exist_ok=True)
    io.open(args.out_hdr, 'w', encoding='utf-8').write('\n'.join(h))

    # ---- 对照报告 ----
    if args.report:
        r = ['# RF 镜像跳转表对照', '',
             '| 下标 | 符号 | 应用侧宏 | 镜像地址 |', '|---:|---|---|---|']
        for i, (name, macro, _sig, _desc) in enumerate(JT_ENTRIES):
            r.append('| %d | `%s` | %s | `0x%08X` |'
                     % (i, name, ('`%s`' % macro) if macro else '（版本串）', syms[name]))
        r.append('')
        io.open(args.report, 'w', encoding='utf-8').write('\n'.join(r))

    print('跳转表项数       : %d（无空槽）' % JT_COUNT)
    print('应用侧头文件     : %s' % args.out_hdr)
    print('ROM RAM 预留结束 : 0x%08X' % rt)
    return 0


if __name__ == '__main__':
    sys.exit(main())
