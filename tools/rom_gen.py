# -*- coding: utf-8 -*-
"""rom_gen.py —— 生成 ROM 镜像的函数跳转表（汇编）与应用侧头文件。

两个产物：

1. rom_jt.S —— 跳转表汇编
   在 ROM_BASE + 0x34 处放一张 4 字节表，第 n 项 = 该 API 在镜像里的绝对地址。
   为什么用汇编生成：跳转表项必须由**链接器**填入最终地址，写死在 C 里做不到。

2. CH572BLE_ROM.h —— 应用侧头文件（等价于官方 CH59xBLE_ROM.h）
   把每个 API 定义成对 ROM 里对应地址的间接调用：
       #define 函数名  ((返回类型 (*)(参数))  BLE_LIB_JT(n))
   应用只需包含这个头文件，**不需要链接任何 BLE 库**。

用法：
    python tools\\rom_gen.py --map <rom.map> --jt <jt_ch592.json> --base 0x4000 \\
        --out-asm <rom_jt.S> --out-hdr <CH572BLE_ROM.h> [--lib <libCH572BLE.a>]
"""
import sys, os, re, json, argparse
sys.stdout.reconfigure(encoding='utf-8')


def load_map_symbols(map_path):
    """从 GNU ld 的 .map 文件里抽取 符号 -> 地址。"""
    syms = {}
    # 形如： 0x00004000                BLE_LibInit
    pat = re.compile(r'^\s+0x([0-9a-fA-F]{8,16})\s+([A-Za-z_][\w\.]*)\s*$')
    for line in open(map_path, 'r', errors='replace'):
        m = pat.match(line)
        if m:
            syms[m.group(2)] = int(m.group(1), 16)
    return syms


def load_nm_symbols(path, nm_exe=None):
    """备用：从 nm 输出里抽取符号（格式：地址 类型 名字）。"""
    syms = {}
    pat = re.compile(r'^([0-9a-fA-F]{8})\s+\S\s+(\S+)$')
    for line in open(path, 'r', errors='replace'):
        m = pat.match(line.strip())
        if m:
            syms[m.group(2)] = int(m.group(1), 16)
    return syms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--map', required=True, help='ROM 链接产生的 .map')
    ap.add_argument('--jt', required=True, help='rom_jt_extract.py 输出的 json')
    ap.add_argument('--base', required=True, help='ROM 基址，如 0x00026000')
    ap.add_argument('--out-asm', required=True, help='输出的跳转表汇编')
    ap.add_argument('--out-hdr', required=True, help='输出的应用侧头文件')
    ap.add_argument('--report', default='', help='可选：写出对照报告')
    # ROM 镜像的实际大小（字节）。用于在头文件里写出准确的地址空间说明，
    # 以及 LIB_FLASH_MAX_SIZE / LIB_FLASH_END 等边界宏。
    # 由构建脚本在最终链接后从 map 里读出再传进来。
    ap.add_argument('--rom-size', type=lambda x: int(x, 0), default=0,
                    help='ROM 镜像实际大小（字节），如 89316')
    # 可选：把"只生成跳转表汇编、不生成头文件"打开。
    # 迭代链接阶段只需要汇编，头文件要等最终大小确定后再生成。
    ap.add_argument('--no-header', action='store_true',
                    help='只生成跳转表汇编，不生成头文件')
    # 应用侧可用 Flash 的结束地址（不含）。用于在头文件里说明分区。
    ap.add_argument('--app-flash-end', type=lambda x: int(x, 0), default=0,
                    help='app 可用 Flash 结束地址（不含），如 0x0001F000')
    # SNV（绑定信息）存储区起始地址。
    # ★ 必须与 ROM 镜像的位置错开 —— SNV 是运行期会被擦写的区域，
    #   如果落在 ROM 区间里，写绑定信息就会把协议栈代码擦掉。
    ap.add_argument('--snv-addr', type=lambda x: int(x, 0), default=0,
                    help='SNV 绑定信息存储区起始地址，如 0x0001F000')
    # SNV 区大小（字节）。默认 4 KB —— 留足余量，避免以后改绑定相关功能时
    # 又要动布局。（绑定信息实际只用 256 字节，但 4 KB 的余量对 240 KB 的
    # Flash 来说占比很小，不值得为省它去动地址。）
    ap.add_argument('--snv-size', type=lambda x: int(x, 0), default=0x1000,
                    help='SNV 区大小（字节），默认 4096')
    args = ap.parse_args()

    base = int(args.base, 16)
    jt = json.load(open(args.jt, encoding='utf-8'))
    funcs = jt['functions']
    uuids = jt['uuids']
    ver_idx = jt.get('ver_lib', 0)

    syms = load_map_symbols(args.map)
    if not syms:
        print('[错误] 从 .map 里没解析到任何符号，请检查 map 格式')
        sys.exit(1)

    DBG = os.environ.get('ROM_GEN_DEBUG')
    if DBG:
        print('map 里解析到 %d 个符号' % len(syms))
        for k in ('BLE_LibInit', 'GAPRole_PeripheralInit', 'TMOS_SystemProcess'):
            print('   %-28s = %s' % (k, hex(syms[k]) if k in syms else '缺失'))

    # -----------------------------------------------------------------------
    # 1. 决定跳转表规模
    # -----------------------------------------------------------------------
    used_idx = sorted(set([v['index'] for v in funcs.values()] + [ver_idx]))
    max_idx = max(used_idx)
    # 对齐到 4 项（16 字节），方便人工核对
    n_entries = ((max_idx + 1) + 3) // 4 * 4

    # -----------------------------------------------------------------------
    # 2. 逐项解析符号地址
    # -----------------------------------------------------------------------
    entries = {}        # index -> (name, addr 或 None)
    missing = []        # CH572 固件库里没有的 API（CH592 有）

    for name, info in funcs.items():
        idx = info['index']
        if name in syms:
            entries[idx] = (name, syms[name])
        else:
            entries[idx] = (name, None)
            missing.append((idx, name))

    # TMOS_SystemProcess 需要单独处理：
    #   官方 CH59x 把它放在 RAM 跳转表（BLE_LIB_RAM_JT(0)），不占 Flash 跳转表下标。
    #   本工程把它当成一个普通 API，分配一个新的跳转表下标，应用即可直接调用。
    #   如果 CH59x 的清单里本来就有它（在 funcs 里），就沿用原下标。
    #   必须在生成汇编之前完成分配，否则新下标会落在表外。
    #
    # 除了 TMOS_SystemProcess，本工程 ROM 自己还导出了几个"ROM 胶水 API"
    # （注册中断钩子、取中断入口），应用侧也必须能调到，同样分配新下标。
    # 这几项不在 CH59x 的 ABI 清单里，属于本工程扩展，统一追加在表尾。
    def alloc_jt(name):
        """给一个 ROM 符号分配空闲跳转表下标。"""
        nonlocal n_entries
        if name in funcs:
            return funcs[name]['index']
        if syms.get(name) is None:
            return None
        used = set(v['index'] for v in funcs.values()) | set(range(n_entries)) | set(entries)
        cand = n_entries
        while cand in used:
            cand += 1
        if cand + 1 > n_entries:
            n_entries = ((cand + 1) + 3) // 4 * 4     # 扩容并保持 4 项对齐
        entries[cand] = (name, syms[name])
        return cand

    EXTRA_ROM_API = (
        'TMOS_SystemProcess',           # 主循环调度入口
        'CH572BLE_ROM_Init',            # 注册应用中断钩子
        'CH572BLE_ROM_GetHandles',      # 取 ROM 中断入口
        'FLASH_EEPROM_CMD',             # Share the RAM-resident ISP implementation
        'TMOS_TimerSet',                # CH572 clock recalibration API
    )
    extra_idx = {}
    for nm in EXTRA_ROM_API:
        idx = alloc_jt(nm)
        if idx is not None:
            extra_idx[nm] = idx
    tmOS_jt_idx = extra_idx.get('TMOS_SystemProcess')

    # 版本字符串：VER_LIB 指向镜像里的版本串（地址从符号表取，取不到就用表尾兜底）
    ver_ptr = syms.get('_rom_version_str')
    if ver_ptr is None:
        ver_ptr = base + 0x34 + n_entries * 4

    # -----------------------------------------------------------------------
    # 3. 生成跳转表汇编
    # -----------------------------------------------------------------------
    lines = []
    lines.append('/* 本文件由 tools/rom_gen.py 自动生成，请勿手工修改 */')
    lines.append('/* ROM 基址 = 0x%08X，跳转表项数 = %d */' % (base, n_entries))
    lines.append('')
    lines.append('    .section .rom_jt,"a",@progbits')
    lines.append('    .align 2')
    lines.append('    .globl _rom_jump_table')
    lines.append('_rom_jump_table:')

    for i in range(n_entries):
        if i in entries:
            name, addr = entries[i]
            if addr is None:
                lines.append('    .word 0            /* JT(%3d) %s —— 本平台无此 API */' % (i, name))
            else:
                lines.append('    .word %s            /* JT(%3d) %s */' % (name, i, name))
        elif i == ver_idx:
            lines.append('    .word _rom_version_str  /* JT(%3d) VER_LIB */' % i)
        else:
            lines.append('    .word 0            /* JT(%3d) 预留 */' % i)

    lines.append('')
    lines.append('/* 跳转表结束标记，便于 rom_inspect 定位 */')
    lines.append('    .globl _rom_jt_end')
    lines.append('_rom_jt_end:')
    lines.append('')

    os.makedirs(os.path.dirname(os.path.abspath(args.out_asm)), exist_ok=True)
    open(args.out_asm, 'w', encoding='utf-8').write('\n'.join(lines))

    # 版本字符串：VER_LIB 指向镜像里的版本串
    ver_ptr = syms.get('_rom_version_str')
    if ver_ptr is None:
        ver_ptr = base + 0x34 + n_entries * 4      # 兜底：紧跟在跳转表后面

    # -----------------------------------------------------------------------
    # 4. 生成应用侧头文件
    #
    #    需要知道 ROM 镜像的**实际大小**才能在头文件里写出准确的地址空间。
    #    大小由构建脚本在最终链接后从 .map 读出，经 --rom-size 传进来；
    #    如果没传（比如迭代链接阶段用 --no-header），就跳过。
    # -----------------------------------------------------------------------
    if args.no_header:
        return 0

    if args.rom_size > 0:
        rom_size = args.rom_size
    else:
        # 没给大小就退而用"到用户 Flash 末尾"的余量做上界，并在注释里说明是估算
        rom_size = 0x3C000 - base
    rom_end = base + rom_size

    h = []
    h.append('/*******************************************************************************')
    h.append(' * 文件名称 : CH572BLE_ROM.h')
    h.append(' * 描    述 : CH572 BLE ROM 镜像的应用侧头文件（自动生成，请勿手工修改）')
    h.append(' *')
    h.append(' *            用法与 WCH 官方 CH59xBLE_ROM.h 完全一致：')
    h.append(' *              1) 定义宏  CH572BLE_ROM=1')
    h.append(' *              2) #include "CONFIG.h"（内部会自动切到本头文件）')
    h.append(' *              3) 链接时**不要**加 -lCH572BLE，协议栈已经在 ROM 里了')
    h.append(' *')
    h.append(' *            地址空间：')
    h.append(' *              CODE : 0x%08X - 0x%08X  (%d 字节 / %.1f KB)'
             % (base, rom_end - 1, rom_size, rom_size / 1024.0))
    h.append(' *                     （BLE 协议栈 ROM 镜像占用的 Flash 区间）')
    h.append(' *              DATA : 0x20000000 - 0x20002FFF  (12 KB, CH572 全部 SRAM)')
    if args.app_flash_end > 0:
        h.append(' *')
        h.append(' *            Flash 分区（CH572 用户区共 240 KB）：')
        h.append(' *              0x00000000 - 0x%08X  app 主体（连续一整块）'
                 % (args.app_flash_end - 1))
        h.append(' *              0x%08X - 0x%08X  SNV 绑定信息存储区（%d B）'
                 % (args.app_flash_end, base - 1, args.snv_size))
        h.append(' *              0x%08X - 0x%08X  BLE 协议栈 ROM 镜像（本文件对应的库）'
                 % (base, rom_end - 1))
        h.append(' *              0x0003C000 - 0x0003FFFF  芯片固定占用（BootLoader + INFO）')
    h.append(' *')
    h.append(' *            镜像组成：')
    h.append(' *              +0x00  镜像标识（魔数 "CH57"）')
    h.append(' *              +0x08  UUID 表（%d 项，每项 2 字节）' % (max(uuids.values()) + 1))
    h.append(' *              +0x34  函数跳转表（%d 项，每项 4 字节）' % n_entries)
    h.append(' ******************************************************************************/')
    h.append('#ifndef __CH572BLE_ROM_H')
    h.append('#define __CH572BLE_ROM_H')
    h.append('#define ROM_JT_ENTRY_COUNT %d' % n_entries)
    h.append('')
    h.append('#ifdef __cplusplus')
    h.append('extern "C" {')
    h.append('#endif')
    h.append('#include  "stdint.h"')
    h.append('')
    h.append('/* ---------------------------------------------------------------------------')
    h.append(' * 协议栈类型与常量定义')
    h.append(' *')
    h.append(' * ROM 方式下应用**不能**再包含 CH572BLEPeri_LIB.h —— 那会把 API 声明成普通')
    h.append(' * 函数符号，与下面的 BLE_LIB_JT 宏冲突。但协议栈的 typedef / struct /')
    h.append(' * 枚举 / 常量宏仍然需要，所以这里包含一份从官方头文件抽取出来的')
    h.append(' * "纯类型"版本（由 tools/rom_hdr_extract.py 生成）。')
    h.append(' * ------------------------------------------------------------------------- */')
    h.append('#include "CH572BLE_ROM_types.h"')
    h.append('')

    # ---- 基础类型（下面的 typedef 有 #ifndef 保护，与 types 头文件重复也无害） ----
    h.append('/* ---------------------------------------------------------------------------')
    h.append(' * 基础类型定义')
    h.append(' * ------------------------------------------------------------------------- */')
    h.append('#ifndef BOOL')
    h.append('typedef uint8_t                 BOOL;')
    h.append('#endif')
    h.append('#ifndef TRUE')
    h.append('#define TRUE                    1')
    h.append('#endif')
    h.append('#ifndef FALSE')
    h.append('#define FALSE                   0')
    h.append('#endif')
    h.append('#ifndef NULL')
    h.append('#define NULL                    0')
    h.append('#endif')
    h.append('#ifndef SUCCESS')
    h.append('#define SUCCESS                 0x00')
    h.append('#endif')
    h.append('#ifndef bStatus_t')
    h.append('typedef uint8_t                 bStatus_t;')
    h.append('#endif')
    h.append('#ifndef tmosTaskID')
    h.append('typedef uint8_t                 tmosTaskID;')
    h.append('#endif')
    h.append('#ifndef tmosEvents')
    h.append('typedef uint16_t                tmosEvents;')
    h.append('#endif')
    h.append('#ifndef tmosTimer')
    h.append('typedef uint32_t                tmosTimer;')
    h.append('#endif')
    h.append('#ifndef tmosSnvId_t')
    h.append('typedef uint16_t                tmosSnvId_t;')
    h.append('#endif')
    h.append('#ifndef tmosSnvLen_t')
    h.append('typedef uint16_t                tmosSnvLen_t;')
    h.append('#endif')
    h.append('')

    # ---- 回调类型 ----
    h.append('/* ---------------------------------------------------------------------------')
    h.append(' * 回调函数类型')
    h.append(' * ------------------------------------------------------------------------- */')
    h.append('typedef uint32_t (*pfnSrandCB)( void );')
    h.append('typedef uint32_t (*pfnIdleCB)( uint32_t );')
    h.append('typedef void     (*pfnLSICalibrationCB)( void );')
    h.append('typedef uint16_t (*pfnTempSampleCB)( void );')
    h.append('typedef void     (*pfnEventCB)( uint32_t timeUs );')
    h.append('typedef void     (*pfnLibStatusErrorCB)( uint8_t code, uint32_t status );')
    h.append('typedef tmosEvents (*pTaskEventHandlerFn)( tmosTaskID taskID, tmosEvents event );')
    h.append('typedef uint32_t (*pfnFlashReadCB)( uint32_t addr, uint32_t num, uint32_t *pBuf );')
    h.append('typedef uint32_t (*pfnFlashWriteCB)( uint32_t addr, uint32_t num, uint32_t *pBuf );')
    h.append('typedef uint32_t (*pfnGetSysClock)( void );')
    h.append('typedef void     (*pfnSetSysClockIRQ)( void );')
    h.append('')

    # ---- 可配置项（ROM 方式下 ch572_ble.h 不参与编译，这些默认值必须在这里给出） ----
    h.append('/* ---------------------------------------------------------------------------')
    h.append(' * 可配置项（ROM 方式默认值）')
    h.append(' *')
    h.append(' * 说明：静态库方式下这些宏由 ch572_ble.h 提供；ROM 方式下应用不包含')
    h.append(' *       ch572_ble.h（它内部会引入 CH572BLEPeri_LIB.h 的 API 声明，')
    h.append(' *       与下面的 BLE_LIB_JT 宏冲突），所以在这里补一份默认值。')
    h.append(' *       可以在工程预定义宏里覆盖。')
    h.append(' * ------------------------------------------------------------------------- */')
    h.append('#ifndef BLE_MEMHEAP_SIZE')
    h.append('#define BLE_MEMHEAP_SIZE            (512 * 10)   /* 示例协议栈 RAM 堆，产品配置需上板验证 */')
    h.append('#endif')
    h.append('#ifndef BLE_MAC')
    # 出厂 MAC 可能没烧（实测 CH572D-R0-1V1 的 0x3F018 读出全 0）。
    # 全 0 地址是 BLE 保留值，手机会忽略 -> 表现为"协议栈跑通但搜不到"。
    # 所以默认打开 BLE_MAC，用 main.c 里的 MacAddr[6] 作设备地址。
    h.append('#define BLE_MAC                     TRUE         /* 用 main.c 的 MacAddr[6]；'
             '芯片出厂 MAC 可能为空 */')
    h.append('#endif')
    h.append('#ifndef HAL_SLEEP')
    h.append('#define HAL_SLEEP                   FALSE')
    h.append('#endif')
    h.append('#ifndef BLE_SNV')
    h.append('#define BLE_SNV                     TRUE')
    h.append('#endif')
    h.append('#ifndef BLE_SNV_ADDR')
    h.append('#define BLE_SNV_ADDR                (0x%08X)'
             % (args.snv_addr if args.snv_addr else 0x3B000))
    h.append('#endif')
    h.append('#ifndef BLE_SNV_BLOCK')
    h.append('#define BLE_SNV_BLOCK               256')
    h.append('#endif')
    h.append('#ifndef BLE_SNV_NUM')
    h.append('#define BLE_SNV_NUM                 1')
    h.append('#endif')
    h.append('#ifndef BLE_BUFF_MAX_LEN')
    h.append('#define BLE_BUFF_MAX_LEN            27')
    h.append('#endif')
    h.append('#ifndef BLE_BUFF_NUM')
    h.append('#define BLE_BUFF_NUM                3')
    h.append('#endif')
    h.append('#ifndef BLE_TX_NUM_EVENT')
    h.append('#define BLE_TX_NUM_EVENT            1')
    h.append('#endif')
    h.append('#ifndef BLE_TX_POWER')
    h.append('#define BLE_TX_POWER                LL_TX_PWR_0_DBM')
    h.append('#endif')
    h.append('#ifndef BLE_CALIBRATION_ENABLE')
    h.append('#define BLE_CALIBRATION_ENABLE      TRUE')
    h.append('#endif')
    h.append('#ifndef BLE_CALIBRATION_PERIOD')
    h.append('#define BLE_CALIBRATION_PERIOD      120000')
    h.append('#endif')
    h.append('#ifndef TEM_SAMPLE')
    h.append('#define TEM_SAMPLE                  TRUE')
    h.append('#endif')
    h.append('#ifndef CLK_OSC32K')
    h.append('#define CLK_OSC32K                  1')
    h.append('#endif')
    h.append('#ifndef PERIPHERAL_MAX_CONNECTION')
    h.append('#define PERIPHERAL_MAX_CONNECTION   1')
    h.append('#endif')
    h.append('')
    h.append('/** 协议栈内存堆，由应用在 main.c 中定义（必须 4 字节对齐） */')
    h.append('extern uint32_t MEM_BUF[BLE_MEMHEAP_SIZE / 4];')
    h.append('#if (defined(BLE_MAC)) && (BLE_MAC == TRUE)')
    h.append('extern const uint8_t MacAddr[6];')
    h.append('#endif')
    h.append('')

    # ---- 地址宏 ----
    h.append('/* ---------------------------------------------------------------------------')
    h.append(' * ROM 地址布局')
    h.append(' *')
    h.append(' *   LIB_FLASH_BASE_ADDRESSS  ROM 镜像起始地址')
    h.append(' *   LIB_FLASH_SIZE           ROM 镜像实际大小（字节）')
    h.append(' *   LIB_FLASH_END            镜像结束地址（不含）')
    h.append(' *   LIB_FLASH_MAX_SIZE        镜像区到用户 Flash 末尾的可用上限')
    h.append(' *')
    h.append(' *   应用侧可以用 LIB_FLASH_END 做边界检查，例如确认某个地址')
    h.append(' *   确实落在 ROM 里，而不是跑到空白 Flash 上去了。')
    h.append(' * ------------------------------------------------------------------------- */')
    h.append('#ifndef LIB_FLASH_BASE_ADDRESSS')
    h.append('#define LIB_FLASH_BASE_ADDRESSS    0x%08X' % base)
    h.append('#endif')
    h.append('')
    h.append('/** ROM 镜像实际占用大小（字节），由链接结果自动填入 */')
    h.append('#define LIB_FLASH_SIZE             0x%08X' % rom_size)
    h.append('/** ROM 镜像结束地址（不含），= LIB_FLASH_BASE_ADDRESSS + LIB_FLASH_SIZE */')
    h.append('#define LIB_FLASH_END              (LIB_FLASH_BASE_ADDRESSS + LIB_FLASH_SIZE)')
    h.append('/** 镜像区可用上限（到用户 Flash 末尾 0x3BFFF 为止） */')
    h.append('#define LIB_FLASH_MAX_SIZE         0x%08X' % (0x3C000 - base))
    h.append('#define LIB_RAM_MAX_SIZE           0x00003000')
    h.append('')
    h.append('#define RAM_BASE_ADDRESSS          0x20000000')
    h.append('#define UUID_BASE_ADDRESSS         (LIB_FLASH_BASE_ADDRESSS + 0x08)')
    h.append('#define FUNCITON_BASE_ADDRESSS     (LIB_FLASH_BASE_ADDRESSS + 0x34)')
    h.append('')
    h.append('/** 取 UUID 表第 n 项的地址（返回 const uint8_t*，可直接当 UUID 字节数组用） */')
    h.append('#define BLE_LIB_UUID(n)            ( UUID_BASE_ADDRESSS + (n) * 2 )')
    h.append('/** 取跳转表第 n 项的函数地址 */')
    h.append('#define BLE_LIB_JT(n)              (*(uint32_t*)( FUNCITON_BASE_ADDRESSS + (n) * 4 ))')
    h.append('/** 库版本字符串（跳转表第 %d 项指向它） */' % ver_idx)
    h.append('#define VER_LIB                    ((const uint8_t*)  BLE_LIB_JT(%d))' % ver_idx)
    h.append('')

    # ---- UUID 宏 ----
    h.append('/* ---------------------------------------------------------------------------')
    h.append(' * GATT UUID（直接指向 ROM 里的 UUID 表）')
    h.append(' * ------------------------------------------------------------------------- */')
    for name, idx in sorted(uuids.items(), key=lambda kv: kv[1]):
        h.append('#define  %-24s ((const uint8_t*) BLE_LIB_UUID(%d))' % (name, idx))
    h.append('')

    # ---- 函数宏 ----
    h.append('/* ---------------------------------------------------------------------------')
    h.append(' * 协议栈 API（通过跳转表间接调用 ROM 里的实现）')
    h.append(' *')
    h.append(' * 注意：CH592 有、CH572 没有的 API（主机角色 / GATT 客户端 / BLE 5.x 高级')
    h.append(' *       特性）在本头文件里被刻意省略 —— 误用会直接编译报错，')
    h.append(' *       而不是运行时跳到地址 0。')
    h.append(' * ------------------------------------------------------------------------- */')
    n_avail = 0
    for name, info in sorted(funcs.items(), key=lambda kv: kv[1]['index']):
        idx = info['index']
        ret, cargs = info['ret'], info['args']
        if entries.get(idx, (None, None))[1] is None:
            continue
        n_avail += 1
        h.append('/* JT(%3d) */' % idx)
        h.append('#define  %-30s (( %s (*)(%s) ) BLE_LIB_JT(%d))' % (name, ret, cargs, idx))
    h.append('')

    # ---- RAM 跳转表项 & ROM 中断入口 ----
    # WCH 官方 CH59x 把 TMOS_SystemProcess / BB_IRQLibHandler / LLE_IRQLibHandler
    # 放在 RAM 固定地址（BLE_LIB_RAM_JT），由 ROM 初始化时填进去。
    # Handles are const Flash data; application IRQ wrappers call the ordinary
    # library bodies. ROM highcode/data/bss still require reserved SRAM.
    ram_funcs = jt.get('ram_functions', {})
    h.append('/* ---------------------------------------------------------------------------')
    h.append(' * ROM 中断入口与主循环（对应官方 CH59x 的 BLE_LIB_RAM_JT）')
    h.append(' *')
    h.append(' * 官方做法：把下面这几个入口的地址写在 RAM 固定地址 0x20000000 + n*4，')
    h.append(' *           应用用 *(uint32_t*)(0x20000000 + n*4) 取。')
    h.append(' * 本工程做法：由 ROM 导出 CH572BLE_ROM_GetHandles()，应用调用一次拿到')
    h.append(' *           全部入口地址。ROM RAM 范围由 rom_ram_info.h/.ld 生成。')
    h.append(' *')
    h.append(' * 应用初始化顺序：')
    h.append(' *   1) 复制 ROM highcode/data 并清零 bss，然后注册中断钩子；')
    h.append(' *   2) 编译 hal/rom_irq.c：BB/LLE 向量指向应用中断包装函数，')
    h.append(' *      包装函数调用 handles 的 bb_lib_irq/lle_lib_irq；')
    h.append(' *   3) 初始化 BLE/HAL，主循环调用 TMOS_SystemProcess()。')
    h.append(' * ------------------------------------------------------------------------- */')
    h.append('')
    h.append('/** ROM 中断入口结构体（字段顺序与 ROM 内部一致） */')
    h.append('typedef struct')
    h.append('{')
    h.append('    uint32_t magic;                     /* 有效标志：0x524F4D48 (\'ROMH\') */')
    h.append('    void (*bb_irq)(void);               /* 基带中断，装到 BB_IRQn */')
    h.append('    void (*lle_irq)(void);              /* 链路层中断，装到 LLE_IRQn */')
    h.append('    uint32_t (*tmos_process)(void);     /* 预留 */')
    h.append('    /* ★ 协议栈内部真正的中断服务程序入口。')
    h.append('     * 官方 CH572 的 MCU.c 会把 LLE_IRQLibHandler 的地址赋给')
    h.append('     * g_LLE_IRQLibHandlerLocation 供协议栈内部使用，ROM 方式下由此导出。')
    h.append('     * 注意它与上面的 lle_irq 不是一回事：')
    h.append('     *   lle_irq      = 向量表里的转接桩（会转调应用注册的钩子）')
    h.append('     *   lle_lib_irq  = 协议栈内部的链路层中断服务程序本体 */')
    h.append('    void (*lle_lib_irq)(void);')
    h.append('    void (*bb_lib_irq)(void);')
    h.append('} rom_handles_t;')
    h.append('')
    h.append('#define ROM_HANDLE_MAGIC            0x524F4D48U')
    h.append('')
    h.append('/** 应用钩子表 —— 与 rom/rom_support.c 里的 rom_app_hooks_t 必须逐字段一致 */')
    h.append('typedef struct')
    h.append('{')
    h.append('    uint32_t magic;                     /* 有效标志：0x43483537 (\'CH57\') */')
    h.append('    void (*rtc_irq_handler)(void);')
    h.append('    void (*ble_irq_handler)(void);')
    h.append('    void (*lle_irq_handler)(void);')
    h.append('    void (*sys_tick_handler)(void);')
    h.append('    void (*uart_irq_handler)(void);')
    h.append('    void (*gpio_irq_handler)(void);')
    h.append('    void (*tmr_irq_handler)(void);')
    h.append('    void (*i2c_irq_handler)(void);')
    h.append('    void (*spi_irq_handler)(void);')
    h.append('    void (*pwm_irq_handler)(void);')
    h.append('    void (*keyscan_irq_handler)(void);')
    h.append('} rom_app_hooks_t;')
    h.append('')
    h.append('#define ROM_HOOK_MAGIC              0x43483537U')
    h.append('')
    if ram_funcs:
        h.append('/** 官方 CH59x 的 RAM 跳转表项（仅作对照，本工程不使用） */')
        for name, info in sorted(ram_funcs.items(), key=lambda kv: kv[1]['index']):
            h.append('/* RAM_JT(%d)  对应官方 %s */' % (info['index'], name))
        h.append('')
    h.append('/**')
    h.append(' * @brief  注册应用中断处理钩子，并返回 ROM 版本字符串')
    h.append(' * @param  hooks - 应用钩子表；传 NULL 表示只取版本号')
    h.append(' * @return ROM 版本字符串（例如 "CH572_BLE_ROM_V1.0"）')
    h.append(' */')
    if 'CH572BLE_ROM_Init' in extra_idx:
        h.append('#define CH572BLE_ROM_Init(hooks)   (( const char *(*)(rom_app_hooks_t *) ) BLE_LIB_JT(%d))(hooks)'
                 % extra_idx['CH572BLE_ROM_Init'])
    else:
        h.append('extern const char *CH572BLE_ROM_Init(rom_app_hooks_t *hooks);')
    h.append('')
    h.append('/** 兼容别名，方便从官方 CH59x 代码迁移 */')
    h.append('#define BLE_ROM_Init                CH572BLE_ROM_Init')
    h.append('')
    h.append('/**')
    h.append(' * @brief  取得 ROM 内部中断处理入口（BB / LLE）')
    h.append(' * @return 指向 rom_handles_t 的只读指针')
    h.append(' */')
    if 'CH572BLE_ROM_GetHandles' in extra_idx:
        h.append('#define CH572BLE_ROM_GetHandles()   (( const rom_handles_t *(*)(void) ) BLE_LIB_JT(%d))()'
                 % extra_idx['CH572BLE_ROM_GetHandles'])
    else:
        h.append('extern const rom_handles_t *CH572BLE_ROM_GetHandles(void);')
    h.append('')

    # TMOS 主循环入口：只有在协议栈里确实导出了 TMOS_SystemProcess 时才定义，
    # 避免宏指向一个不存在的跳转表下标（那会在运行时跳到随机地址）。
    if tmOS_jt_idx is not None:
        h.append('/** 主循环调用的 TMOS 调度入口（实现在 ROM 里） */')
        h.append('#define TMOS_SystemProcess()        (( void (*)(void) ) BLE_LIB_JT(%d))()'
                 % tmOS_jt_idx)
        h.append('')
    else:
        h.append('/* 注意：本 ROM 镜像未导出 TMOS_SystemProcess，应用需自行提供主循环调度。 */')
        h.append('')

    if 'FLASH_EEPROM_CMD' in extra_idx:
        h.append('#define CH572BLE_ROM_FlashCommand(cmd, addr, buf, len) ((uint32_t (*)(uint8_t, uint32_t, void *, uint32_t)) BLE_LIB_JT(%d))(cmd, addr, buf, len)' % extra_idx['FLASH_EEPROM_CMD'])
        h.append('')
    if 'TMOS_TimerSet' in extra_idx:
        h.append('#define TMOS_TimerSet(conf) ((bStatus_t (*)(bleClockConfig_t *)) BLE_LIB_JT(%d))(conf)' % extra_idx['TMOS_TimerSet'])
        h.append('')

    h.append('')
    h.append('#ifdef __cplusplus')
    h.append('}')
    h.append('#endif')
    h.append('')
    h.append('#endif /* __CH572BLE_ROM_H */')
    os.makedirs(os.path.dirname(os.path.abspath(args.out_hdr)), exist_ok=True)
    open(args.out_hdr, 'w', encoding='utf-8').write('\n'.join(h))

    # -----------------------------------------------------------------------
    # 5. 报告
    # -----------------------------------------------------------------------
    print('跳转表项数       : %d' % n_entries)
    print('可用 API         : %d' % n_avail)
    print('本平台缺失的 API : %d' % len(missing))
    if missing and DBG:
        for idx, name in sorted(missing):
            print('    JT(%3d) %s' % (idx, name))
    print('生成跳转表       : %s' % args.out_asm)
    print('生成头文件       : %s' % args.out_hdr)

    if args.report:
        with open(args.report, 'w', encoding='utf-8') as f:
            f.write('# ROM 跳转表对照\n\n')
            f.write('| JT 下标 | API | 状态 | ROM 地址 |\n|---|---|---|---|\n')
            for i in range(n_entries):
                if i in entries:
                    name, addr = entries[i]
                    st = ('0x%08X' % addr) if addr else '——'
                    ok = '可用' if addr else '本平台无此 API'
                    f.write('| %d | `%s` | %s | %s |\n' % (i, name, ok, st))
                elif i == ver_idx:
                    f.write('| %d | VER_LIB | 版本字符串 | 0x%08X |\n' % (i, ver_ptr))
                else:
                    f.write('| %d | （预留） | — | — |\n' % i)
        print('生成对照报告     : %s' % args.report)


if __name__ == '__main__':
    main()
