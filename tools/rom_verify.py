# -*- coding: utf-8 -*-
"""rom_verify.py —— ROM 镜像 / 跳转表 / 头文件 一致性验证。

ROM 方案最容易出错的地方是"跳转表里写的地址"和"镜像里符号的真实地址"不一致：
两者由不同步骤产生，只要差一轮迭代就会全线错位，而且烧进去才会发现。

本脚本做四件事：
    1. 校验镜像头部（魔数 / UUID 表）；
    2. **以镜像为基准遍历跳转表的每一个槽位**（0 .. 项数-1），逐槽判定：
         非 0 槽 -> 必须等于某个符号的真实地址（或版本串、或表内自洽）
         0    槽 -> 必须是"本平台确实没有的 API"或纯预留
       项数默认写成注释里的 ROM_JT_ENTRY_COUNT，也可用 --count 覆盖；
       以前这里是"以 jt_map.json 为基准"，因而漏掉了表尾的扩展 API
       （TMOS_SystemProcess / CH572BLE_ROM_Init / CH572BLE_ROM_GetHandles /
        FLASH_EEPROM_CMD / TMOS_TimerSet）—— 恰恰是应用真正调用的那几个。
    3. 校验头文件：ROM 基址、每个 BLE_LIB_JT(n) 的下标必须有非 0 槽、
       0 槽不得被头文件暴露（否则调用即跳地址 0）。
    4. 头部版本串与符号表一致。

用法：
    python tools\\rom_verify.py --bin <rom.bin> --syms <符号表> --base 0x20000 \\
        --jt-json <jt_map.json> [--hdr include/CH572BLE_ROM.h] [--count 164]
"""
import sys, os, re, json, argparse, struct

sys.stdout.reconfigure(encoding='utf-8')

RT_ABS = 0x00010000          # 视为"绝对地址、不是表内偏移"的下界


def load_syms(path):
    """支持两种格式：
        A) nm 输出：  '00004034 T BLE_LibInit' / '00004034 t 局部函数'
                     （第二列的类型字符有大写也有小写，必须都收）
        B) 自定义表： 'BLE_LibInit 0x00004034'
    """
    syms = {}
    for line in open(path, 'r', errors='replace'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^([0-9a-fA-F]{8})\s+([A-Za-z])\s+(\S+)$', line)
        if m:
            # 同名符号可能多处出现（弱符号/局部），保留第一个即可
            syms.setdefault(m.group(3), int(m.group(1), 16))
            continue
        m = re.match(r'^(\S+)\s+0x([0-9a-fA-F]+)$', line)
        if m:
            syms.setdefault(m.group(1), int(m.group(2), 16))
    return syms


def parse_header_slots(path):
    """从应用侧头文件里抽出 slot -> 宏名集合。

    两种宏形态都要认（扩展 API 是带参数的函数式宏）：
        #define  RF_SetFrequency   (( bStatus_t (*)(...) ) BLE_LIB_JT(141))
        #define TMOS_SystemProcess()  (( void (*)(void) ) BLE_LIB_JT(144))()
    """
    slots = {}
    txt = open(path, encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'#define\s+(\w+)(?:\([^)]*\))?\s+[^\n]*?BLE_LIB_JT\((\d+)\)', txt):
        slots.setdefault(int(m.group(2)), set()).add(m.group(1))
    return slots


def infer_count(blob, base):
    """从镜像里的注释拿不到项数时的兜底：用 rom_jt 结束标记不好判断，
    改用"镜像里第一个落在 ROM 区间之外的 32 位值"来界定表尾。"""
    n = 0
    off = 0x34
    while off + 4 <= len(blob):
        v = struct.unpack_from('<I', blob, off)[0]
        if v != 0 and not (base <= v < base + len(blob)):
            break
        n += 1
        off += 4
    return n


def classify_slots(blob, base, n_entries, idx2name, syms, ver_idx, ver_str):
    """逐槽判定跳转表。返回 (计数, 不匹配列表)。

    非 0 槽必须是下面之一，否则算不匹配：
        a) json 里有这个名字，且值 == 该符号的真实地址；
        b) 值恰好是某个已知符号的地址（别名，如 CH572BLE_ROM_FlashCommand -> FLASH_EEPROM_CMD）；
        c) 值落在镜像区间内（表内自洽指针，例如指向已复制到 RAM 的 highcode）。
    0 槽必须是下面之一，否则算不匹配：
        a) json 里有名字但符号表里确实没有该符号（本平台无此 API）；
        b) json 里没有名字（纯预留/对齐槽）。
    特别地：json 里有名字、符号也存在、但槽为 0 —— 这是最危险的情况
    （调用即跳地址 0），必须报错。
    """
    addr2name = {}
    for nm, a in syms.items():
        addr2name.setdefault(a, nm)

    cnt = dict(ok=0, ptr=0, zero_api=0, zero_res=0)
    mismatches = []

    for idx in range(n_entries):
        off = base + 0x34 + idx * 4 - base
        v = None
        if 0 <= off and off + 4 <= len(blob):
            v = struct.unpack_from('<I', blob, off)[0]
        name = idx2name.get(idx)

        if v is None:
            mismatches.append((idx, name or '?', 'N/A', '读出越界'))
            continue

        if idx == ver_idx:
            if ver_str is not None and v == ver_str:
                cnt['ok'] += 1
            else:
                mismatches.append((idx, 'VER_LIB', '0x%08X' % v,
                                   '应为 _rom_version_str 0x%08X' % (ver_str or 0)))
            continue

        if v == 0:
            if name is not None and name in syms:
                mismatches.append((idx, name, '0x00000000',
                                   '符号存在(0x%08X)但槽为 0 -> 调用即跳 0' % syms[name]))
            elif name is not None:
                cnt['zero_api'] += 1
            else:
                cnt['zero_res'] += 1
            continue

        if name is not None and name in syms:
            if v == syms[name]:
                cnt['ok'] += 1
            else:
                mismatches.append((idx, name, '0x%08X' % v, '应为 0x%08X' % syms[name]))
        elif v in addr2name:
            cnt['ok'] += 1
        elif base <= v < base + len(blob):
            cnt['ptr'] += 1
        else:
            mismatches.append((idx, name or '?', '0x%08X' % v,
                               '既不是已知符号地址，也不在镜像区间内'))

    return cnt, mismatches


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin', required=True)
    ap.add_argument('--syms', required=True)
    ap.add_argument('--base', required=True)
    ap.add_argument('--jt-json', required=True)
    ap.add_argument('--hdr', default='')
    ap.add_argument('--report', default='')
    ap.add_argument('--count', type=int, default=0,
                    help='跳转表项数；不给则从头文件 ROM_JT_ENTRY_COUNT 或镜像推断')
    args = ap.parse_args()

    base = int(args.base, 16)
    blob = open(args.bin, 'rb').read()
    syms = load_syms(args.syms)
    jt = json.load(open(args.jt_json, encoding='utf-8'))
    funcs = jt['functions']
    ver_idx = jt.get('ver_lib', 0)

    fails = []
    warns = []

    def rd32(addr):
        off = addr - base
        if off < 0 or off + 4 > len(blob):
            return None
        return struct.unpack_from('<I', blob, off)[0]

    # ---- 项数：优先命令行，其次头文件，最后推断 ----
    n_entries = args.count
    hdr_txt = ''
    if args.hdr and os.path.exists(args.hdr):
        hdr_txt = open(args.hdr, encoding='utf-8', errors='replace').read()
        if not n_entries:
            m = re.search(r'#define\s+ROM_JT_ENTRY_COUNT\s+(\d+)', hdr_txt)
            if m:
                n_entries = int(m.group(1))
    if not n_entries:
        n_entries = infer_count(blob, base)
        warns.append('未能确定跳转表项数，按镜像推断为 %d 项' % n_entries)

    print('=' * 88)
    print('ROM 镜像一致性验证')
    print('=' * 88)
    print('  镜像      : %s (%d 字节)' % (args.bin, len(blob)))
    print('  基址      : 0x%08X' % base)
    print('  跳转表项数: %d' % n_entries)
    print('  符号表    : %d 个符号' % len(syms))
    print()

    # ---- 1. 头部校验 ----
    print('-' * 88)
    print('1. 镜像头部')
    print('-' * 88)
    magic = rd32(base)
    if magic is None:
        fails.append('镜像太小，读不到头部')
    else:
        ok = (magic == 0x43483537)
        print('  +0x00 魔数 : 0x%08X  %s' % (magic, '[OK] "CH57"' if ok else '[失败] 期望 0x43483537'))
        if not ok:
            fails.append('镜像魔数不对：0x%08X' % magic)

    # UUID 表：Device Name(15) 应为 0x2A00（小端 00 2A）
    off = base + 0x08 + 15 * 2
    if off + 2 <= base + len(blob):
        uuid = struct.unpack_from('<H', blob, off - base)[0]
        ok = (uuid == 0x2A00)
        print('  +0x%02X UUID(15) Device Name : 0x%04X  %s'
              % (0x08 + 30, uuid, '[OK]' if ok else '[失败] 期望 0x2A00'))
        if not ok:
            fails.append('UUID 表下标 15 不是 Device Name：0x%04X' % uuid)

    # ---- 2. 跳转表逐槽核对（以镜像为基准，覆盖全部项）----
    print()
    print('-' * 88)
    print('2. 跳转表逐槽核对（以镜像为基准，0 .. %d）' % (n_entries - 1))
    print('-' * 88)

    # 反向表：符号地址 -> 名字（一个地址可能被多个符号共享，取第一个）
    idx2name = dict((v['index'], k) for k, v in funcs.items())
    ver_str = syms.get('_rom_version_str')

    cnt, mismatches = classify_slots(blob, base, n_entries, idx2name,
                                     syms, ver_idx, ver_str)
    n_ok = cnt['ok']
    n_ptr = cnt['ptr']
    n_zero_api = cnt['zero_api']
    n_zero_res = cnt['zero_res']

    print('  地址正确(对上符号) : %d 项' % n_ok)
    print('  镜像内指针(自洽)   : %d 项' % n_ptr)
    print('  本平台无此 API(0)  : %d 项' % n_zero_api)
    print('  纯预留槽(0)        : %d 项' % n_zero_res)
    print('  地址不匹配         : %d 项' % len(mismatches))
    if n_ok + n_ptr + n_zero_api + n_zero_res + len(mismatches) != n_entries:
        fails.append('槽位分类合计 %d != 项数 %d（统计逻辑有漏）'
                     % (n_ok + n_ptr + n_zero_api + n_zero_res + len(mismatches), n_entries))

    if mismatches:
        print()
        print('  !! 不匹配明细（最多显示 25 条）：')
        for idx, name, got, exp in mismatches[:25]:
            print('     JT(%3d) %-34s 表内=%s  %s' % (idx, name, got, exp))
        fails.append('跳转表有 %d 项不匹配' % len(mismatches))
    else:
        print('  [通过] 全部 %d 项都自洽' % n_entries)

    # ---- 3. 头文件一致性 ----
    if hdr_txt:
        print()
        print('-' * 88)
        print('3. 应用侧头文件一致性')
        print('-' * 88)
        m = re.search(r'#define LIB_FLASH_BASE_ADDRESSS\s+0x([0-9A-Fa-f]+)', hdr_txt)
        if m:
            hb = int(m.group(1), 16)
            ok = (hb == base)
            print('  头文件基址 : 0x%08X  %s' % (hb, '[OK]' if ok else '[失败] 与镜像不符'))
            if not ok:
                fails.append('头文件 ROM 基址 0x%08X != 镜像基址 0x%08X' % (hb, base))
        else:
            fails.append('头文件里找不到 LIB_FLASH_BASE_ADDRESSS')

        slots = parse_header_slots(args.hdr)
        n_macro = sum(len(v) for v in slots.values())
        print('  头文件 API 宏 : %d 个，覆盖 %d 个槽位' % (n_macro, len(slots)))
        if n_macro < 20:
            warns.append('头文件导出的 API 宏偏少（%d 个），可能符号解析有问题' % n_macro)

        bad_zero = sorted(i for i in slots if i < n_entries and rd32(base + 0x34 + i * 4) == 0)
        bad_oob = sorted(i for i in slots if i >= n_entries)
        if bad_zero:
            fails.append('头文件把 %d 个空槽暴露成可调用 API（调用即跳地址 0）：%s'
                         % (len(bad_zero), bad_zero[:10]))
        if bad_oob:
            fails.append('头文件引用了超出跳转表范围的槽位：%s' % bad_oob[:10])
        if not bad_zero and not bad_oob:
            print('  [OK] 每个 BLE_LIB_JT(n) 都指向非空槽，且未越界')

        unused = [i for i in range(n_entries)
                  if i not in slots and i != ver_idx and rd32(base + 0x34 + i * 4) not in (0, None)]
        if unused:
            warns.append('镜像里有 %d 个非空槽未被头文件暴露（可能是刻意保留）：%s'
                         % (len(unused), unused[:12]))
    elif args.hdr:
        fails.append('指定的头文件不存在：%s' % args.hdr)

    # ---- 4. 汇总 ----
    print()
    print('=' * 88)
    if fails:
        print('结论: [失败] 发现 %d 个问题' % len(fails))
        for f in fails:
            print('   - ' + f)
    else:
        print('结论: [通过] ROM 镜像、跳转表、头文件三者一致，可以烧录')

    if warns:
        print()
        print('提示:')
        for w in warns:
            print('   - ' + w)

    if args.report:
        with open(args.report, 'w', encoding='utf-8') as f:
            f.write('# ROM 镜像验证报告\n\n')
            f.write('- 镜像: `%s` (%d 字节)\n' % (args.bin, len(blob)))
            f.write('- 基址: `0x%08X`\n' % base)
            f.write('- 跳转表: %d 项；对上符号 %d 项，镜像内指针 %d 项，'
                    '本平台无此 API %d 项，预留 %d 项，不匹配 %d 项\n\n'
                    % (n_entries, n_ok, n_ptr, n_zero_api, n_zero_res, len(mismatches)))
            f.write('## 结论\n\n%s\n' % ('通过' if not fails else '失败：' + '；'.join(fails)))
            if mismatches:
                f.write('\n## 不匹配明细\n\n| JT | API | 表内值 | 期望 |\n|---|---|---|---|\n')
                for idx, name, got, exp in mismatches:
                    f.write('| %d | `%s` | %s | %s |\n' % (idx, name, got, exp))

    return 0 if not fails else 2


if __name__ == '__main__':
    sys.exit(main())
