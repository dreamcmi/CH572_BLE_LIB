/*******************************************************************************
 * 文件名称 : test_should_pass.c
 * 描    述 : 正向测试用例 —— 只使用 CH572 支持的从机 API，应当编译通过。
 *
 *            同时验证：
 *              1) ch572_ble.h 的能力探测宏取值符合预期；
 *              2) 从机角色相关的 API 声明齐全、可正常引用；
 *              3) 用 #if 做平台条件编译的写法可行。
 *
 *            验证方式：运行 scripts\run_tests.ps1
 ******************************************************************************/
#include "CONFIG.h"

/* ---- 1. 能力宏取值检查（用预处理期断言，写错会直接编译失败） ---- */
#if !CH572_BLE_HAS_PERIPHERAL
#error "CH572 应当支持从机角色"
#endif
#if !CH572_BLE_HAS_BROADCASTER
#error "CH572 应当支持广播者角色"
#endif
#if CH572_BLE_HAS_CENTRAL
#error "CH572 不应声称支持主机角色"
#endif
#if CH572_BLE_HAS_OBSERVER
#error "CH572 不应声称支持观察者角色"
#endif
#if CH572_BLE_HAS_GATT_CLIENT
#error "CH572 不应声称支持 GATT 客户端"
#endif

/* ---- 2. 从机 API 可引用性检查 ---- */
void test_peripheral_api_available(void)
{
    /* 这些函数在 CH572 的固件库里都存在，取地址即可证明确实有声明 */
    (void)&GAPRole_PeripheralInit;
    (void)&GAPRole_PeripheralStartDevice;
    (void)&GAPRole_PeripheralConnParamUpdateReq;
    (void)&GAPRole_SetParameter;
    (void)&GAPRole_GetParameter;
    (void)&GAPRole_TerminateLink;
    (void)&GAPRole_UpdateLink;
    (void)&GAPRole_UpdatePHY;
    (void)&GAPRole_ReadRssiCmd;
    (void)&GATTServApp_RegisterService;
    (void)&GATTServApp_InitCharCfg;
    (void)&GATTServApp_ReadCharCfg;
    (void)&GATTServApp_ProcessCCCWriteReq;
    (void)&GATT_Notification;
    (void)&GATT_Indication;
    (void)&GAPBondMgr_SetParameter;
    (void)&GAPBondMgr_GetParameter;
    (void)&GAPBondMgr_PasscodeRsp;
    (void)&GAPBondMgr_PeriSecurityReq;
    (void)&GGS_SetParameter;
    (void)&GGS_GetParameter;
    (void)&TMOS_ProcessEventRegister;
    (void)&TMOS_SystemProcess;
    (void)&tmos_start_task;
    (void)&tmos_stop_task;
    (void)&tmos_set_event;
    (void)&tmos_start_reload_task;
    (void)&BLE_LibInit;
    (void)&CH57x_BLEInit;
    (void)&BLE_ReadRssi;
    (void)&BLE_ReadCfo;
    (void)&RF_Tx;
    (void)&RF_Rx;
}

/* ---- 3. 跨平台条件编译写法示范 ---- */
void test_conditional_compilation(void)
{
#if CH572_BLE_HAS_CENTRAL
    /* 这段在 CH572 上不会被编译，因此即使里面写了主机 API 也不报错 */
    GAPRole_CentralInit();
    GAPRole_CentralStartDevice(0, 0, 0);
#else
    /* CH572 走这条路径 */
    GAPRole_PeripheralInit();
#endif
}

/* ---- 4. 编译期常量检查：配置项类型与范围合理 ---- */
char test_assert_heap_size[(BLE_MEMHEAP_SIZE >= 4096) ? 1 : -1];
char test_assert_heap_align[(BLE_MEMHEAP_SIZE % 4 == 0) ? 1 : -1];
char test_assert_buf_len[(BLE_BUFF_MAX_LEN >= 27) ? 1 : -1];
