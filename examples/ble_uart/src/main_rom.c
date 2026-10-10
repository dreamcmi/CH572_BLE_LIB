/* CH572 fixed-address BLE image example. Build with CH572BLE_ROM=1. */
#include "CONFIG.h"
#include "ble_uart_service.h"
#include "rom_ram_info.h"

/* 应用侧中断包装函数（实现在 hal/rom_irq.c，__INTERRUPT + __HIGH_CODE）。
 * 这里显式声明：SetVTFIRQ 需要它们的地址，而它们不是协议栈头文件里的符号。 */
void BB_IRQHandler(void);
void LLE_IRQHandler(void);

__attribute__((aligned(4))) uint32_t MEM_BUF[BLE_MEMHEAP_SIZE / 4];

#if defined(BLE_MAC) && (BLE_MAC == TRUE)
/* Demonstration address, in display order. Assign a unique address per device. */
const uint8_t MacAddr[6] = {0xC2, 0x00, 0x00, 0x00, 0x00, 0x01};
#endif

static void CopyRomRam(uint32_t vma, uint32_t lma, uint32_t size)
{
    volatile uint8_t *dst = (volatile uint8_t *)vma;
    const uint8_t *src = (const uint8_t *)lma;
    for(uint32_t i = 0; i < size; ++i)
        dst[i] = src[i];
}

/* The image has no reset handler. Initialize it before calling ANY image API. */
static void RomRam_Init(void)
{
    CopyRomRam(ROM_RAM_HIGHCODE_VMA, ROM_RAM_HIGHCODE_LMA, ROM_RAM_HIGHCODE_SIZE);
    CopyRomRam(ROM_RAM_DATA_VMA, ROM_RAM_DATA_LMA, ROM_RAM_DATA_SIZE);
    volatile uint8_t *dst = (volatile uint8_t *)ROM_RAM_BSS_START;
    for(uint32_t i = 0; i < ROM_RAM_BSS_SIZE; ++i)
        dst[i] = 0;
    __asm__ volatile("fence.i" ::: "memory");
}

/* ===========================================================================
 * 中断接线：为什么第一次武装还不够
 *
 * ★★ CH572 的 BLEB/BLEL 不走中断向量表，走 PFIC 的 VTF(Vector-Table-Free) ★★
 *
 *   这是第六次实机排查的结论，由 ROM 镜像反汇编直接证实：
 *
 *     BB_DevInit  (ROM 0x357A4)
 *         sb 20, 0x53(0xE000E000)   -> VTFIDR[3] = BLEB_IRQn = 20
 *         VTFADDR[3] = 0x200000DC <BB_IRQLibFunction> | ENABLE
 *     LLE_DevInit (ROM 0x35CD6)
 *         sb 21, 0x52(0xE000E000)   -> VTFIDR[2] = BLEL_IRQn = 21
 *         VTFADDR[2] = 0x200004B6 <LLE_IRQFIOFHandler> | ENABLE
 *
 *   ★ 关键：BLE_RegInit() 内部会再调一次 LLE_DevInit + BB_DevInit
 *     （ROM 0x2A296 / 0x2A2A6），而 HAL 的周期校准分支会调 BLE_RegInit()。
 *     所以 VTFADDR 会被**周期性重写回 ROM 的地址**。
 *
 *     只在 main() 里武装一次是不够的 —— 首次校准（HAL_Init 后 800 tick，
 *     约 500ms）就会把它覆盖掉。这曾经导致实机上"BLE中断="从心跳第 2 次
 *     起一直是 0，而设备其实广播/连接全都正常，非常容易误判成射频有问题。
 *
 *     现在的做法：武装逻辑集中在 hal/rom_irq.c 的 HAL_RomVtfRefresh()，
 *     HAL_ProcessEvent 的校准分支处理完 BLE_RegInit() 之后会再调一次。
 *     main() 这里只做首次武装（让上电后到首次校准之间也能计数）。
 *
 *   为什么还要留向量表里那两项：万一某个 ROM 修订版没有自己武装 VTF，
 *   向量表就是唯一入口。包装函数不到 40 字节且在 .highcode 里，留着是免费保险。
 * =========================================================================== */
static void InstallRomIrqHandlers(void)
{
    rom_app_hooks_t hooks = {0};
    const rom_handles_t *h = CH572BLE_ROM_GetHandles();
    const char *version;

    if(!h || h->magic != ROM_HANDLE_MAGIC || !h->bb_irq || !h->lle_irq)
    {
        PRINT("ROM handles invalid\n");
        while(1) { }
    }

    hooks.magic = ROM_HOOK_MAGIC;
    hooks.ble_irq_handler = Debug_BbIrqHook;
    hooks.lle_irq_handler = Debug_LleIrqHook;
    /* Register outside PRINT: the release PRINT macro discards its arguments.
     * 对"未自己武装 VTF"的 ROM 修订版，这条钩子路径是唯一通路，必须保留。 */
    version = CH572BLE_ROM_Init(&hooks);
    (void)version;
    /* hal/rom_irq.c supplies strong ISR symbols to the Flash vector table. */
    PRINT("ROM version: %s\n", version);
}

int main(void)
{
    /* Keep the debug port enabled so a stalled image remains recoverable. */
    HSECFG_Capacitance(HSECap_18p);
    SetSysClock(CLK_SOURCE_HSE_PLL_100MHz);
#ifdef DEBUG
    GPIOA_SetBits(bTXD_0);
    GPIOA_ModeCfg(bTXD_0, GPIO_ModeOut_PP_5mA);
    UART_Remap(ENABLE, UART_TX_REMAP_PA3, UART_RX_REMAP_PA2);
    UART_DefInit();
    PRINT("\nCH572 BLE fixed image, sysclk=%u\n", (unsigned)GetSysClock());
#endif

    /* Validate the Flash header without executing code from an absent image. */
    if(*(const volatile uint32_t *)LIB_FLASH_BASE_ADDRESSS != ROM_HOOK_MAGIC)
    {
        PRINT("BLE image missing at 0x%08X\n", (unsigned)LIB_FLASH_BASE_ADDRESSS);
        while(1) { }
    }
    RomRam_Init();
    PRINT("[1/4] ROM RAM initialized (%u bytes)\n", (unsigned)ROM_RAM_RESERVE_SIZE);
    InstallRomIrqHandlers();
    __asm__ volatile("csrsi mstatus, 8" ::: "memory");
    PRINT("[2/4] BLE init\n");
    CH57x_BLEInit();
    /* 首次武装 VTF 指向应用包装函数（仅 DEBUG 构建真正动作）。
     * 协议栈已经武装过一次指向 ROM 自己，这里抢回来给诊断用；
     * 之后每次周期校准后由 HAL_ProcessEvent 再检查/重装。 */
    (void)HAL_RomVtfRefresh();
#ifdef DEBUG
    PRINT("VTF armed for diagnostics: [2]=0x%08X [3]=0x%08X\n",
          (unsigned)PFIC->VTFADDR[2], (unsigned)PFIC->VTFADDR[3]);
#endif
    HAL_Init();
    PRINT("[3/4] GAP and GATT init\n");
    GAPRole_PeripheralInit();
    BLE_UART_Init();
    PRINT("[4/4] TMOS running, name=CH572-BLE-UART\n");
    while(1)
        TMOS_SystemProcess();
}
