# MEMORY.md —— CH572 固化协议栈工程速查表

> **用途**：给「后续接手的人或会话」看的**事实速查表**。
> 本文里每个数字都是从**当前构建产物实测**出来的，不是估算、不是抄旧文档。
> 背景与推导看 [`docs/交接文档.md`](docs/交接文档.md)；
> 修复记录与实机验收步骤看 [`docs/CH572固化协议栈修复与测试.md`](docs/CH572固化协议栈修复与测试.md)。
>
> 最后核对：全量重建 + 38 项自检通过；**实机已于 2026-10-10 跑通广播/连接**。
> 复现方法：`build.ps1 -Target all` → `build.ps1 -Target rom -DebugBuild` → `run_tests.ps1`。
>
> 图文说明见 [`docs/html/index.html`](docs/html/index.html)（总览 / 快速上手 / 运行流程图 / 固件介绍）。

> ## 实机状态（2026-10-10）
>
> | 项目 | 结果 |
> |---|---|
> | 上电启动、串口日志、RAM 初始化、协议栈 9 步 | ✅ 通过 |
> | 广播 + 手机搜到 `CH572-BLE-UART` | ✅ 通过 |
> | 连接 | ✅ 通过 |
> | 扫描请求 `GAP_SCAN_REQUEST_EVENT` | ✅ 收到（`4F:3D:7C:D5:A8:B4`） |
> | GATT 读写 / Notify / 配对绑定掉电保持 | ⏳ 待测 |
> | 长时间稳定性（≥3 分钟，覆盖 120 s 校准） | ⏳ 待测 |
> | 低功耗 / OTA | ❌ 未启用 / 未实现 |
>
> 首次验证用的固件：`build/out/ble_uart-rom-debug-combined.hex`。

> ⚠️ **本文在 2026-10-10 做过一次整体重写。** 在此之前它记录的是旧布局
> （ROM 87.2 KB / 跳转表 156 项 / `.highcode` 1,980 B / 可用栈 4,560 B 等），
> 那些数字**全部作废**。同期还留下四篇结论相反的文档
> （`docs/最终结论-ROM方式不可行.md`、`…不可用.md`、`docs/ROM方案不可行说明.md`），
> 它们是故障历史，**不是当前结论** —— 本工程现在走的就是 ROM 方式。

---

## 一、Flash 布局（实测）

```
地址范围              大小      内容                              烧录?
──────────────────────────────────────────────────────────────────────────
0x00000 - 0x1EFFF   124.0 KB   app 可用区（连续一整块）            ✔ 应用 hex
0x1F000 - 0x1FFFF     4.0 KB   SNV 绑定信息存储区                  ✘ 运行期写
0x20000 - 0x372CB    92.7 KB   BLE 协议栈 ROM 镜像                 ✔ 单独烧一次
0x372CC - 0x3BFFF    19.3 KB   余量（ROM 将来变大可往后扩）         —
0x3C000 - 0x3FFFF    16.0 KB   芯片固定（BootLoader + INFO）        ✘ 不可碰
```

关键地址：

| 名称 | 值 |
|---|---|
| `ROM_BASE`（镜像基址） | `0x00020000` |
| ROM 结束（不含） | `0x000372CC`（`LIB_FLASH_SIZE = 0x172CC` = 94,924 字节） |
| `FLASH_ROM_END` / app 区间结束 | `0x0001F000` |
| `SNV_ADDR` / `BLE_SNV_ADDR` | `0x0001F000`（独占 4 KB 物理扇区） |
| app 起点 | `0x00000000`（复位向量） |
| ROM 内部：魔数 / UUID 表 / 跳转表 | `+0x00` / `+0x08`（22 项 × 2 B）/ `+0x34`（164 项 × 4 B） |
| ROM 内部：`.data` 初值 LMA | `0x000372A0`（44 字节） |

三份产物的体积（`riscv-wch-elf-size`，单位字节）：

| 产物 | text | data | bss | Flash 覆盖 |
|---|---|---|---|---|
| ROM 镜像 `CH572BLE_ROM.elf` | 94,880 | 44 | 1,140 | `0x20000–0x372CB`，94,924 B |
| ROM 方式应用（发布） | 10,960 | 520 | 5,232 | `0x00000–0x030FB`，12,500 B |
| ROM 方式应用（调试） | — | 628 | 5,292 | 23,892 B |
| 静态库方式应用（对照） | 85,596 | 624 | 6,280 | 86,220 B |

**关键对比**：ROM 方式让**应用固件**从 86 KB 降到 12.5 KB，但芯片上的总代码量反而更多
（92.7 + 12.5 = 105 KB vs 86 KB）。价值在 **OTA 升级包 86 KB → 12.5 KB**，以及升级不动协议栈。

---

## 二、RAM 布局（12,288 字节，全部实测）

ROM 方式（发布版）：

```
0x20000000 - 0x20000D44   3,396 B   ROM 的 .highcode（运行代码，应用负责搬运）
0x20000D44 - 0x20000D70      44 B   ROM 的 .data
0x20000D70 - 0x200011E4   1,140 B   ROM 的 .bss
            ─ ROM 小计 4,580 B，结束于 0x200011E4 ─
0x200011E4 - 0x200014BC     728 B   应用 .highcode
0x200014C0 - 0x200016C8     520 B   应用 .data
0x200016C8 - 0x20002B38   5,232 B   应用 .bss（其中 MEM_BUF 5,120 B）
0x20002B38 - 0x20003000   1,224 B   可用栈  ← 发布版
```

三种配置的 RAM 对照：

| | 静态库方式 | ROM 方式（发布） | ROM 方式（调试） |
|---|---|---|---|
| ROM 区 | — | 4,580 B | 4,580 B |
| 应用 `.highcode` | 3,772 B | 728 B | 736 B |
| 应用 `.data` | 624 B | 520 B | 628 B |
| 应用 `.bss` | 6,280 B | 5,232 B | 5,292 B |
| **可用栈** | **1,608 B** | **1,224 B** | **1,048 B** |

`.bss` 的 98% 是 `MEM_BUF = BLE_MEMHEAP_SIZE / 4` 个 `uint32_t`，
当前 `BLE_MEMHEAP_SIZE = 512*10 = 5,120`。
**RAM 调优唯一有效的手段就是改它** —— 每 ±512 字节，栈余量 ∓512 字节。

`MEM_BUF` 位置：`0x20001738`（在 ROM 保留区 `0x200011E4` 之后，不冲突）。

⚠️ **栈余量是本工程最紧的资源。** `Link_rom.ld` 的下限断言是 1,024 字节，
调试版只剩 24 字节余量 —— 任何新增 RAM 变量都可能把链接打挂（这不是 bug，是 RAM 真不够）。

---

## 三、跳转表（164 项，实测构成）

| 分类 | 项数 | 说明 |
|---|---|---|
| 指向 Flash 代码 | 95 | ROM 镜像里的普通函数 |
| 指向 RAM 代码 | 5 | `tmos_rand`/`tmos_memcmp`/`tmos_memset`/`tmos_memcpy`/`TMOS_SystemProcess`，必须先搬运 `.highcode` 才能调 |
| 本平台无此 API（槽=0） | 43 | GATT 客户端 / GAP 主机 / Observer / 扩展广播等 |
| 纯预留槽（槽=0） | 21 | 表尾对齐用，无名字、无宏、不可调用 |
| **合计** | **164** | |

**表尾 5 个扩展 API 是本工程自己分配的**（ROM 建表时扩容）：

| JT 下标 | 名字 |
|---|---|
| 144 | `TMOS_SystemProcess` |
| 148 | `CH572BLE_ROM_Init` |
| 152 | `CH572BLE_ROM_GetHandles` |
| 156 | `FLASH_EEPROM_CMD`（头文件里别名成 `CH572BLE_ROM_FlashCommand`） |
| 160 | `TMOS_TimerSet` |

**43 个不可用 API + 21 个预留槽在头文件里都没有宏** → 误用是编译期/链接期报错，
不会运行时跳到地址 0。`tools/rom_verify.py` 会专门检查"有没有空槽被暴露成可调用宏"。

### 能力边界（硬事实）

| 项目 | CH592 | CH572 |
|---|---|---|
| 归档对象数 | 70 | 54 |
| 全局符号数 | 1,462 | 841 |
| 同名符号 | 813（完全相同） | 813 |
| CH592 独有（全部主机侧） | 649 | — |
| 指令集需求 | — | `rv32imac_zicsr_zifencei` + `Zba/Zbb/Zbc/Zbs` + WCH 私有 `xw` |

支持：BLE 从机、自定义 GATT、配对绑定、广播者、连接参数更新、MTU/PHY/RSSI、2.4G 私有点对点。
**不支持**：主机（Central）、观察者、GATT 客户端、多从机并发、扩展/周期广播、跳频。

能力宏在 `include/ch572_ble.h`：

```c
#define CH572_BLE_HAS_PERIPHERAL        1   /* 从机 */
#define CH572_BLE_HAS_BROADCASTER       1   /* 广播者 */
#define CH572_BLE_HAS_CENTRAL           0
#define CH572_BLE_HAS_OBSERVER          0
#define CH572_BLE_HAS_GATT_CLIENT       0
#define CH572_BLE_HAS_PERIODIC_SYNC     0
#define CH572_BLE_HAS_EXT_ADV           0
#define CH572_BLE_HAS_RF_HOPPING        0
#define CH572_BLE_HAS_MULTI_PERIPHERAL  0
```

> 决策规则：设备是「被别人连」→ CH572 够用；「主动去连别人」→ 换 CH592。

---

## 四、中断走 PFIC VTF，不走中断向量表 ★最容易搞错的一点★

**CH572 的 BLEB/BLEL 中断不经过中断向量表**，走 PFIC 的 VTF(Vector-Table-Free) 通道 #2/#3。
协议栈在 `BB_DevInit` / `LLE_DevInit` 里自己写这两个寄存器（ROM 反汇编实证）：

```asm
000357c6: li  a5,20          # BLEB_IRQn
000357c8: sb  a5,83(a3)      # a3=0xE000E000 → VTFIDR[3] = 20
000357d4: ori a5,a5,1        # <BB_IRQLibFunction> | ENABLE
000357d8: sw  a5,108(a3)     # VTFADDR[3]
; LLE 同款：VTFIDR[2]=21，VTFADDR[2]=0x200004B6 <LLE_IRQFIOFHandler>
```

真正的中断链（ROM 内部闭环、ABI 正确、不依赖应用）：

```
VTFADDR[2/3] → LLE_IRQFIOFHandler / BB_IRQLibFunction（__INTERRUPT，mret 结束）
             → LLE_IRQLibHandler / BB_IRQLibHandler（普通函数，ret 结束）
```

由此得到四条必须记住的结论：

1. **`Link_rom.ld` 里 `.vectors` 的 BLEB/BLEL 两项、`hal/rom_irq.c` 的包装函数、
   ROM 的转接桩与钩子表，默认都不会被硬件取用** —— 它们是死代码。
   **不要**再把"ROM 方式搜不到设备"归因于"中断向量悬空"（那是第五次排查的错误结论）。
2. 静态库构建里 BB/LLE 向量槽指向 `j .` 死循环，**同样无害**，别误判成缺陷。
3. **调试版心跳里的 `BLE中断=` 必须先把 VTF 抢回来才有效**，否则恒为 0。
   但只抢一次是不够的 —— 见第 4 条。
4. ★ **VTF 会被周期校准周期性改回去**（首次实机跑通时踩到的坑）。

### 第 4 条：ROM 里的初始化函数会被周期重放

首次实机跑通时日志出现矛盾：**设备广播、连接、扫描请求全部正常，但 `BLE中断=0`**，
且 `VTF 入口` 显示的仍是 ROM 的原始地址（`0x200004B7` / `0x200000DD`）。

**根因**：`BLE_RegInit()` 内部会再调一次 `LLE_DevInit` + `BB_DevInit`
（ROM 反汇编 `0x2A296` / `0x2A2A6`），这两个函数**每次都把 VTFADDR 写回 ROM 自己的 ISR**。
而周期校准由 `HAL_REG_INIT_EVENT` 驱动，首次在 `HAL_Init()` 后 800 tick（约 500 ms）触发。
所以**只在 `main()` 里武装一次，首次校准就把它覆盖掉了**。

**修复**：武装逻辑集中到 `hal/rom_irq.c` 的 `HAL_RomVtfRefresh()`，只在 `VTFADDR`
确实不等于应用包装地址时才写（正常路径零开销），两处调用：

| 调用点 | 作用 |
|---|---|
| `main()` 里 `CH57x_BLEInit()` 之后 | 首次武装，让上电到首次校准之间也能计数 |
| `HAL_ProcessEvent()` 校准分支里 `BLE_RegInit()` 之后 | **关键** —— 抵消协议栈的重写 |

```c
/* hal/rom_irq.c —— 只在确实被改回 ROM 时才重新武装 */
uint8_t HAL_RomVtfRefresh(void)
{
#ifdef DEBUG
    uint8_t fixed = 0;
    if(PFIC->VTFADDR[2] != (((uint32_t)LLE_IRQHandler) | 0x1u))
    { SetVTFIRQ((uint32_t)LLE_IRQHandler, BLEL_IRQn, 2, ENABLE); fixed = 1; }
    if(PFIC->VTFADDR[3] != (((uint32_t)BB_IRQHandler)  | 0x1u))
    { SetVTFIRQ((uint32_t)BB_IRQHandler,  BLEB_IRQn, 3, ENABLE); fixed = 1; }
    return fixed;
#else
    return 0;      /* 发布构建不动 VTF */
#endif
}
```

校准发生时若检测到被改回，会打印 `校准后 VTF 被协议栈改回 ROM，已重新武装` ——
**这行日志本身就是"周期校准真的在跑"的证据**。

> **判断计数是否有效的口诀**：
> `VTF 入口` 是 `0x200012xx` → 应用包装在跑，计数有效；
> `VTF 入口` 是 `0x200004B7` / `0x200000DD` → 又被改回 ROM 了，计数必然为 0，
> **但功能仍然完全正常，不要据此判断射频有问题**。

> **通用教训**：只要某个 ROM 函数在运行期被周期调用，它做的所有寄存器配置都会被
> **周期性重放**。应用侧任何"抢过来"的状态都必须挂在同一个周期上重新确认，
> 否则只能活到第一次重放。

发布构建不参与这条循环，中断直接进 ROM 的真 ISR（少一层转接）。

---

## 五、工具链

**只支持 WCH 官方定制工具链。** 脚本自动探测，顺序：

```
1) -Toolchain 参数指定的目录
2) PATH 里的 riscv-wch-elf-gcc / riscv-none-embed-gcc
3) MounRiver Studio 默认安装位置
```

本机实际路径：

```
D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\
    components\WCH\Toolchain\RISC-V Embedded GCC12\bin\riscv-wch-elf-gcc.exe
    →  xPack GNU RISC-V Embedded GCC 12.2.0
```

编译参数：

```
-march=rv32imac_zicsr_zifencei -mabi=ilp32 -mcmodel=medany -msmall-data-limit=8
```

**不支持通用工具链**（`riscv32-esp-elf-gcc` 等）—— 协议栈用了 `mcpy` 私有指令
与 `WCH-Interrupt-fast` 中断属性。找不到工具链时脚本直接报错退出。

> 注意：ROM 链接关掉了 `relax`，且 `/DISCARD/` 了 `.riscv.attributes`，
> 所以 `objdump -d` 会把 WCH 私有指令显示成 `fld/fsd` 之类的浮点指令。
> 那不是真的浮点指令，别慌 —— 用 `tools/isa_check.py` 判断指令集兼容性。

---

## 六、四条硬约束（改地址前必须读）

| # | 约束 | 违反后果 |
|---|---|---|
| 1 | **SNV 区不能落进 ROM 区间** | 写绑定信息时擦掉协议栈代码，**只有上板才发现** |
| 2 | **两种方式的 `BLE_SNV_ADDR` 必须一致**（`ch572_ble.h` 与生成的 `CH572BLE_ROM.h`） | 换协议栈方式后配对信息全丢 |
| 3 | **改 ROM 基址必须同步改 `Link_rom.ld` 的 `FLASH_ROM_END`**：`= 新基址 − SNV 大小` | app 与 ROM 抢同一段 Flash |
| 4 | **SNV 必须独占 4 KB 物理擦除扇区**（不是 256 字节） | 多块共用扇区时会擦掉邻居；`HAL.c` 用 `#error` 拦住了这种配置 |

约束 1/2 由 `run_tests.ps1` 的 **4b-2 节**自动检查；约束 4 是编译期 `#error`。

---

## 七、常用命令

```powershell
cd CH572_BLE_LIB

# 生成 ROM 镜像 + 头文件（唯一的 ROM 产物入口；现在会就地校验跳转表一致性）
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1

# 换协议栈库（自动备份旧库）
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 -Lib "新库路径\libCH572BLE_PERI.a"

# 探测推荐 ROM 基址
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 -Probe

# 全量构建：静态库 + ROM 镜像 + 两种方式的应用固件
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target all
#   单项目标：-Target lib / example / rom / clean
#   调试版：  -Target rom -DebugBuild

# 自检（38 项，必须全过）
powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1

# ---- 改过 .ps1 脚本后必跑这两条 ----
#   1) 修 BOM：edit 工具会吃掉 UTF-8 BOM，PowerShell 5.1 会按 GBK 解析中文而语法报错
python tools\fix_ps1_encoding.py
#   2) 验语法（用 PowerShell 自己的解析器）
python tools\check_ps1_syntax.py

# 查内存布局 / 栈余量
python tools\mem_layout.py build\out\ble_uart-rom.elf
python tools\mem_layout.py build\out\ble_uart-rom.elf --list

# 逐项核对跳转表（现在 make_rom.ps1 已自动跑，这里用于手工复查）
python tools\rom_verify.py --bin lib\CH572BLE_ROM.bin --syms lib\CH572BLE_ROM_SYMS.txt `
    --base 0x20000 --jt-json build\rom\jt_map.json --hdr include\CH572BLE_ROM.h

# 验证 rom_verify.py 本身没失灵（负向测试，故意改坏槽位要求它报错）
python tests\test_rom_verify_negative.py

# 查镜像头部结构
python tools\rom_inspect.py lib\CH572BLE_ROM.hex 0x20000 164

# 检查图文文档（标签配对 / 链接有效 / SVG 完整）
python tools\check_html_docs.py

# 验证 HAL 的关键初始化真的编进了固件
python tools\verify_hal_fixes.py
```

> ⚠️ **给 AI 助手/自动化脚本的警告**：不要用
> `(Get-Content -Raw $f) -replace ... | Set-Content $f -Encoding UTF8`
> 这类 PowerShell 往返改写中文文档。PowerShell 5.1 会按系统 ANSI(GBK) 读取 UTF-8 文件，
> 把中文变成 mojibake；而 GBK 无法映射的字节会被替换成 `?`，**造成不可逆的信息丢失**
> （本文件就因此被毁过一次）。要改中文文件请用能显式指定 UTF-8 的工具，
> 并保留原有的 BOM 状态。

---

## 八、烧录

```
① lib\CH572BLE_ROM.hex        → 0x00020000 起     （只烧一次）
② build\out\ble_uart-rom.hex  → 0x00000000 起     （以后只更新这个）
```

- **首轮建议直接烧合并镜像 `build/out/ble_uart-rom-debug-combined.hex`**，
  一次下载应用 + 协议栈，不用手动加偏移。
- 独立应用 HEX 只适合**已有匹配 ROM 镜像**时更新；下载器必须按应用扇区擦除，
  并保留 `0x1F000` 的绑定信息与 `0x20000` 起的协议栈。
- **下载器不要勾"整片擦除"**，否则 ROM 和 SNV 一起没了。
  要干净绑定状态就单独擦 `0x1F000` 那个 4 KB 扇区。
- 静态库方式（不用 ROM）只烧 `build\out\ble_uart.hex` 到 `0x0`。

---

## 九、改东西的对照表

| 想改什么 | 改哪里 | 之后必须 |
|---|---|---|
| ROM 基址 | `make_rom.ps1 -RomBase` + `src/Ld/Link_rom.ld` 的 `FLASH_ROM_END` | 重新生成 ROM + 重编应用 + 自检 |
| SNV 地址/大小 | `src/Ld/Link_rom.ld`、`BLE_SNV_ADDR`（两个头文件） | 两边同步 + 自检 |
| 协议栈 RAM 堆 | `BLE_MEMHEAP_SIZE`（两个头文件里都有默认值） | 查 `.map` 里的栈余量 + 自检 |
| 收发缓存 | `BLE_BUFF_MAX_LEN` / `BLE_BUFF_NUM` | 自检 |
| 应用 Flash 区间 | `src/Ld/Link_rom.ld` 的 `FLASH_ROM_END` | 自检 |
| 跳转表内容 | `tools/rom_gen.py` 的 `EXTRA_ROM_API`（**别手改 `rom_jt.S` / 占位表项数**） | 重新生成 ROM |
| ROM 头部布局 | `rom/rom_entry.c`（段名）+ `rom/rom_link.ld`（ASSERT 校验） | 重新生成 ROM |
| 兼容桩 | `tools/rom_stub_gen.py`（**别手改 `rom/rom_stub.c`**） | 重新生成 ROM |
| 低功耗 | `HAL_SLEEP` + `BLE_MEMHEAP_SIZE`（见下） | 实测电流 + 唤醒后连接保持 |

**打开低功耗**（一个 `-D` 里不能带逗号，必须用数组形式）：

```powershell
powershell -Command "& '.\scripts\build.ps1' -Target rom `
    -Defines @('HAL_SLEEP=TRUE','BLE_MEMHEAP_SIZE=4096')"
```

| 配置 | 链接栈余量 | 结果 |
|---|---|---|
| 堆 5,120，睡眠关 | 1,224 B | ✅ 当前默认 |
| 堆 5,120，睡眠开 | 1,080 B | ❌ 低于 1,024 下限 |
| 堆 4,608，睡眠开 | 不足 | ❌ |
| **堆 4,096，睡眠开** | **1,168 B** | ✅ 链接通过（**实机未验证**） |

原因：开睡眠会经 `HAL_TimeInit() → RTC_InitClock()` 间接引入 newlib 浮点支持
（`RTC.h` 的 `US_TO_RTC` 等宏是 double 运算），实测多占 0x90 = 144 字节。

---

## 十、自动生成的文件（别手改）

| 文件 | 生成者 |
|---|---|
| `lib/CH572BLE_ROM.hex` / `.bin` / `.map` / `_SYMS.txt` | `scripts/make_rom.ps1` |
| `lib/CH572BLE_ROM_JT.md` | `tools/rom_gen.py` |
| `include/CH572BLE_ROM.h` | `tools/rom_gen.py` |
| `include/CH572BLE_ROM_types.h` | `tools/rom_hdr_extract.py` |
| `rom/rom_stub.c` | `tools/rom_stub_gen.py` |
| `rom/rom_version.h` | `tools/rom_version_gen.py` |
| `rom/rom_ram_info.h` / `.ld` | `tools/gen_rom_ram_info.py` |
| `build/rom/rom_jt.S` | `tools/rom_gen.py` |

---

## 十一、构建流程要点（五趟链接）

```
趟 1  占位链接（跳转表放 $JT_PLACEHOLDER_ENTRIES = 164 个 0）★ 必须关 --gc-sections
趟 2  生成跳转表汇编 + 应用头文件（--no-header，只更新 asm）
趟 3  迭代链接 #1（开 --gc-sections）+ 重算跳转表
趟 4  迭代链接 #2（开 --gc-sections）+ 重算跳转表
趟 5  最终链接（不重算跳转表）
      → 从 .map 读真实大小 → 生成 rom_ram_info.h + 应用头文件 → objcopy 出 hex/bin
      → ★ rom_verify.py 逐槽校验 164 项（2026-10-10 起接入，失败即 exit 1）
```

- **趟 1 关 gc 的原因**：占位表不引用符号，开 gc 会把协议栈全裁光。
- **占位表项数**：写死 164（= 当前真实项数）。改参考头文件或换库后如果报
  `rom_jt size mismatch`，就调大 `make_rom.ps1` 里的 `$JT_PLACEHOLDER_ENTRIES`。
  脚本会用 ABI 参考估一个"下界"并在不够时警告。
- **开 gc 的收益**：裁掉约 14 KB 只服务主机角色的死代码。

---

## 十二、能力拦截测试（验证边界是否生效）

```powershell
# 误用主机 API 必须编译失败（预期）
riscv-wch-elf-gcc -march=rv32imac_zicsr_zifencei -mabi=ilp32 -Os `
  -Iinclude -Isrc/StdPeriphDriver -c -o nul tests/test_should_fail.c
# 期望报错：CH572 BLE 固件库不提供该 API，请检查平台能力宏 CH572_BLE_HAS_xxx

# 只用从机 API 必须编译通过
riscv-wch-elf-gcc ... -c -o nul tests/test_should_pass.c
```

⚠️ 这套编译期拦截**只在静态库方式**生效（`CONFIG.h` 在 `CH572BLE_ROM` 下包含的是
`CH572BLE_ROM.h`，不含带 `__attribute__((error))` 的 `ch572_ble.h`）。
ROM 方式下误用 43 个不可用 API 只会在**链接期**报 undefined reference
（因为应用不链接 BLE 库，所以仍然拦得住，只是晚一步）。

---

## 十三、已知未验证项（上板前必读）

### ✅ 已实机验证（2026-10-10）

| 项目 | 结果 |
|---|---|
| 上电启动 / 串口日志 / 4 步初始化 / BLEInit 9 步 | ✅ 通过 |
| RAM 初始化、VTF、协议栈启动 | ✅ 通过 |
| 广播 + 手机搜到 `CH572-BLE-UART` | ✅ 通过 |
| 连接 | ✅ 通过 |
| 扫描请求 `GAP_SCAN_REQUEST_EVENT` | ✅ 收到（`4F:3D:7C:D5:A8:B4`） |

首次验证固件：`build/out/ble_uart-rom-debug-combined.hex`。

### ⏳ 仍待验证

| 项目 | 状态 |
|---|---|
| GATT 读写 / Notify / 长写分包 | ⏳ 代码就绪，待按验收步骤逐项测 |
| 配对绑定掉电保持 | ⏳ 未验证（SNV 配置是否正确的关键验证点：配对 → 断电 → 重连） |
| 长时间稳定性 / 周期校准（默认 120 s） | ⏳ 未验证；建议保持连接 ≥ 3 分钟，并观察有无 `校准后 VTF 被协议栈改回 ROM` 日志 |
| 运行期栈水位 | ⏳ 无检测手段；1,224 B 是**链接期静态预算**，不是实测最大栈深 |
| 发布版行为 | ⏳ 未单独验证。发布版无日志、少 176 B 栈、SysTick 行为不同，**不是同一份代码的行为** |
| **低功耗（HAL_SLEEP）** | ❌ 未验证，且默认关闭。打开需降堆到 4,096 B；PFIC VTF 寄存器在睡眠后是否保持、唤醒后连接是否准时、实际休眠电流，全部待测 |
| OTA 流程 | ❌ 未实现（`tools/ota_feasibility.py` 是规划推算） |

### ✅ 软件层面已验证

| 项目 | 状态 |
|---|---|
| PC 侧编译/链接（零 warning） | ✅ |
| 跳转表地址一致性 | ✅ 164 项逐槽核对，0 项不匹配（`make_rom.ps1` 自动跑） |
| 指令集兼容性 | ✅ 三份 ELF 都查过（无 32 位原子/浮点 opcode） |
| 内存布局 / 栈余量 | ✅ 已验证（链接期） |
| 38 项自检 | ✅ 全部通过 |

---

## 十四、自检清单（38 项的分组）

| 分组 | 检查内容 |
|---|---|
| 1 | 静态库存在、体积合理、8 个关键符号可查 |
| 2 | 能力拦截宏有效（误用主机 API 必须编译失败，且报中文提示） |
| 3 | 正向用例编译通过且无 warning |
| 4 | 示例固件存在、指令集兼容 |
| 4b | ROM 镜像存在、头部结构符合 WCH 约定、**跳转表逐项一致** |
| **4b-2** | **Flash 分区一致性**：`LIB_FLASH_SIZE` 与 bin 大小一致、SNV 不与 ROM 重叠、SNV 在 Flash 内、app 区间非空 |
| 4c | ROM 方式应用体积正常（< 30 KB）、未误链静态库、指令集兼容 |
| 5 | RAM 余量（栈 ≥ 900 字节） |
| 6 | 发布/调试合并 HEX 与当前 ELF 一致；启动复制、中断、ISP、分区有效 |
| 7 | **自检工具自身回归**：故意改坏跳转表槽位要求 `rom_verify.py` 报错；`.ps1` 语法正确；HTML 文档标签/链接/SVG 有效 |

---

## 十五、HAL 初始化项对照（第一、二次实机排查结论）

**背景**：曾经出现「烧录成功但搜不到任何蓝牙信号」。根因是 `hal/HAL.c` 的
`CH57x_BLEInit()` 漏了官方 `HAL/MCU.c` 里的关键初始化步骤
（官方实现在 **`HAL/MCU.c`**，不在 `HAL.c` —— 一开始找错了文件）。

### 必须有的 6 项（缺一不可）

| # | 项目 | 官方写法 | 漏掉的后果 |
|---|---|---|---|
| 1 | **MISC_CTRL 射频位** | `sys_safe_access_enable(); R32_MISC_CTRL = (R32_MISC_CTRL&~(0x3f<<24))\|(0xe<<24); sys_safe_access_disable();` | ★ **射频完全不工作** —— 应用能跑但搜不到信号 |
| 2 | **SysTick** | `__SysTick_Config(SysTick_LOAD_RELOAD_Msk);` | 协议栈时基与随机数种子失效 |
| 3 | **LLE ISR 地址** | `g_LLE_IRQLibHandlerLocation = (uint32_t)LLE_IRQLibHandler;` | 静态库方式下协议栈找不到链路层 ISR。**ROM 方式的 VTF 是协议栈自己装的，这个变量实测没被库引用**，但保持与官方一致 |
| 4 | **中断优先级** | `PFIC_SetPriority(BLEL_IRQn, 0xF0);` | 中断嵌套时连接事件可能丢失 |
| 5 | **版本一致性** | `tmos_memcmp(VER_LIB, VER_FILE, tmos_strlen(VER_FILE))` | 头文件与库不匹配时行为异常且难查。⚠️ ROM 方式下这个校验**已经自证化**（两边版本串同源），见第十六节 |
| 6 | **MAC 地址** | `GetMACAddress(cfg.MacAddr)`（不能用全 0） | 协议栈可能拿不到有效地址。实测 CH572D-R0-1V1 出厂 MAC 读出全 0，所以示例用 `BLE_MAC=TRUE` + 自定义地址 `C2:00:00:00:00:01` |

### 两个必须对齐的参数值

| 参数 | 官方值 | 错误值的后果 |
|---|---|---|
| `cfg.WindowWidening` | **120**（us，"Wait rf start window"） | 太小（如 2us）射频未稳定就收发 → 广播发不出 |
| `cfg.WaitWindow` | 不设（保持 memset 后的 0） | 设成 1 属于无依据的偏离 |

### 初始化顺序（2026-10-10 修正）

`HAL_Init()` 的顺序必须与官方一致：**先注册任务 → 再 `HAL_TimeInit()` → 最后
`tmos_start_task()`**。之前本工程把 `HAL_TimeInit()` 放在 `CH57x_BLEInit()` 末尾
（在 `HAL_Init()` 之前），于是 `tmos_start_task(HAL_REG_INIT_EVENT, 800)` 是在
TMOS 时钟还没建立时排的程，首次周期校准的触发时刻不确定（不会造成功能故障，
但属于无依据的偏离）。现在 `HAL_Init()` 里按官方顺序补调（ROM 方式下会跳过，
因为 `CH57x_BLEInit()` 已经调过）。

### 怎么验证这些项真的编进了固件

```powershell
python tools\verify_hal_fixes.py
```

反汇编 `CH57x_BLEInit`，检查 SysTick 寄存器写入、`0x40001048` 读改写、
安全访问签名、PFIC 优先级写入、版本比较调用。

---

## 十六、版本串必须与协议栈 VER_FILE 一致（第二次实机排查结论）

**现象**：串口能打印了，但停在 `[3/6] 正在初始化 BLE 协议栈...`，报
`head file error...`，BLE 起不来。

**根因**：ROM 镜像里的 `VER_LIB` 原本写的是自定义名字 `CH572_BLE_ROM_V1.0`，
而头文件期望的 `VER_FILE` 是协议栈真实版本 `CH572_BLE_PERI_LIB_V1.3`，
两边前缀不一致 → 校验失败 → `while(1)`。

**修复**：`rom/rom_entry.c` 的版本串改为 `#include "rom_version.h"`，
由 `tools/rom_version_gen.py` 从 `include/CH572BLE_ROM_types.h` 读出 `VER_FILE`
生成，`make_rom.ps1` 每次构建都重新生成，保证永不同步遗漏。

**为什么用"生成头文件"而不是命令行 `-D`**：`-DROM_VERSION_STR="CH572_BLE_PERI_LIB_V1.3"`
会被 shell 吃掉引号，`V1.3` 里的点号让编译器把它当小数，
报 `'CH572_BLE_PERI_LIB_V1' undeclared here`，结果版本串变成空。

**验证**：

```powershell
# 两边都应是 CH572_BLE_PERI_LIB_V1.3
python -c "import re;d=open('lib/CH572BLE_ROM.bin','rb').read();i=d.find(b'CH572');print(re.match(rb'[ -~]+',d[i:]).group())"
```

⚠️ **已知局限（2026-10-10 记录）**：这个校验现在**两边同源**，所以恒真。
用 `-Lib` 换入不同修订的协议栈库时，运行期不会再报 `head file error`。
换库时请自己确认版本，或改进 `make_rom.ps1` 让它从库文件本身提取版本串比对。

---

## 十七、ROM 的 .data/.bss 没人初始化（第三次实机排查结论）

**现象**：串口走到 `[BLEInit 8/9] 调用 BLE_LibInit（关键步骤）` 就停住。

**根因（ROM 方式的结构性缺陷）**：ROM 镜像里有自己的变量，但 **ROM 没有上电入口**
—— 它不像普通固件那样有 startup 去搬 `.data`、清 `.bss`。于是同时踩两个坑：

1. 应用的 `.highcode` 也被搬到 `0x20000000`，**直接覆盖 ROM 的变量**
2. ROM 的 `.data` 初值永远写不进 RAM，`.bss` 是上电垃圾值

**为什么静态库方式没这个问题**：协议栈的 `.data/.bss` 就是应用的 `.data/.bss`，
由同一个 startup 统一处理。

### 修复（三部分）

1. **ROM 侧导出自己的 RAM 区范围**：`tools/gen_rom_ram_info.py` 从链接好的 ROM ELF
   直接读出 `.highcode/.data/.bss` 的 VMA/LMA/大小，生成 `rom/rom_ram_info.h`（给 C）
   与 `rom/rom_ram_info.ld`（给链接脚本）。两个文件都在 `make_rom.ps1` 里自动生成。
2. **应用预留这段 RAM**：`Link_rom.ld` 里
   `RAM (xrw) : ORIGIN = ROM_RAM_RESERVE_END, LENGTH = 0x20003000 - ROM_RAM_RESERVE_END`
   > 一开始试过用 `.rom_reserved_ram (NOLOAD)` 段推进位置计数器，
   > 但 ld 不允许位置计数器在输出段之间往回走，会报
   > `region RAM overflowed by 536868348 bytes`。直接改 MEMORY 的 ORIGIN 才对。
3. **应用替 ROM 完成初始化**：`main_rom.c` 的 `RomRam_Init()` 在**任何 ROM 函数调用之前**
   （即 `CH57x_BLEInit()` 之前）执行：搬 `.highcode`、搬 `.data`、清 `.bss`、`fence.i`。

### 排查手段

```powershell
python tools\mem_layout.py build\out\ble_uart-rom.elf
```

它按 LMA 列出 Flash 覆盖、按 VMA 列出 RAM 段与栈余量。**凡是 ROM 方式出现
"协议栈初始化卡死"，先跑它**，确认 ROM 保留区（4,580 B）与 `MEM_BUF` 不重叠。

---

## 十八、早期的"中断钩子无限递归"结论已被推翻

**第四次排查**当时的结论是：`InstallRomIrqHandlers()` 把钩子填成了转接桩自己，
导致 `BB_IRQHandler -> 钩子 -> BB_IRQHandler -> ...` 无限递归、栈溢出。

**现在看这个结论本身没错，但整条路径是死的** —— 见第四节：BLEB/BLEL 走 VTF，
根本不经过 `BB_IRQHandler` 转接桩，也就谈不上递归。
当时"日志一切正常但射频不工作"的真因是 **RAM 没初始化**（第十七节）
和 **MISC_CTRL 射频位**（第十五节），不是递归。

保留这段历史是为了记住两条仍然有效的教训：

1. `rom_handles_t` 里 `bb_irq`/`lle_irq`（转接桩）与 `bb_lib_irq`/`lle_lib_irq`
   （协议栈真 ISR）是**刻意分开**的两组字段，别混淆。
2. **ROM 与应用是两个独立链接的镜像，中间的接缝必须显式对齐**：
   早期踩过的 `FLASH_ROM_END` 符号冲突、ROM 的 `.data/.bss` 没人初始化、
   依赖链接期同名符号自动接上 —— 都是同一个根源的变体。

---

## 十九、文档地图

| 文档 | 状态 |
|---|---|
| **README.md** | ★ 当前入口，产物清单与分区 |
| **MEMORY.md**（本文） | ★ 当前事实速查表 |
| **docs/CH572固化协议栈修复与测试.md** | ★ 当前修复说明 + 实机验收步骤 + VTF 更正 + 低功耗 |
| **docs/html/index.html** | ★ 图文总览（含快速上手 / 运行流程图 / 固件介绍四页） |
| docs/交接文档.md | 有历史标记，背景推导仍可参考 |
| docs/使用说明.md、架构与适配分析.md | 有历史标记 |
| docs/最终结论-ROM方式不可行.md | ⚠️ **作废**（故障历史） |
| docs/最终结论-ROM方式不可用.md | ⚠️ **作废**（故障历史） |
| docs/ROM方案不可行说明.md | ⚠️ **作废**（故障历史） |
| docs/ROM镜像方案说明.md、从零开始-ROM镜像制作教程.md | 设计/教学，部分数字已过时 |
| docs/实机排查指南.md | 上板排查手册；搜不到信号时按它走 |
| docs/内存布局与OTA规划.html、运行流程.html | 规划推算，部分数字已过时 |

> 修复过程中曾留下两份"修复前原始文件"快照（`repair_backup_20261009/`、
> `build/pre_fix_backup/`），**2026-10-10 已删除** —— 项目已纳入 Git 管理，
> 改动历史由版本控制记录，不再需要手工快照。
