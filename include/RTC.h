/********************************** (C) COPYRIGHT *******************************
 * File Name          : RTC.h
 * Author             : WCH
 * Version            : V1.0
 * Date               : 2016/04/12
 * Description        :
 *********************************************************************************
 * Copyright (c) 2021 Nanjing Qinheng Microelectronics Co., Ltd.
 * Attention: This software (modified or not) and binary are used for 
 * microcontroller manufactured by Nanjing Qinheng Microelectronics.
 *******************************************************************************/

/******************************************************************************/
#ifndef __RTC_H
#define __RTC_H

#ifdef __cplusplus
extern "C" {
#endif

#ifdef CLK_OSC32K
#if (CLK_OSC32K==1)
#define FREQ_RTC    32000
#else
#define FREQ_RTC    32768
#endif
#endif /* CLK_OSC32K */


#define CLK_PER_US                  (1.0 / ((1.0 / Freq_LSI) * 1000 * 1000))
#define CLK_PER_MS                  (CLK_PER_US * 1000)

#define US_PER_CLK                  (1.0 / CLK_PER_US)
#define MS_PER_CLK                  (US_PER_CLK / 1000.0)

#define RTC_TO_US(clk)              ((uint32_t)((clk) * US_PER_CLK + 0.5))
#define RTC_TO_MS(clk)              ((uint32_t)((clk) * MS_PER_CLK + 0.5))

#define US_TO_RTC(us)               ((uint32_t)((us) * CLK_PER_US + 0.5))
#define MS_TO_RTC(ms)               ((uint32_t)((ms) * CLK_PER_MS + 0.5))

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

extern volatile uint32_t RTCTigFlag;
extern uint32_t lsiFrq;

/**
 * @brief   Initialize time Service.
 */
void HAL_TimeInit(void);

/**
 * @brief   配置RTC触发时间
 *
 * @param   time    - 触发时间.
 */
extern void RTC_SetTignTime(uint32_t time);

/**
 * @brief   配置BLE相关时间
 *
 * @param   lsifreq    - LSI频率.
 */
extern bleClockConfig_t BLE_ClockConfig(uint32_t lsifreq);

#ifdef __cplusplus
}
#endif

#endif
