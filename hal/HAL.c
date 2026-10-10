/*******************************************************************************
 * 文件名称 : HAL.c
 * 描    述 : 硬件抽象层实现。
 *
 *            ★ 本文件是整个适配工程里最关键的"补缺口"文件 ★
 *
 *            WCH 的 CH572 SDK 里只给了 HAL.h 头文件，没有给 HAL.c 的源码，
 *            因此 CH57x_BLEInit() 一直没有实现，协议栈无法启动。
 *            本文件按照 CH59x 官方 HAL.c 的写法，并结合 CH572 的
 *            libCH572BLE_PERI.a 实际提供的符号，补全了这份实现。
 *
 *            它做的事情：
 *              1) CH57x_BLEInit() —— 填 bleConfig_t，调用 BLE_LibInit()；
 *              2) 注册协议栈需要的 6 个回调（随机数/空闲/Flash读/Flash写/
 *                 时钟配置/状态上报）；
 *              3) HAL_Init()      —— 注册 HAL 任务、启动周期校准事件；
 *              4) HAL_ProcessEvent() —— 处理 LED/按键/寄存器初始化等杂务。
 ******************************************************************************/
#include "CONFIG.h"

/*********************************************************************
 * 全局变量
 */

/** HAL 任务的 TMOS 任务号，由 TMOS 动态分配 */
tmosTaskID halTaskID;

/** 运行时诊断心跳事件位（仅 DEBUG 构建用） */
#define HAL_HEARTBEAT_EVENT         0x0008

/* ---------------------------------------------------------------------------
 * 初始化进度追踪宏
 *
 * 只在 DEBUG 打开时生效。CH57x_BLEInit() 内部步骤多，实机如果卡死，
 * 靠这些标记能精确定位到是哪一步（发布版固件里这些字符串不会编进去）。
 * ------------------------------------------------------------------------- */
#ifdef DEBUG
#define BLEINIT_TRACE(n, msg)   PRINT("  [BLEInit %s] %s\n", (n), (msg))
#else
#define BLEINIT_TRACE(n, msg)   do { } while(0)
#endif


/**
 * @brief 协议栈内部使用的"链路层中断服务程序地址"变量。
 *
 * 官方 CH572 的 HAL/MCU.c 里定义了这个全局变量，并在 CH57x_BLEInit() 中
 * 把 LLE_IRQLibHandler 的地址赋给它。协议栈在配置中断时读取它。
 *
 * 这里保留同名定义是为了与官方行为完全一致 —— ROM 方式下它的值来自
 * CH572BLE_ROM_GetHandles()->lle_lib_irq（即 ROM 里那个真正的 ISR）。
 */
uint32_t g_LLE_IRQLibHandlerLocation = 0;

/*********************************************************************
 * 协议栈内存堆的编译期检查
 *
 * 说明：MEM_BUF 需要应用层定义（见 examples 里的 main.c），这里只做合理性断言。
 *       官方建议堆不小于 6KB，但 CH572 只有 12KB RAM，实测 5KB（512*10）
 *       足以跑通从机全功能（广播/连接/GATT读写/Notify/配对绑定），
 *       因此下限放到 4KB —— 低于这个值连接建立后会内存分配失败。
 */
#if (BLE_MEMHEAP_SIZE < 4096)
#error "BLE_MEMHEAP_SIZE 不能小于 4096 字节，否则协议栈连接后会内存分配失败"
#endif

#if ((BLE_MEMHEAP_SIZE % 4) != 0)
#error "BLE_MEMHEAP_SIZE 必须是 4 的整数倍（协议栈按 4 字节对齐分配）"
#endif

#if (BLE_MEMHEAP_SIZE > 8192)
#warning "BLE_MEMHEAP_SIZE 超过 8192 字节，CH572 只有 12KB RAM，应用可能没有栈空间了"
#endif

/* ---------------------------------------------------------------------------
 * 低功耗与 RAM 预算的冲突（编译期提醒）
 *
 * HAL_SLEEP=TRUE 会通过 HAL_TimeInit() -> RTC_InitClock() 间接引入 newlib 的
 * 浮点支持（RTC.h 的 CLK_PER_US / US_TO_RTC 等宏是 double 运算），实测多占
 * 约 0x90 字节 RAM（主要是 impure_data 与 libgcc 的 adddf3/divdf3/muldf3 等）。
 *
 * 当前 512*10=5120 的堆配置下，调试版 ROM 固件的链接栈余量只有 1,048 字节，
 * 而 Link_rom.ld 要求至少 1,024 字节 —— 只剩 24 字节余量。所以打开睡眠会
 * 直接把链接打挂（报 "CH572 requires at least 1024 bytes of stack"），
 * 这不是 bug 而是 RAM 真的不够。
 *
 * 处理办法：把堆调小到 4,096 字节再开睡眠（用 -Defines 传，不要改头文件）：
 *
 *     powershell -Command "& '.\scripts\build.ps1' -Target rom `
 *         -Defines @('HAL_SLEEP=TRUE','BLE_MEMHEAP_SIZE=4096')"
 *
 * 实测（发布版 ROM 固件）：堆 5,120 时栈余量 1,224 字节；打开睡眠后掉到
 * 1,080 字节（不足下限）；再把堆降到 4,096 后回到 1,168 字节，链接通过。
 *
 * 注意两个坑：
 *   1) -Defines 里一个 -D 不能带逗号。写成 -Defines HAL_SLEEP=TRUE,BLE_MEMHEAP_SIZE=4096
 *      会变成宏体 "TRUE,BLE_MEMHEAP_SIZE=4096"，报 token "=" is not valid
 *      in preprocessor expressions。用 @(...) 数组形式分两个 -D。
 *   2) 4,096 字节是 BLE_MEMHEAP_SIZE 的下限（见上面的 #error），再小不行。
 *
 * ★ 上面这条命令只保证"编得过、链接得过"。睡眠的唤醒源、实际功耗、以及唤醒后
 *   协议栈是否还能准时处理连接事件，**必须实机验证** —— CH572 的 PFIC VTF
 *   寄存器在睡眠后是否保持，本项目尚未确认。参见
 *   docs\CH572固化协议栈修复与测试.md 的低功耗一节。
 * ------------------------------------------------------------------------- */
#if (HAL_SLEEP == TRUE)
#if (BLE_MEMHEAP_SIZE > 4096)
#warning "HAL_SLEEP=TRUE 会增加约 0x90 字节 RAM 占用；实测堆 5,120 时链接会报栈不足，请同时用 -Defines 传 BLE_MEMHEAP_SIZE=4096"
#endif
#endif

/* Flash 擦除的物理粒度是 4KB。SNV 的逻辑块可以小于 4KB，但起始地址必须
 * 位于独占的擦除扇区边界；多个逻辑块只有在每块均为整扇区整数倍时才不会
 * 共用物理扇区。共享扇区需要读改写（RMW），当前 HAL 不实现 RMW，因此拒绝。 */
#if (BLE_SNV == TRUE)
#if (BLE_SNV_BLOCK <= 0) || (BLE_SNV_NUM <= 0)
#error "SNV block size/count must be greater than zero"
#endif
#if (BLE_SNV_BLOCK > 0xFFFF) || (BLE_SNV_NUM > 0xFF)
#error "SNV block size/count does not fit bleConfig_t fields"
#endif
#if ((BLE_SNV_BLOCK % FLASH_MIN_WR_SIZE) != 0)
#error "SNV block size must be a multiple of the 4-byte Flash write unit"
#endif
#if ((BLE_SNV_ADDR % FLASH_BLOCK_SIZE) != 0)
#error "SNV address must be aligned to the 4KB physical erase sector"
#endif
#if (BLE_SNV_NUM > 1) && ((BLE_SNV_BLOCK % FLASH_BLOCK_SIZE) != 0)
#error "Multiple SNV blocks sharing a 4KB erase sector require RMW support"
#endif
#endif

/** 验证 SNV 逻辑范围及其实际会触及的 4KB 擦除扇区。 */
static uint32_t HAL_SNVConfigRange(uint32_t *pStart, uint32_t *pEnd,
                                   uint32_t *pPhysicalEnd)
{
    uint32_t start = (uint32_t)BLE_SNV_ADDR;
    uint32_t blockSize = (uint32_t)BLE_SNV_BLOCK;
    uint32_t blockCount = (uint32_t)BLE_SNV_NUM;
    uint32_t totalSize;
    uint32_t end;
    uint32_t physicalEnd;

    if((blockSize == 0) || (blockCount == 0) ||
       (blockSize > 0xFFFFu) || (blockCount > 0xFFu) ||
       ((blockSize % FLASH_MIN_WR_SIZE) != 0) ||
       (blockCount > (0xFFFFFFFFu / blockSize)) ||
       ((start % FLASH_BLOCK_SIZE) != 0))
    {
        return 0;
    }

    totalSize = blockSize * blockCount;
    if((start > (0xFFFFFFFFu - totalSize)) ||
       ((start & (FLASH_MIN_WR_SIZE - 1u)) != 0))
    {
        return 0;
    }
    end = start + totalSize;
    if((end > FLASH_ROM_MAX_SIZE) ||
       (end > (0xFFFFFFFFu - (FLASH_BLOCK_SIZE - 1u))))
    {
        return 0;
    }

    physicalEnd = ((end + FLASH_BLOCK_SIZE - 1u) / FLASH_BLOCK_SIZE) *
                  FLASH_BLOCK_SIZE;
    if(physicalEnd > FLASH_ROM_MAX_SIZE)
    {
        return 0;
    }

    if(pStart != NULL)       *pStart = start;
    if(pEnd != NULL)         *pEnd = end;
    if(pPhysicalEnd != NULL) *pPhysicalEnd = physicalEnd;
    return 1;
}

/*********************************************************************
 * @fn      HAL_SrandCB
 *
 * @brief   随机数种子回调。协议栈在生成随机地址、SM 配对随机数时调用。
 *
 * @return  32 位随机种子
 */
static uint32_t HAL_SrandCB(void)
{
    /* CH572 没有独立的硬件随机数发生器，这里用内部 LSI 计数 + 芯片唯一 ID 混合。
     * SYS_GetSysTickCnt() 返回自由运行的系统节拍计数器，抖动足以做种子。 */
    uint32_t seed = SYS_GetSysTickCnt();

    /* 混入 Flash 中的芯片唯一 ID，保证不同芯片上电种子不同 */
    uint8_t uid[8] = {0};
    GET_UNIQUE_ID(uid);
    for(uint8_t i = 0; i < 8; i++)
    {
        seed = (seed << 5) ^ (seed >> 27) ^ (uint32_t)uid[i];
    }
    return seed;
}

/*********************************************************************
 * @fn      HAL_IdleCB
 *
 * @brief   空闲回调。协议栈在等待射频事件期间会调用它，让 MCU 进入低功耗。
 *
 * @param   timeUs - 允许空闲的时间（微秒）
 * @return  实际空闲的时间
 */
static uint32_t HAL_IdleCB(uint32_t timeUs)
{
#if (HAL_SLEEP == TRUE)
    /* 开启睡眠时，把允许空闲的时间交给 CH57x_LowPower()，
     * 它会安排 RTC 在 timeUs 之后唤醒 MCU。 */
    return CH57x_LowPower(timeUs);
#else
    /* 不睡眠时直接返回，让 CPU 空转等待（调试阶段推荐） */
    (void)timeUs;
    return 0;
#endif
}

/*********************************************************************
 * @fn      HAL_FlashReadCB
 *
 * @brief   SNV 读取回调。协议栈用它读取配对绑定信息。
 *
 * @param   addr - Flash 起始地址
 * @param   num  - 读取长度（4 字节单位，协议栈回调 ABI）
 * @param   pBuf - 目标缓冲区
 * @return  0 表示成功
 */
static uint32_t HAL_FlashReadCB(uint32_t addr, uint32_t num, uint32_t *pBuf)
{
    uint32_t snvStart;
    uint32_t snvEnd;
    uint32_t byteCount;

    if(num == 0)
    {
        return 0;
    }
    if((num > (0xFFFFFFFFu / 4u)) || (pBuf == NULL) ||
       ((addr & (FLASH_MIN_WR_SIZE - 1u)) != 0) ||
       ((((uintptr_t)pBuf) & (FLASH_MIN_WR_SIZE - 1u)) != 0))
    {
        return 1;
    }

    byteCount = num * 4u;
    if(!HAL_SNVConfigRange(&snvStart, &snvEnd, NULL))
    {
        return 1;
    }
    if((addr > (0xFFFFFFFFu - byteCount)) ||
       (addr < snvStart) ||
       ((addr + byteCount) > snvEnd) ||
       ((addr + byteCount) > FLASH_ROM_MAX_SIZE))
    {
        return 1;
    }

    /* CH572 的 Flash 读取是 memory-mapped 的，但走 ROM 命令更稳妥，
     * 且能正确处理跨页与 cache 一致性问题。 */
    FLASH_ROM_READ(addr, (void *)pBuf, byteCount);
    return 0;
}

/*********************************************************************
 * @fn      HAL_FlashWriteCB
 *
 * @brief   SNV 写入回调。协议栈用它保存配对绑定信息。
 *
 * @param   addr - Flash 起始地址（必须 4 字节对齐）
 * @param   num  - 写入长度（4 字节单位，协议栈回调 ABI）
 * @param   pBuf - 源数据缓冲区
 * @return  0 表示成功
 *
 * @note    FLASH_ROM_ERASE 的物理粒度是 4KB。SNV 配置必须独占其覆盖的
 *          物理扇区；多个逻辑块共用扇区时必须先实现 RMW，本 HAL 会拒绝该配置。
 */
static uint32_t HAL_FlashWriteCB(uint32_t addr, uint32_t num, uint32_t *pBuf)
{
    uint32_t snvStart;
    uint32_t snvEnd;
    uint32_t physicalEnd;
    uint32_t byteCount;
    uint32_t sectorStart;
    uint32_t sectorEnd;

    if(num == 0)
    {
        return 0;
    }
    if((num > (0xFFFFFFFFu / 4u)) || (pBuf == NULL) ||
       ((addr & (FLASH_MIN_WR_SIZE - 1u)) != 0) ||
       ((((uintptr_t)pBuf) & (FLASH_MIN_WR_SIZE - 1u)) != 0))
    {
        return 1;
    }

    byteCount = num * 4u;
    if(!HAL_SNVConfigRange(&snvStart, &snvEnd, &physicalEnd))
    {
        return 1;
    }
    if((addr > (0xFFFFFFFFu - byteCount)) ||
       (addr < snvStart) ||
       ((addr + byteCount) > snvEnd) ||
       ((addr + byteCount) > FLASH_ROM_MAX_SIZE))
    {
        return 1;
    }

    /* 擦除命令 0x01 擦除整扇区（4KB），Length 不能当成可按字节擦除。 */
    sectorStart = (addr / FLASH_BLOCK_SIZE) * FLASH_BLOCK_SIZE;
    if((addr + byteCount) > (0xFFFFFFFFu - (FLASH_BLOCK_SIZE - 1u)))
    {
        return 1;
    }
    sectorEnd = ((addr + byteCount + FLASH_BLOCK_SIZE - 1u) /
                 FLASH_BLOCK_SIZE) * FLASH_BLOCK_SIZE;
    if((sectorStart < snvStart) || (sectorStart >= physicalEnd) ||
       (sectorEnd > physicalEnd) || (sectorEnd > FLASH_ROM_MAX_SIZE))
    {
        return 1;
    }

    /* 配置检查保证共享扇区不会擦掉其它 SNV block 或应用数据。 */
    for(uint32_t a = sectorStart; a < sectorEnd; a += FLASH_BLOCK_SIZE)
    {
        if(FLASH_ROM_ERASE(a, FLASH_BLOCK_SIZE) != 0)
        {
            return 1;
        }
    }

    return (FLASH_ROM_WRITE(addr, (void *)pBuf, byteCount) == 0) ? 0 : 1;
}

/*********************************************************************
 * @fn      HAL_StaCB
 *
 * @brief   协议栈状态上报回调。协议栈内部出现异常时回调此函数。
 *
 * @param   code   - 状态码
 * @param   status - 附加状态
 */
static void HAL_StaCB(uint8_t code, uint32_t status)
{
    (void)code;
    (void)status;
    /* 产品上可以在这里点亮错误指示灯或者打印日志。
     * 调试阶段可在 PRINT 打开后输出 code/status。 */
}

/*********************************************************************
 * @fn      CH57x_BLEInit
 *
 * @brief   BLE 协议栈初始化（本工程补全的关键函数）。
 *
 * @note    调用顺序要求：
 *              1. 先把系统时钟配好（SetSysClock）；
 *              2. 再调用 CH57x_BLEInit()；
 *              3. 最后调用 HAL_TimeInit() 建立 RTC 时基。
 */
void CH57x_BLEInit(void)
{
    bleConfig_t cfg;
    uint8_t     i;

    /* ---- 0. 版本一致性检查 ----
     * 官方做法：比对 ROM/库里的版本串与头文件期望的版本串，不一致就停住。
     * 这一步能在启动阶段就抓出"头文件与库不匹配"，避免后面出现莫名其妙的
     * 行为异常。ROM 方式下 VER_LIB 指向 ROM 里的版本串。 */
    if(tmos_memcmp(VER_LIB, VER_FILE, tmos_strlen(VER_FILE)) == FALSE)
    {
        PRINT("head file error... 库版本与头文件不匹配\n");
        while(1) { }
    }

    BLEINIT_TRACE("1/9", "配置 SysTick");

    /* SysTick is a free-running BLE timebase. Release builds use the official
     * maximum reload with STIE disabled; DEBUG builds use 10ms sampling. */
#ifdef DEBUG
    (void)SysTick_Config(FREQ_SYS / 100u);
    SysTick->SR = 0;
#else
    (void)__SysTick_Config(SysTick_LOAD_RELOAD_Msk);
    SysTick->SR = 0;
    SysTick->CTLR &= ~SysTick_CTLR_STIE;
#endif

    BLEINIT_TRACE("2/9", "配置 MISC_CTRL 射频位");

    /* ---- 2. 射频相关的 MISC_CTRL 配置 ----
     * ★ 这一句也不能省。官方 CH572 的 MCU.c 里明确把 R32_MISC_CTRL 的
     *   bit24-29 设成 0x0E（其余位保持不变）。寄存器手册没给出这 6 位的
     *   公开定义，但 WCH 自己的 BLE 初始化里必须设它 ——
     *   漏掉会导致**射频完全不工作**（应用能跑、但搜不到任何蓝牙信号）。
     *   注意：该寄存器属于安全访问区，必须用 sys_safe_access_enable/disable 包裹。 */
    sys_safe_access_enable();
    R32_MISC_CTRL = (R32_MISC_CTRL & (~(0x3f << 24))) | (0xe << 24);
    sys_safe_access_disable();

    BLEINIT_TRACE("3/9", "登记 LLE 中断入口");

    /* ---- 3. 把 ROM 里的 LLE 中断处理入口登记给协议栈 ----
     * 协议栈在处理链路层事件时会通过这个变量找到真正的 LLE 中断服务程序。
     * ROM 方式下必须指向 ROM 里导出的 LLE_IRQLibHandler
     * （不是向量表里的那个转接桩 LLE_IRQHandler）。
     *
     * 静态库方式下按官方 MCU.c 直接登记 LLE_IRQLibHandler 地址。 */
#ifdef CH572BLE_ROM
    {
        const rom_handles_t *h = CH572BLE_ROM_GetHandles();
        if(h && h->magic == ROM_HANDLE_MAGIC && h->lle_lib_irq)
        {
            g_LLE_IRQLibHandlerLocation = (uint32_t)h->lle_lib_irq;
        }
    }
#else
    g_LLE_IRQLibHandlerLocation = (uint32_t)LLE_IRQLibHandler;
#endif

    BLEINIT_TRACE("4/9", "设置 BLE 中断优先级");

    /* ---- 4. BLE 链路层中断优先级 ----
     * 官方给的是 0xF0（最低优先级）。周期校准、连接事件都靠这个中断，
     * 优先级不设的话在中断嵌套场景下可能被长时间抢占，导致连接事件丢失。 */
    PFIC_SetPriority(BLEL_IRQn, 0xF0);

    BLEINIT_TRACE("5/9", "清零 bleConfig_t");

    /* ---- 5. 清零配置结构体，避免野值 ---- */
    tmos_memset(&cfg, 0, sizeof(bleConfig_t));

    /* ---- 6. 协议栈内存堆：直接使用应用定义的 MEM_BUF ---- */
    cfg.MEMAddr = (uint32_t)MEM_BUF;
    cfg.MEMLen = (uint16_t)BLE_MEMHEAP_SIZE;

    BLEINIT_TRACE("6/9", "填内存堆参数");

    /* ---- 7. SNV（绑定信息存储）配置 ---- */
#if (BLE_SNV == TRUE)
    /* 检查逻辑 SNV 范围和擦除时会触及的完整 4KB 物理扇区。 */
    if(!HAL_SNVConfigRange(NULL, NULL, NULL))
    {
        PRINT("SNV config error... invalid logical or physical Flash range\n");
        while(1) { }
    }
    cfg.SNVAddr = (uint32_t)BLE_SNV_ADDR;
    cfg.SNVBlock = BLE_SNV_BLOCK;
    cfg.SNVNum = BLE_SNV_NUM;
    cfg.readFlashCB = HAL_FlashReadCB;
    cfg.writeFlashCB = HAL_FlashWriteCB;
#else
    cfg.SNVAddr = 0;    /* 传 0 表示不保存绑定信息 */
    cfg.SNVBlock = 0;
    cfg.SNVNum = 0;
    cfg.readFlashCB = NULL;
    cfg.writeFlashCB = NULL;
#endif

    /* ---- 8. 收发包缓冲区 ---- */
    cfg.BufNumber = BLE_BUFF_NUM;
    cfg.BufMaxLen = BLE_BUFF_MAX_LEN;
    cfg.TxNumEvent = BLE_TX_NUM_EVENT;
    cfg.RxNumEvent = BLE_BUFF_NUM;

    /* ---- 9. 射频参数 ---- */
    cfg.TxPower = BLE_TX_POWER;
    /* ★ WindowWidening 是"射频启动等待窗口"，单位 us。
     *   官方 CH572 的 MCU.c 用的是 120。这个值太小（比如 2us）会导致
     *   射频还没稳定就开始收发，表现为广播发不出去、连接建立失败。
     *   WaitWindow 官方不设置（保持 memset 后的 0），这里也不设。 */
    cfg.WindowWidening = 120;

    BLEINIT_TRACE("7a/9", "填 SNV / 缓冲 / 射频参数完成，准备读 MAC");

    /* ---- 10. MAC 地址 ---- */
#if (defined(BLE_MAC)) && (BLE_MAC == TRUE)
    /* 用应用定义的 MAC。注意官方是**逆序**填进去的：
     * MacAddr[0..5] 按大端书写，协议栈要小端，所以这里 5-i 反转。 */
    for(i = 0; i < 6; i++)
    {
        cfg.MacAddr[i] = MacAddr[5 - i];
    }
#else
    /* 用芯片 eFlash 里出厂固化的 MAC 地址。
     * ★ 不能像早期版本那样填全 0 —— 填 0 时协议栈可能拿不到有效地址，
     *   而且这与官方行为不一致。GetMACAddress 走的是 FLASH_EEPROM_CMD，
     *   由 libISP572.a 提供（链接时必须带 -lISP572）。 */
    {
        uint8_t mac[6];
        GetMACAddress(mac);
        for(i = 0; i < 6; i++)
        {
            cfg.MacAddr[i] = mac[i];
        }
    }
#endif

    BLEINIT_TRACE("7b/9", "读 MAC 地址完成");

    /* ---- 11. 回调注册 ---- */
    cfg.srandCB = HAL_SrandCB;
    cfg.idleCB = HAL_IdleCB;
    cfg.staCB = HAL_StaCB;

    BLEINIT_TRACE("8/9", "调用 BLE_LibInit（关键步骤）");

    /* ---- 12. 内存堆合理性检查（官方要求 ≥ 3KB） ---- */
    if(!cfg.MEMAddr || cfg.MEMLen < 3 * 1024)
    {
        PRINT("MEM_BUF 配置错误：地址为空或堆小于 3KB\n");
        while(1) { }
    }

    /* ---- 13. 启动协议栈 ---- */
    i = BLE_LibInit(&cfg);
    if(i != 0)
    {
        /* 初始化失败：协议栈没有跑起来，产品上应当在此复位或点亮故障灯。
         * 常见原因：MEM_BUF 未定义、堆太小、Flash 中 MAC 无效。 */
        PRINT("LIB init error code: %x\n", i);
        while(1) { }
    }

    BLEINIT_TRACE("9/9", "建立 RTC 时基");

    /* ---- 14. 建立 RTC 时基并注册到 TMOS ---- */
    HAL_TimeInit();
}

/*********************************************************************
 * @fn      HAL_Init
 *
 * @brief   初始化 HAL 任务
 *
 * @note    顺序必须与官方 HAL/MCU.c 的 HAL_Init 一致：
 *              1) 先注册任务拿到 task ID；
 *              2) 再 HAL_TimeInit() 把 RTC 时基挂到 TMOS；
 *              3) 最后才 tmos_start_task(...)。
 *
 *          ★ 为什么第 2 步不能放到最后：TMOS 的计划任务时间戳是相对
 *            "当前 TMOS 时钟"算的，而 TMOS 时钟由 HAL_TimeInit() 里的
 *            TMOS_TimerInit(&conf) 建立。若在它之前就 tmos_start_task()，
 *            超时基准是未初始化的值 —— 首次周期校准（HAL_REG_INIT_EVENT）
 *            的触发时刻就是不确定的。以前本工程的顺序反了，这里改正。
 *            周期校准早触发一次不会造成功能故障（处理完会按
 *            BLE_CALIBRATION_PERIOD 重新排程），但属于无依据的偏离。
 *
 *          ROM 方式下 HAL_TimeInit() 已经在 CH57x_BLEInit() 末尾调用过，
 *          这里不重复调（重复跑一遍 LSI 校准是白耗时间）。
 */
void HAL_Init(void)
{
    /* 先注册任务取得有效 task ID，再安排任何任务事件。 */
    halTaskID = TMOS_ProcessEventRegister(HAL_ProcessEvent);

#ifndef CH572BLE_ROM
    /* 静态库方式：CH57x_BLEInit() 里没有建时基，由这里补上（官方顺序）。 */
    HAL_TimeInit();
#endif

#if (HAL_SLEEP == TRUE)
    /* 官方 HAL/MCU.c 在这里调 HAL_SleepInit()：配置睡眠唤醒源。
     * 以前本工程漏了这一句 —— 后果是即使补上 SLEEP_RTC_* 宏，
     * R8_SLP_WAKE_CTRL 里的 RB_SLP_RTC_WAKE 也不会置位，
     * 睡下去就没有唤醒源，表现为"进睡眠后不再响应 BLE"。 */
    HAL_SleepInit();
    PRINT("HAL_SleepInit done, sleep enabled\n");
#endif

#ifdef DEBUG
    /* ---- 启动运行时诊断心跳 ----
     * 定期打印一次，用来区分"卡死"和"程序在跑但中断/射频没工作"。
     * 用 TMOS 的周期任务，不占用额外硬件定时器。
     * 1000 个 TMOS tick；TMOS 一个 tick = 625us（见 MS1_TO_SYSTEM_TIME），
     * 所以实际周期约 625ms。 */
    tmos_start_reload_task(halTaskID, HAL_HEARTBEAT_EVENT, 1000);
#endif

#if (BLE_CALIBRATION_ENABLE == TRUE)
    /* 沿用官方 HAL/MCU.c：注册任务后延迟首次 RF/LSI 校准。 */
    tmos_start_task(halTaskID, HAL_REG_INIT_EVENT, 800);
#endif
}

/*********************************************************************
 * @fn      HAL_ProcessEvent
 *
 * @brief   HAL 任务事件处理
 *
 * @param   task_id - TMOS 分配的任务号
 * @param   events  - 待处理事件位图
 * @return  未处理完的事件位（本任务全部处理完，返回 0）
 *
 * @note    ★ 返回值语义 ★
 *          TMOS 把非 0 返回值当作"这些位还没处理完"，会**再次**调用本函数。
 *          官方 HAL/MCU.c 的每个分支都写 return 0，含义就是"都处理完了"。
 *          本工程以前写的是 return events —— 只要进来时 events 里还有本函数
 *          不认识的位，那一位就会被原样退回，TMOS 反复回调、主循环被占满。
 *          现在统一：处理完自己的位就 return 0。
 *          对应地，HAL.h 里只声明本函数真正处理的事件位。
 */
tmosEvents HAL_ProcessEvent(tmosTaskID task_id, tmosEvents events)
{
    uint8_t *msgPtr;

    /* ---- 系统消息（SYS_EVENT_MSG）必须先处理 ---- */
    if(events & SYS_EVENT_MSG)
    {
        msgPtr = tmos_msg_receive(task_id);
        if(msgPtr != NULL)
        {
            /* 目前 HAL 层不订阅任何系统消息，收到后直接释放，
             * 避免消息堆泄漏。 */
            tmos_msg_deallocate(msgPtr);
        }
        events &= (tmosEvents)~SYS_EVENT_MSG;
    }

#if (BLE_CALIBRATION_ENABLE == TRUE)
    /* 沿用官方周期校准流程：重置 RF、重测 LSI、更新 BLE 时钟配置与唤醒裕量。 */
    if(events & HAL_REG_INIT_EVENT)
    {
        uint8_t state;
        bleClockConfig_t conf;

        BLE_RegInit();
        lsiFrq = RTC_InitClock(Count_1024);
        conf = BLE_ClockConfig(lsiFrq);
        state = TMOS_TimerSet(&conf);
        (void)state;
        LSIWakeup_MaxTime = GET_WakeUpLSIMaxTime();
#ifdef CH572BLE_ROM
        /* ★ 必须放在 BLE_RegInit() 之后 ★
         * BLE_RegInit() 内部会调 LLE_DevInit/BB_DevInit，那两步会把 PFIC 的
         * VTFADDR[2]/[3] 重写回 ROM 内的真 ISR，从而踢掉应用侧的计数包装。
         * 不重新武装的话，心跳里的"BLE中断="从第一次校准（约 500ms）起就永远
         * 是 0，而设备功能其实完全正常 —— 这个假数字会把实机排查带偏。
         * 发布构建里 HAL_RomVtfRefresh() 是空实现，不产生代码。 */
        if(HAL_RomVtfRefresh())
        {
#ifdef DEBUG
            PRINT("校准后 VTF 被协议栈改回 ROM，已重新武装\n");
#endif
        }
#endif
        tmos_start_task(halTaskID, HAL_REG_INIT_EVENT,
                        MS1_TO_SYSTEM_TIME(BLE_CALIBRATION_PERIOD));
    }
#endif
    if(events & HAL_REG_INIT_EVENT)
    {
        events &= (tmosEvents)~HAL_REG_INIT_EVENT;
    }

#ifdef DEBUG
    /* ---- 运行时诊断心跳：定期打印快照 ---- */
    if(events & HAL_HEARTBEAT_EVENT)
    {
        Debug_PrintSnapshot();
        events &= (tmosEvents)~HAL_HEARTBEAT_EVENT;
    }
#endif

    /* ---- 测试事件 ---- */
    if(events & HAL_TEST_EVENT)
    {
        /* 预留：可用于产测模式 */
        events &= (tmosEvents)~HAL_TEST_EVENT;
    }

    if(events != 0)
    {
        /* 走到这里说明有人给 HAL 排了本函数不认识的事件位。
         * 不能把它退回去（会被无限回调），但也不能装作没事 ——
         * 调试版打一条日志，发布版直接丢弃。 */
#ifdef DEBUG
        PRINT("HAL_ProcessEvent: 丢弃未处理的未知事件位 0x%04X\n",
              (unsigned)events);
#endif
    }
    return 0;
}

/*********************************************************************
 * @fn      SysTick_Handler
 *
 * @brief   SysTick 中断服务程序。
 *
 * @note    DEBUG 构建启用 10ms 采样，发布版关闭 STIE。保留正确的中断 ABI，
 *          并清除 CNTIF，避免 IRQ flag 残留。
 */
__INTERRUPT __HIGH_CODE void SysTick_Handler(void)
{
#ifdef DEBUG
    Debug_SysTickSample();
#endif
    SysTick->SR = 0;
}

/******************************** endfile @ HAL ******************************/
