# CH572 纯 2.4G 收发固件（独立协议栈镜像方式）

> 这是一个**独立的固件**：只做 2.4G 点对点收发。
> RF 库做成**固定地址镜像**装在芯片上，应用通过跳转表调用它，
> **应用里完全没有 RF 库的代码** —— 以后改应用只传 ~1.7 KB。

---

## 一、这是什么

WCH 的 CH572 EVT 包里有两个独立的库：

| 库 | 大小 | 对象数 | 用途 |
|---|---|---|---|
| `libCH572BLE_PERI.a` | 613 KB | 54 | 完整 BLE 协议栈（前面几篇文档讲的） |
| **`libCH57xRF.a`** | **24 KB** | **4** | **纯 2.4G 收发** |

本文走的是第二条：把 `libCH57xRF.a` **预链接到固定地址**，在镜像头部放一张
**跳转表**，应用从此不链接 RF 库、只通过绝对地址调用。

### 和 BLE 固化协议栈的关系

**思路完全一样**（都是"库做镜像 + 跳转表 + 应用独立更新"），但有三处关键差异：

| | BLE 固化协议栈 | **RF 固定地址镜像** |
|---|---|---|
| 跳转表槽位 | 必须沿用 WCH 官方下标，**43 个缺失 API 要留空槽** | **13 个 API 全部可用，连续编号、零空槽** |
| 为什么 | 官方 ABI 契约（`BLE_LIB_JT(48)` 必须还是那个函数） | RF 库**没有官方 ABI 头**，不受既有编号约束 |
| 版本串 | 必须自己造（要参与 `VER_FILE` 校验，造错会卡死） | **直接引库自己的 `VER_RF_LIB`**，天然一致 |
| UUID 表 | 有（GATT 标准 UUID，22 项） | 无 |
| 兼容桩 | 有（10 个，库被裁过符号） | 无（`nm -u` 为空） |

> **这是本工程唯一一处"不需要为缺失 API 预留位置"的地方。**
> BLE 那边留空槽是被 ABI 绑住的；RF 这边没有这个约束 ——
> 13 个 API 连续排，**跳转表就 52 字节**。

### 和 BLE 方案的体积对比

| | BLE 固化协议栈 | **纯 RF（本方案）** |
|---|---|---|
| 要烧的文件 | 2 个（92.7 KB + 12.5 KB） | **2 个（5.4 KB + 1.7 KB）** |
| 芯片代码量 | 105,000 B | **7,084 B** |
| 应用 `.text` | 10,960 B | **1,660 B** |
| 可用栈 | 1,224 B | **8,300 B** |
| 依赖协议栈 | BLE + TMOS + GAP + GATT + SM | **无** |
| 需要 BLE 内存堆 | 是（5,120 B） | **不需要** |

**重烧只需传 1,660 字节的代码**（hex 4.7 KB）。

---

## 二、产物与烧录

| 文件 | 大小 | 烧录地址 | 频率 |
|---|---|---|---|
| `lib/CH572RF_ROM.hex` | 15,345 B（bin 5,424 B） | **0x00010000** | **只烧一次** |
| `build/out/rf_rom_app-tx.hex` | **4,711 B** | 0x00000000 | 以后只更新这个 |
| `build/out/rf_rom_app-rx.hex` | 4,740 B | 0x00000000 | 同上 |
| `build/out/rf_rom_app-tx-debug.hex` | 22,682 B | 0x00000000 | 带串口日志 |
| `build/out/rf_rom_app-rx-debug.hex` | 23,296 B | 0x00000000 | 带串口日志 |

### 烧录要点

- **两个文件**：① 镜像 → `0x10000` 起（只烧一次）；② 应用 → `0x0` 起
- 以后单独更新应用时，下载器**必须保留 `0x10000` 起的镜像**
- 纯 RF 没有 SNV 绑定数据要保，整片擦除不会丢配置 —— 但会擦掉镜像，
  所以擦完整片要**重新烧镜像**

### Flash 分区

```
0x00000 - 0x0FFFF   应用可用区（64 KB）
0x10000 - 0x1152F   RF 库镜像（5,424 B）
0x11530 - 0x3BFFF   余量（170.7 KB）
0x3C000 - 0x3FFFF   芯片固定（BootLoader + INFO）
```

---

## 三、构建

```powershell
cd CH572_BLE_LIB

# ① 生成镜像（只需一次；换库/换基址才要重跑）
powershell -ExecutionPolicy Bypass -File scripts\make_rf_rom.ps1

# ② 构建应用（不链接 RF 库）
powershell -ExecutionPolicy Bypass -File scripts\build_rf_rom_app.ps1 -Role both -DebugBuild
```

`-Role` 可选 `TX` / `RX` / `both`（默认 `both`）。加 `-DebugBuild` 开串口日志。

```powershell
# 只要发送端（发布版）
powershell -ExecutionPolicy Bypass -File scripts\build_rf_rom_app.ps1 -Role TX

# 改镜像基址（默认 0x10000）
powershell -ExecutionPolicy Bypass -File scripts\make_rf_rom.ps1 -RomBase 0x20000
```

> ⚠️ **改了基址必须重编应用** —— 基址写死在 `include/CH572RF_ROM.h` 里。
>
> ⚠️ **不要用 `build.ps1` 编这个** —— 那是 BLE 方案的入口。
> `examples/rf_rom_app/.rfonly` 标记已让 `build.ps1` 跳过它。

---

## 四、接线与日志

| 信号 | 引脚 | 说明 |
|---|---|---|
| UART TX | **PA3** → USB-TTL RXD | 115200 8N1（仅 `-DebugBuild` 版有输出） |
| UART RX | **PA2** ← USB-TTL TXD | 本固件不接收串口命令 |
| GND | 与 USB-TTL 共地 | 必接 |

发送端烧 tx、接收端烧 rx。

**发送端**期望输出：

```
===== 纯 RF（固定地址镜像方式）=====
sysclk      = 100000000 Hz
镜像基址    = 0x00010000
镜像 RAM 到 = 0x20000C58
[rf_rom] 镜像版本：CH57x_RF_BASIC_LIB_V1.2.0
[rf_rom] 已搬运镜像 RAM：highcode 3112 B / data 0 B / bss 48 B
[rf_rom] 初始化完成
[rf_rom] TX  freq=2402000 addr=0x71762345 len=16
[tx] 100
[tx] 200
```

**接收端**期望输出：

```
[rx] seq=0 data=00 00 00 00 A0 A1 A2 A3 A4 A5 A6 A7
[rx] seq=1 data=01 00 00 00 A0 A1 A2 A3 A4 A5 A6 A7
...
```

`seq` 是发送端写在头 4 字节的包序号，每包 +1；跳变会打印 `丢包!` —— 不用额外工具就能看丢包率。

---

## 五、可调参数

全部在 `examples/rf_rom_app/include/rf_rom_cfg.h`。

| 宏 | 默认值 | 说明 |
|---|---|---|
| `RF_APP_ROLE` | `1`（TX） | 0=接收端，1=发送端。构建脚本用 `-Role` 自动传，一般不用手改 |
| `RF_APP_FREQ_KHZ` | `2402000` | 频点，**单位 kHz**（范围 2400000–2483500），不是频道号 |
| `RF_APP_ACCESS_ADDR` | `0x71762345` | 同步字。别用全 0/全 1，也别用 BLE 广播地址 `0x8E89BED6` |
| `RF_APP_CRC_INIT` | `0x555555` | CRC 初值 |
| `RF_APP_CRC_POLY` | `0x80032D` | CRC 多项式（24 位 BLE 标准） |
| `RF_APP_TX_POWER` | `LL_TX_PWR_0_DBM` | 发射功率，−25 ~ +6 dBm |
| `RF_APP_PHY_MODE` | `PHY_MODE_2G4` | `PHY_MODE_PHY_1M` / `PHY_MODE_PHY_2M` / `PHY_MODE_2G4` |
| `RF_APP_PKT_LEN` | `16` | 每包字节数（1M/2M PHY 上限 255） |
| `RF_APP_TX_GAP_LOOPS` | `2000000` | 发送间隔（粗延时循环次数） |
| `RF_APP_RX_TIMEOUT` | `0` | 接收超时，单位 0.5us；0 = 不超时 |

> ⚠️ **收发两端的频点 / 同步字 / CRC / PHY 模式必须完全一致**，
> 否则静默收不到，而且不会有任何报错。

---

## 六、镜像内部结构

```
+0x00  魔数 8 字节（'7''5''R''F' + 4 字节保留）
+0x08  版本串指针 → 指向库自己的 "CH57x_RF_BASIC_LIB_V1.2.0"
+0x0C  跳转表 13 项 × 4 B = 52 B（零空槽）
+0x40  代码：
         .text 2,248 B 留在 Flash
         .highcode 3,112 B，LMA 在 0x10908 —— 运行时要搬到 RAM 0x20000000
```

跳转表内容（实测，**13 项里 10 项指向 RAM**）：

| 下标 | 符号 | 位置 |
|---:|---|---|
| 0 | `VER_RF_LIB` | Flash |
| 1 | `RFRole_BasicInit` | Flash |
| 2–7 | `RFRole_Stop` / `Shut` / `RFIP_StartTx` / `SetRx` / `SingleChannel` / `SetTxPower` | **RAM** |
| 8 | `RFIP_Calibration` | Flash |
| 9–12 | `RFIP_WakeUpRegInit` / `ReadCrc` / `BB_LibIRQHandler` / `LLE_LibIRQHandler` | **RAM** |

镜像占用的 RAM：`0x20000000` – `0x20000C58`（3,160 B）。

---

## 七、应用侧必须做对的三件事 ★

镜像**没有上电入口**，它不初始化自己的 RAM，也不接管中断。所以应用必须补上：

| # | 做什么 | 在代码里 | 漏掉的后果 |
|---|---|---|---|
| 1 | **校验镜像魔数** | `RF_RomImagePresent()` | 镜像没烧时会跳到空白 Flash，症状极难查 |
| 2 | **搬运镜像 RAM**<br>搬 3,112 B `.highcode` + 清 48 B `.bss` | `RfRomRam_Init()` | 跳到一片空白 RAM（全 0 = 非法指令）→ HardFault |
| 3 | **中断走跳转表** | `LLE_IRQHandler → RF_LleIrqHandler() → RF_JT(12)` | 中断悬空，收不到包 |

第 2 点顺序很重要：**必须在任何 RF 调用之前**，且搬完要 `fence.i`。

第 3 点为什么必须走跳转表：镜像不参与应用的链接，
`BB_LibIRQHandler` / `LLE_LibIRQHandler` 这些符号在应用里**根本不存在**，
所以只能运行时从跳转表取地址。多一层间接调用，换来应用可独立更新。

---

## 八、两个必须知道的坑

### 8.1 两个头文件的 `LL_TX_PWR_*` 数值不一样 ★

| 宏 | `CH572rf.h` | `CH572BLEPeri_LIB.h` |
|---|---|---|
| `LL_TX_PWR_MINUS_10_DBM` | `0x05` | `0x04` |
| `LL_TX_PWR_MINUS_8_DBM` | `0x07` | `0x06` |
| `LL_TX_PWR_MINUS_5_DBM` | `0x0A` | `0x08` |

**两个头绝对不能同时包含。** 重定义警告会被忽略，实际生效的是**先包含的那个** ——
而 `libCH57xRF.a` 是按 RF 头的值编的，用错值就设错发射功率。

本工程的处理：
- `CH572rf.h` 放在**独立目录** `include/rf/`
- `rf_rom_app.c` **刻意不 include `CONFIG.h`**，切断 `ch572_ble.h` 那条链
- 构建脚本里 `-I include\rf` 排在 `-I include` 前面

### 8.2 纯 RF 库不自己接管中断 ★

BLE 方案下协议栈通过 PFIC VTF 直接抢走 BLEB/BLEL，应用的向量表包装函数其实是死代码。
**纯 RF 库没有这一步**，所以必须自己写包装函数，并显式使能：

```c
PFIC_EnableIRQ(BLEB_IRQn);
PFIC_EnableIRQ(BLEL_IRQn);
```

**漏掉的症状：发送好像成功，但接收端永远收不到。**

---

## 九、一致性校验

```powershell
# 镜像 / 跳转表 / 头文件 三方一致性（make_rf_rom.ps1 已自动跑，这里手工复查）
python tools\verify_rf_rom.py --bin lib\CH572RF_ROM.bin --syms lib\CH572RF_ROM_SYMS.txt `
    --base 0x10000 --hdr include\CH572RF_ROM.h --ram-hdr rom_rf\rom_rf_ram_info.h
```

两道自动防线：

| 防线 | 在哪 | 检查什么 |
|---|---|---|
| 镜像生成后**就地**校验 | `make_rf_rom.ps1` 末尾 | 13 项跳转表逐项对符号地址；指向 RAM 的项必须落在 RAM 预留区内；头文件基址与项数一致 |
| 应用构建后校验 | `build_rf_rom_app.ps1` 末尾 | **应用里不许出现 RF 库实现符号**（防止误链接 `libCH57xRF.a`） |

---

## 十、工程文件位置

| 文件 | 说明 |
|---|---|
| `rom_rf/rom_rf_link.ld` | 镜像链接脚本 |
| `rom_rf/rom_rf_entry.c` | 镜像头部（魔数 + 版本串指针） |
| `rom_rf/rom_rf_ram_info.h` / `.ld` | **自动生成**：镜像占用的 RAM 区 |
| `tools/gen_rf_jt.py` | 跳转表 + 应用侧头文件生成器 |
| `tools/gen_rf_ram_info.py` | 从镜像 ELF 提取 RAM 段信息 |
| `tools/verify_rf_rom.py` | 镜像一致性校验 |
| `scripts/make_rf_rom.ps1` | 生成镜像 |
| `examples/rf_rom_app/src/rf_rom_app.c` | 走跳转表的应用 |
| `examples/rf_rom_app/include/rf_rom_cfg.h` | 所有可调参数 |
| `examples/rf_rom_app/.rfonly` | 标记文件 —— 让 `build.ps1` 跳过本示例 |
| `scripts/build_rf_rom_app.ps1` | 构建应用 |
| `src/Ld/Link_rf_rom.ld` | 应用链接脚本 |
| `lib/libCH57xRF.a` | WCH 纯 RF 库（从 EVT 包复制） |
| `include/rf/CH572rf.h` | WCH RF 头（独立目录，避免与 BLE 头冲突） |
| `include/CH572RF_ROM.h` | **自动生成**：应用侧头文件 |

---

## 十一、已知限制（上板前必读）

| 项目 | 状态 |
|---|---|
| 编译 / 链接 / 指令集 | ✅ 已验证（零警告、与 CH572 指令集兼容） |
| 镜像跳转表一致性 | ✅ 13 项逐项对符号地址，**0 项不匹配** |
| 应用不含 RF 库代码 | ✅ 8 个实现符号全部不存在（实测） |
| **实机收发** | ❌ **未验证** —— 需要两块板对通 |
| 通信距离 / 实际发射功率 | ❌ 未测 |
| 丢包率 / 长时间稳定性 | ❌ 未测 |
| 低功耗（睡眠收发） | ❌ 未实现；现在是纯轮询，CPU 一直跑 |
| 精确收发周期 | ❌ 现在是主循环粗延时；要精确周期请改用 TMR 中断（参考官方 `RF_Basic` 例程） |

### 上板第一步建议

1. 先烧镜像（`lib/CH572RF_ROM.hex` → `0x10000`），再烧应用
2. 看串口有没有 `[rf_rom] 镜像版本：CH57x_RF_BASIC_LIB_V1.2.0`
   —— 打印"镜像不存在或魔数不对"就是镜像没烧或烧错地址
3. 看有没有 `[rf_rom] 已搬运镜像 RAM` —— 没有就是 `RfRomRam_Init()` 顺序错了
4. 确认两端打印的 `freq` / `addr` 一致
5. 接收端应开始打印递增的 `seq`；收不到就按顺序查：
   - 两端 `RF_APP_FREQ_KHZ` / `RF_APP_ACCESS_ADDR` / `RF_APP_PHY_MODE` 是否一致
   - `R32_MISC_CTRL` 的 bit24-29 是否被设成 `0x0E`（`RfApp_Init` 里做了）
   - 有没有漏 `PFIC_EnableIRQ(BLEB_IRQn/BLEL_IRQn)`
   - 天线是否接好（CH572 射频是内部 PA，但需要外部匹配网络）
