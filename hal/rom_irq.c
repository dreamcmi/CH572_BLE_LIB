/* Application-owned interrupt entries. Vector table can remain in Flash.
 *
 * ===========================================================================
 * 为什么这里还有一个 HAL_RomVtfRefresh()
 *
 * ★ 背景：CH572 的 BLEB/BLEL 中断走 PFIC 的 VTF(Vector-Table-Free) 通道
 *   #2/#3，不走中断向量表。协议栈在 BB_DevInit / LLE_DevInit 里写：
 *
 *       VTFIDR[3] = 20,  VTFADDR[3] = <BB_IRQLibFunction>  | ENABLE
 *       VTFIDR[2] = 21,  VTFADDR[2] = <LLE_IRQFIOFHandler> | ENABLE
 *
 * ★ 坑：BLE_RegInit() 内部会再调一次 LLE_DevInit + BB_DevInit（反汇编实证：
 *   ROM 0x2A296 / 0x2A2A6），所以**每次周期校准都会把 VTFADDR 重写回 ROM 的
 *   地址**。周期校准由 HAL_REG_INIT_EVENT 驱动，首次在 HAL_Init 后 800 tick、
 *   之后每 BLE_CALIBRATION_PERIOD（120 秒）一次。
 *
 *   后果：如果只在 main() 里武装一次 VTF（早期做法），第一次校准就把它覆盖掉，
 *   之后 Debug_*IrqHook 再也不会被调用，心跳里的"BLE中断="永远是 0 ——
 *   而设备功能其实完全正常（ROM 的 ISR 一直在干活）。这个数字会把人带偏。
 *
 * ★ 所以：每次校准处理完都要重新武装一次，并且用 HAL_RomVtfRefresh() 做
 *   "只在确实需要时才写"的自检 —— 这样既能保持计数，又不会在正常路径上
 *   做无谓的寄存器写。
 *
 * ★ 仅 DEBUG 生效。发布构建不碰 VTF，保持 ROM 出厂路径少一层转接。
 * =========================================================================== */

#include "CONFIG.h"

#ifdef CH572BLE_ROM

void BB_IRQHandler(void);
void LLE_IRQHandler(void);

/* 应用包装函数的地址，带 ENABLE 位（与 SetVTFIRQ 内部写法一致：addr|0x1）。 */
#define ROM_VTF_LLE_EXPECT  (((uint32_t)LLE_IRQHandler) | 0x1u)
#define ROM_VTF_BB_EXPECT   (((uint32_t)BB_IRQHandler)  | 0x1u)

__INTERRUPT __HIGH_CODE void BB_IRQHandler(void)
{
    Debug_BbIrqHook();
}

__INTERRUPT __HIGH_CODE void LLE_IRQHandler(void)
{
    Debug_LleIrqHook();
}

/*********************************************************************
 * @fn      HAL_RomVtfRefresh
 *
 * @brief   确认 PFIC VTF 仍指向应用侧的计数包装函数；被改回 ROM 就重新武装。
 *
 * @return  1 = 本次做了重新武装（说明有人改过 VTF）；0 = 本来就是对的
 *
 * @note    调用点有两个：
 *             - main() 里 CH57x_BLEInit() 之后的首次武装
 *             - HAL_ProcessEvent() 的周期校准分支，在 BLE_RegInit() 之后
 *          第二个是必须的，理由见文件头。
 */
uint8_t HAL_RomVtfRefresh(void)
{
#ifdef DEBUG
    uint8_t fixed = 0;

    if(PFIC->VTFADDR[2] != ROM_VTF_LLE_EXPECT)
    {
        SetVTFIRQ((uint32_t)LLE_IRQHandler, BLEL_IRQn, 2, ENABLE);
        fixed = 1;
    }
    if(PFIC->VTFADDR[3] != ROM_VTF_BB_EXPECT)
    {
        SetVTFIRQ((uint32_t)BB_IRQHandler, BLEB_IRQn, 3, ENABLE);
        fixed = 1;
    }
    return fixed;
#else
    /* 发布构建：不动 VTF，中断直接进 ROM 内的真 ISR。 */
    return 0;
#endif
}

#endif /* CH572BLE_ROM */
