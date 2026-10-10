/*******************************************************************************
 * 文件名称 : ble_uart_service.h
 * 描    述 : 自定义 BLE 串口透传服务（GATT 服务层）对外接口
 *
 *            服务定义：
 *              Service UUID : 0xFFE0
 *              CHAR1 (0xFFE1) 只读，1 字节      —— 用于读回简单参数
 *              CHAR2 (0xFFE2) 读写，BLE_BUFF_MAX_LEN-7 字节 —— 通用数据缓冲区
 *              CHAR3 (0xFFE3) 只写，BLE_BUFF_MAX_LEN-7 字节 —— 手机 -> 设备（透传下行）
 *              CHAR4 (0xFFE4) Notify，BLE_BUFF_MAX_LEN-7 字节 —— 设备 -> 手机（透传上行）
 *              CHAR5 (0xFFE5) 需要认证才能读，5 字节 —— 演示受保护的属性
 *
 *            注意上面写的是 -7 不是 -4：BLE 单包有效载荷 = ATT_MTU - 3，而
 *            ATT_MTU = BLE_BUFF_MAX_LEN - 4（协议栈在 L2CAP 头里占 4 字节），
 *            所以 (BLE_BUFF_MAX_LEN - 4) - 3 = BLE_BUFF_MAX_LEN - 7。
 *            BLE_BUFF_MAX_LEN = 27 时 ATT_MTU = 23，单包可带 20 字节。
 ******************************************************************************/
#ifndef __BLE_UART_SERVICE_H
#define __BLE_UART_SERVICE_H

#ifdef __cplusplus
extern "C" {
#endif

#include "CONFIG.h"

/*********************************************************************
 * 常量定义
 */

/** 服务 UUID */
#define BLE_UART_SERV_UUID          0xFFE0

/** 各特征值 UUID */
#define BLE_UART_CHAR1_UUID         0xFFE1
#define BLE_UART_CHAR2_UUID         0xFFE2
#define BLE_UART_CHAR3_UUID         0xFFE3
#define BLE_UART_CHAR4_UUID         0xFFE4
#define BLE_UART_CHAR5_UUID         0xFFE5

/** 服务在服务位图中的位置 */
#define BLE_UART_SERVICE            0x00000001

/**
 * @brief 透传数据长度。
 *        等于 ATT_MTU - 3。BLE_BUFF_MAX_LEN 为 27 时 ATT_MTU=23，单包可带 20 字节。
 */
#define BLE_UART_DATA_LEN           (BLE_BUFF_MAX_LEN - 7)

/** 参数 ID，供 BLE_UART_SetParameter / GetParameter 使用 */
#define BLE_UART_CHAR1              0
#define BLE_UART_CHAR2              1
#define BLE_UART_CHAR3              2
#define BLE_UART_CHAR4              3
#define BLE_UART_CHAR5              4

/*********************************************************************
 * 回调类型
 */

/**
 * @brief 特征值变化回调
 * @param paramID - 变化的参数 ID（BLE_UART_CHARx）
 * @param pValue  - 新数据指针
 * @param len     - 数据长度
 */
typedef void (*bleUartChange_t)(uint8_t paramID, uint8_t *pValue, uint16_t len);

/** 应用回调集合 */
typedef struct
{
    bleUartChange_t pfnChange;      /*!< 特征值被手机写入时回调 */
} bleUartCBs_t;

/*********************************************************************
 * API
 */

/**
 * @brief   注册 GATT 服务并启动业务逻辑
 * @note    应在 GAPRole_PeripheralInit() 之后、主循环之前调用。
 */
extern void BLE_UART_Init(void);

/**
 * @brief   本应用的 TMOS 任务事件处理函数
 * @param   task_id - TMOS 分配的任务号
 * @param   events  - 事件位图
 * @return  未处理的事件位
 * @note    由 TMOS_ProcessEventRegister() 注册，不需要手工调用。
 */
extern uint16_t BLE_UART_ProcessEvent(uint8_t task_id, uint16_t events);

/**
 * @brief   把 GATT 服务注册到 GATT Server
 * @param   services - 服务位图，传 BLE_UART_SERVICE
 * @return  SUCCESS 或错误码
 */
extern bStatus_t BLE_UART_AddService(uint32_t services);

/**
 * @brief   注册应用回调
 * @param   appCallbacks - 回调集合指针
 * @return  SUCCESS 或错误码
 */
extern bStatus_t BLE_UART_RegisterAppCBs(bleUartCBs_t *appCallbacks);

/**
 * @brief   写特征值（仅用于 CHAR1/CHAR2/CHAR5，CHAR4 用 BLE_UART_Notify）
 * @param   param - 参数 ID
 * @param   len   - 数据长度
 * @param   value - 数据指针
 * @return  SUCCESS 或错误码
 */
extern bStatus_t BLE_UART_SetParameter(uint8_t param, uint8_t len, void *value);

/**
 * @brief   读特征值
 * @param   param - 参数 ID
 * @param   value - 输出缓冲区
 * @return  SUCCESS 或错误码
 */
extern bStatus_t BLE_UART_GetParameter(uint8_t param, void *value);

/**
 * @brief   通过 CHAR4 发送 Notify 给手机
 * @param   connHandle - 连接句柄
 * @param   pNoti      - 通知结构体（由 GATT_bm_alloc 分配）
 * @return  SUCCESS 或错误码
 */
extern bStatus_t BLE_UART_Notify(uint16_t connHandle, attHandleValueNoti_t *pNoti);

/**
 * @brief   便捷接口：直接发送一段数据给手机
 * @param   data - 数据指针
 * @param   len  - 数据长度（不超过 BLE_UART_DATA_LEN）
 * @return  SUCCESS / bleNotConnected / bleMemAllocError
 */
extern bStatus_t BLE_UART_SendData(uint8_t *data, uint16_t len);

/**
 * @brief   查询当前是否已连接
 * @return  TRUE 表示已连接
 */
extern uint8_t BLE_UART_IsConnected(void);

/**
 * @brief   获取当前连接句柄
 * @return  连接句柄，未连接时返回 GAP_CONNHANDLE_INIT
 */
extern uint16_t BLE_UART_GetConnHandle(void);

/**
 * @brief   由 GAP 角色层在连接建立/断开时通知服务层
 * @param   connHandle - 连接句柄；断开时传 GAP_CONNHANDLE_INIT
 * @note    仅供 ble_uart.c 内部调用，业务代码不需要直接调。
 */
extern void BLE_UART_OnLinkEstablished(uint16_t connHandle);

#ifdef __cplusplus
}
#endif

#endif /* __BLE_UART_SERVICE_H */
