/*******************************************************************************
 * 文件名称 : rf_rom_app.c
 * 描    述 : 「通过固定地址镜像调用纯 RF 库」的应用示例
 *
 *            射频功能由 lib\CH572RF_ROM.hex（固定地址镜像，烧在 0x10000）
 *            提供。本应用 **不链接任何 RF / BLE 库**，只通过
 *            include\CH572RF_ROM.h 里的跳转表宏调用镜像里的函数：
 *
 *                RF_RoleInit(&conf)   →  RF_JT(1)  →  镜像里的 RFRole_BasicInit
 *                RF_StartTx(&param)   →  RF_JT(4)  →  镜像里的 RFIP_StartTx
 *                ...
 *
 *            所以应用自身极小（.text 1,660 B），可在不动镜像的前提下单独更新：
 *                应用 hex 4,711 B（对比：把库编进来的做法要 17,704 B）
 *
 *            ★ 三个必须做对的地方 ★
 *              1. 校验镜像魔数，避免"镜像没烧/烧错位置"时跳到空地址
 *              2. 搬运镜像的 RAM 段（3,112 B）—— 必须在**任何** RF 调用之前
 *              3. 中断向量表指向本地包装函数，包装函数再转跳转表里的真 ISR
 *
 *            构建：scripts\build_rf_rom_app.ps1
 ******************************************************************************/

/* 底层 SDK（寄存器 / 时钟 / GPIO / UART） */
#include "CH57x_common.h"
#include "CH57x_clk.h"
#include "CH57x_gpio.h"
#include "CH57x_uart.h"

/* 镜像侧头文件：提供 RF_JT(n) 与 RF_RoleInit / RF_StartTx / RF_SetRx 等宏
 * 它内部会 include CH572rf.h 取数据类型，所以**不要**再手动包含 BLE 头 */
#include "CH572RF_ROM.h"

/* 镜像占用的 RAM 段信息（由 scripts\make_rf_rom.ps1 自动生成） */
#include "rom_rf_ram_info.h"

/* 所有可调参数（频点 / 同步字 / CRC / 功率 / 包长 / 角色…） */
#include "rf_rom_cfg.h"

/*=============================================================================
 * 1. 中断入口
 *
 * 向量表在 Flash（.vectors），startup_CH572.S 里 BB_IRQHandler / LLE_IRQHandler
 * 是弱定义；这里给强定义。
 *
 * ★ 为什么必须走跳转表 ★
 *   镜像不参与应用的链接，BB_LibIRQHandler / LLE_LibIRQHandler 这些符号
 *   在应用里**不存在**，所以只能运行时从跳转表取地址：
 *       LLE_IRQHandler → RF_LleIrqHandler() → RF_JT(12) → 镜像里的真 ISR
 *   多一层间接调用，换来的是应用可以独立于镜像更新。
 *===========================================================================*/
__INTERRUPT __HIGH_CODE void LLE_IRQHandler(void)
{
    RF_LleIrqHandler();
}

__INTERRUPT __HIGH_CODE void BB_IRQHandler(void)
{
    RF_BbIrqHandler();
}

/*=============================================================================
 * 2. 搬运镜像的 RAM 段 ★ 必须在任何 RF 调用之前 ★
 *
 * 镜像里有 3,112 字节的 .highcode（必须搬到 RAM 才能执行）和 48 字节 .bss。
 * 镜像自己没有上电入口，没人替它做这件事 —— 跳过或做晚了，
 * 调用 RF_JT(n) 会跳到一片空白 RAM（全 0 = 非法指令）。
 *===========================================================================*/
static void RfRomRam_Init(void)
{
    volatile uint8_t *dst;
    const uint8_t *src;
    uint32_t i;

    /* .highcode：从 Flash 镜像搬到 RAM */
    dst = (volatile uint8_t *)RF_ROM_HIGHCODE_VMA;
    src = (const uint8_t *)RF_ROM_HIGHCODE_LMA;
    for(i = 0; i < RF_ROM_HIGHCODE_SIZE; i++)
    {
        dst[i] = src[i];
    }

    /* .data：有初值的变量 */
    if(RF_ROM_DATA_SIZE != 0)
    {
        dst = (volatile uint8_t *)RF_ROM_DATA_VMA;
        src = (const uint8_t *)RF_ROM_DATA_LMA;
        for(i = 0; i < RF_ROM_DATA_SIZE; i++)
        {
            dst[i] = src[i];
        }
    }

    /* .bss：清零（上电是垃圾值） */
    dst = (volatile uint8_t *)RF_ROM_BSS_VMA;
    for(i = 0; i < RF_ROM_BSS_SIZE; i++)
    {
        dst[i] = 0;
    }

    /* 刚写完代码段，必须刷指令缓存/流水线 */
    __asm__ volatile("fence.i" ::: "memory");
}

/*=============================================================================
 * 3. 运行时状态
 *===========================================================================*/
static volatile uint32_t g_txFinish = 0;
static volatile uint32_t g_rxCount  = 0;
static volatile uint32_t g_rxCrcErr = 0;
static volatile uint32_t g_timeout  = 0;

#if (RF_APP_ROLE == RF_ROLE_TX)
static uint32_t g_txTotal = 0;
#else
static uint32_t g_lastSeq = 0xFFFFFFFFUL;
#endif

static __attribute__((aligned(4))) uint8_t g_txBuf[RF_APP_PKT_LEN];
static __attribute__((aligned(4))) uint8_t g_rxBuf[64];

static rfipTx_t g_txParam;
static rfipRx_t g_rxParam;

/*=============================================================================
 * 4. RF 回调（由镜像在中断上下文里调用）
 *===========================================================================*/
static void RfApp_ProcessCB(rfRole_States_t state, uint8_t id)
{
    (void)id;
    if(state & RF_STATE_TX_FINISH) { g_txFinish++; }
    if(state & RF_STATE_RX)        { g_rxCount++;  }
    if(state & RF_STATE_RX_CRCERR) { g_rxCrcErr++; }
    if(state & RF_STATE_TIMEOUT)   { g_timeout++;  }
}

/*=============================================================================
 * 5. 参数与初始化
 *===========================================================================*/
static void RfApp_InitParams(void)
{
    memset(&g_txParam, 0, sizeof(g_txParam));
    memset(&g_rxParam, 0, sizeof(g_rxParam));

    g_txParam.accessAddress = RF_APP_ACCESS_ADDR;
    g_txParam.crcInit       = RF_APP_CRC_INIT;
    g_txParam.crcPoly       = RF_APP_CRC_POLY;
    g_txParam.frequency     = RF_APP_FREQ_KHZ;
    g_txParam.properties    = RF_APP_PHY_MODE;
    g_txParam.txDMA         = (uint32_t)g_txBuf;
    g_txParam.txLen         = RF_APP_PKT_LEN;
    g_txParam.waitTime      = 80 * 2;       /* 官方注释：不低于 80us */
    g_txParam.txPowerVal    = RF_APP_TX_POWER;

    g_rxParam.accessAddress = RF_APP_ACCESS_ADDR;
    g_rxParam.crcInit       = RF_APP_CRC_INIT;
    g_rxParam.crcPoly       = RF_APP_CRC_POLY;
    g_rxParam.frequency     = RF_APP_FREQ_KHZ;
    g_rxParam.properties    = RF_APP_PHY_MODE;
    g_rxParam.rxDMA         = (uint32_t)g_rxBuf;
    g_rxParam.rxMaxLen      = RF_APP_PKT_LEN;
    g_rxParam.timeOut       = RF_APP_RX_TIMEOUT;
}

static uint8_t RfApp_Init(void)
{
    rfRoleConfig_t conf;

    /* --- 镜像存在性校验 ---
     * 不做这一步的话，"镜像没烧"会表现为跳到 Flash 空白区（全 0xFF）
     * 或地址 0，症状很难查。 */
    if(!RF_RomImagePresent())
    {
        RFPRINT("[rf_rom] 镜像不存在或魔数不对（基址 0x%08X）\n",
                (unsigned)RF_ROM_BASE_ADDRESSS);
        return 1;
    }
    RFPRINT("[rf_rom] 镜像版本：%s\n", RF_LibVersion());

    /* --- 搬运镜像 RAM 段（必须最先做）--- */
    RfRomRam_Init();
    RFPRINT("[rf_rom] 已搬运镜像 RAM：highcode %u B / data %u B / bss %u B\n",
            (unsigned)RF_ROM_HIGHCODE_SIZE, (unsigned)RF_ROM_DATA_SIZE,
            (unsigned)RF_ROM_BSS_SIZE);

    /* --- 射频安全访问区（与静态链接版完全一样）--- */
    sys_safe_access_enable();
    R32_MISC_CTRL = (R32_MISC_CTRL & (~(0x3f << 24))) | (0xe << 24);
    sys_safe_access_disable();

    /* --- 通过跳转表调用镜像的初始化 --- */
    memset(&conf, 0, sizeof(conf));
    conf.rfProcessCB = RfApp_ProcessCB;
    conf.processMask = RF_STATE_RX | RF_STATE_RX_CRCERR |
                       RF_STATE_TX_FINISH | RF_STATE_TIMEOUT;

    if(RF_RoleInit(&conf) != 0)
    {
        RFPRINT("[rf_rom] RF_RoleInit 失败\n");
        return 1;
    }

    /* --- 使能中断（纯 RF 库不自己接管，必须应用使能）--- */
    PFIC_EnableIRQ(BLEB_IRQn);
    PFIC_EnableIRQ(BLEL_IRQn);

    return 0;
}

/*=============================================================================
 * 6. 发送 / 接收主循环
 *===========================================================================*/
#if (RF_APP_ROLE == RF_ROLE_TX)
static void RfApp_RunTx(void)
{
    for(uint8_t i = 0; i < RF_APP_PKT_LEN; i++)
    {
        g_txBuf[i] = (uint8_t)(RF_APP_TX_PATTERN_BASE + i);
    }

    RFPRINT("[rf_rom] TX  freq=%lu addr=0x%08lX len=%u\n",
            (unsigned long)RF_APP_FREQ_KHZ, (unsigned long)RF_APP_ACCESS_ADDR,
            (unsigned)RF_APP_PKT_LEN);

    while(1)
    {
        g_txBuf[0] = (uint8_t)(g_txTotal >> 0);
        g_txBuf[1] = (uint8_t)(g_txTotal >> 8);
        g_txBuf[2] = (uint8_t)(g_txTotal >> 16);
        g_txBuf[3] = (uint8_t)(g_txTotal >> 24);

        g_txFinish = 0;
        if(RF_StartTx(&g_txParam) == 0)      /* ← 走跳转表 */
        {
            while(g_txFinish == 0) { }
            g_txTotal++;
        }

        for(volatile uint32_t t = 0; t < RF_APP_TX_GAP_LOOPS; t++) { }

        if(g_txTotal != 0 && (g_txTotal % 100) == 0)
        {
            RFPRINT("[tx] %lu\n", (unsigned long)g_txTotal);
        }
    }
}
#endif

#if (RF_APP_ROLE != RF_ROLE_TX)
static void RfApp_RunRx(void)
{
    RFPRINT("[rf_rom] RX  freq=%lu addr=0x%08lX len=%u\n",
            (unsigned long)RF_APP_FREQ_KHZ, (unsigned long)RF_APP_ACCESS_ADDR,
            (unsigned)RF_APP_PKT_LEN);

    if(RF_SetRx(&g_rxParam) != 0)            /* ← 走跳转表 */
    {
        RFPRINT("[rf_rom] RF_SetRx 失败\n");
        while(1) { }
    }

    while(1)
    {
        if(g_rxCount != 0)
        {
            uint32_t seq;
            g_rxCount = 0;

            seq = (uint32_t)g_rxBuf[0]
                | ((uint32_t)g_rxBuf[1] << 8)
                | ((uint32_t)g_rxBuf[2] << 16)
                | ((uint32_t)g_rxBuf[3] << 24);

            if(g_lastSeq != 0xFFFFFFFFUL && seq != g_lastSeq + 1)
            {
                RFPRINT("[rx] 丢包! 期望 %lu 收到 %lu（CRC错 %lu）\n",
                        (unsigned long)(g_lastSeq + 1), (unsigned long)seq,
                        (unsigned long)g_rxCrcErr);
                g_rxCrcErr = 0;
            }
            g_lastSeq = seq;

            RFPRINT("[rx] seq=%lu data=%02X %02X %02X %02X %02X %02X %02X %02X\n",
                    (unsigned long)seq,
                    g_rxBuf[0], g_rxBuf[1], g_rxBuf[2], g_rxBuf[3],
                    g_rxBuf[4], g_rxBuf[5], g_rxBuf[6], g_rxBuf[7]);

            (void)RF_SetRx(&g_rxParam);      /* 一次一收，重新武装 */
        }

        if(g_timeout != 0)
        {
            g_timeout = 0;
            RFPRINT("[rx] 超时，重新武装\n");
            (void)RF_SetRx(&g_rxParam);
        }
    }
}
#endif

/*=============================================================================
 * 7. main
 *===========================================================================*/
int main(void)
{
    HSECFG_Capacitance(HSECap_18p);
    SetSysClock(CLK_SOURCE_HSE_PLL_100MHz);

#ifdef DEBUG
    GPIOA_SetBits(bTXD_0);
    GPIOA_ModeCfg(bTXD_0, GPIO_ModeOut_PP_5mA);
    UART_Remap(ENABLE, UART_TX_REMAP_PA3, UART_RX_REMAP_PA2);
    UART_DefInit();
    RFPRINT("\n===== 纯 RF（固定地址镜像方式）=====\n");
    RFPRINT("sysclk      = %u Hz\n", (unsigned)GetSysClock());
    RFPRINT("镜像基址    = 0x%08X\n", (unsigned)RF_ROM_BASE_ADDRESSS);
    RFPRINT("镜像 RAM 到 = 0x%08X\n", (unsigned)RF_ROM_RAM_RESERVE_END);
#endif

    RfApp_InitParams();

    if(RfApp_Init() != 0)
    {
        RFPRINT("[rf_rom] 初始化失败\n");
        while(1) { }
    }
    RFPRINT("[rf_rom] 初始化完成\n");

#if (RF_APP_ROLE == RF_ROLE_TX)
    RfApp_RunTx();
#else
    RfApp_RunRx();
#endif

    return 0;
}

/******************************** endfile @ rf_rom_app ************************/
