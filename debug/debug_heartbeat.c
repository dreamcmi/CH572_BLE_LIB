/*******************************************************************************
 * 文件名称 : debug_heartbeat.c
 * 描    述 : 运行时诊断 —— 定期心跳、BLE 中断计数、中断入口存活检查。
 *
 *            为什么要它：
 *              出现"协议栈初始化全过、广播日志正常，但射频不工作"时，
 *              光看初始化日志分不清是
 *                (a) 程序卡死了
 *                (b) 程序在正常跑，但 BLE 中断没接上 / 处理函数是空的
 *              定期打印就能直接区分：
 *                * 心跳还在涨 → 程序没死，问题在中断或射频
 *                * 心跳停了   → 卡死，最后一次打印之后的位置就是嫌疑
 *
 *            ★ 条件编译约定（很重要，别改乱）★
 *
 *              g_dbgBleIrqCount / g_dbgHeartbeat   定义  → 无条件
 *              Debug_PrintSnapshot / LooksLikeCode 定义  → 仅 DEBUG
 *              Debug_BbIrqHook / Debug_LleIrqHook  定义  → 无条件（ROM 方式）
 *
 *              为什么 hook 要无条件：main_rom.c 无条件把它们注册进
 *              ROM 的钩子表，发布版（无 DEBUG）也必须能链接。
 *              只有"累加计数"那一句在发布版里被去掉。
 *
 *            ★ 注意：VTF。CH572 的 BLEB/BLEL 走 PFIC VTF 而不走向量表，
 *              默认状态下硬件直接从 ROM 里的真 ISR 进入，本文件的两个 hook
 *              不会被调用、计数恒为 0。调试构建由 main_rom.c 重新武装 VTF
 *              指向应用包装函数后计数才有效。详见文件末尾的长注释。
 ******************************************************************************/
#include "CONFIG.h"

/* ===========================================================================
 * 全局计数器 —— 定义放在所有 #ifdef 之外
 * =========================================================================== */

/** BLE 中断计数：中断每进一次 +1。
 *  一直不动就说明射频中断根本没进来（向量表没装对 / 中断被屏蔽）。 */
volatile uint32_t g_dbgBleIrqCount = 0;

#ifdef DEBUG

/** 心跳计数：每 tick +1，用来判断程序是否还在跑 */
volatile uint32_t g_dbgHeartbeat = 0;

/** 上一次心跳时刻，用来计算实际间隔 */
volatile uint32_t g_dbgLastTick = 0;

/** 观测到的最大心跳间隔（间隔异常变大说明有阻塞） */
volatile uint32_t g_dbgMaxGap = 0;

/*********************************************************************
 * @fn      Debug_LooksLikeCode
 *
 * @brief   判断一个地址处是否"像是有代码"。
 *
 * @note    依据：RISC-V 里全 0（0x00000000）和全 FF（0xFFFFFFFF）都不是
 *          合法指令。若目标处是这两者之一，几乎可以肯定那里没有代码。
 *
 *          这是 ROM 方式下的关键检查：协议栈的 .highcode 若没被搬进 RAM，
 *          那些 RAM 地址读出来就是 0x00000000 —— 而调用方仍会 jalr 过去。
 *
 * @param   addr - 待检查地址（应为 RAM 中的函数入口）
 * @return  1 = 像是有代码；0 = 空白或不在 RAM 范围
 */
uint32_t Debug_LooksLikeCode(uint32_t addr)
{
    uint32_t w0, w1;

    /* 只对"可执行存储区"做判断：
     *   RAM  : 0x20000000 - 0x20003000（应用高速代码 + ROM 变量区）
     *   Flash: 0x00000000 - 0x00040000（应用固件 + ROM 镜像）
     * 其它地址（0 之类）直接算无效。 */
    if(!((addr >= 0x20000000 && addr <= 0x20002FF8) ||
         (addr > 0 && addr <= 0x0003FFF8)))
    {
        return 0;
    }

    /* RISC-V function entries can be aligned to two bytes. Read bytes so the
     * diagnostic itself cannot fault on an unaligned 32-bit load. */
    const volatile uint8_t *code = (const volatile uint8_t *)addr;
    w0 = code[0] | ((uint32_t)code[1] << 8) |
         ((uint32_t)code[2] << 16) | ((uint32_t)code[3] << 24);
    w1 = code[4] | ((uint32_t)code[5] << 8) |
         ((uint32_t)code[6] << 16) | ((uint32_t)code[7] << 24);

    if((w0 == 0x00000000 && w1 == 0x00000000) ||
       (w0 == 0xFFFFFFFF && w1 == 0xFFFFFFFF))
    {
        return 0;       /* 空白 */
    }
    return 1;
}

/*********************************************************************
 * @fn      Debug_PrintSnapshot
 *
 * @brief   打印一次运行时诊断快照（由 HAL 的周期事件调用）。
 *
 * @note    串口是轮询发送，这里不会被中断打断，安全。
 */
void Debug_PrintSnapshot(void)
{
    uint32_t now = SYS_GetSysTickCnt();
    uint32_t gap = 0;

    if(g_dbgLastTick)
    {
        gap = now - g_dbgLastTick;
        if(gap > g_dbgMaxGap)
        {
            g_dbgMaxGap = gap;
        }
    }
    g_dbgLastTick = now;
    g_dbgHeartbeat++;

    PRINT("[心跳 %u] tick=%u  间隔=%u  BLE中断=%u\n",
          (unsigned)g_dbgHeartbeat,
          (unsigned)(now / 100000),     /* 100MHz -> 10ms 单位 */
          (unsigned)gap,
          (unsigned)g_dbgBleIrqCount);

#ifdef CH572BLE_ROM
    /* ---- ROM 方式特有：中断究竟从哪里进 ----
     *
     * ★★ 关键事实（第六次实机排查结论）★★
     *   CH572 的 BLEB/BLEL **不走中断向量表**，走 PFIC 的 VTF(Vector-Table-Free)。
     *   协议栈自己在 LLE_DevInit / BB_DevInit 里写入：
     *       VTFIDR[2] = BLEL_IRQn(21)   VTFADDR[2] = LLE_IRQFIOFHandler | ENABLE
     *       VTFIDR[3] = BLEB_IRQn(20)   VTFADDR[3] = BB_IRQLibFunction  | ENABLE
     *   所以下面这一行才是真正在跑的中断入口。
     *
     *   _vector_base[BLEB_IRQn]/[BLEL_IRQn] 是死项（VTF 优先取用），
     *   只在 VTF 万一没生效时才会被硬件取到 —— 一并打出来便于对照。
     */
    {
        extern uint32_t _vector_base[];
        const rom_handles_t *h = CH572BLE_ROM_GetHandles();

        if(h && h->magic == ROM_HANDLE_MAGIC)
        {
            PRINT("      VTF 入口: [2]=0x%08X %s  [3]=0x%08X %s\n",
                  (unsigned)PFIC->VTFADDR[2],
                  Debug_LooksLikeCode(PFIC->VTFADDR[2] & 0xFFFFFFFEu) ? "(有代码)" : "(空白)",
                  (unsigned)PFIC->VTFADDR[3],
                  Debug_LooksLikeCode(PFIC->VTFADDR[3] & 0xFFFFFFFEu) ? "(有代码)" : "(空白)");
            PRINT("      VTF 中断号: [2]=%u  [3]=%u（期望 %d / %d）\n",
                  (unsigned)PFIC->VTFIDR[2], (unsigned)PFIC->VTFIDR[3],
                  (int)BLEL_IRQn, (int)BLEB_IRQn);
        }
        else
        {
            PRINT("      ROM 句柄表 magic 不对，拿不到中断入口\n");
        }

        PRINT("      向量表[%d]=0x%08X [%d]=0x%08X（VTF 生效时不走这里）\n",
              (int)BLEB_IRQn, (unsigned)_vector_base[BLEB_IRQn],
              (int)BLEL_IRQn, (unsigned)_vector_base[BLEL_IRQn]);
    }
#endif

    PRINT("      MEM_BUF=%p 堆=%u 最大间隔=%u\n",
          (void *)MEM_BUF, (unsigned)BLE_MEMHEAP_SIZE, (unsigned)g_dbgMaxGap);
}

#endif /* DEBUG */

/* ===========================================================================
 * BLE 中断计数包装
 *
 * ★ 不受 DEBUG 控制 ★ —— main_rom.c 无条件注册它们，发布版也要能链接。
 *
 * ★★ 为什么必须重新武装 VTF 才能计数（第六次实机排查结论）★★
 *
 *   CH572 的 BLEB/BLEL 不走中断向量表，走 PFIC VTF：
 *       LLE_DevInit: VTFIDR[2]=21, VTFADDR[2]=LLE_IRQFIOFHandler  (ROM 内)
 *       BB_DevInit : VTFIDR[3]=20, VTFADDR[3]=BB_IRQLibFunction   (ROM 内)
 *   因此硬件默认直接从 ROM 里的真 ISR 进入，本文件的两个 hook
 *   **一次也不会被调用**，g_dbgBleIrqCount 会永远停在 0。
 *
 *   main_rom.c 的 InstallRomIrqHandlers() 在调试构建里重新把
 *   VTFADDR[2]/[3] 指向应用自己的 rom_irq.c 包装函数，计数才有意义：
 *
 *     VTFADDR[2] -> LLE_IRQHandler(__INTERRUPT/mret, 在 .highcode)
 *                       -> Debug_LleIrqHook（计数）
 *                            -> ROM 的 lle_lib_irq（协议栈真 ISR, ret）
 *
 *   发布构建不重新武装，保持 ROM 出厂路径（少一层转接）。
 *
 *   计数一直为 0        -> 中断根本没进来（VTF / 使能 / 射频问题）
 *   计数在涨但功能不正常 -> 问题在协议栈内部
 *
 *   本函数是应用侧普通 C 函数（位于 RAM），不在 ROM 里，
 *   所以不会出现"转接桩调自己"的递归。
 * =========================================================================== */
#ifdef CH572BLE_ROM

void Debug_BbIrqHook(void)
{
    const rom_handles_t *h;

#ifdef DEBUG
    g_dbgBleIrqCount++;
#endif

    h = CH572BLE_ROM_GetHandles();
    if(h && h->magic == ROM_HANDLE_MAGIC && h->bb_lib_irq)
    {
        h->bb_lib_irq();
    }
}

void Debug_LleIrqHook(void)
{
    const rom_handles_t *h;

#ifdef DEBUG
    g_dbgBleIrqCount++;
#endif

    h = CH572BLE_ROM_GetHandles();
    if(h && h->magic == ROM_HANDLE_MAGIC && h->lle_lib_irq)
    {
        h->lle_lib_irq();
    }
}

#endif /* CH572BLE_ROM */

/******************************** endfile @ debug_heartbeat *******************/
