/*******************************************************************************
 * 文件名称 : rf_rom_cfg.h
 * 描    述 : 纯 2.4G 点对点收发（固定地址镜像方式）—— 所有可调参数集中在这里
 *
 *            ★ 本工程不使用蓝牙协议栈 ★
 *            射频功能由独立的固定地址镜像提供（镜像里装的是 WCH 的
 *            libCH57xRF.a），应用通过跳转表调用，**不链接任何 RF/BLE 库**。
 *
 *            因此：
 *              - 不初始化 TMOS，不需要 BLE 内存堆 MEM_BUF
 *              - 不包含 ch572_ble.h / CONFIG.h（那两个头与 CH572rf.h 的
 *                LL_TX_PWR_* 数值不同，混用会取到错的值）
 *              - 编译时包含 include\rf\（CH572rf.h）+ include\（CH57x_*.h）
 ******************************************************************************/
#ifndef __RF_ROM_CFG_H
#define __RF_ROM_CFG_H

/*=============================================================================
 * 一、角色选择
 *
 *   RF_APP_ROLE  0 = 接收端（RX）
 *                 1 = 发送端（TX）
 *
 *   构建：scripts\build_rf_rom_app.ps1 -Role both -DebugBuild
 *         （脚本用 -Role 参数自动传 -DRF_APP_ROLE，无需手改这里）
 *===========================================================================*/
#ifndef RF_APP_ROLE
#define RF_APP_ROLE             1
#endif

#define RF_ROLE_RX              0
#define RF_ROLE_TX              1

/*=============================================================================
 * 二、射频参数（收发两端必须完全一致，否则收不到）
 *===========================================================================*/

/** 通信频点，单位 kHz（范围 2400000 ~ 2483500）。
 *  ★ 单位容易搞错：官方例程里那个 TEST_FREQUENCY=16 是"频道号"式写法，
 *    而驱动内部按 kHz 判范围（反汇编 RFIP_SetChannel 实证），
 *    所以这里直接给 kHz 数值。BLE 广播频点参考：2402 / 2426 / 2480 MHz。 */
#define RF_APP_FREQ_KHZ         2402000UL

/** 接入地址（同步字）。收发两端必须相同。
 *  取值任意 32 位，但**不能是全 0 或全 1**，也不能与 BLE 广播地址
 *  (0x8E89BED6) 相同，避免与附近 BLE 设备互相干扰。 */
#define RF_APP_ACCESS_ADDR      0x71762345UL

/** CRC 初值与多项式。收发两端必须相同。
 *  多项式用 24 位 BLE 标准值（x^24+x^10+x^9+x^6+x^4+x^3+x+1）。 */
#define RF_APP_CRC_INIT         0x555555UL
#define RF_APP_CRC_POLY         0x80032DUL

/** 发射功率，可选值见 CH572rf.h 的 LL_TX_PWR_xxx_DBM（-25 ~ +6 dBm） */
#define RF_APP_TX_POWER         LL_TX_PWR_0_DBM

/** 射频模式：PHY_MODE_PHY_1M / PHY_MODE_PHY_2M / PHY_MODE_2G4
 *  纯私有协议建议 PHY_MODE_2G4；跟 BLE 物理层兼容时用 PHY_MODE_PHY_1M。 */
#define RF_APP_PHY_MODE         PHY_MODE_2G4

/** 收/发数据长度（每包字节数）。1M/2M PHY 上限 255。 */
#define RF_APP_PKT_LEN          16

/** 发送间隔（主循环里的粗延时，循环次数）。
 *  纯轮询，不用定时器；要精确周期请改用 TMR 中断（见官方 RF_Basic 例程）。 */
#define RF_APP_TX_GAP_LOOPS     2000000UL

/** 接收超时，单位 0.5us；0 = 不超时（一直等） */
#define RF_APP_RX_TIMEOUT       0UL

/** 收包 DMA 缓冲大小，要 >= RF_APP_PKT_LEN 且 4 字节对齐 */
#define RF_APP_RX_BUF_SIZE      64

/** 发送端：每包数据的填充基值（0xA0 起递增），便于接收端肉眼确认 */
#define RF_APP_TX_PATTERN_BASE  0xA0

/*=============================================================================
 * 三、自检 / 诊断
 *===========================================================================*/

/** 调试版串口（PA3 TX / PA2 RX，115200 8N1）
 *  注意：发布版编掉所有输出，连 UART 都不初始化。 */
#ifdef DEBUG
#include <stdio.h>
#define RFPRINT(X...)           printf(X)
#else
#define RFPRINT(X...)
#endif

#endif /* __RF_ROM_CFG_H */
