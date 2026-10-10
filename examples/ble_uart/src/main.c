/*******************************************************************************
 * 文件名称 : main.c
 * 描    述 : 示例工程主入口 —— CH572 BLE 串口透传从机
 *
 *            本示例演示了一个完整的 BLE 从机应用应当怎么写：
 *              1) 配置系统时钟（BLE 要求 32MHz 晶振 + PLL 到 100MHz）
 *              2) 定义协议栈内存堆 MEM_BUF
 *              3) 初始化协议栈 CH57x_BLEInit()
 *              4) 初始化 GAP 从机角色 GAPRole_PeripheralInit()
 *              5) 初始化自定义 GATT 服务
 *              6) 进入 TMOS 主循环
 *
 *            连接上之后：
 *              - 手机写 0xFFF3 特征值 -> 数据从串口发出
 *              - 串口收到的数据 -> 通过 0xFFF4 特征值 Notify 给手机
 ******************************************************************************/
#include "CONFIG.h"
#include "ble_uart_service.h"

/* 需要 UART_Remap / UART_DefInit 等串口 API（调试打印用） */
#include "CH57x_uart.h"

/*********************************************************************
 * 全局变量
 */

/**
 * @brief 协议栈内存堆。
 *
 * 这是整个工程里唯一必须由应用层提供的全局数组。
 * 大小由 BLE_MEMHEAP_SIZE 决定，必须 4 字节对齐。
 */
__attribute__((aligned(4))) uint32_t MEM_BUF[BLE_MEMHEAP_SIZE / 4];

#if(defined(BLE_MAC)) && (BLE_MAC == TRUE)
/**
 * @brief 自定义蓝牙 MAC 地址（小端序）。
 *        只有在预定义宏 BLE_MAC=1 时才会被使用。
 *        注意：这里写的是示例值，量产时必须换成自己的地址段。
 */
/* ★ 设备地址（BLE_MAC = TRUE 时使用）★
 *
 * 为什么需要它：
 *   官方 Peripheral 示例依赖芯片出厂 MAC（走 GetMACAddress() 读 INFO 区），
 *   但实测 CH572D-R0-1V1 的出厂 MAC 位置读出全 0。
 *   BLE 规范里 00:00:00:00:00:00 是保留值，手机会直接忽略这种广播设备 ——
 *   表现为"协议栈初始化全部成功，但手机搜不到"。
 *
 * 这里给一个合法的本地管理地址（最高字节 0xC2 = 0b11000010，最高两位是 11，符合 BLE 静态地址要求
 * 范围，不会冒用任何真实厂商的 OUI）。做产品时请换成你申请的 OUI。
 *
 * 顺序：数组按**大端显示顺序**书写（和手机上看到的一致），
 *       协议栈内部会反转成小端，这一点官方 MCU.c 已经处理。
 */
const uint8_t MacAddr[6] = {0xC2, 0x00, 0x00, 0x00, 0x00, 0x01};
#endif

/*********************************************************************
 * @fn      Main_Circulation
 *
 * @brief   主循环：不停地把控制权交给 TMOS 调度器。
 *
 * @note    所有 BLE 事件、定时器、消息都在 TMOS_SystemProcess() 内部处理，
 *          应用不要在这里写阻塞逻辑，否则会破坏 BLE 的连接时序。
 */
__HIGH_CODE
__attribute__((noinline))
void Main_Circulation(void)
{
    while(1)
    {
        TMOS_SystemProcess();
    }
}

/*********************************************************************
 * @fn      main
 *
 * @brief   主函数
 *
 * @return  不会返回
 */
int main(void)
{
    /* ---- 1. 关闭两线调试口，释放 PA7/PA8（如果产品不用调试口） ---- */
    R16_PIN_ALTERNATE &= ~RB_PIN_DEBUG_EN;

    /* ---- 2. 配置 32MHz 晶振负载电容与系统时钟 ----
     *        BLE 协议栈要求 HSE 为 32MHz，系统主频用 PLL 倍频到 100MHz。 */
    HSECFG_Capacitance(HSECap_18p);
    SetSysClock(CLK_SOURCE_HSE_PLL_100MHz);

#if(defined(HAL_SLEEP)) && (HAL_SLEEP == TRUE)
    /* 开启睡眠时，把所有 IO 设为上拉输入以降低漏电 */
    GPIOA_ModeCfg(GPIO_Pin_All, GPIO_ModeIN_PU);
#endif

    /* ---- 3. 初始化调试串口（可选，PRINT 宏打开时才需要） ---- */
#ifdef DEBUG
    GPIOA_SetBits(bTXD_0);
    GPIOA_ModeCfg(bTXD_0, GPIO_ModeOut_PP_5mA);     /* 先输出高电平再配置为输出，避免毛刺 */
    UART_Remap(ENABLE, UART_TX_REMAP_PA3, UART_RX_REMAP_PA2);
    UART_DefInit();
#endif
    PRINT("%s\n", VER_FILE);

    /* ---- 4. 初始化 BLE 协议栈（内存堆 / SNV / 回调 / RTC 时基） ---- */
    CH57x_BLEInit();

    /* ---- 5. 初始化 HAL 任务 ---- */
    HAL_Init();

    /* ---- 6. 初始化 GAP 从机角色 ---- */
    GAPRole_PeripheralInit();

    /* ---- 7. 初始化自定义 GATT 服务与业务逻辑 ---- */
    BLE_UART_Init();

    /* ---- 8. 进入主循环 ---- */
    Main_Circulation();

    /* 不会执行到这里 */
    return 0;
}

/******************************** endfile @ main ******************************/
