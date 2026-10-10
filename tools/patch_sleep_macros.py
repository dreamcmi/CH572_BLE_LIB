# -*- coding: utf-8 -*-
"""一次性补丁：给 hal/RTC.c 与 include/RTC.h 补上 RTC_MAX_COUNT / SLEEP_RTC_*。

背景：
    hal/SLEEP.c 的 CH57x_LowPower() 用了 SLEEP_RTC_MIN_TIME / SLEEP_RTC_MAX_TIME，
    但这两个宏全工程都没有定义（官方 CH572 SDK 的 HAL/include/CONFIG.h 里有）。
    结果是 HAL_SLEEP=TRUE 直接编译失败。
    另外 SLEEP_RTC_MAX_TIME 依赖 RTC_MAX_COUNT，而它只在 RTC.c 里定义，
    SLEEP.c 看不到 —— 所以必须把它挪到 RTC.h。

注意：include/RTC.h 与 hal/RTC.c 是 GBK 编码的官方文件，不能按 UTF-8 读，
      也不能把编码改掉（否则 PowerShell 工具链和编辑器都会看到乱码）。
      本脚本显式按 GBK 往返，保持原编码。
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RTC_H = os.path.join(ROOT, 'include', 'RTC.h')
RTC_C = os.path.join(ROOT, 'hal', 'RTC.c')

ANCHOR_H = '#define MS_TO_RTC(ms)               ((uint32_t)((ms) * CLK_PER_MS + 0.5))\n'
INSERT_H = '''#define MS_TO_RTC(ms)               ((uint32_t)((ms) * CLK_PER_MS + 0.5))

/* RTC 计数器是 24 位；取一个不会自然回绕的上限当"绝对时间轴"的模。
 * 原本定义在 RTC.c 里，SLEEP.c 要用（算 sleep 时长与唤醒点），故上移到头文件。 */
#ifndef RTC_MAX_COUNT
#define RTC_MAX_COUNT               0xA8C00000
#endif

/* 睡眠时长的上下限（单位：一个 RTC 周期）。官方 CH572 SDK 的
 * HAL/include/CONFIG.h 里有这两个默认值，本工程漏了，导致 HAL_SLEEP=TRUE
 * 编译不过。太短（< 1ms）睡眠没意义还增加唤醒开销，太长（> 30s）会让
 * 协议栈的连接事件失准，所以两头都夹住，超出就在 CH57x_LowPower 里直接返回。 */
#ifndef SLEEP_RTC_MIN_TIME
#define SLEEP_RTC_MIN_TIME          US_TO_RTC(1000)
#endif
#ifndef SLEEP_RTC_MAX_TIME
#define SLEEP_RTC_MAX_TIME          (RTC_MAX_COUNT - 1000 * 1000 * 30)
#endif
'''

REMOVE_C = '#define RTC_MAX_COUNT                   0xA8C00000\n'

# 说明：hal/SLEEP.c 里的 `volatile uint32_t i;` 是个从未使用的上游遗留局部，
# 打开 HAL_SLEEP 后用 -Wall 会报 unused-variable。这里**故意不动它** ——
# 本工程的原则是厂商源码除了必要适配不改，一个已存在的告警不值得去改写厂商文件。
# 另有 hal/SLEEP.c:52 的 SLEEP_RTC_MIN/MAX_TIME 依赖 RTC.h，见上面的说明。


def patch(path, old, new, expect=1):
    """GBK 往返打补丁。锚点按 LF 书写，实际文件是 CRLF，比较前统一。"""
    raw = open(path, 'rb').read()
    text = raw.decode('gbk')
    crlf = '\r\n' in text
    norm = text.replace('\r\n', '\n')
    n = norm.count(old)
    if n != expect:
        print('  [跳过] %s：锚点出现 %d 次（期望 %d）'
              % (os.path.basename(path), n, expect))
        return False
    norm = norm.replace(old, new, 1)
    if crlf:
        norm = norm.replace('\n', '\r\n')
    open(path, 'wb').write(norm.encode('gbk'))
    print('  [OK]   %s' % os.path.basename(path))
    return True


def main():
    ok = True
    print('补 RTC_MAX_COUNT / SLEEP_RTC_* ：')
    if os.path.exists(RTC_H):
        ok &= patch(RTC_H, ANCHOR_H, INSERT_H)
    if os.path.exists(RTC_C):
        ok &= patch(RTC_C, REMOVE_C, '', expect=1)
    # 复验编码没变
    for p in (RTC_H, RTC_C):
        if os.path.exists(p):
            try:
                open(p, 'rb').read().decode('gbk')
                state = 'GBK 可读'
            except UnicodeDecodeError:
                state = '!! 编码已损坏'
                ok = False
            print('  %-10s %s' % (os.path.basename(p), state))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
