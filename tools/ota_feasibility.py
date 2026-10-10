# -*- coding: utf-8 -*-
"""ota_feasibility.py —— 一次性分析脚本：新布局（ROM 靠后）下的 OTA 可行性。

ROM 现在占 Flash 高位（默认 0x20000 起），把尾部吃掉了。
本脚本算出：在新布局下，几种 OTA 方案各需要多少空间、是否放得下。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from hex_probe import load_hex

FLASH_END = 0x3C000          # 用户 Flash 末尾（不含）
ROM_BASE = 0x20000           # 当前默认 ROM 基址

m, _ = load_hex(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'lib', 'CH572BLE_ROM.hex'))
rom_lo, rom_hi = min(m), max(m) + 1
ROM_SIZE = rom_hi - rom_lo

print('=' * 76)
print('当前布局（ROM 靠后）')
print('=' * 76)
print('  ROM 基址（默认）  : 0x%05X' % ROM_BASE)
print('  ROM 精确尺寸      : %d 字节 (%.2f KB)' % (ROM_SIZE, ROM_SIZE / 1024))
print('  ROM 占用区间      : 0x%05X - 0x%05X' % (ROM_BASE, ROM_BASE + ROM_SIZE - 1))
print('  ROM 之后剩余      : %d 字节' % (FLASH_END - ROM_BASE - ROM_SIZE))
print()

# ---------------------------------------------------------------------------
print('=' * 76)
print('方案一：不用 ROM（静态协议栈）+ 官方 OTA 双镜像')
print('=' * 76)
need = 0x1000 + 108 * 1024 * 2 + 12 * 1024 + 8 * 1024
print('  OTA flag 4K + imageA 108K + imageB 108K + IAP 12K + 数据 8K')
print('  合计需要          : %d 字节 (%.1f KB)' % (need, need / 1024))
print('  用户 Flash        : %d 字节 (240 KB)' % FLASH_END)
print('  结论              : %s' % ('放得下' if need <= FLASH_END else '放不下'))
print('  说明              : 该方案本来就不使用 ROM，与新布局无关。')
print()

# ---------------------------------------------------------------------------
print('=' * 76)
print('方案二：ROM 靠后 + 双镜像（imageA / imageB）')
print('=' * 76)
reserved = ROM_BASE + ROM_SIZE        # ROM 与它之前的空间已被占用
print('  已被 ROM 占走      : 0x00000 - 0x%05X  (%d 字节 / %.1f KB)'
      % (ROM_BASE + ROM_SIZE - 1, reserved, reserved / 1024))
print('  剩余给 OTA 的空间   : 0x%05X - 0x%05X  (%d 字节 / %.1f KB)'
      % (ROM_BASE + ROM_SIZE, FLASH_END - 1,
         FLASH_END - ROM_BASE - ROM_SIZE, (FLASH_END - ROM_BASE - ROM_SIZE) / 1024))
print()
fix = 0x1000 + 12 * 1024 + 4 * 1024        # flag + IAP + SNV
avail = FLASH_END - ROM_BASE              # ROM 之前全部可给双镜像
per = (avail - fix) // 2
print('  ROM 之前可用       : %d 字节 (%.1f KB)' % (avail, avail / 1024))
print('  扣掉 flag 4K + IAP 12K + SNV 4K 后，每个镜像可分 %d 字节 (%.1f KB)'
      % (per, per / 1024))
print('  结论              : %s' % ('可行' if per >= 16 * 1024 else '空间太紧'))
print('  代价              : 需要自研跳转方案（ROM 夹在中间，两镜像必须同址）；')
print('                      官方 JumpIAP 不支持这种排布。')
print()

# ---------------------------------------------------------------------------
print('=' * 76)
print('方案三：ROM 靠后 + 单下载区（升级包最小）')
print('=' * 76)
snv = 0x1000
iap = 12 * 1024
# app 与下载区都要落在 ROM 之前
avail3 = ROM_BASE - snv - iap
appsz = avail3 // 2
print('  ROM 之前可用       : %d 字节 (%.1f KB)' % (ROM_BASE, ROM_BASE / 1024))
print('  扣掉 SNV 4K + IAP 12K -> %d 字节 (%.1f KB)' % (avail3, avail3 / 1024))
print('  划成 app 与下载区两半，各 %d 字节 (%.1f KB)' % (appsz, appsz / 1024))
print('  结论              : %s' % ('可行' if appsz >= 16 * 1024 else '空间太紧'))
print('  优点              : 升级包只需传 app（约 7 KB），不用重传协议栈')
print()

# ---------------------------------------------------------------------------
print('=' * 76)
print('关键结论')
print('=' * 76)
print('  ROM 挪到高位后，Flash 尾部被 ROM 吃掉，OTA 的分区空间从"尾部"')
print('  变成了"ROM 之前的中段"，规划方式要跟着变：')
print()
print('  * 要 A/B 双镜像 + 官方实现  -> 别用 ROM，回到静态协议栈方式')
print('  * 要最小升级包（7 KB）      -> 用 ROM + 单下载区，自研跳转')
print('  * 不用 OTA                  -> 当前布局就是最优（app 拿 124 KB 连续空间）')
