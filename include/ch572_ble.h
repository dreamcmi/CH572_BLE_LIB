/*******************************************************************************
 * 文件名称 : ch572_ble.h
 * 作    者 : CH572 BLE 静态库适配工程
 * 版    本 : V1.0
 * 日    期 : 2025
 * 描    述 : CH572 BLE 静态库的统一对外头文件（应用层唯一需要包含的 BLE 头文件）
 *
 *            本文件做了三件事：
 *              1) 汇总工程的可配置项（内存堆、SNV、发射功率、连接数等）并给出默认值；
 *              2) 引入官方固件库头文件 CH572BLEPeri_LIB.h（真正的协议栈 API 声明）；
 *              3) 定义"能力探测宏"，让应用代码可以在编译期判断哪些角色/特性可用，
 *                 从而让同一份上层代码既能编译 CH592（全功能库）也能编译 CH572（从机库）。
 *
 * 说    明 : 本工程不修改也不重新编译 WCH 的协议栈二进制，只是把官方
 *            libCH572BLE_PERI.a 重新整理、补齐工程化外壳并做 API 适配。
 ******************************************************************************/
#ifndef __CH572_BLE_H
#define __CH572_BLE_H

#ifdef __cplusplus
extern "C" {
#endif

#include "stdint.h"

/*=============================================================================
 * 零、内部辅助宏
 *===========================================================================*/

/**
 * @brief "该 API 在当前固件库中不存在"的统一报错宏。
 *
 * 原理：借 GCC 的 __attribute__((error)) 特性。只要有代码真正调用了被它
 *       修饰的函数，编译就会**立刻失败**并打印出中文提示，而不是把问题
 *       拖到链接期（一堆 undefined reference 很难定位）。
 *
 * 注意：这里只做声明、不给定义。因为该函数永远不应该被调用，所以链接器
 *       不会去找它的实体；一旦真有代码调用它，编译器会在编译期就报错。
 */
#if defined(__GNUC__) && !defined(__clang__)
__attribute__((error("CH572 BLE 固件库不提供该 API，请检查平台能力宏 CH572_BLE_HAS_xxx")))
void _ch572_ble_missing_api(const char *msg);
#else
#error "本工程的能力拦截宏依赖 GCC 的 __attribute__((error))，请使用 RISC-V GCC 编译"
#endif

/** 统一入口宏：任何位置调用都会在编译期报错 */
#define _CH572_BLE_MISSING_API_(msg) \
    do { _ch572_ble_missing_api(msg); } while (0)

/*=============================================================================
 * 一、固件库版本标识
 *===========================================================================*/

/** 本适配工程的版本号 */
#define CH572_BLE_ADAPTER_VERSION   "1.0.0"

/** 底层固件库版本号（与 CH572BLEPeri_LIB.h 中的 VER_FILE 保持一致） */
#define CH572_BLE_LIB_VERSION       "CH572_BLE_PERI_LIB_V1.3"

/** 编译期芯片标识，供上层区分平台使用 */
#define CH572_BLE_CHIP_CH572        1
#define CH572_BLE_CHIP_CH592        2

#ifndef CH572_BLE_CHIP
#define CH572_BLE_CHIP              CH572_BLE_CHIP_CH572
#endif

/*=============================================================================
 * 二、可配置项（可以在工程预定义宏里覆盖，也可以直接改这里的默认值）
 *===========================================================================*/

/**
 * @brief 协议栈使用的 RAM 堆大小（单位：字节）
 *        CH572 只有 12KB SRAM，这块堆是最大的单一 RAM 消耗项。
 *
 *        实测数据（ble_uart 示例、-Os 编译）：
 *          BLE_MEMHEAP_SIZE  静态占用(bss)   剩余给栈的空间
 *              6144            7296 字节         约  224 字节（偏紧！）
 *              5120            6272 字节         约 1248 字节（推荐）
 *
 *        官方说明要求"不小于 6K"，实测 5120 字节即可正常完成广播、连接、
 *        GATT 读写与 Notify（本工程的示例已验证）。若你的应用要开更大的
 *        MTU（BLE_BUFF_MAX_LEN > 27）或更多缓存包，请按每档 +512 递增，
 *        并用 build\out\*.map 里的 _ebss 与 .stack 余量确认没有压栈越界。
 */
#ifndef BLE_MEMHEAP_SIZE
#define BLE_MEMHEAP_SIZE            (512 * 10)
#endif

/**
 * @brief 是否使用自定义蓝牙 MAC 地址
 *        FALSE —— 使用芯片出厂固化的 MAC 地址（推荐，无需额外配置）
 *        TRUE  —— 使用 main.c 中定义的 MacAddr[6] 数组（小端序）
 */
#ifndef BLE_MAC
#define BLE_MAC                     TRUE 
#endif

/**
 * @brief 是否开启低功耗睡眠。开启后需要 HAL 层提供 RTC 与唤醒逻辑。
 */
#ifndef HAL_SLEEP
#define HAL_SLEEP                   FALSE
#endif

/**
 * @brief 是否开启 SNV（Simplified Non-Volatile）绑定信息存储。
 *        配对绑定信息会写进 DataFlash，掉电不丢。
 *        注意：CH572 的 BLE_SNV_ADDR 必须落在用户可用 Flash 范围内。
 */
#ifndef BLE_SNV
#define BLE_SNV                     TRUE
#endif

/** SNV 使用的 Flash 起始地址（紧邻 ROM 镜像之前，独占 4 KB 扇区） */
#ifndef BLE_SNV_ADDR
/* SNV 绑定信息存储区。
 *
 * ★ 这个地址必须与 ROM 方式（CH572BLE_ROM.h）里的 BLE_SNV_ADDR 保持一致，
 *   否则从静态库方式切到 ROM 方式时，已配对的绑定信息会因为地址不同而全部丢失。
 *   本工程的约定：SNV = 0x1F000 - 0x1FFFF（紧邻 ROM 镜像之前，预留 4 KB）。
 *
 * 说明：CH572 的 Flash **物理擦除粒度是 4 KB**（见 ISP572.h 与 hal/HAL.c 的
 *   FLASH_BLOCK_SIZE），不是 256 字节。绑定信息实际只用
 *   BLE_SNV_BLOCK × BLE_SNV_NUM = 256 × 1 = 256 字节，但仍必须独占一整个
 *   4 KB 扇区 —— HAL 不实现读改写(RMW)，多块共用扇区会互相擦掉。
 *   这里预留 4 KB 也顺便留足余量，以后扩展绑定功能不用再动整个 Flash 布局。 */
#define BLE_SNV_ADDR                (0x1F000)
#endif

/** SNV 块大小。必须是 4 字节 Flash 写入单位的整数倍（协议栈按 32 位字读写） */
#ifndef BLE_SNV_BLOCK
#define BLE_SNV_BLOCK               256
#endif

/** SNV 块数量 */
#ifndef BLE_SNV_NUM
#define BLE_SNV_NUM                 1
#endif

/**
 * @brief 单个连接的最大数据包长度。
 *        27 对应 ATT_MTU = 23（BLE 4.0 默认值）。
 *        要跑大数据吞吐可以调到 251，但会显著增加 RAM 占用。
 */
#ifndef BLE_BUFF_MAX_LEN
#define BLE_BUFF_MAX_LEN            27
#endif

/** 控制器缓存的收发包数量。必须大于同时建立的连接数。 */
#ifndef BLE_BUFF_NUM
#define BLE_BUFF_NUM                3
#endif

/** 每个连接事件最多发送几个数据包（提高吞吐可设为 4~6，但更耗电） */
#ifndef BLE_TX_NUM_EVENT
#define BLE_TX_NUM_EVENT            1
#endif

/** 发射功率，可选值见 CH572BLEPeri_LIB.h 中的 LL_TX_PWR_xxx 宏 */
#ifndef BLE_TX_POWER
#define BLE_TX_POWER                LL_TX_PWR_0_DBM
#endif

/** 是否开启定时频偏校准 */
#ifndef BLE_CALIBRATION_ENABLE
#define BLE_CALIBRATION_ENABLE      TRUE
#endif

/** 定时校准周期，单位毫秒 */
#ifndef BLE_CALIBRATION_PERIOD
#define BLE_CALIBRATION_PERIOD      120000
#endif

/** 是否开启温度采样校准 */
#ifndef TEM_SAMPLE
#define TEM_SAMPLE                  TRUE
#endif

/** 32K RTC 时钟源：0=外部 32768Hz，1=内部 32000Hz，2=内部 32768Hz */
#ifndef CLK_OSC32K
#define CLK_OSC32K                  1
#endif

/**
 * @brief 从机角色最大同时连接数。
 *        CH572 的从机库只支持单连接，这个宏必须保持为 1。
 */
#ifndef PERIPHERAL_MAX_CONNECTION
#define PERIPHERAL_MAX_CONNECTION   1
#endif

/*=============================================================================
 * 三、引入官方固件库头文件
 *
 * 说明：官方头文件 CH572BLEPeri_LIB.h 用 GBK 编码保存，其中包含了完整的
 *       协议栈 API 声明（TMOS / GAP / GATT / L2CAP / SM / LL / RF 等）。
 *       本工程不改动它，直接原样引用。
 *===========================================================================*/
#include "CH572BLEPeri_LIB.h"

/*=============================================================================
 * 四、能力探测宏（Capability Probe）
 *
 * 背景：WCH 给 CH592 提供的 libCH59xBLE.a 是"全功能库"，包含
 *       Central(主机) / Peripheral(从机) / Observer(观察者) / Broadcaster(广播者)
 *       四种角色；而给 CH572 提供的 libCH572BLE_PERI.a 是"从机库"，只包含
 *       Peripheral 与 Broadcaster 角色。
 *
 *       为了让上层代码可移植，这里用宏把"能力"显式表达出来。
 *       应用代码应当写成：
 *
 *           #if CH572_BLE_HAS_CENTRAL
 *               GAPRole_CentralInit();
 *           #else
 *               #error "本平台不支持主机角色"
 *           #endif
 *
 *       这样换平台时编译期就能发现问题，而不是链接期报一堆 undefined reference。
 *===========================================================================*/

/** 从机角色（Peripheral）—— CH572 支持 */
#define CH572_BLE_HAS_PERIPHERAL        1

/** 广播者角色（Broadcaster，只发广播不连接）—— CH572 支持 */
#define CH572_BLE_HAS_BROADCASTER       1

/** 主机角色（Central，主动扫描并发起连接）—— CH572 不支持 */
#define CH572_BLE_HAS_CENTRAL           0

/** 观察者角色（Observer，只扫描不连接）—— CH572 不支持 */
#define CH572_BLE_HAS_OBSERVER          0

/** GATT 客户端（Client，发现/读写对端服务）—— CH572 不支持 */
#define CH572_BLE_HAS_GATT_CLIENT       0

/** 周期广播同步（Periodic Advertising Sync）—— CH572 不支持 */
#define CH572_BLE_HAS_PERIODIC_SYNC     0

/** 扩展广播（Extended Advertising，BLE 5.0 长广播包）—— CH572 不支持 */
#define CH572_BLE_HAS_EXT_ADV           0

/** 2.4G 私有协议跳频（RF_FrequencyHopping*）—— CH572 不支持 */
#define CH572_BLE_HAS_RF_HOPPING        0

/** 多从机并发（同时被多个主机连接）—— CH572 不支持 */
#define CH572_BLE_HAS_MULTI_PERIPHERAL  0

/*=============================================================================
 * 五、缺少的 API 统一拦截
 *
 * 作用：当应用误用了 CH572 不提供的 API 时，给出明确的编译错误信息，
 *       而不是让链接器抛出难懂的 "undefined reference to ..."。
 *
 * 用法：应用代码里如果写了 GAPRole_CentralInit()，编译时就会看到
 *       "CH572 固件库不含主机角色 API" 的提示。
 *===========================================================================*/

#if !CH572_BLE_HAS_CENTRAL
/* ---- 主机角色相关 API ---- */
#define GAPRole_CentralInit() \
    _CH572_BLE_MISSING_API_("CH572 固件库不含主机角色 API：GAPRole_CentralInit")
#define GAPRole_CentralStartDevice(a, b, c) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含主机角色 API：GAPRole_CentralStartDevice")
#define GAPRole_CentralStartDiscovery(a, b, c, d, e) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含主机角色 API：GAPRole_CentralStartDiscovery")
#define GAPRole_CentralCancelDiscovery() \
    _CH572_BLE_MISSING_API_("CH572 固件库不含主机角色 API：GAPRole_CentralCancelDiscovery")
#define GAPRole_CentralEstablishLink(a, b, c, d, e, f) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含主机角色 API：GAPRole_CentralEstablishLink")
#endif

#if !CH572_BLE_HAS_OBSERVER
/* ---- 观察者角色相关 API ---- */
#define GAPRole_ObserverInit() \
    _CH572_BLE_MISSING_API_("CH572 固件库不含观察者角色 API：GAPRole_ObserverInit")
#define GAPRole_ObserverStartDevice(a, b, c) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含观察者角色 API：GAPRole_ObserverStartDevice")
#define GAPRole_ObserverStartDiscovery(a, b, c, d, e) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含观察者角色 API：GAPRole_ObserverStartDiscovery")
#define GAPRole_ObserverCancelDiscovery() \
    _CH572_BLE_MISSING_API_("CH572 固件库不含观察者角色 API：GAPRole_ObserverCancelDiscovery")
#endif

#if !CH572_BLE_HAS_GATT_CLIENT
/* ---- GATT 客户端相关 API ---- */
#define GATT_InitClient() \
    _CH572_BLE_MISSING_API_("CH572 固件库不含 GATT 客户端 API：GATT_InitClient")
#define GATT_DiscAllPrimaryServices(a, b, c) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含 GATT 客户端 API：GATT_DiscAllPrimaryServices")
#define GATT_DiscAllChars(a, b, c) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含 GATT 客户端 API：GATT_DiscAllChars")
#define GATT_ReadCharValue(a, b, c) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含 GATT 客户端 API：GATT_ReadCharValue")
#define GATT_WriteCharValue(a, b, c) \
    _CH572_BLE_MISSING_API_("CH572 固件库不含 GATT 客户端 API：GATT_WriteCharValue")
#endif

/*=============================================================================
 * 六、CH592 风格别名
 *
 * 让习惯 CH59x 命名习惯的代码可以少改一点。
 *===========================================================================*/

/** CH592 里叫 GAPRole_PeripheralInit，CH572 里也叫这个名字，无需别名 */
/** 宏开关名称差异：CH592 用 CH59xBLE_ROM 选择 ROM 版符号表，
 *  CH572 没有 ROM 版，统一按 Flash 静态库方式链接，因此屏蔽该开关。 */
#ifdef CH59xBLE_ROM
#undef CH59xBLE_ROM
#endif

/*=============================================================================
 * 七、常用全局对象声明
 *===========================================================================*/

/** 协议栈内存堆，由应用在 main.c 中定义（必须 4 字节对齐） */
extern uint32_t MEM_BUF[BLE_MEMHEAP_SIZE / 4];

#if (defined(BLE_MAC)) && (BLE_MAC == TRUE)
/** 自定义蓝牙 MAC 地址，由应用在 main.c 中定义，小端序 */
extern const uint8_t MacAddr[6];
#endif

#ifdef __cplusplus
}
#endif

#endif /* __CH572_BLE_H__ */
