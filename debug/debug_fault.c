/*******************************************************************************
 * 文件名称 : debug_fault.c
 * 描    述 : HardFault / NMI 现场打印。
 *
 *            用途：程序"跑着跑着不动了"时，靠它把出事时的关键寄存器打出来，
 *                  而不是只看到一片沉默。
 *
 *            打印内容：
 *              mcause / mepc / mtval   出错原因与出错指令地址
 *              SP / RA                 当前栈指针与返回地址
 *              [SP+0..+64]             栈顶 64 字节（可回溯调用链的线索）
 *
 *            实现要点：
 *              用 __attribute__((interrupt("WCH-Interrupt-fast"))) 让编译器
 *              负责现场保存；再取一个**栈上局部变量的地址**当锚点，
 *              所有现场数据都用相对该锚点的偏移读取 —— 这样不依赖
 *              "入口时 SP 是多少"这个假设，最稳妥。
 *
 *            注意：这里只能用 PRINT（轮询式串口发送），不能用任何
 *                  需要中断/动态内存的东西，否则会二次异常。
 ******************************************************************************/
#include "CONFIG.h"

#ifdef DEBUG

/* 栈现场读取辅助。
 *
 * 注意用**指针**而不是把局部变量当数组下标 —— 后者会让编译器以为越界访问
 * （sp_anchor 只有 4 字节），报一堆 -Warray-bounds 警告。
 * 这里用内联汇编取当前 SP 到指针里，再按偏移读，语义清晰也无警告。 */
#define FAULT_SLOT(n)   (((volatile uint32_t *)fault_sp)[(n)])

void HardFault_Handler(void) __attribute__((interrupt("WCH-Interrupt-fast")));
void HardFault_Handler(void)
{
    uint32_t *fault_sp;     /* 栈锚点：后面所有读取都相对它 */
    uint32_t mcause, mepc, mtval, ra, sp;

    __asm__ volatile("mv %0, ra" : "=r"(ra));
    __asm__ volatile("mv %0, sp" : "=r"(sp));
    fault_sp = (uint32_t *)sp;
    mcause = __get_MCAUSE();
    mepc   = __get_MEPC();
    mtval  = __get_MTVAL();

    PRINT("\n");
    PRINT("========================================\n");
    PRINT("  ★★★ HardFault ★★★\n");
    PRINT("========================================\n");
    PRINT("  mcause = 0x%08X\n", (unsigned)mcause);
    PRINT("  mepc   = 0x%08X\n", (unsigned)mepc);
    PRINT("  mtval  = 0x%08X\n", (unsigned)mtval);
    PRINT("  SP     = 0x%08X\n", (unsigned)sp);
    PRINT("  RA     = 0x%08X\n", (unsigned)ra);
    PRINT("----------------------------------------\n");
    PRINT("  栈现场（相对锚点，每行 4 个字）：\n");
    {
        int i;
        for(i = 0; i < 16; i++)
        {
            PRINT("    [+%02X] %08X %08X %08X %08X\n",
                  (unsigned)(i * 16),
                  (unsigned)FAULT_SLOT(i * 4 + 0),
                  (unsigned)FAULT_SLOT(i * 4 + 1),
                  (unsigned)FAULT_SLOT(i * 4 + 2),
                  (unsigned)FAULT_SLOT(i * 4 + 3));
        }
    }
    PRINT("----------------------------------------\n");
    PRINT("  怎么用这些信息：\n");
    PRINT("    * mepc 指向出错的那条指令 —— 用 objdump 反汇编这个地址，\n");
    PRINT("      就能看到是哪一行代码、调用了哪个函数。\n");
    PRINT("    * mcause 常见值：\n");
    PRINT("        0x00000005  Load access fault（读了非法地址）\n");
    PRINT("        0x00000007  Store access fault（写了非法地址）\n");
    PRINT("        0x00000002  Illegal instruction（非法指令）\n");
    PRINT("        0x00000001  Instruction access fault（取指失败，\n");
    PRINT("                    常见于跳到空白 RAM）\n");
    PRINT("========================================\n");
    PRINT("  程序在此停住（死循环），上面的信息就是现场。\n");

    while(1) { }
}



/* ===========================================================================
 * 卡死定位器：用 SysTick 中断采样 PC
 *
 * ★ 为什么需要它 ★
 *   程序卡在 Main_Circulation() 里时，靠 TMOS 的心跳也停了 —— 没有任何输出，
 *   根本不知道卡在哪。而 SysTick 中断是**硬件**周期性触发的，
 *   不依赖主循环，正好可以用它当"外部观察者"。
 *
 * 原理：
 *   * 每次 SysTick 中断读 mepc（被打断的那条指令地址）
 *   * 若连续 STUCK_SAMPLES 次采到的 PC 完全相同，判定为"卡住了"，
 *     在**中断里**直接把 PC 和栈现场打印出来（不依赖主循环）
 *   * 打印后置标志，避免反复刷屏
 * =========================================================================== */

#define STUCK_SAMPLES   300     /* 连续这么多次采样 PC 不变就判定卡死。
 * SysTick 已配成 10ms 一次，300 次 = 3 秒。 */

void Debug_SysTickSample(void)
{
    static uint32_t last_pc = 0;
    static uint32_t same   = 0;
    static uint32_t done   = 0;
    uint32_t pc;
    uint32_t *sp;
    int i;

    if(done)
    {
        return;             /* 已经报过了，不再刷屏 */
    }

    pc = __get_MEPC();

    if(pc == last_pc)
    {
        same++;
    }
    else
    {
        same = 0;
        last_pc = pc;
        return;             /* PC 在变，说明还在跑 */
    }

    if(same < STUCK_SAMPLES)
    {
        return;
    }

    /* ---- 判定卡死，立刻打印 ---- */
    done = 1;
    __asm__ volatile("mv %0, sp" : "=r"(sp));

    PRINT("\n");
    PRINT("========================================\n");
    PRINT("  >>> 检测到卡死 <<<\n");
    PRINT("========================================\n");
    PRINT("  连续 %u 次 SysTick 采样，PC 都停在:\n", (unsigned)same);
    PRINT("      PC = 0x%08X\n", (unsigned)pc);
    PRINT("      SP = 0x%08X\n", (unsigned)(uint32_t)sp);
    PRINT("  ----------------------------------------\n");
    PRINT("  栈现场（SP 附近 32 个字）：\n");
    for(i = 0; i < 32; i += 4)
    {
        PRINT("    [+%02X] %08X %08X %08X %08X\n",
              (unsigned)(i * 4),
              (unsigned)sp[i + 0], (unsigned)sp[i + 1],
              (unsigned)sp[i + 2], (unsigned)sp[i + 3]);
    }
    PRINT("  ----------------------------------------\n");
    PRINT("  怎么用：\n");
    PRINT("    拿 PC 的地址去反汇编（objdump -d），即可看到卡在哪个函数。\n");
    PRINT("    若 PC 落在 0x2000xxxx（RAM），那多半是 ROM 的 .highcode ——\n");
    PRINT("    那些代码必须位于 RAM 才有内容，没搬进去就是空的。\n");
    PRINT("========================================\n");
}

#endif /* DEBUG */

/******************************** endfile @ debug_fault ***********************/
