# CH572 BLE 固定地址协议栈工程

本工程把 WCH 的 `libCH572BLE_PERI.a` 重定位到片上 Flash 的固定地址，并生成跳转表供独立应用调用。协议栈的普通 highcode、变量和应用堆仍占用 SRAM；这里的“ROM 镜像”是固定地址 Flash 镜像。

2026-10-09 已修复启动复制、RAM 预留、中断 ABI、release 初始化被空 PRINT 宏删除、Flash/SNV 回调和周期校准问题。发布版、调试版和静态版均完成软件构建检查。

**2026-10-10 实机验证通过**：调试版固件在 CH572 上电后正常出日志、正常广播，手机（实测扫描端 `4F:3D:7C:D5:A8:B4`）能搜到 `CH572-BLE-UART`、能连接并触发 `GAP_SCAN_REQUEST_EVENT`。这是本工程第一次实机跑通。

详细原因、验收步骤与已知限制见 [本次修复说明](docs/CH572固化协议栈修复与测试.md)；事实速查表见 [MEMORY.md](MEMORY.md)；图文说明见 [docs/html/index.html](docs/html/index.html)。

先从 `CH572_BLE_LIB` 目录构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1 -Target all
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1 -Target rom -DebugBuild
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_tests.ps1
```

需要本机 MounRiver Studio 的 WCH RISC-V 工具链和 Python 3。脚本会自动查找 WCH GCC，也可通过 `-Toolchain` 指定目录。

首轮烧录建议使用包含应用与协议栈的 **`build/out/ble_uart-rom-debug-combined.hex`**。使用 HEX 内置地址，一次下载整个合并镜像；UART 是 PA3 TX、PA2 RX，115200、8N1。正式版本使用 `ble_uart-rom-combined.hex`，关闭诊断输出。

| 产物 | 用途 |
|---|---|
| `build/out/ble_uart-rom-debug-combined.hex` | 调试版应用 + 协议栈，首次测试用 |
| `build/out/ble_uart-rom-combined.hex` | 发布版应用 + 协议栈 |
| `build/out/ble_uart-rom[-debug].hex` | 仅应用，使用配套协议栈镜像 |
| `lib/CH572BLE_ROM.hex` | 独立协议栈镜像，基址 0x20000 |
| `build/out/*-combined.json` | 固件 SHA256、Flash/RAM 大小与软件验证状态 |
| `build/out/ble_uart.hex` | 静态链接版对照固件 |

当前默认分区：

| 范围 | 内容 |
|---|---|
| 0x00000–0x1EFFF | 应用可用 Flash |
| 0x1F000–0x1FFFF | SNV 独占 4KB 擦除扇区 |
| 0x20000–0x372CB | 当前协议栈镜像，94,924 字节 |
| 0x20000000–0x200011E3 | ROM 使用的 SRAM，4,580 字节 |
| 0x200011E4–0x20002FFF | 应用高速代码、变量、5KB BLE 堆和栈 |

链接预留栈：发布版 1,224 字节，调试版 1,048 字节。这是静态预算，实机仍需观察稳定性和最大栈深度。

应用使用 `main_rom.c`、`CH572BLE_ROM=1` 和 `Link_rom.ld`；先复制 ROM highcode/data、清零 bss、注册钩子，再启用中断并初始化 BLE。ROM 应用编译 `hal/rom_flash.c`、`hal/rom_irq.c`，Flash 命令通过 ROM 跳转表调用。

> **中断走的是 PFIC VTF（Vector-Table-Free），不是中断向量表。** 协议栈在
> `BB_DevInit`/`LLE_DevInit` 里自己把 `VTFADDR[2]/[3]` 指向 ROM 内的真 ISR，
> 所以向量表里那两项和 `hal/rom_irq.c` 的包装函数默认**不会被硬件取用**。
> 调试构建为了能统计 BLE 中断次数，会在 `CH57x_BLEInit()` 之后重新武装 VTF
> 指向应用包装函数（见 `main_rom.c` 的 `ArmVtfForDebug()`）。
> 细节与实证见 [MEMORY.md 第四节](MEMORY.md) 和
> [修复说明](docs/CH572固化协议栈修复与测试.md) 的 VTF 一节。

ROM 应用不链接 `libCH572BLE.a` 或 `libISP572.a`。更换协议栈、基址或 RAM 布局后必须重新生成镜像与头文件，并重编应用；`rom_ram_info.h/.ld` 自动同步边界。单独更新应用时下载器必须保留 ROM 和 SNV 扇区；首次测试用合并 HEX 更直接。

自检核对 164 个跳转表项、22 个 UUID、元数据与 ELF 加载地址、IRQ 的 `mret`、RAM 代码、Flash 分区、HEX 校验和及指令集，并验证发布和调试合并文件与当前 ELF 一致。自 2026-10-10 起 `make_rom.ps1` 会在生成镜像后**就地**跑跳转表 / 镜像 / 头文件三方一致性校验，并且自检里加了"故意改坏槽位必须报错"的回归测试。软件检查通过不等于手机连接已通过。

**低功耗默认关闭**，且打开后需要同时把 `BLE_MEMHEAP_SIZE` 降到 4,096（详见修复说明的低功耗一节）。**实机烧录、广播、连接、配对保持、周期校准、休眠电流全部尚未验证** —— 这是当前唯一的拦路虎。

旧文档的“ROM 方式不可行”结论保留作为故障历史，已加过时标记。当前事实以 [MEMORY.md](MEMORY.md) 为准。
