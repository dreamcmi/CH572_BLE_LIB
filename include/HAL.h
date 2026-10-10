/*******************************************************************************
 * 文件名称 : HAL.h
 * 描    述 : 硬件抽象层对外接口。
 *
 *            HAL 层负责三件事：
 *              1) 把 BLE 协议栈需要的"硬件相关回调"注册进去（随机数、空闲、
 *                 Flash 读写、时钟）；
 *              2) 初始化协议栈内存堆并调用固件库的 BLE_LibInit()；
 *              3) 作为一个 TMOS 任务参与调度，处理需要周期运行的杂务。
 ******************************************************************************/
#ifndef __HAL_H
#define __HAL_H

#ifdef __cplusplus
extern "C" {
#endif

#include "CONFIG.h"
#include "RTC.h"
#include "SLEEP.h"

/*----------------------------------------------------------------------------
 * HAL 任务事件位
 *
 * ★ 只保留本工程真正会排程并处理的事件位 ★
 *
 * 这里以前还借鉴官方模板声明了 LED_BLINK_EVENT(0x0001) 和
 * HAL_KEY_EVENT(0x0002)，但本工程既没有 LED 也没有按键支持，
 * HAL_ProcessEvent() 里也没有对应的处理分支。这种"声明了但没人处理"
 * 的事件位很危险：TMOS 要求任务处理完就清位，而 HAL_ProcessEvent 只清掉
 * 自己认识的那几位，一旦谁给 HAL 排了一个没人处理的位，那一位永远不会被清，
 * TMOS 会把 HAL 任务当成"一直在忙"，主循环被它占满、BLE 直接饿死。
 * 所以不需要的事件位不在这里声明 —— 想加事件就得同时在 HAL_ProcessEvent 里加分支。
 *--------------------------------------------------------------------------*/
#define HAL_REG_INIT_EVENT          0x2000  /*!< 射频寄存器初始化 / 周期校准 */
#define HAL_TEST_EVENT              0x4000  /*!< 预留测试事件       */

/*----------------------------------------------------------------------------
 * 全局变量
 *--------------------------------------------------------------------------*/

/** HAL 任务的 TMOS 任务号 */
extern tmosTaskID halTaskID;

/*----------------------------------------------------------------------------
 * 对外函数
 *--------------------------------------------------------------------------*/

/**
 * @brief   初始化 BLE 协议栈（内存堆、配置结构体、硬件回调）
 * @note    必须在 TMOS 相关 API 之前调用；调用前应先配置好系统时钟。
 */
extern void CH57x_BLEInit(void);

/**
 * @brief   初始化 HAL 任务（注册任务号、启动周期性事件）
 */
extern void HAL_Init(void);

/**
 * @brief   HAL 任务事件处理函数
 * @param   task_id - TMOS 分配的任务号
 * @param   events  - 待处理事件位图
 * @return  未处理完的事件位
 */
extern tmosEvents HAL_ProcessEvent(tmosTaskID task_id, tmosEvents events);

#ifdef __cplusplus
}
#endif


/*********************************************************************
 * RTC 中断处理函数声明
 *
 * @note  实现由官方 HAL/RTC.c 提供（那个文件本工程未做任何修改）。
 *        ROM 方式下应用需要把这个入口注册给 ROM 的钩子表：
 *            rom_app_hooks_t.rtc_irq_handler = RTC_IRQHandler;
 *        所以这里给出声明，避免应用侧再自己写 extern。
 *
 *        放在本工程自己的 HAL.h 里，而不是去改官方 RTC.h ——
 *        保持官方独立文件原样。
 *********************************************************************/
extern void RTC_IRQHandler(void);


/*********************************************************************
 * 运行时诊断接口（仅 DEBUG 构建有效，实现见 debug/debug_heartbeat.c）
 *
 * 用途：程序"跑着跑着不动了"时，靠定期心跳区分
 *         (a) 真的卡死了
 *         (b) 程序在跑，但 BLE 中断没接上 / 处理函数是空的
 *********************************************************************/
#ifdef DEBUG
extern volatile uint32_t g_dbgBleIrqCount;      /* BLE 中断计数 */
extern volatile uint32_t g_dbgHeartbeat;        /* 心跳计数 */

/** 打印一次运行时诊断快照（由 HAL 周期事件调用） */
extern void Debug_PrintSnapshot(void);

/** 判断某地址处是否像是有代码（用来查 ROM 的中断入口是否为空） */
extern uint32_t Debug_LooksLikeCode(uint32_t addr);
#endif


/** 协议栈内部使用的链路层中断入口地址（定义在 hal/HAL.c）。
 *  官方 CH572 的 MCU.c 里也有这个全局变量，协议栈会读它。 */
extern uint32_t g_LLE_IRQLibHandlerLocation;


/** BLE 中断计数包装（累加 g_dbgBleIrqCount 后转调 ROM 的真 ISR） */
extern void Debug_BbIrqHook(void);
extern void Debug_LleIrqHook(void);


/** 卡死定位器：在 SysTick 中断里采样 PC，判定卡死后直接打印现场 */
extern void Debug_SysTickSample(void);

/*********************************************************************
 * ROM 方式专用：PFIC VTF 维护（实现见 hal/rom_irq.c）
 *
 * CH572 的 BLEB/BLEL 走 PFIC 的 VTF 通道 #2/#3 而不是中断向量表。
 * 协议栈每次 BLE_RegInit()（周期校准）都会调用 LLE_DevInit/BB_DevInit，
 * 从而把 VTFADDR 重写回 ROM 内的真 ISR —— 这会覆盖应用侧的计数包装。
 *
 * HAL_RomVtfRefresh() 检查 VTFADDR 是否仍指向应用包装，不对就重新武装。
 * 仅 DEBUG 构建真正动作；发布构建直接返回 0，不碰 VTF。
 *
 * 调用点：main() 首次武装 + HAL_ProcessEvent() 周期校准之后。
 *********************************************************************/
extern uint8_t HAL_RomVtfRefresh(void);

#endif /* __HAL_H */
