/* =============================================================================
 *  rom_support.c —— ROM 镜像的"自包含支撑层"
 *
 *  作用：BLE 协议栈对象文件（官方 libCH572BLE_PERI.a）在链接成固定地址镜像时，
 *        会引用一批"外部"符号。这些符号必须由镜像自己提供，否则 ROM 里的代码
 *        一旦被调用就会跳到无效地址。
 *
 *  这些符号分三类：
 *    1. 中断处理函数（RTC_IRQHandler 等）—— 协议栈要求它们存在于固定位置
 *    2. 编译器运行时（memcpy / __udivdi3 ...）—— 由 libc / libgcc 提供，
 *       但此处协议栈要求可重定位版本，故一并打包进镜像
 *    3. 与主板/应用相关的钩子 —— ROM 里只能做成"函数指针表"，由应用在启动时
 *       注册自己的实现（这是 ROM 方案的关键设计：ROM 不能链接到应用）
 * ========================================================================== */

#include <stdint.h>
#include <string.h>
#include "CH57x_common.h"

/* ---------------------------------------------------------------------------
 * 应用钩子表
 *
 * ROM 镜像不能直接调用应用代码（两者独立编译、独立链接），
 * 因此所有"需要应用提供实现"的回调都通过这张表间接调用。
 * 应用在 main() 最开始调用 CH572BLE_ROM_Init() 注册自己的实现。
 * ------------------------------------------------------------------------- */
typedef struct
{
    uint32_t magic;                     /* 有效性标志，防止未初始化就使用 */
    void (*rtc_irq_handler)(void);
    void (*ble_irq_handler)(void);      /* BB 中断 */
    void (*lle_irq_handler)(void);      /* LLE 中断 */
    void (*sys_tick_handler)(void);
    void (*uart_irq_handler)(void);
    void (*gpio_irq_handler)(void);
    void (*tmr_irq_handler)(void);
    void (*i2c_irq_handler)(void);
    void (*spi_irq_handler)(void);
    void (*pwm_irq_handler)(void);
    void (*keyscan_irq_handler)(void);
} rom_app_hooks_t;

#define ROM_HOOK_MAGIC      0x43483537U   /* 'CH57' */

rom_app_hooks_t g_romAppHooks = {0};

/* ---------------------------------------------------------------------------
 * ROM 中断处理入口地址导出
 *
 * 为什么需要这个：
 *   BLE 的基带中断(GPIOA 之后第 4 项，即 BB_IRQn)与链路层中断(LLE_IRQn)的处理函数
 *   实现在 ROM 里（BB_IRQLibHandler / LLE_IRQLibHandler）。应用必须把它们装进
 *   自己的中断向量表，否则射频中断触发后会跳到应用的弱定义死循环里，
 *   BLE 完全无法工作。
 *
 *   WCH 官方 CH59x 的做法是把这两个地址写进 RAM 固定地址（BLE_LIB_RAM_JT），
 *   应用用 *(uint32_t*)(0x20000000 + n*4) 取。本工程改成更直白的做法：
 *   由 ROM 导出一个函数，应用调用一次就能拿到全部需要的入口地址，
 *   然后填进自己的向量表。这样不占用 RAM，也不依赖固定地址约定。
 * ------------------------------------------------------------------------- */
typedef struct
{
    uint32_t magic;                 /* 有效标志，见 ROM_HANDLE_MAGIC */
    void (*bb_irq)(void);           /* 基带中断转接桩（装到 BB_IRQn） */
    void (*lle_irq)(void);          /* 链路层中断转接桩（装到 LLE_IRQn） */
    uint32_t (*tmos_process)(void); /* 预留：TMOS 主循环入口 */
    /* ★ 协议栈内部真正的中断服务程序入口。
     * 官方 CH572 的 MCU.c 会把 LLE_IRQLibHandler 的地址赋给
     * g_LLE_IRQLibHandlerLocation 供协议栈内部使用，ROM 方式下必须导出来。
     * 注意它与上面的 lle_irq 不是一回事：
     *   lle_irq        = 向量表里的转接桩（会转调应用注册的钩子）
     *   lle_lib_irq    = 协议栈内部的链路层中断服务程序本体 */
    void (*lle_lib_irq)(void);
    void (*bb_lib_irq)(void);
} rom_handles_t;

#define ROM_HANDLE_MAGIC    0x524F4D48U     /* 'ROMH' */

/* BB_IRQHandler / LLE_IRQHandler 定义在下面（先声明，后取地址） */
void BB_IRQHandler(void);
void LLE_IRQHandler(void);

/* BB_IRQLibHandler / LLE_IRQLibHandler 是协议栈导出的内部中断服务程序，
 * 直接取它们的地址填进句柄表。 */
extern void BB_IRQLibHandler(void);
extern void LLE_IRQLibHandler(void);

static const rom_handles_t g_romHandles = {
    ROM_HANDLE_MAGIC,
    BB_IRQHandler,          /* 转接桩 */
    LLE_IRQHandler,         /* 转接桩 */
    0,                      /* tmos_process 预留 */
    LLE_IRQLibHandler,      /* ★ 协议栈内部 ISR，应用要用它设 g_LLE_IRQLibHandlerLocation */
    BB_IRQLibHandler,       /* 同上（基带） */
};

/*********************************************************************
 * @fn      CH572BLE_ROM_GetHandles
 *
 * @brief   取得 ROM 内部中断处理入口地址。
 *
 * @return  指向 rom_handles_t 的指针（只读，不要修改）
 *
 * @note    应用在装中断向量表时调用，例如：
 *              const rom_handles_t *h = CH572BLE_ROM_GetHandles();
 *              h->bb_irq();   // 或把 h->bb_irq 填进向量表
 */
const rom_handles_t *CH572BLE_ROM_GetHandles(void)
{
    return &g_romHandles;
}

/*********************************************************************
 * @fn      CH572BLE_ROM_Init
 * @brief   由应用调用，注册中断处理钩子并返回 ROM 版本号
 * @return  ROM 库版本字符串指针
 *
 * @note    为什么需要它：
 *          ROM 里的协议栈在运行中会触发 BLE 基带中断与链路层中断，
 *          而中断处理函数位于 ROM 内部。应用必须把这两个入口装到自己的
 *          中断向量表（或通过本钩子表转接），否则射频中断触发后会落到
 *          应用启动文件里的弱定义死循环，BLE 完全不工作。
 */
const char *CH572BLE_ROM_Init(rom_app_hooks_t *hooks)
{
    extern const char _rom_version_str[];

    if (hooks && hooks->magic == ROM_HOOK_MAGIC)
    {
        g_romAppHooks = *hooks;
    }
    return _rom_version_str;
}

/*********************************************************************
 * 只有 BLE 相关的两个中断需要 ROM 转接：
 *   - BB_IRQHandler  （基带中断）：协议栈内部逻辑会用到，必须由 ROM 提供
 *   - LLE_IRQHandler （链路层中断）：同上
 *
 * 其余外设中断（RTC / UART / GPIO / TMR ...）与 BLE 协议栈无关，
 * 由应用自己的驱动直接实现即可，ROM 不参与，避免和应用的实现重复定义。
 *********************************************************************/
#define ROM_IRQ_STUB(name, field)                       \
    __INTERRUPT __HIGH_CODE void name(void)              \
    {                                                   \
        if (g_romAppHooks.magic == ROM_HOOK_MAGIC &&    \
            g_romAppHooks.field != 0)                   \
        {                                               \
            g_romAppHooks.field();                      \
            return;                                     \
        }                                               \
        while (1) { }                                   \
    }

ROM_IRQ_STUB(BB_IRQHandler,     ble_irq_handler)
ROM_IRQ_STUB(LLE_IRQHandler,    lle_irq_handler)


/******************************** endfile @ rom_support ***********************/
