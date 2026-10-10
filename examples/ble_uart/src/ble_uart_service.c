/*******************************************************************************
 * 文件名称 : ble_uart_service.c
 * 描    述 : 自定义 BLE 串口透传服务实现（GATT Server 侧）
 *
 *            本文件是"应用怎么写 GATT 服务"的完整范例，包含：
 *              - 属性表（Service / Characteristic 声明 / 值 / CCC / 用户描述）
 *              - 读回调、写回调、连接状态回调
 *              - 参数读写接口与 Notify 发送接口
 *
 *            属性表的结构是固定的，顺序不能乱：
 *              [0] 服务声明
 *              [1] 特征值1 声明   [2] 特征值1 值   [3] 特征值1 用户描述
 *              [4] 特征值2 声明   [5] 特征值2 值   [6] 特征值2 用户描述
 *              [7] 特征值3 声明   [8] 特征值3 值   [9] 特征值3 用户描述
 *              [10] 特征值4 声明  [11] 特征值4 值  [12] 特征值4 CCC  [13] 用户描述
 *              [14] 特征值5 声明  [15] 特征值5 值  [16] 特征值5 用户描述
 ******************************************************************************/
#include "CONFIG.h"
#include "ble_uart_service.h"

/*********************************************************************
 * 常量
 */

/** 特征值 4（Notify）在属性表里的下标，发送通知时需要用它取 handle */
#define BLE_UART_CHAR4_VALUE_IDX        11

/** 用户描述字符串 */
static uint8_t bleUartChar1UserDesp[] = "Char1 Read\0";
static uint8_t bleUartChar2UserDesp[] = "Char2 R/W\0";
static uint8_t bleUartChar3UserDesp[] = "Char3 Write\0";
static uint8_t bleUartChar4UserDesp[] = "Char4 Notify\0";
static uint8_t bleUartChar5UserDesp[] = "Char5 Auth\0";

/*********************************************************************
 * 局部变量
 */

/** 应用回调集合 */
static bleUartCBs_t *bleUart_AppCBs = NULL;

/** 服务 UUID（小端序存放） */
static const uint8_t bleUartServUUID[ATT_BT_UUID_SIZE] = {
    LO_UINT16(BLE_UART_SERV_UUID), HI_UINT16(BLE_UART_SERV_UUID)};

/** 各特征值 UUID（小端序存放） */
static const uint8_t bleUartChar1UUID[ATT_BT_UUID_SIZE] = {
    LO_UINT16(BLE_UART_CHAR1_UUID), HI_UINT16(BLE_UART_CHAR1_UUID)};
static const uint8_t bleUartChar2UUID[ATT_BT_UUID_SIZE] = {
    LO_UINT16(BLE_UART_CHAR2_UUID), HI_UINT16(BLE_UART_CHAR2_UUID)};
static const uint8_t bleUartChar3UUID[ATT_BT_UUID_SIZE] = {
    LO_UINT16(BLE_UART_CHAR3_UUID), HI_UINT16(BLE_UART_CHAR3_UUID)};
static const uint8_t bleUartChar4UUID[ATT_BT_UUID_SIZE] = {
    LO_UINT16(BLE_UART_CHAR4_UUID), HI_UINT16(BLE_UART_CHAR4_UUID)};
static const uint8_t bleUartChar5UUID[ATT_BT_UUID_SIZE] = {
    LO_UINT16(BLE_UART_CHAR5_UUID), HI_UINT16(BLE_UART_CHAR5_UUID)};

/** 服务声明属性 */
static const gattAttrType_t bleUartService = {ATT_BT_UUID_SIZE, bleUartServUUID};

/** 各特征值的属性标志位 */
static uint8_t bleUartChar1Props = GATT_PROP_READ | GATT_PROP_WRITE;
static uint8_t bleUartChar2Props = GATT_PROP_READ | GATT_PROP_WRITE;
static uint8_t bleUartChar3Props = GATT_PROP_WRITE | GATT_PROP_WRITE_NO_RSP;
static uint8_t bleUartChar4Props = GATT_PROP_NOTIFY;
static uint8_t bleUartChar5Props = GATT_PROP_READ;

/** 各特征值的实际数据 */
static uint8_t bleUartChar1[1] = {0};
static uint8_t bleUartChar2[BLE_UART_DATA_LEN] = {0};
static uint8_t bleUartChar3[BLE_UART_DATA_LEN] = {0};
static uint8_t bleUartChar4[BLE_UART_DATA_LEN] = {0};
static uint8_t bleUartChar5[5] = {0};

/** 特征值 4 的 CCC 描述符（每个连接一份） */
static gattCharCfg_t bleUartChar4Config[PERIPHERAL_MAX_CONNECTION];

/** 当前连接句柄 */
static uint16_t bleUartConnHandle = GAP_CONNHANDLE_INIT;

/*********************************************************************
 * 属性表
 */
static gattAttribute_t bleUartAttrTbl[] = {
    /* [0] 服务声明 */
    {
        {ATT_BT_UUID_SIZE, primaryServiceUUID},     /* 属性类型：Primary Service */
        GATT_PERMIT_READ,                           /* 权限：可读 */
        0,                                          /* handle：由协议栈分配 */
        (uint8_t *)&bleUartService                  /* 属性值：服务 UUID */
    },

    /* ---- 特征值 1：可读可写，1 字节 ---- */
    /* [1] 声明 */
    {
        {ATT_BT_UUID_SIZE, characterUUID},
        GATT_PERMIT_READ,
        0,
        &bleUartChar1Props},
    /* [2] 值 */
    {
        {ATT_BT_UUID_SIZE, bleUartChar1UUID},
        GATT_PERMIT_READ | GATT_PERMIT_WRITE,
        0,
        bleUartChar1},
    /* [3] 用户描述 */
    {
        {ATT_BT_UUID_SIZE, charUserDescUUID},
        GATT_PERMIT_READ,
        0,
        bleUartChar1UserDesp},

    /* ---- 特征值 2：可读可写，多字节通用缓冲区 ---- */
    {
        {ATT_BT_UUID_SIZE, characterUUID},
        GATT_PERMIT_READ,
        0,
        &bleUartChar2Props},
    {
        {ATT_BT_UUID_SIZE, bleUartChar2UUID},
        GATT_PERMIT_READ | GATT_PERMIT_WRITE,
        0,
        bleUartChar2},
    {
        {ATT_BT_UUID_SIZE, charUserDescUUID},
        GATT_PERMIT_READ,
        0,
        bleUartChar2UserDesp},

    /* ---- 特征值 3：只写（手机 -> 设备，透传下行） ---- */
    {
        {ATT_BT_UUID_SIZE, characterUUID},
        GATT_PERMIT_READ,
        0,
        &bleUartChar3Props},
    {
        {ATT_BT_UUID_SIZE, bleUartChar3UUID},
        GATT_PERMIT_WRITE,                          /* 不允许读，只能写 */
        0,
        bleUartChar3},
    {
        {ATT_BT_UUID_SIZE, charUserDescUUID},
        GATT_PERMIT_READ,
        0,
        bleUartChar3UserDesp},

    /* ---- 特征值 4：Notify（设备 -> 手机，透传上行） ---- */
    {
        {ATT_BT_UUID_SIZE, characterUUID},
        GATT_PERMIT_READ,
        0,
        &bleUartChar4Props},
    /* [11] 值：注意这个下标要和 BLE_UART_CHAR4_VALUE_IDX 保持一致 */
    {
        {ATT_BT_UUID_SIZE, bleUartChar4UUID},
        0,                                          /* Notify 特征值本身没有读写权限 */
        0,
        bleUartChar4},
    /* [12] CCC：客户端特征配置，由协议栈读写 */
    {
        {ATT_BT_UUID_SIZE, clientCharCfgUUID},
        GATT_PERMIT_READ | GATT_PERMIT_WRITE,
        0,
        (uint8_t *)bleUartChar4Config},
    /* [13] 用户描述 */
    {
        {ATT_BT_UUID_SIZE, charUserDescUUID},
        GATT_PERMIT_READ,
        0,
        bleUartChar4UserDesp},

    /* ---- 特征值 5：需要认证才能读，演示权限控制 ---- */
    {
        {ATT_BT_UUID_SIZE, characterUUID},
        GATT_PERMIT_READ,
        0,
        &bleUartChar5Props},
    {
        {ATT_BT_UUID_SIZE, bleUartChar5UUID},
        GATT_PERMIT_AUTHEN_READ,                    /* 必须配对加密后才能读 */
        0,
        bleUartChar5},
    {
        {ATT_BT_UUID_SIZE, charUserDescUUID},
        GATT_PERMIT_READ,
        0,
        bleUartChar5UserDesp},
};

/*********************************************************************
 * 回调函数前置声明
 */
static bStatus_t bleUart_ReadAttrCB(uint16_t connHandle, gattAttribute_t *pAttr,
                                    uint8_t *pValue, uint16_t *pLen, uint16_t offset,
                                    uint16_t maxLen, uint8_t method);
static bStatus_t bleUart_WriteAttrCB(uint16_t connHandle, gattAttribute_t *pAttr,
                                     uint8_t *pValue, uint16_t len, uint16_t offset,
                                     uint8_t method);
static void bleUart_HandleConnStatusCB(uint16_t connHandle, uint8_t changeType);

/** 服务回调集合 */
static gattServiceCBs_t bleUartCBs = {
    bleUart_ReadAttrCB,     /* 读回调 */
    bleUart_WriteAttrCB,    /* 写回调 */
    NULL                    /* 授权回调，暂不使用 */
};

/*********************************************************************
 * 内部函数
 */

/*********************************************************************
 * @fn      bleUart_OnPhoneData
 *
 * @brief   手机通过 CHAR3 写入数据后的处理（透传下行）。
 *
 * @param   pData - 数据指针
 * @param   len   - 数据长度
 *
 * @note    这里就是"蓝牙收到的数据要做什么"的落地点。
 *          示例仅把数据回环给 CHAR2 并原样 Notify 回去，
 *          产品上应改成 UART_SendString() 之类真正的外设操作。
 */
static void bleUart_OnPhoneData(uint8_t *pData, uint16_t len)
{
    /* 1) 保存到 CHAR2，方便手机回读校验 */
    if(len > BLE_UART_DATA_LEN)
    {
        len = BLE_UART_DATA_LEN;
    }
    tmos_memcpy(bleUartChar2, pData, len);

    /* 2) 原样通过 CHAR4 回传（回环测试）。
     *    真实产品这里应改为：把数据写到串口 / 驱动外设。 */
    BLE_UART_SendData(pData, len);

    /* 3) 通知应用层 */
    if(bleUart_AppCBs && bleUart_AppCBs->pfnChange)
    {
        bleUart_AppCBs->pfnChange(BLE_UART_CHAR3, pData, len);
    }
}

/*********************************************************************
 * 对外接口实现
 */

/*********************************************************************
 * @fn      BLE_UART_AddService
 *
 * @brief   把本服务的属性表注册到 GATT Server
 *
 * @param   services - 服务位图
 * @return  SUCCESS 或错误码
 */
bStatus_t BLE_UART_AddService(uint32_t services)
{
    bStatus_t status = SUCCESS;

    /* 初始化 CCC 描述符（用 INVALID_CONNHANDLE 表示初始化所有连接） */
    GATTServApp_InitCharCfg(INVALID_CONNHANDLE, bleUartChar4Config);

    /* 注册连接状态变化回调，断连时需要复位 CCC */
    linkDB_Register(bleUart_HandleConnStatusCB);

    if(services & BLE_UART_SERVICE)
    {
        status = GATTServApp_RegisterService(bleUartAttrTbl,
                                             GATT_NUM_ATTRS(bleUartAttrTbl),
                                             GATT_MAX_ENCRYPT_KEY_SIZE,
                                             &bleUartCBs);
    }

    return status;
}

/*********************************************************************
 * @fn      BLE_UART_RegisterAppCBs
 *
 * @brief   注册应用回调
 */
bStatus_t BLE_UART_RegisterAppCBs(bleUartCBs_t *appCallbacks)
{
    if(appCallbacks)
    {
        bleUart_AppCBs = appCallbacks;
        return SUCCESS;
    }
    return bleAlreadyInRequestedMode;
}

/*********************************************************************
 * @fn      BLE_UART_SetParameter
 *
 * @brief   写特征值
 */
bStatus_t BLE_UART_SetParameter(uint8_t param, uint8_t len, void *value)
{
    bStatus_t ret = SUCCESS;

    switch(param)
    {
        case BLE_UART_CHAR1:
            if(len == sizeof(bleUartChar1))
            {
                tmos_memcpy(bleUartChar1, value, sizeof(bleUartChar1));
            }
            else
            {
                ret = bleInvalidRange;
            }
            break;

        case BLE_UART_CHAR2:
            if(len <= sizeof(bleUartChar2))
            {
                tmos_memcpy(bleUartChar2, value, len);
            }
            else
            {
                ret = bleInvalidRange;
            }
            break;

        case BLE_UART_CHAR3:
            if(len <= sizeof(bleUartChar3))
            {
                tmos_memcpy(bleUartChar3, value, len);
            }
            else
            {
                ret = bleInvalidRange;
            }
            break;

        case BLE_UART_CHAR4:
            if(len <= sizeof(bleUartChar4))
            {
                tmos_memcpy(bleUartChar4, value, len);
            }
            else
            {
                ret = bleInvalidRange;
            }
            break;

        case BLE_UART_CHAR5:
            if(len == sizeof(bleUartChar5))
            {
                tmos_memcpy(bleUartChar5, value, sizeof(bleUartChar5));
            }
            else
            {
                ret = bleInvalidRange;
            }
            break;

        default:
            ret = INVALIDPARAMETER;
            break;
    }

    return ret;
}

/*********************************************************************
 * @fn      BLE_UART_GetParameter
 *
 * @brief   读特征值
 */
bStatus_t BLE_UART_GetParameter(uint8_t param, void *value)
{
    bStatus_t ret = SUCCESS;

    switch(param)
    {
        case BLE_UART_CHAR1:
            tmos_memcpy(value, bleUartChar1, sizeof(bleUartChar1));
            break;

        case BLE_UART_CHAR2:
            tmos_memcpy(value, bleUartChar2, sizeof(bleUartChar2));
            break;

        case BLE_UART_CHAR3:
            tmos_memcpy(value, bleUartChar3, sizeof(bleUartChar3));
            break;

        case BLE_UART_CHAR4:
            tmos_memcpy(value, bleUartChar4, sizeof(bleUartChar4));
            break;

        case BLE_UART_CHAR5:
            tmos_memcpy(value, bleUartChar5, sizeof(bleUartChar5));
            break;

        default:
            ret = INVALIDPARAMETER;
            break;
    }

    return ret;
}

/*********************************************************************
 * @fn      BLE_UART_Notify
 *
 * @brief   通过 CHAR4 发送通知
 */
bStatus_t BLE_UART_Notify(uint16_t connHandle, attHandleValueNoti_t *pNoti)
{
    uint16_t cccValue = GATTServApp_ReadCharCfg(connHandle, bleUartChar4Config);

    /* 只有手机使能了 Notify 才允许发送 */
    if(cccValue & GATT_CLIENT_CFG_NOTIFY)
    {
        pNoti->handle = bleUartAttrTbl[BLE_UART_CHAR4_VALUE_IDX].handle;
        return GATT_Notification(connHandle, pNoti, FALSE);
    }

    return bleIncorrectMode;
}

/*********************************************************************
 * @fn      BLE_UART_SendData
 *
 * @brief   便捷接口：申请内存并发送一段数据
 */
bStatus_t BLE_UART_SendData(uint8_t *data, uint16_t len)
{
    attHandleValueNoti_t noti;
    uint16_t             allocLen;

    /* 未连接则直接返回 */
    if(bleUartConnHandle == GAP_CONNHANDLE_INIT)
    {
        return bleNotConnected;
    }

    if(len > BLE_UART_DATA_LEN)
    {
        len = BLE_UART_DATA_LEN;
    }

    /* 从 GATT 内存池申请发送缓冲 */
    noti.pValue = (uint8_t *)GATT_bm_alloc(bleUartConnHandle, ATT_HANDLE_VALUE_NOTI,
                                           len, &allocLen, 0);
    if(noti.pValue == NULL)
    {
        return bleMemAllocError;
    }

    noti.len = len;
    tmos_memcpy(noti.pValue, data, len);

    /* 交给协议栈发送；失败时释放缓冲，避免内存泄漏 */
    {
        bStatus_t status = BLE_UART_Notify(bleUartConnHandle, &noti);
        if(status != SUCCESS)
        {
            GATT_bm_free((gattMsg_t *)&noti, ATT_HANDLE_VALUE_NOTI);
        }
        return status;
    }
}

/*********************************************************************
 * @fn      BLE_UART_IsConnected
 */
uint8_t BLE_UART_IsConnected(void)
{
    return (bleUartConnHandle != GAP_CONNHANDLE_INIT) ? TRUE : FALSE;
}

/*********************************************************************
 * @fn      BLE_UART_GetConnHandle
 */
uint16_t BLE_UART_GetConnHandle(void)
{
    return bleUartConnHandle;
}

/*********************************************************************
 * @fn      BLE_UART_OnLinkEstablished
 *
 * @brief   由 peripheral 角色在连接建立/断开时调用，更新连接句柄
 *
 * @param   connHandle - 新的连接句柄，断开时传 GAP_CONNHANDLE_INIT
 */
void BLE_UART_OnLinkEstablished(uint16_t connHandle)
{
    bleUartConnHandle = connHandle;
}

/*********************************************************************
 * GATT 回调实现
 */

/*********************************************************************
 * @fn      bleUart_ReadAttrCB
 *
 * @brief   读属性回调。协议栈收到读请求时调用。
 *
 * @note    注意：服务声明、特征值声明、CCC 这三类属性的读由协议栈内部处理，
 *          不会走到这里。这里只处理我们自己定义的特征值。
 */
static bStatus_t bleUart_ReadAttrCB(uint16_t connHandle, gattAttribute_t *pAttr,
                                    uint8_t *pValue, uint16_t *pLen, uint16_t offset,
                                    uint16_t maxLen, uint8_t method)
{
    bStatus_t status = SUCCESS;

    /* 本服务的特征值都不支持长读（blob） */
    if(offset > 0)
    {
        return ATT_ERR_ATTR_NOT_LONG;
    }

    if(pAttr->type.len == ATT_BT_UUID_SIZE)
    {
        uint16_t uuid = BUILD_UINT16(pAttr->type.uuid[0], pAttr->type.uuid[1]);

        switch(uuid)
        {
            case BLE_UART_CHAR1_UUID:
            case BLE_UART_CHAR5_UUID:
            {
                /* 这两个特征值长度固定 */
                uint16_t fixedLen = (uuid == BLE_UART_CHAR1_UUID) ?
                                    sizeof(bleUartChar1) : sizeof(bleUartChar5);
                *pLen = (maxLen > fixedLen) ? fixedLen : maxLen;
                tmos_memcpy(pValue, pAttr->pValue, *pLen);
                break;
            }

            case BLE_UART_CHAR2_UUID:
            {
                /* 变长缓冲区：长度取实际写入长度。
                 * 简化处理：这里直接返回整个缓冲区长度，
                 * 产品上如果需要精确长度，应额外维护一个 bleUartChar2Len 变量。 */
                *pLen = (maxLen > sizeof(bleUartChar2)) ? sizeof(bleUartChar2) : maxLen;
                tmos_memcpy(pValue, pAttr->pValue, *pLen);
                break;
            }

            default:
                *pLen = 0;
                status = ATT_ERR_ATTR_NOT_FOUND;
                break;
        }
    }
    else
    {
        /* 不支持 128 位 UUID */
        *pLen = 0;
        status = ATT_ERR_INVALID_HANDLE;
    }

    (void)connHandle;
    (void)method;
    return status;
}

/*********************************************************************
 * @fn      bleUart_WriteAttrCB
 *
 * @brief   写属性回调。协议栈收到写请求时调用。
 */
static bStatus_t bleUart_WriteAttrCB(uint16_t connHandle, gattAttribute_t *pAttr,
                                     uint8_t *pValue, uint16_t len, uint16_t offset,
                                     uint8_t method)
{
    bStatus_t status = SUCCESS;
    uint8_t   notifyApp = 0xFF;

    /* 需要授权的属性，协议栈已经在权限检查阶段拦截，这里再保险一次 */
    if(gattPermitAuthorWrite(pAttr->permissions))
    {
        return ATT_ERR_INSUFFICIENT_AUTHOR;
    }

    if(pAttr->type.len == ATT_BT_UUID_SIZE)
    {
        uint16_t uuid = BUILD_UINT16(pAttr->type.uuid[0], pAttr->type.uuid[1]);

        switch(uuid)
        {
            case BLE_UART_CHAR1_UUID:
                if(offset == 0)
                {
                    if(len > sizeof(bleUartChar1))
                    {
                        status = ATT_ERR_INVALID_VALUE_SIZE;
                    }
                }
                else
                {
                    status = ATT_ERR_ATTR_NOT_LONG;
                }
                if(status == SUCCESS)
                {
                    tmos_memcpy(pAttr->pValue, pValue, sizeof(bleUartChar1));
                    notifyApp = BLE_UART_CHAR1;
                }
                break;

            case BLE_UART_CHAR2_UUID:
                if(offset == 0)
                {
                    if(len > sizeof(bleUartChar2))
                    {
                        status = ATT_ERR_INVALID_VALUE_SIZE;
                    }
                }
                else
                {
                    status = ATT_ERR_ATTR_NOT_LONG;
                }
                if(status == SUCCESS)
                {
                    tmos_memcpy(pAttr->pValue, pValue, len);
                    notifyApp = BLE_UART_CHAR2;
                }
                break;

            case BLE_UART_CHAR3_UUID:
                /* 透传下行通道：手机写入就立即交给业务处理 */
                if(offset == 0)
                {
                    if(len > sizeof(bleUartChar3))
                    {
                        status = ATT_ERR_INVALID_VALUE_SIZE;
                    }
                }
                else
                {
                    status = ATT_ERR_ATTR_NOT_LONG;
                }
                if(status == SUCCESS)
                {
                    tmos_memcpy(pAttr->pValue, pValue, len);
                    bleUart_OnPhoneData(pValue, len);
                }
                break;

            case GATT_CLIENT_CHAR_CFG_UUID:
                /* CCC 写入：手机打开/关闭 Notify 时走这里 */
                status = GATTServApp_ProcessCCCWriteReq(connHandle, pAttr, pValue, len,
                                                        offset, GATT_CLIENT_CFG_NOTIFY);
                break;

            default:
                status = ATT_ERR_ATTR_NOT_FOUND;
                break;
        }
    }
    else
    {
        status = ATT_ERR_INVALID_HANDLE;
    }

    /* 特征值真的变化了才回调应用层（CCC 除外） */
    if((notifyApp != 0xFF) && bleUart_AppCBs && bleUart_AppCBs->pfnChange)
    {
        bleUart_AppCBs->pfnChange(notifyApp, pValue, len);
    }

    (void)method;
    return status;
}

/*********************************************************************
 * @fn      bleUart_HandleConnStatusCB
 *
 * @brief   连接状态变化回调。断开连接时必须复位 CCC，
 *          否则下次连接会残留上一次的 Notify 使能状态。
 */
static void bleUart_HandleConnStatusCB(uint16_t connHandle, uint8_t changeType)
{
    if(connHandle == LOOPBACK_CONNHANDLE)
    {
        return;
    }

    if((changeType == LINKDB_STATUS_UPDATE_REMOVED) ||
       ((changeType == LINKDB_STATUS_UPDATE_STATEFLAGS) && (!linkDB_Up(connHandle))))
    {
        GATTServApp_InitCharCfg(connHandle, bleUartChar4Config);
    }
}

/******************************** endfile @ ble_uart_service ******************/
