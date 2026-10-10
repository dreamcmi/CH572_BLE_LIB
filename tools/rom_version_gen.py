# -*- coding: utf-8 -*-
"""rom_version_gen.py —— 生成 ROM 版本串头文件。

为什么要生成头文件，而不是用命令行 -D 传字符串宏：
    命令行里的字符串宏会被 shell 吃掉引号，`V1.3` 里的点号会让编译器把它
    当成小数，报错 `'CH572_BLE_PERI_LIB_V1' undeclared here`。
    生成头文件则完全没有引号问题。

用法：
    python tools\\rom_version_gen.py <协议栈头文件> <输出头文件>

它从协议栈头文件里读出：
    #define VER_FILE  "CH572_BLE_PERI_LIB_V1.3"
然后生成：
    #define ROM_VERSION_STR     "CH572_BLE_PERI_LIB_V1.3"

为什么两者必须一致：
    应用启动时 CH57x_BLEInit() 会做校验
        tmos_memcmp(VER_LIB, VER_FILE, tmos_strlen(VER_FILE))
    VER_LIB 指向 ROM 里的这个串，VER_FILE 来自头文件。
    前缀不一致就会打印 "head file error" 并停在 while(1)，BLE 完全起不来。
"""
import io, os, re, sys

sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_VER = 'CH572_BLE_PERI_LIB_V1.3'

TEMPLATE = '''/* 本文件由 tools/rom_version_gen.py 自动生成，请勿手工修改。
 *
 * 内容：ROM 镜像里 VER_LIB 指向的版本字符串。
 *
 * 它必须与协议栈头文件里的 VER_FILE 一致，因为应用启动时
 * CH57x_BLEInit() 会做校验：
 *     tmos_memcmp(VER_LIB, VER_FILE, tmos_strlen(VER_FILE))
 * 不一致就会打印 "head file error" 并停在 while(1)，BLE 起不来。
 *
 * 每次 make_rom.ps1 都会重新生成，保证与当前协议栈版本同步。
 */
#ifndef __ROM_VERSION_H
#define __ROM_VERSION_H

#define ROM_VERSION_STR     "%(ver)s"

#endif /* __ROM_VERSION_H */
'''


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    src, dst = sys.argv[1], sys.argv[2]

    ver = None
    if os.path.exists(src):
        t = io.open(src, encoding='utf-8', errors='replace').read()
        m = re.search(r'#define\s+VER_FILE\s+"([^"]+)"', t)
        if m:
            ver = m.group(1)

    if ver:
        print('[OK] 协议栈版本串: %s' % ver)
    else:
        ver = DEFAULT_VER
        print('[警告] 没能从 %s 读出 VER_FILE，用兜底值 %s' % (src, ver))

    d = os.path.dirname(os.path.abspath(dst))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

    io.open(dst, 'w', encoding='utf-8').write(TEMPLATE % {'ver': ver})
    print('[OK] 已生成 %s' % dst)
    return 0


if __name__ == '__main__':
    sys.exit(main())
