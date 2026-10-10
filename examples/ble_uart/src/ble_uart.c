/*******************************************************************************
 * 文件名称 : ble_uart.c
 * 描    述 : BLE 从机（Peripheral）角色任务实现。
 *
 *            本文件负责 GAP 层的事情：
 *              - 注册 TMOS 任务
 *              - 配置广播数据、扫描响应、连接参数
 *              - 配置配对绑定参数（GAP Bond Manager）
 *              - 注册 GAP / GATT 基础服务
 *              - 启动设备（开始广播）
 *              - 处理连接状态变化、RSSI 读取、连接参数更新
 *
 *            业务数据怎么收发由 ble_uart_service.c 负责，两者通过回调衔接。
 ******************************************************************************/
#include "CONFIG.h"
#include "ble_uart_service.h"

/*********************************************************************
 * 常量定义
 */

/** 广播间隔，单位 0.625ms。160 * 0.625 = 100ms */
#define BLE_UART_ADV_INTERVAL           160

/** 期望的最小连接间隔，单位 1.25ms。8 * 1.25 = 10ms */
#define BLE_UART_CONN_INTERVAL_MIN      8

/** 期望的最大连接间隔，单位 1.25ms。48 * 1.25 = 60ms */
#define BLE_UART_CONN_INTERVAL_MAX      48

/** 从机延迟（可跳过多少个连接事件不响应） */
#define BLE_UART_SLAVE_LATENCY          0

/** 连接超时，单位 10ms。500 * 10 = 5000ms */
#define BLE_UART_CONN_TIMEOUT           500

/** 设备名，最长 GAP_DEVICE_NAME_LEN-1 个字符 */
#define BLE_UART_DEVICE_NAME            "CH572-BLE-UART"

/** 广播数据中携带的厂商 ID（沁恒的 Bluetooth SIG Company ID） */
#define WCH_COMPANY_ID                  0x07D7

/** 通用可发现模式（这些宏在 WCH 的库头文件里没有统一定义，需要自己声明） */
#define DEFAULT_DISCOVERABLE_MODE       GAP_ADTYPE_FLAGS_GENERAL

/** 本任务的事件位 */
#define BLE_UART_START_DEVICE_EVT       0x0001  /*!< 启动设备（开始广播） */
#define BLE_UART_PERIODIC_EVT           0x0002  /*!< 周期任务 */
#define BLE_UART_READ_RSSI_EVT          0x0004  /*!< 读取 RSSI */
#define BLE_UART_PARAM_UPDATE_EVT       0x0008  /*!< 请求更新连接参数 */

/** 周期任务间隔，单位 625us。1600 * 0.625 = 1000ms */
#define BLE_UART_PERIODIC_EVT_PERIOD    MS1_TO_SYSTEM_TIME(1000)

/*********************************************************************
 * 局部变量
 */

/** 本任务的 TMOS 任务号 */
static tmosTaskID bleUartTaskID = INVALID_TASK_ID;

/** 连接状态记录 */
static uint16_t bleUartConnHandle = GAP_CONNHANDLE_INIT;
static uint16_t bleUartConnInterval = 0;
static uint16_t bleUartConnSlaveLatency = 0;
static uint16_t bleUartConnTimeout = 0;

/** 当前协商出的 ATT_MTU */
static uint16_t bleUartMtu = ATT_MTU_SIZE;

/*********************************************************************
 * 广播数据
 *
 * 格式遵循 BLE 广播数据规范：每个字段 = [长度][类型][数据...]
 */

/** 广播数据：Flags + 16 位服务 UUID */
static uint8_t bleUartAdvData[] = {
    /* Flags：通用可发现模式，不支持 BR/EDR */
    0x02,
    GAP_ADTYPE_FLAGS,
    DEFAULT_DISCOVERABLE_MODE | GAP_ADTYPE_FLAGS_BREDR_NOT_SUPPORTED,

    /* 服务 UUID：告诉主机本机提供 0xFFE0 服务 */
    0x03,
    GAP_ADTYPE_16BIT_MORE,
    LO_UINT16(BLE_UART_SERV_UUID),
    HI_UINT16(BLE_UART_SERV_UUID),
};

/** 扫描响应数据：完整设备名 + 连接间隔范围 + 发射功率 */
static uint8_t bleUartScanRspData[] = {
    /* 完整设备名 */
    0x11,                               /* 长度 = 1(类型) + 16(名字) */
    GAP_ADTYPE_LOCAL_NAME_COMPLETE,
    'C', 'H', '5', '7', '2', '-', 'B', 'L', 'E',
    '-', 'U', 'A', 'R', 'T', ' ', ' ',     /* 补齐到 16 字节 */
};

/** GAP 设备名属性 */
static uint8_t bleUartDeviceName[GAP_DEVICE_NAME_LEN] = BLE_UART_DEVICE_NAME;

/*********************************************************************
 * 回调集合
 */

/* 前置声明 */
static void bleUartStateCB(gapRole_States_t newState, gapRoleEvent_t *pEvent);
static void bleUartRssiCB(uint16_t connHandle, int8_t rssi);
static void bleUartParamUpdateCB(uint16_t connHandle, uint16_t connInterval,
                                 uint16_t connSlaveLatency, uint16_t connTimeout);
static void bleUartPairingStateCB(uint16_t connHandle, uint8_t state, uint8_t status);
static void bleUartPasscodeCB(uint8_t *deviceAddr, uint16_t connHandle,
                              uint8_t uiInputs, uint8_t uiOutputs);
static void bleUartProcessTMOSMsg(tmos_event_hdr_t *pMsg);
static void bleUartProcessEvent(uint8_t paramID, uint8_t *pValue, uint16_t len);
static void bleUartPeriodicTask(void);

/** GAP 从机角色回调 */
static gapRolesCBs_t bleUartRoleCBs = {
    bleUartStateCB,         /* 连接状态变化 */
    bleUartRssiCB,          /* RSSI 读取结果 */
    bleUartParamUpdateCB,   /* 连接参数更新 */
};

/** 广播者回调（从机角色下用不到，但需要把扫描请求通知接上） */
static gapRolesBroadcasterCBs_t bleUartBroadcasterCBs = {
    NULL,   /* 从机角色不使用 */
    NULL,   /* 收到扫描请求的回调，可按需实现 */
};

/** 配对绑定回调 */
static gapBondCBs_t bleUartBondMgrCBs = {
    bleUartPasscodeCB,      /* 配对密钥回调 */
    bleUartPairingStateCB,  /* 配对状态回调 */
    NULL,                   /* OOB 回调 */
};

/** 服务层回调 */
static bleUartCBs_t bleUartServiceCBs = {
    bleUartProcessEvent,    /* 特征值变化回调 */
};

/*********************************************************************
 * @fn      BLE_UART_Init
 *
 * @brief   初始化 BLE 从机应用（注册任务、配置参数、注册服务、启动设备）
 */
void BLE_UART_Init(void)
{
    /* ---- 1. 注册 TMOS 任务 ---- */
    bleUartTaskID = TMOS_ProcessEventRegister(BLE_UART_ProcessEvent);

    /* ---- 2. 配置 GAP 从机角色参数 ---- */
    {
        uint8_t  advEnable = TRUE;
        uint16_t minInterval = BLE_UART_CONN_INTERVAL_MIN;
        uint16_t maxInterval = BLE_UART_CONN_INTERVAL_MAX;

        GAPRole_SetParameter(GAPROLE_ADVERT_ENABLED, sizeof(uint8_t), &advEnable);
        GAPRole_SetParameter(GAPROLE_SCAN_RSP_DATA, sizeof(bleUartScanRspData), bleUartScanRspData);
        GAPRole_SetParameter(GAPROLE_ADVERT_DATA, sizeof(bleUartAdvData), bleUartAdvData);
        GAPRole_SetParameter(GAPROLE_MIN_CONN_INTERVAL, sizeof(uint16_t), &minInterval);
        GAPRole_SetParameter(GAPROLE_MAX_CONN_INTERVAL, sizeof(uint16_t), &maxInterval);
    }

    /* ---- 3. 配置广播间隔 ---- */
    {
        uint16_t advInt = BLE_UART_ADV_INTERVAL;
        GAP_SetParamValue(TGAP_DISC_ADV_INT_MIN, advInt);
        GAP_SetParamValue(TGAP_DISC_ADV_INT_MAX, advInt);
        GAP_SetParamValue(TGAP_ADV_SCAN_REQ_NOTIFY, ENABLE);
    }

    /* ---- 4. 配置配对绑定 ---- */
    {
        uint32_t passkey = 0;                                   /* 配对密码 000000 */
        uint8_t  pairMode = GAPBOND_PAIRING_MODE_WAIT_FOR_REQ;   /* 等待主机发起配对 */
        uint8_t  mitm = FALSE;                                  /* 不要求防中间人 */
        uint8_t  bonding = TRUE;                                /* 保存绑定信息到 Flash */
        uint8_t  ioCap = GAPBOND_IO_CAP_NO_INPUT_NO_OUTPUT;     /* 无输入无输出 */

        GAPBondMgr_SetParameter(GAPBOND_PERI_DEFAULT_PASSCODE, sizeof(uint32_t), &passkey);
        GAPBondMgr_SetParameter(GAPBOND_PERI_PAIRING_MODE, sizeof(uint8_t), &pairMode);
        GAPBondMgr_SetParameter(GAPBOND_PERI_MITM_PROTECTION, sizeof(uint8_t), &mitm);
        GAPBondMgr_SetParameter(GAPBOND_PERI_IO_CAPABILITIES, sizeof(uint8_t), &ioCap);
        GAPBondMgr_SetParameter(GAPBOND_PERI_BONDING_ENABLED, sizeof(uint8_t), &bonding);
    }

    /* ---- 5. 注册 GATT 基础服务 ---- */
    GGS_AddService(GATT_ALL_SERVICES);          /* GAP 服务（设备名、外观等） */
    GATTServApp_AddService(GATT_ALL_SERVICES);  /* GATT 服务（服务变更指示等） */

    /* ---- 6. 注册自定义透传服务 ---- */
    BLE_UART_AddService(GATT_ALL_SERVICES);
    BLE_UART_RegisterAppCBs(&bleUartServiceCBs);

    /* ---- 7. 设置设备名 ---- */
    GGS_SetParameter(GGS_DEVICE_NAME_ATT, GAP_DEVICE_NAME_LEN, bleUartDeviceName);

    /* ---- 8. 设置特征值初值 ---- */
    {
        uint8_t charValue1 = 0x11;
        uint8_t charValue5[5] = {0x01, 0x02, 0x03, 0x04, 0x05};

        BLE_UART_SetParameter(BLE_UART_CHAR1, sizeof(charValue1), &charValue1);
        BLE_UART_SetParameter(BLE_UART_CHAR5, sizeof(charValue5), charValue5);
    }

    /* ---- 9. 接上扫描请求通知回调 ---- */
    GAPRole_BroadcasterSetCB(&bleUartBroadcasterCBs);

    /* ---- 10. 延迟一个事件再启动设备，保证所有初始化都已完成 ---- */
    tmos_set_event(bleUartTaskID, BLE_UART_START_DEVICE_EVT);
}

/*********************************************************************
 * @fn      BLE_UART_ProcessEvent
 *
 * @brief   本任务的 TMOS 事件处理函数
 *
 * @param   task_id - 任务号
 * @param   events  - 事件位图
 * @return  未处理的事件位
 */
uint16_t BLE_UART_ProcessEvent(uint8_t task_id, uint16_t events)
{
    /* 系统消息（协议栈通过消息机制通知应用） */
    if(events & SYS_EVENT_MSG)
    {
        uint8_t *pMsg;

        if((pMsg = tmos_msg_receive(bleUartTaskID)) != NULL)
        {
            bleUartProcessTMOSMsg((tmos_event_hdr_t *)pMsg);
            tmos_msg_deallocate(pMsg);
        }
        return (events ^ SYS_EVENT_MSG);
    }

    /* 启动设备 */
    if(events & BLE_UART_START_DEVICE_EVT)
    {
        GAPRole_PeripheralStartDevice(bleUartTaskID, &bleUartBondMgrCBs, &bleUartRoleCBs);
        return (events ^ BLE_UART_START_DEVICE_EVT);
    }

    /* 周期任务 */
    if(events & BLE_UART_PERIODIC_EVT)
    {
        if(BLE_UART_PERIODIC_EVT_PERIOD)
        {
            tmos_start_task(bleUartTaskID, BLE_UART_PERIODIC_EVT, BLE_UART_PERIODIC_EVT_PERIOD);
        }
        bleUartPeriodicTask();
        return (events ^ BLE_UART_PERIODIC_EVT);
    }

    /* 读取 RSSI */
    if(events & BLE_UART_READ_RSSI_EVT)
    {
        if(bleUartConnHandle != GAP_CONNHANDLE_INIT)
        {
            GAPRole_ReadRssiCmd(bleUartConnHandle);
        }
        return (events ^ BLE_UART_READ_RSSI_EVT);
    }

    /* 请求更新连接参数 */
    if(events & BLE_UART_PARAM_UPDATE_EVT)
    {
        if(bleUartConnHandle != GAP_CONNHANDLE_INIT)
        {
            GAPRole_PeripheralConnParamUpdateReq(bleUartConnHandle,
                                                 BLE_UART_CONN_INTERVAL_MIN,
                                                 BLE_UART_CONN_INTERVAL_MAX,
                                                 BLE_UART_SLAVE_LATENCY,
                                                 BLE_UART_CONN_TIMEOUT,
                                                 bleUartTaskID);
        }
        return (events ^ BLE_UART_PARAM_UPDATE_EVT);
    }

    (void)task_id;
    return 0;
}

/*********************************************************************
 * @fn      bleUartStateCB
 *
 * @brief   GAP 从机连接状态变化回调。
 *
 * @param   newState - 新状态
 * @param   pEvent   - 状态相关的事件参数
 */
static void bleUartStateCB(gapRole_States_t newState, gapRoleEvent_t *pEvent)
{
    switch(newState & GAPROLE_STATE_ADV_MASK)
    {
        case GAPROLE_STARTED:
            /* 协议栈已经启动，但还没开始广播 */
            PRINT("BLE 已启动，设备地址获取中\n");
            break;

        case GAPROLE_ADVERTISING:
            /* 正在广播，等待手机连接 */
            if(pEvent->gap.opcode == GAP_LINK_TERMINATED_EVENT)
            {
                /* 从"刚断开"回到广播状态 */
                PRINT("连接已断开，重新开始广播\n");
                BLE_UART_OnLinkEstablished(GAP_CONNHANDLE_INIT);
                bleUartConnHandle = GAP_CONNHANDLE_INIT;
                /* 断开后主动请求把连接参数改回期望值 */
                tmos_set_event(bleUartTaskID, BLE_UART_PARAM_UPDATE_EVT);
            }
            else
            {
                PRINT("正在广播，等待连接...\n");
            }
            break;

        case GAPROLE_CONNECTED:
        {
            /* 连接建立 */
            gapEstLinkReqEvent_t *pLink = (gapEstLinkReqEvent_t *)pEvent;

            bleUartConnHandle = pLink->connectionHandle;
            bleUartConnInterval = pLink->connInterval;
            bleUartConnSlaveLatency = pLink->connLatency;   /* 注意：库里的字段名是 connLatency */
            bleUartConnTimeout = pLink->connTimeout;
            BLE_UART_OnLinkEstablished(bleUartConnHandle);

            PRINT("已连接，句柄=%d，间隔=%d(1.25ms)，延迟=%d，超时=%d(10ms)\n",
                  bleUartConnHandle, bleUartConnInterval,
                  bleUartConnSlaveLatency, bleUartConnTimeout);

            /* 启动周期任务与 RSSI 读取 */
            tmos_start_task(bleUartTaskID, BLE_UART_PERIODIC_EVT, BLE_UART_PERIODIC_EVT_PERIOD);

            /* 主动发起一次连接参数更新请求 */
            tmos_set_event(bleUartTaskID, BLE_UART_PARAM_UPDATE_EVT);
            break;
        }

        case GAPROLE_WAITING:
            /* 广播超时或连接结束后等待重新广播 */
            BLE_UART_OnLinkEstablished(GAP_CONNHANDLE_INIT);
            bleUartConnHandle = GAP_CONNHANDLE_INIT;
            break;

        case GAPROLE_ERROR:
            PRINT("GAP 角色错误\n");
            break;

        default:
            break;
    }
}

/*********************************************************************
 * @fn      bleUartRssiCB
 *
 * @brief   RSSI 读取结果回调
 */
static void bleUartRssiCB(uint16_t connHandle, int8_t rssi)
{
    PRINT("连接 %d 的 RSSI = %d dBm\n", connHandle, rssi);
}

/*********************************************************************
 * @fn      bleUartParamUpdateCB
 *
 * @brief   连接参数更新完成回调
 */
static void bleUartParamUpdateCB(uint16_t connHandle, uint16_t connInterval,
                                 uint16_t connSlaveLatency, uint16_t connTimeout)
{
    bleUartConnInterval = connInterval;
    bleUartConnSlaveLatency = connSlaveLatency;
    bleUartConnTimeout = connTimeout;

    PRINT("连接参数已更新：间隔=%d，延迟=%d，超时=%d\n",
          connInterval, connSlaveLatency, connTimeout);
    (void)connHandle;
}

/*********************************************************************
 * @fn      bleUartPairingStateCB
 *
 * @brief   配对/绑定状态回调
 */
static void bleUartPairingStateCB(uint16_t connHandle, uint8_t state, uint8_t status)
{
    switch(state)
    {
        case GAPBOND_PAIRING_STATE_STARTED:
            PRINT("开始配对\n");
            break;

        case GAPBOND_PAIRING_STATE_COMPLETE:
            PRINT("配对完成，状态=%d\n", status);
            break;

        case GAPBOND_PAIRING_STATE_BONDED:
            PRINT("绑定成功，绑定信息已保存\n");
            break;

        default:
            break;
    }
    (void)connHandle;
}

/*********************************************************************
 * @fn      bleUartPasscodeCB
 *
 * @brief   配对密钥回调。产品上如果显示能力，应在这里把 passkey 显示出来。
 */
static void bleUartPasscodeCB(uint8_t *deviceAddr, uint16_t connHandle,
                              uint8_t uiInputs, uint8_t uiOutputs)
{
    uint32_t passkey = 0;

    /* 直接使用绑定时配置的默认密码 000000 */
    GAPBondMgr_GetParameter(GAPBOND_PERI_DEFAULT_PASSCODE, &passkey);

    PRINT("配对请求来自 %02X:%02X:%02X:%02X:%02X:%02X，密码=%06lu\n",
          deviceAddr[5], deviceAddr[4], deviceAddr[3],
          deviceAddr[2], deviceAddr[1], deviceAddr[0], passkey);

    /* 回复密码 */
    GAPBondMgr_PasscodeRsp(connHandle, SUCCESS, passkey);

    (void)uiInputs;
    (void)uiOutputs;
}

/*********************************************************************
 * @fn      bleUartProcessTMOSMsg
 *
 * @brief   处理协议栈发来的消息
 *
 * @note    ★ 不要在这里调用 bleUartStateCB() ★
 *
 *          这里曾经写的是：
 *              bleUartStateCB((gapRole_States_t)pEvent->gap.hdr.status, pEvent);
 *          即把**报文头的 status 字段**当成 GAP 状态再派发一次状态回调。
 *          那是错的，官方 peripheral.c 的 Peripheral_ProcessTMOSMsg() 里
 *          GAP_MSG_EVENT 分支只做应用级事件处理，**不碰状态回调**。
 *
 *          为什么危险：状态回调是由协议栈自己按真实状态调用的，
 *          hdr.status 是消息状态码而不是 gapRole_States_t。两者按
 *          & GAPROLE_STATE_ADV_MASK 掩码后可能撞上合法状态值——
 *          例如 bleMemAllocError(0x13)、bleNotConnected(0x14) 掩码后
 *          分别落到 GAPROLE_WAITING / GAPROLE_CONNECTED，
 *          于是断连分支会被误触发（误清 bleUartConnHandle、误发连接参数更新），
 *          或从一个并非链路事件的消息里读出没意义的句柄。
 *
 *          下面按官方做法只处理应用关心的 GAP 事件。
 */
static void bleUartProcessTMOSMsg(tmos_event_hdr_t *pMsg)
{
    switch(pMsg->event)
    {
        case GAP_MSG_EVENT:
        {
            gapRoleEvent_t *pEvent = (gapRoleEvent_t *)pMsg;
            switch(pEvent->gap.opcode)
            {
                case GAP_SCAN_REQUEST_EVENT:
                    /* 手机发了扫描请求（我们回了扫描响应）——说明广播被听见了 */
                    PRINT("收到扫描请求，来自 %02X:%02X:%02X:%02X:%02X:%02X\n",
                          pEvent->scanReqEvt.scannerAddr[5],
                          pEvent->scanReqEvt.scannerAddr[4],
                          pEvent->scanReqEvt.scannerAddr[3],
                          pEvent->scanReqEvt.scannerAddr[2],
                          pEvent->scanReqEvt.scannerAddr[1],
                          pEvent->scanReqEvt.scannerAddr[0]);
                    break;

                case GAP_PHY_UPDATE_EVENT:
                    PRINT("PHY 已更新：RX=0x%02X TX=0x%02X\n",
                          pEvent->linkPhyUpdate.connRxPHYS,
                          pEvent->linkPhyUpdate.connTxPHYS);
                    break;

                default:
                    break;
            }
            break;
        }

        case GATT_MSG_EVENT:
        {
            /* GATT 层消息（读写请求、通知确认等） */
            gattMsgEvent_t *pGattMsg = (gattMsgEvent_t *)pMsg;

            if(pGattMsg->method == ATT_MTU_UPDATED_EVENT)
            {
                /* MTU 协商结果：决定了单包能带多少数据 */
                bleUartMtu = pGattMsg->msg.mtuEvt.MTU;
                PRINT("MTU 已更新为 %d（单包数据上限 %d 字节）\n",
                      bleUartMtu, bleUartMtu - 3);
            }
            break;
        }

        default:
            break;
    }
}

/*********************************************************************
 * @fn      bleUartProcessEvent
 *
 * @brief   服务层通知应用：某个特征值被手机改了
 */
static void bleUartProcessEvent(uint8_t paramID, uint8_t *pValue, uint16_t len)
{
    switch(paramID)
    {
        case BLE_UART_CHAR1:
            PRINT("CHAR1 被写入：0x%02X\n", pValue[0]);
            break;

        case BLE_UART_CHAR2:
            PRINT("CHAR2 被写入 %d 字节\n", len);
            break;

        case BLE_UART_CHAR3:
            /* CHAR3 是透传下行通道，真正的处理在 ble_uart_service.c 里已经做了，
             * 这里只做日志。产品上可以在这里把数据转发到串口。 */
            break;

        default:
            break;
    }
}

/*********************************************************************
 * @fn      bleUartPeriodicTask
 *
 * @brief   周期任务：目前只做 RSSI 读取演示
 */
static void bleUartPeriodicTask(void)
{
    if(bleUartConnHandle != GAP_CONNHANDLE_INIT)
    {
        /* 触发一次 RSSI 读取 */
        tmos_set_event(bleUartTaskID, BLE_UART_READ_RSSI_EVT);
    }
}

/******************************** endfile @ ble_uart **************************/
