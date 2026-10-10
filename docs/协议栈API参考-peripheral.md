# CH572 BLE 协议栈 API 参考（peripheral-only 视角）

> 本文回答一个问题：**只做 BLE 从机（peripheral）功能时，`libCH572BLE_PERI.a` 里
> 哪些协议栈函数用得上、哪些用不上。**
>
> 数据全部来自实测：
> - `lib/CH572BLE_ROM_JT.md` —— 164 项跳转表（唯一可调用入口）
> - `examples/` + `hal/` + `debug/` 源码静态扫描 —— 示例真的调了什么
> - `libCH572BLE_PERI.a` 归档符号 + `build/rom/CH572BLE_ROM.map` —— 代码在不在里面
>
> 重新生成：`python tools\api_table.py`、`python tools\stack_inventory.py`、
> `python tools\check_missing_api.py`

---

## 一、三个层次的"用/不用"

理解这张表的关键是先分清三个层次，**它们完全不同**：

| 层次 | 数量 | 含义 |
|---|---|---|
| **① 跳转表暴露的 API** | **100 个可用** + 43 个本平台没有 + 21 个预留槽 = 164 | 应用**能调用**的全部接口 |
| **② 示例实际调用** | **39 个** | ble_uart 示例真的调了（不代表产品只需要这些） |
| **③ 归档里有但 API 不暴露** | 归档 1,084 个符号，大部分是内部函数 | 应用**不可能**调到（没槽位、没宏） |

⚠️ **最重要的一点**：
"33 个可用 API 示例没用" **不等于**"这 33 个是死代码，可以砍"。
它们里有 `GATT_Indication`、`tmos_msg_allocate`、`GAPBondMgr_*` 等产品大概率要用的接口，
只是**示例用不上**。真正的"拿不到"只有那 43 个。

---

## 二、按功能家族汇总

| 功能家族 | 可用 API | 本平台没有 | 示例已用 | 示例未用 |
|---|---:|---:|---:|---:|
| TMOS 内核 / 内存工具 | 22 | 1 | 13 | 9 |
| LL 链路层 | 11 | 1 | 0 | 11 |
| GAPRole 角色 | 10 | 18 | 6 | 4 |
| GATT | 9 | 18 | 3 | 6 |
| 其他（GGS / RFEND / 内部） | 9 | 1 | 2 | 7 |
| BLE 库管理 | 8 | 0 | 4 | 4 |
| GATTServApp 服务端 | 8 | 0 | 5 | 3 |
| RF 射频 | 7 | 4 | 0 | 7 |
| GAP 通用访问 | 5 | 0 | 1 | 4 |
| GAPBondMgr 绑定 | 4 | 0 | 3 | 1 |
| linkDB 连接数据库 | 3 | 0 | 1 | 2 |
| ATT 属性协议 | 3 | 0 | 0 | 3 |
| Flash / ISP | 1 | 0 | 1 | 0 |
| **合计** | **100** | **43** | **39** | **61** |

---

## 三、示例实际调用的 39 个 API（peripheral 最小可用集）

### 3.1 协议栈管理（4 个）

| API | 用途 | 调用位置 |
|---|---|---|
| `BLE_LibInit` | 启动协议栈，传入 `bleConfig_t` | `hal/HAL.c` |
| `BLE_RegInit` | RF 复位 + 重新校准（周期校准用） | `hal/HAL.c` |
| `CH572BLE_ROM_Init` | 注册中断钩子（ROM 方式专有） | `main_rom.c` |
| `CH572BLE_ROM_GetHandles` | 取 ROM 中断入口（ROM 方式专有） | `main_rom.c`、`hal/HAL.c` |

### 3.2 TMOS 内核（13 个）

| API | 用途 |
|---|---|
| `TMOS_SystemProcess` | **主循环**，应用 `while(1)` 里唯一要调的 |
| `TMOS_ProcessEventRegister` | 注册任务，拿到 `taskID` |
| `TMOS_TimerInit` / `TMOS_TimerSet` | RTC 时基初始化 / 周期校准 |
| `tmos_start_task` | 定时事件（一次性） |
| `tmos_start_reload_task` | 周期事件（调试心跳用） |
| `tmos_set_event` | 主动触发任务事件 |
| `tmos_msg_receive` / `tmos_msg_deallocate` | 收系统消息 / 释放 |
| `tmos_memcmp` / `tmos_memcpy` / `tmos_memset` / `tmos_strlen` | 内存与字符串工具 |

### 3.3 GAP 从机角色（6 个）

| API | 用途 |
|---|---|
| `GAPRole_PeripheralInit` | 初始化从机角色 |
| `GAPRole_PeripheralStartDevice` | **开始广播**（传绑定回调 + 角色回调） |
| `GAPRole_SetParameter` | 设置广播数据 / 扫描响应 / 连接间隔 |
| `GAPRole_PeripheralConnParamUpdateReq` | 主动请求更新连接参数 |
| `GAPRole_ReadRssiCmd` | 读 RSSI |
| `GAPRole_BroadcasterSetCB` | 注册广播者回调 |

### 3.4 GAP 通用参数（1 个）

| API | 用途 |
|---|---|
| `GAP_SetParamValue` | 设 GAP 层参数（如扫描请求通知） |

### 3.5 配对绑定（3 个）

| API | 用途 |
|---|---|
| `GAPBondMgr_SetParameter` | 设配对模式 / IO 能力 / MITM / 绑定开关 / 密码 |
| `GAPBondMgr_GetParameter` | 读参数（如取密码） |
| `GAPBondMgr_PasscodeRsp` | 回复配对密码 |

### 3.6 GATT 服务端（5 个）

| API | 用途 |
|---|---|
| `GATTServApp_RegisterService` | **注册自定义 GATT 服务属性表** |
| `GATTServApp_AddService` | 注册官方服务（如 GAP 服务） |
| `GATTServApp_InitCharCfg` | 初始化 CCCD（Notify 开关） |
| `GATTServApp_ReadCharCfg` | 读 CCCD 状态 |
| `GATTServApp_ProcessCCCWriteReq` | 处理手机写 CCCD |

### 3.7 GATT 消息（3 个）

| API | 用途 |
|---|---|
| `GATT_Notification` | **发通知**（设备 → 手机） |
| `GATT_bm_alloc` / `GATT_bm_free` | 通知缓冲区申请 / 释放 |

### 3.8 连接管理与其他（4 个）

| API | 用途 |
|---|---|
| `linkDB_Register` | 注册连接状态回调 |
| `GGS_AddService` / `GGS_SetParameter` | GAP 服务（设备名、外观） |
| `FLASH_EEPROM_CMD` | SNV 读写走 ROM 的 ISP 实现 |

---

## 四、可用但示例没用到的 61 个 API（产品按需取用）

这些**能调用**，只是示例场景用不上。挑几个产品很可能需要的：

| API | 家族 | 什么时候需要 |
|---|---|---|
| `GATT_Indication` | GATT | 需要**带确认**的上行通知（Notify 无需确认，Indication 要 ACK） |
| `GATT_ExchangeMTU` | GATT | 主动发起 MTU 协商（提升吞吐） |
| `GATT_RegisterForInd` | GATT | 注册 GATT 消息回调 |
| `GATT_FindHandle` | GATT | 按 UUID 查自己的属性句柄 |
| `tmos_msg_allocate` / `tmos_msg_send` | TMOS | 自定义任务间消息传递 |
| `tmos_clear_event` / `tmos_stop_task` | TMOS | 取消已排程的事件 |
| `LL_SetTxPowerLevel` | LL | **动态调发射功率** |
| `LL_Encrypt` / `LL_Decrypt` | LL | 手动加解密 |
| `LL_AddWhiteListDevice` 等 3 个 | LL | 白名单过滤（只允许指定设备连） |
| `SM_*`（3 个可用） | SM | 安全管理细节控制 |
| `GAPRole_SetParameter` 的更多参数 | GAPRole | 广播间隔、从机延迟、超时等 |
| `ATT_GetMTU` | ATT | 查当前协商出的 MTU |
| `RF_SetFrequency` / `RF_Config` 等 | RF | 2.4G 私有协议模式 |
| `GAP_ConfigDeviceAddr` | GAP | 运行时改 MAC |

完整 61 项清单见 `build/api_table.md` 的 B 节标记（`示例已用` 为空的行）。

---

## 五、本平台拿不到的 43 个 API

**已核实：这 43 个符号在 `libCH572BLE_PERI.a` 归档里一个都不存在**
（用 `riscv-wch-elf-nm` 逐名核对，0 个"库里有但被封锁"的情况）。
也就是说 WCH 是真的把代码裁掉了，不是只封了 API。

在 `CH572BLE_ROM.h` 里这些名字**完全没有宏** → 误用是编译期/链接期报错，
不会运行时跳到地址 0。

### 5.1 GATT 客户端（18 个）—— 设备主动去读写别人

```
GATT_DiscAllPrimaryServices      GATT_ReadCharValue         GATT_ReadCharDesc
GATT_DiscPrimaryServiceByUUID    GATT_ReadUsingCharUUID     GATT_ReadLongCharDesc
GATT_FindIncludedServices        GATT_ReadLongCharValue     GATT_WriteCharDesc
GATT_DiscAllChars                GATT_ReadMultiCharValues   GATT_WriteLongCharDesc
GATT_DiscCharsByUUID             GATT_WriteCharValue        GATT_SignedWriteNoRsp
GATT_DiscAllCharDescs            GATT_WriteLongCharValue    GATT_ReliableWrites
```

### 5.2 GAP 主机（Central，6 个）—— 主动发起连接

```
GAPRole_CentralInit              GAPRole_CentralStartDiscovery
GAPRole_CentralStartDevice       GAPRole_CentralCancelDiscovery
GAPRole_CentralEstablishLink     GAPRole_SetHostChanClassification
```

### 5.3 观察者（Observer，4 个）—— 主动扫描

```
GAPRole_ObserverInit             GAPRole_ObserverStartDiscovery
GAPRole_ObserverStartDevice      GAPRole_ObserverCancelDiscovery
```

### 5.4 广播者角色初始化（2 个）

```
GAPRole_BroadcasterInit          GAPRole_BroadcasterStartDevice
```
> 注意：示例里用的 `GAPRole_BroadcasterSetCB` **是可用**的（JT 里的槽位有地址），
> 被拿掉的是 `BroadcasterInit` / `BroadcasterStartDevice` 这两个。

### 5.5 周期同步 / 5.x 新特性（3 个）

```
GAPRole_CreateSync               GAPRole_CancelSync        GAPRole_TerminateSync
```

### 5.6 RF 跳频与绑定擦除（4 个）

```
RF_FrequencyHoppingShut          RF_FrequencyHoppingTx
RF_FrequencyHoppingRx            RF_BondingErase
```

### 5.7 功率 / 隐私 / 路径损耗（4 个）

```
GAPRole_SetPowerlevel            GAPRole_SetPrivacyMode
GAPRole_SetPathLossReporting     LL_SetTxPowerList
```

### 5.8 其他（2 个）

```
RFEND_SetSensitivity             TMOS_TimerIRQHandler
```

> **决策规则**：设备是「被别人连」→ CH572 够用；
> 设备要「主动去连别人」或「主动扫描」→ 换 CH592。

---

## 六、协议栈内部构成（54 个对象，99,978 字节代码）

即使只做从机，**整个协议栈的绝大部分都会被链进镜像** —— `--gc-sections` 只裁掉约 9,680 字节（9.6%）。

| 模块 | 对象数 | 归档代码 | 进入镜像 | 裁掉 |
|---|---:|---:|---:|---:|
| LL 链路层-公共 | 11 | 18,895 | 14,224 | 4,671 |
| GATT 服务端 | 2 | 11,872 | 11,620 | 252 |
| GAP 通用访问 | 2 | 8,752 | 8,752 | 0 |
| SM 安全管理 | 4 | 8,144 | 7,994 | 150 |
| GAP 绑定管理 | 1 | 7,484 | 7,196 | 288 |
| RF 射频前端 | 2 | 7,404 | 7,354 | 50 |
| 其他（bb/ip/linkdb/message/role） | 5 | 6,699 | 5,140 | 1,559 |
| TMOS 内核 | 3 | 6,554 | 4,650 | 1,904 |
| 控制流程输入 | 1 | 4,898 | 4,802 | 96 |
| **GATT 客户端** | 2 | 3,586 | 3,386 | 200 |
| IPC / 消息 | 1 | 3,546 | 3,546 | 0 |
| HCI / 主机接口 | 8 | 3,362 | 3,362 | 0 |
| L2CAP | 4 | 3,190 | 3,190 | 0 |
| ATT 属性协议 | 4 | 2,396 | 2,336 | 60 |
| GAP 从机角色 | 1 | 1,420 | 1,392 | 28 |
| AES 硬件 | 2 | 1,100 | 1,100 | 0 |
| 内存/工具库 | 1 | 676 | 254 | 422 |
| **合计** | **54** | **99,978** | **90,298** | **9,680** |

**两个反直觉的结论**：

1. **库里带着 GATT 客户端和控制角色的代码**（`gatt.o`/`gattclientapp.o` 3,586 B 等），
   而且**裁不掉** —— 因为协议栈内部互相调用（L2CAP → ATT → GATT），
   单靠 `--gc-sections` 从外部切不断。
2. **能"瘦身"的只有 API 层**。这也解释了为什么本工程做 ROM 镜像这条路：
   既然裁不动，就把它固化下来，让**应用**变小、OTA 只传应用。

> 想让镜像真正变小，只能改协议栈源码（拿不到）或换芯片（CH592 更全但更大）。

---

## 七、为什么空槽必须留着（能不能连续排？）

**能，但不能** —— 技术上完全做得到，代价是不值得。这一节把账算清楚。

### 7.1 下标不是我们定的，是 WCH 定的 ABI 契约

`CH572BLE_ROM.h` 是照 WCH 官方 `CH59xBLE_ROM.h` 的下标逐项对齐生成的，
而官方头文件里下标是**显式写死**的：

```c
#define  GATT_DiscAllChars      (( bStatus_t (*)(...)) BLE_LIB_JT(45))
#define  GATT_ReadCharValue     (( bStatus_t (*)(...)) BLE_LIB_JT(48))
#define  GATT_WriteNoRsp        (( bStatus_t (*)(...)) BLE_LIB_JT(52))
```

而 `BLE_LIB_JT(n)` 就是一句数组取址：

```c
#define BLE_LIB_JT(n)  (*(uint32_t*)(FUNCITON_BASE_ADDRESSS + (n) * 4))
```

所以 **48 号槽"必须是 GATT_ReadCharValue 的位置"**，
不管 CH572 有没有实现它。空槽就是"这里本来有个 API，本平台没有"的占位。

### 7.2 空槽的实际分布 —— 它们夹在真 API 中间

| 下标范围 | 长度 | 前一个可用 API | 后一个可用 API |
|---|---:|---|---|
| 17 | 1 | `TMOS_TimerInit` | `TMOS_GetSystemClock` |
| **42–51** | 10 | `GATT_ExchangeMTU` | `GATT_WriteNoRsp` |
| **53–60** | 8 | `GATT_WriteNoRsp` | `GATT_bm_alloc` |
| 85–90 | 6 | `GAPRole_UpdateLink` | `GAPRole_PeripheralInit` |
| 94–98 | 5 | `GAPRole_PeripheralConnParamUpdateReq` | `RF_RoleInit` |
| 116–120 | 5 | `RF_SetChannel` | `BLE_ReadCfo` |
| 123–125 | 3 | `GAPRole_UpdatePHY` | `tmos_start_reload_task` |
| 128–129 | 2 | `GATTServApp_SendServiceChangedInd` | `RFEND_TXCtuneSet` |
| 133–134 | 2 | `GAPBondMgr_PeriSecurityReq` | `LL_AdvertiseEventRegister` |
| 136–140 | 5 | `LL_AdvertiseEventRegister` | `RF_SetFrequency` |
| 142–143 | 2 | `RF_SetFrequency` | `TMOS_SystemProcess` |
| 145–147 / 149–151 / 153–155 / 157–159 | 各 3 | 表尾 5 个扩展 API 之间的对齐 | |
| 161–163 | 3 | `TMOS_TimerSet` | — |

**空槽不是"堆在表尾"的**，而是散布在整张表里。看 42–51 那一段：
`GATT_ExchangeMTU`(41) 后面紧跟着 10 个空槽，然后才是 `GATT_WriteNoRsp`(52)。
想压缩就必须把 52 提前到 42 —— 于是后面**所有**下标都要跟着挪。

### 7.3 压缩的代价（实测推演）

把 100 个可用 API 按相对顺序压成 0–99：

| API | 现在下标 | 压紧后 | 位移 |
|---|---:|---:|---:|
| `VER_LIB` / `tmos_rand` / … `tmos_memset` | 0–5 | 0–5 | 0 |
| `RF_SetFrequency` | 141 | 94 | **−47** |
| `TMOS_SystemProcess` | 144 | 95 | **−49** |
| `CH572BLE_ROM_Init` | 148 | 96 | **−52** |
| `CH572BLE_ROM_GetHandles` | 152 | 97 | **−55** |
| `FLASH_EEPROM_CMD` | 156 | 98 | **−58** |
| `TMOS_TimerSet` | 160 | 99 | **−61** |

**100 个 API 里 83 个下标会变**（只有表头几个不动）。带来三个后果：

1. **放弃 WCH 的 ABI 契约**。再也无法用官方 `CH59xBLE_ROM.h` 作参考 ——
   而那张表是"哪个下标对应哪个函数"的**唯一权威来源**。本工程能建出跳转表，
   靠的就是从官方头文件抽下标；自己另发明一套编号，等于把这个依据扔掉。
2. **已编译的代码静默失效**。同一个 `BLE_LIB_JT(48)`，压缩前是"无此 API"，
   压缩后可能变成另一个真实函数 —— 不是链接报错，而是**调用到不相干的函数**。
   这比跳到 0 危险得多。
3. **省下的 Flash = 64 个空槽 × 4 字节 = 256 字节**，占 94,924 字节镜像的 **0.27%**。

### 7.4 结论

**代价（丢掉 ABI 契约 + 静默错调的风险）远大于收益（256 字节）。所以空槽保留。**

顺带说：那 256 字节里真正"浪费"的只是 21 个纯预留槽（84 字节）；
另外 43 个空槽（172 字节）承载着信息 —— 它们明确标记了
"这个官方 API 在 CH572 上不存在"，是能力边界的一部分。

### 7.5 "不存在"是怎么被拦住的（跳 0 其实不可怕）

有人会担心"万一调到空槽怎么办"。三层防护：

| 层 | 机制 |
|---|---|
| 1. 编译期 | 那 43 个 API 在 `CH572BLE_ROM.h` 里**完全没有宏** → 写 `GATT_ReadCharValue(...)` 编译报错 |
| 2. 链接期 | 应用不链接 BLE 库，符号也不在归档里 → 就算绕过宏也是 `undefined reference` |
| 3. 万一真跳到 0 | RISC-V 规范明确把**全 0 编码定为非法指令**（防止跳转到未初始化内存）。所以会触发 Illegal Instruction 异常 → 应用侧 `HardFault_Handler` 打印 `mcause/mepc` 后停机，**是响亮失败，不是静默跑飞** |

第 3 层其实是 RISC-V 的刻意设计，所以空槽填 0 是"安全"的，
不需要改成别的哨兵值。

> 想彻底消除"不小心加到某个函数"的风险，正确做法不是压紧下标，
> 而是**别手改 `tools/rom_gen.py` 的槽位分配**，并让 `make_rom.ps1` 的
> `rom_verify.py` 每次都逐槽核对（它已经在做了）。

---

## 八、兼容桩（10 个）
WCH 在 CH572 库里裁掉了 10 个符号，但留下的代码仍引用它们，
构建时用弱桩补位（`rom/rom_stub.c`，由 `tools/rom_stub_gen.py` 生成）：

| 桩 | 引用者 | 最终是否进镜像 |
|---|---|---|
| `ATT_HandleValueConfirmation` | `att.o` | ✅ 进了（GATT 服务端路径可能触及） |
| `ATT_WriteReq` | `gatt.o` | ✅ 进了 |
| `ATT_ParseReadBlobRsp` | `gatt.o` | ❌ 被 GC |
| `ATT_ParseReadRsp` | `gatt.o` | ❌ |
| `ATT_ParseWriteRsp` | `gatt.o` | ❌ |
| `ATT_ReadBlobReq` | `gatt.o` | ❌ |
| `ATT_ReadReq` | `gatt.o` | ❌ |
| `LL_SetPrivacyMode` | `api_c.o` | ❌ |
| `l2capFindPsm` | `l2cap.o` | ❌ |
| `l2capNumActiveChannnels` | `l2cap.o` | ❌ |

8 个被 GC 说明它们只被"客户端/主机侧"的死代码引用；
剩下 2 个（`ATT_HandleValueConfirmation`、`ATT_WriteReq`）在从机路径上可达，
实机验证广播/连接/GATT 正常说明**它们没有被真的调用到** —— 但这是实机结论，
不是静态保证（见 `docs/CH572固化协议栈修复与测试.md` 的未验证项）。
