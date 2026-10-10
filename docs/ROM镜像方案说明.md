> **历史记录（2026-10-09 已更新）**：下文含旧布局、旧烧录步骤和旧故障结论。当前修复、内存预算及测试步骤请看 [CH572 固化协议栈修复与测试](CH572固化协议栈修复与测试.md)。发布/调试镜像已通过软件检查，实机验证由用户进行。

# CH572 BLE ROM 镜像方案 —— 设计与实现

本文说明「把 CH572 的 BLE 协议栈做成 `CH59xBLE_ROM.hex` 那样的固定地址 hex/bin 库镜像」
是怎么设计、怎么实现、怎么校验的。

> 相关文档：
> - 交付物总览与快速开始 → [`../README.md`](../README.md)
> - 两个 SDK 的逐符号差异分析 → [`架构与适配分析.md`](架构与适配分析.md)
> - 操作手册与 FAQ → [`使用说明.md`](使用说明.md)

---

## 一、目标与约束

### 1.1 目标

做到与 WCH 官方 CH592 的 ROM 方案完全对等的使用体验：

| 能力 | CH592（官方） | CH572（本工程） |
|---|---|---|
| 库以固定地址 hex/bin 形式提供 | ✓ `CH59xBLE_ROM.hex` | ✓ `CH572BLE_ROM.hex` / `.bin` |
| 应用侧头文件，API 定义为跳转表调用 | ✓ `CH59xBLE_ROM.h` | ✓ `CH572BLE_ROM.h` |
| 应用不链接任何 BLE 库 | ✓ | ✓ |
| 应用固件体积大幅下降 | ✓（44 KB 应用区） | ✓（19 KB vs 229 KB） |
| 中断入口由 ROM 提供、应用接管 | ✓（RAM 跳转表） | ✓（导出取地址函数） |
| 版本字符串可读 | ✓ `VER_LIB` | ✓ `VER_LIB` |
| UUID 表在镜像里 | ✓ `BLE_LIB_UUID(n)` | ✓ `BLE_LIB_UUID(n)` |

### 1.2 约束

1. **没有协议栈源码。** WCH 未公开 BLE 协议栈 C 源码，只有
   `libCH572BLE_PERI.a` 里的二进制对象。所以方案必须是
   「把现成对象**重定位**到固定地址」，而不是重新编译。
2. **CH572 的 BLE 是从机裁剪版。** 649 个主机侧符号不存在，镜像里也就没有。
3. **CH572 只有 240 KB 用户 Flash、12 KB RAM。**
   镜像本身约 88 KB，必须给应用留出足够空间。
4. **CH572 没有 BLE ROM 区。** CH592 的协议栈固化在片上 ROM（0x10000 起），
   CH572 没有这个硬件区域，所以「ROM 镜像」实际上是**烧进 Flash 固定段的库镜像**，
   概念上等价、位置上不同。

---

## 二、镜像内存布局

### 2.1 镜像内部布局（严格对齐 WCH 约定）

```
ROM_BASE + 0x00    8 字节     镜像标识
                              +0x00  uint32 魔数 0x43483537（"CH57"，小端）
                              +0x04  uint32 保留
ROM_BASE + 0x08   44 字节     UUID 表（22 项 × 2 字节，小端 GATT UUID）
ROM_BASE + 0x34   N×4 字节    函数跳转表（本工程 156 项）
之后                          版本字符串 "CH572_BLE_ROM_V1.0"
之后                          代码 + 只读数据
```

这个布局**不是猜的**，是从官方 `CH59xBLE_ROM_PERI.hex` 反解出来的：

```
$ python tools/rom_inspect.py CH592EVT-1.7/.../CH59xBLE_ROM_PERI.hex 0xC000
头部：
  +0000  6F 00 62 76 00 00 00 00  00 18 01 18 00 28 01 28 ...
  +0x00 魔数 = 0x7662006F（厂商自定义）
  UUID 表：
    BLE_LIB_UUID( 0) = 0x1800  Generic Access (GAP)
    BLE_LIB_UUID( 1) = 0x1801  Generic Attribute (GATT)
    BLE_LIB_UUID( 2) = 0x2800  Primary Service
    ...（共 22 项，与 CH59xBLE_ROM.h 里的下标一一对应）
  函数跳转表：
    BLE_LIB_JT(  0) = 0x0002CEC0  → VER_LIB 版本字符串
    BLE_LIB_JT(  1) = 0x0000DEC2  → tmos_rand
    BLE_LIB_JT(  7) = 0x0000E0CA  → tmos_set_event
    ...
  跳转表里有 3 项指向 RAM（0x2000_xxxx）——
  这是 BLE_LIB_RAM_JT 机制的实现：TMOS_SystemProcess / BB_IRQLibHandler / LLE_IRQLibHandler
```

### 2.2 Flash 分区（ROM 方式）

```
CH572 用户 Flash = 240 KB = 0x00000 - 0x3BFFF

0x00000000 - 0x0001EFFF  124 KB   app 主体（连续一整块）← 只更新这一块
0x0001F000 - 0x0001FFFF    4 KB   SNV 绑定信息存储区（运行期写，不烧录）
0x00020000 - 0x00035CE3  ~88 KB   BLE 协议栈 ROM 镜像 ← 先烧一次，不再改动
0x00035CE4 - 0x0003BFFF  ~25 KB   余量（ROM 将来长大了可往后扩）
0x0003C000 - 0x0003FFFF   16 KB   芯片固定占用（BootLoader + INFO）>FLASH_APP AT>FLASH_APP
0x0001A000 - 0x0003AFFF  132 KB   应用主体                 ← 只更新这一块
0x0001F000 - 0x0001FFFF    4 KB   SNV 绑定信息存储区
```

分区在两边都有强制保证：

- ROM 侧：`rom/rom_link.ld` 里 `ROM (rx) : ORIGIN = ROM_BASE, LENGTH = 208K`，
  超了直接链接失败。
- 应用侧：`src/Ld/Link_rom.ld` 里 `FLASH_ROM_END = 0x0001F000`（= ROM 基址 0x20000 − SNV 4 KB），
  应用主体从 ROM 之后开始分配。

> 本工程把 ROM 放在 **Flash 高位**（0x20000 起），把前面 0x0 - 0x1EFFF 这 124 KB
> 连续空间整块留给 app —— app 不再被 ROM 切成两段，链接脚本因此只有一个 MEMORY 区域，
> 复位向量与代码在同一块里，也不需要跨段跳板。

### 2.3 头部布局的强制校验

GNU ld 在同一个输出段内会按**输入段名的字母序**排列，如果直接把
`_rom_magic` 和 `_rom_uuid_table` 都放进 `.rom_header`，
字母序会让 uuid 排到 magic 前面，头部布局就全错了。所以必须拆成两个命名段。

解决办法：**拆成两个命名段**，在链接脚本里按序拼接，并加 ASSERT：

```ld
.rom_header ROM_BASE :
{
    KEEP(*(.rom_magic))     /* +0x00  8 字节 */
    . = ALIGN(4);
    KEEP(*(.rom_uuid))      /* +0x08  44 字节 */
    . = ALIGN(4);
} >ROM

ASSERT(. == ROM_BASE + 0x34,
       "ROM 头部布局错误：.rom_header 必须正好 0x34 字节（8 字节标识 + 44 字节 UUID 表）")
```

另外，`_rom_magic` 用**一个 8 字节数组**而不是两个 `uint32_t` 变量 ——
同一段内变量的顺序由编译器后端决定，`-O` 下不保证等于源码声明顺序。
合成一个对象后地址完全确定。

---

## 三、跳转表 ABI

### 3.1 为什么要沿用 CH592 的下标

`CH59xBLE_ROM.h` 里每个 API 都绑定了一个固定的 `BLE_LIB_JT(n)` 下标。
本工程**刻意沿用同一套下标编号**，好处是：

- 从 CH592 ROM 工程迁移代码过来，头文件里的下标不用改；
- 两平台的 API 对照表可以并排看；
- 将来 WCH 若补齐 CH572 的 ROM 库，下标对得上。

做法：`tools/rom_jt_extract.py` 从官方 `CH59xBLE_ROM.h` 里
把「下标 → API」映射抽出来（137 个函数 + 22 个 UUID），作为 ABI 基准。

### 3.2 缺失项怎么处理

CH592 有、CH572 没有的 API（43 项，如 `GAPRole_CentralInit`、
`GATT_DiscAllPrimaryServices`）在跳转表里填 **0**，
并且在生成的头文件里**完全省略该宏** —— 于是误用会在**编译期**报错
（`implicit declaration of function`），而不是运行时跳到地址 0 跑飞。

`lib/CH572BLE_ROM_JT.md` 是完整的逐项对照表，标明每一项目前是
「可用」还是「本平台无此 API」。

### 3.3 RAM 跳转表项的替代方案

官方 CH59x 有 3 个入口不走 Flash 跳转表，而是放在 RAM 固定地址：

```c
#define BLE_LIB_RAM_JT(n)   (*(uint32_t*)( 0x20000000 + n*4 ))
#define TMOS_SystemProcess  ((void (*)(void)) BLE_LIB_RAM_JT(0))
#define BB_IRQLibHandler    ((void (*)(void)) BLE_LIB_RAM_JT(1))
#define LLE_IRQLibHandler   ((void (*)(void)) BLE_LIB_RAM_JT(2))
```

CH572 的 SRAM 只有 12 KB，再占 12 字节做跳转表不划算，而且这套约定
对应用来说很隐晦（要记住 0x20000000 的偏移）。本工程改成**显式导出**：

```c
/* ROM 侧：rom/rom_support.c */
typedef struct {
    uint32_t magic;                     /* 0x524F4D48 ('ROMH') */
    void (*bb_irq)(void);               /* 基带中断 */
    void (*lle_irq)(void);              /* 链路层中断 */
    uint32_t (*tmos_process)(void);     /* 预留 */
} rom_handles_t;

const rom_handles_t *CH572BLE_ROM_GetHandles(void);
```

`TMOS_SystemProcess` 则作为一个普通 API 分配新的跳转表下标，
应用直接通过 `BLE_LIB_JT(n)` 调用。

### 3.4 本工程扩展的 ROM API

除了 CH59x 的 ABI，本工程额外导出 3 个 ROM 胶水 API，占用表尾空闲下标：

| API | 作用 |
|---|---|
| `TMOS_SystemProcess` | 主循环调度入口 |
| `CH572BLE_ROM_Init` | 注册应用中断钩子，返回版本字符串 |
| `CH572BLE_ROM_GetHandles` | 取 ROM 中断入口地址 |

下标是在生成时动态分配的空闲项，不会和 CH59x 的 ABI 冲突。
完整下标见 `lib/CH572BLE_ROM_JT.md`。

---

## 四、构建流程：三趟链接

跳转表的内容（每个 API 的绝对地址）依赖链接结果，而链接又需要跳转表存在 ——
这是个循环依赖。解决办法是**先占位、再收敛**：

```
┌─ 趟 0：准备对象
│    ar x libCH572BLE_PERI.a            → 54 个协议栈 .o
│    编译 rom/rom_entry.c                → 镜像头部数据
│    编译 rom/rom_support.c              → 中断转接 + 钩子表
│    编译 rom_stub.c（自动生成）          → 裁剪残留符号的弱桩
│    编译 src/StdPeriphDriver/*.c        → ROM 自包含所需的外设驱动
│
├─ 趟 1：占位链接（不开 --gc-sections）
│    跳转表先放 152 个 0 占位
│    目的：让每一个协议栈函数都被保留，从 map 里取到**全部符号的地址**
│    产出：rom_pass1.map
│
├─ 趟 2：生成跳转表 + 应用侧头文件
│    tools/rom_gen.py --map rom_pass1.map --jt jt_map.json
│        ├─ 用 map 里的符号地址填跳转表（汇编形式，地址由链接器最终确定）
│        └─ 生成 CH572BLE_ROM.h（每个 API → BLE_LIB_JT(n) 宏）
│    汇编 rom_jt.S → rom_jt.o
│
├─ 趟 3：迭代链接 #1（开 --gc-sections）
│    跳转表现在真实引用了所有要暴露的 API，它们成了 GC 根；
│    协议栈里"只有主机角色才会用到"的死代码被裁掉，
│    连带解决它们引用的、PERI 库里根本不存在的符号。
│    用新的 map 重新生成跳转表（消除表自身占位带来的地址偏移）
│
├─ 趟 4：迭代链接 #2（开 --gc-sections）
│    再生成一次，确保地址收敛
│
└─ 趟 5：最终链接（不重算跳转表）
     保证"表内容"与"镜像布局"严格一致
     产出：CH572BLE_ROM.elf / .hex / .bin
```

对应 `scripts/make_rom.ps1` 的实现。

### 4.1 为什么趟 1 必须关掉 `--gc-sections`

`--gc-sections` 会从"根"出发做可达性分析，把没人引用的段裁掉。
趟 1 的跳转表是 152 个 0，**不引用任何符号**，
所以整个协议栈都会被判定为不可达而裁掉 —— map 里就只剩头部几个符号，
跳转表无从生成。

所以趟 1 必须关掉 `--gc-sections`。若开启，`可用 API` 会从 94 个降到 14 个。

### 4.2 为什么趟 2 之后要开 `--gc-sections`

协议栈对象里有大量"只有主机角色才会走到"的代码，它们引用了
`ATT_ReadReq`、`LL_SetPrivacyMode` 这类 PERI 库**根本不提供**的符号。
不开 gc 的话这些引用必须全部有定义，否则链接失败。

开了 gc 之后，这些代码从跳转表（GC 根）不可达 → 被裁掉 →
它们的未定义引用也就不再是问题。

实测：趟 1（无 gc）全量链接 text 约 103 KB；
趟 5（有 gc）最终镜像 text 约 88 KB，裁掉约 15 KB 死代码。

### 4.3 裁剪残留符号的弱桩

即使开了 gc，仍有少数符号需要桩兜底（保险起见）。
`tools/rom_stub_gen.py` 自动扫描：

1. 收集协议栈归档里**所有**对象的已定义符号
   （**注意**：必须包含 LOCAL 绑定的 `.bss`/`.sbss` 数据 ——
   `gapRole_state`、`gBleHostPara` 这些在目标文件里是 local 的，
   只收集 `STB_GLOBAL` 会把它们误判为"缺失"并生成桩，
   那会把真实数据覆盖掉，属于致命错误（会多出几十个本不该存在的桩）。
2. 从"未定义符号"里减去已定义的，剩下的就是真缺的；
3. 跳过 libc（`memcpy` / `memset` / `strlen` / `__udivdi3` …）与
   `__` 开头的编译器运行时符号 —— 它们由 `-lc` / `-lgcc` 提供；
4. 按命名习惯区分函数与变量，分别生成函数桩（返回 0）与变量桩（零初始化）。

本工程实测只需要 8 个函数桩 + 2 个变量桩。

---

## 五、应用侧头文件的生成

`tools/rom_gen.py` 生成 `include/CH572BLE_ROM.h`，结构如下：

```c
#ifndef __CH572BLE_ROM_H
#define __CH572BLE_ROM_H

/* 1) 协议栈类型与常量（从官方头文件抽取的"纯类型"版本） */
#include "CH572BLE_ROM_types.h"

/* 2) 可配置项默认值（ROM 方式下 ch572_ble.h 不参与编译） */
#define BLE_MEMHEAP_SIZE   (512 * 10)
...

/* 3) ROM 地址布局与访问宏 */
#define LIB_FLASH_BASE_ADDRESSS    0x00020000
#define FUNCITON_BASE_ADDRESSS     (LIB_FLASH_BASE_ADDRESSS + 0x34)
#define BLE_LIB_JT(n)              (*(uint32_t*)( FUNCITON_BASE_ADDRESSS + (n) * 4 ))
#define BLE_LIB_UUID(n)            ( UUID_BASE_ADDRESSS + (n) * 2 )
#define VER_LIB                    ((const uint8_t*) BLE_LIB_JT(0))

/* 4) GATT UUID（直接指向 ROM 里的 UUID 表） */
#define primaryServiceUUID         ((const uint8_t*) BLE_LIB_UUID(2))
#define characterUUID              ((const uint8_t*) BLE_LIB_UUID(5))
...

/* 5) 协议栈 API（94 个可用的，其余 43 个刻意省略） */
#define tmos_start_task   (( BOOL (*)(tmosTaskID, tmosEvents, tmosTimer) ) BLE_LIB_JT(9))
#define GAPRole_PeripheralInit (( bStatus_t (*)(void) ) BLE_LIB_JT(46))
...

/* 6) ROM 中断入口与钩子表 */
typedef struct { ... } rom_handles_t;
typedef struct { ... } rom_app_hooks_t;
#define CH572BLE_ROM_Init(hooks)     (( ... ) BLE_LIB_JT(153))(hooks)
#define CH572BLE_ROM_GetHandles()    (( ... ) BLE_LIB_JT(154))()
#define TMOS_SystemProcess()         (( ... ) BLE_LIB_JT(152))()
#endif
```

### 5.1 为什么需要一个单独的 `CH572BLE_ROM_types.h`

ROM 方式下应用**不能**包含 `CH572BLEPeri_LIB.h` —— 那会把 API 声明成普通
函数符号（`extern bStatus_t tmos_start_task(...)`），
和头文件里的 `#define tmos_start_task (... ) BLE_LIB_JT(9)` 冲突。

但协议栈的 typedef / struct / enum / 常量宏（`bleConfig_t`、
`gapRoleEvent_t`、`attHandleValueNoti_t`、`GATT_PROP_*` …）应用仍然需要。

所以 `tools/rom_hdr_extract.py` 从官方头文件里抽出"纯类型"部分单独成文。
抽取规则：

| 内容 | 处理 |
|---|---|
| `typedef ...;`（含函数指针类型） | 保留 |
| `struct` / `enum` / `union` 定义 | 保留 |
| `#define`（含续行的多行宏） | 保留 |
| `extern` 变量与函数声明 | 丢弃 |
| 普通函数原型 | 丢弃 |
| include guard / `#include` / `extern "C"` | 丢弃 |

实测抽出 107 个 typedef + 691 个 `#define`，输出 826 行。

> 这里踩过两个坑：
> 1. 源文件是 Windows 换行，如果只按 `'\n'` 切分，行尾会残留 `'\r'`，
>    拼回去时会在 `#define ... \` 续行宏中间插入空行，把宏拆断；
> 2. 官方头文件里有些函数宏的原型是跨行的，抽取脚本必须**先把续行折叠成单行**
>    再去匹配，否则参数串里会混进反斜杠。

---

## 六、校验：三道关卡

ROM 方案最容易出错的地方是「跳转表里写的地址」与「镜像里符号的真实地址」
不一致 —— 两者由不同步骤产生，差一轮迭代就全线错位，
而且只有烧进芯片才会暴露。所以本工程做了三道自动校验：

### 6.1 头部结构校验（`tools/rom_inspect.py`）

```
镜像地址范围 : 0x00020000 - 0x00035CE3   数据 89316 字节 (87.6 KB)
+0x00 魔数 : 0x43483537  [OK] "CH57"
UUID 表：识别为已知 GATT UUID 的项数: 22 / 22
函数跳转表：
  项数            : 156
  指向 ROM 内部   : 96 项
  空项(0)         : 52 项（本平台不提供的 API）
  指向镜像外部    : 0 项 [OK]
结论: [OK] 镜像结构符合 WCH ROM 约定
```

### 6.2 跳转表逐项核对（`tools/rom_verify.py`）

从符号表取出每个 API 的真实地址，逐项与镜像里第 n 项的 32 位值比对：

```
地址正确          : 94 项
本平台无此 API(0) : 43 项
地址不匹配        : 0 项
[通过] 跳转表与镜像完全一致

头文件基址 : 0x00020000  [OK]
结论: [通过] ROM 镜像、跳转表、头文件三者一致，可以烧录
```

### 6.3 指令集兼容性校验（`tools/isa_check.py`）

CH572 内核是 **RV32IMBC**：没有 A 扩展（原子指令）、没有浮点单元。
用 `rv32imac` 编译理论上可能生成 `amo*` / `lr.w` / `sc.w`，
这些指令在 CH572 上会触发非法指令异常直接跑飞。

脚本先从 ELF 符号表取出全部 `STT_FUNC` 符号，**只对真实函数区间**
线性扫描解码 —— 直接扫整个 `.text` 会把字符串和跳转表当成指令，
产生大量假阳性（会误报出十几条"浮点指令"和上百条"未知 opcode"，
实际全是数据）。

```
函数符号: 676 个（只扫描这些真实代码区间）
解码指令数: 28998
  compressed(16bit) 18622 / op-imm 2643 / branch 1347 / load 1337 / ...
  [通过] 无 A 扩展原子指令、无浮点指令。
  [通过] 无标准 RISC-V 之外的 opcode。
```

三道校验全部集成进 `scripts/run_tests.ps1`，共 26 项检查。

---

## 七、实测数据

### 7.1 ROM 镜像

```
文件                lib/CH572BLE_ROM.hex   251,288 字节（Intel HEX 文本）
                    lib/CH572BLE_ROM.bin    89,316 字节（纯二进制）
地址范围            0x00020000 - 0x00035CE3
跳转表              156 项（94 项可用 / 43 项本平台无 / 其余为扩展项）
可用 API            94 个
内存占用            text 约 88 KB，data 48 字节，bss 1,152 字节
```

### 7.2 两种方式的应用固件对比

同一个 `ble_uart` 示例，同一套应用源码：

```
   text     data      bss      dec      hex   filename
  80916      588     6272    87776    156e0   ble_uart.elf        （静态库方式）
   6360      476     5272    12108     2f4c   ble_uart-rom.elf    （ROM 方式）

hex 文件：约 225 KB  →  约 19 KB
应用可用栈：1,232 字节  →  1,712 字节
```

**代码体积降低 92%**。原因很直接：ROM 方式下应用固件里
只剩业务逻辑 + HAL + 外设驱动，协议栈那 74 KB 代码在 ROM 镜像里。

### 7.3 RAM 占用（ROM 方式）

```
0x20000A30  _sbss        应用静态数据起点
0x20001EC8  _ebss        bss 结束（共 5,272 字节，其中 MEM_BUF 占 5,120）
0x20003000  _eusrstack   RAM 顶端
            可用栈 ≈ 1,712 字节
```

ROM 方式比静态库方式**省下 1,000 字节 bss**：
因为 ROM 里的 HAL/SNV 缓冲区不再占用应用的 RAM。

---

## 八、已知限制与后续可做的事

### 8.1 限制

1. **只支持从机角色。** 与静态库方式相同，受芯片固件限制。
2. **ROM 基址变更需要同步改两处**：`make_rom.ps1 -RomBase` 与
   `src/Ld/Link_rom.ld` 的 `FLASH_ROM_END`。
   建议在脚本里加自动同步（见下）。
3. **实机未验证。** PC 侧校验完备，但没有 CH572 EVT 板做上板测试。

### 8.2 可做的事

| 事项 | 说明 |
|---|---|
| ROM 基址自动化 | 让 `make_rom.ps1` 把最终 `_rom_end_addr` 写进 `Link_rom.ld` 或生成一个 `rom_layout.h`，避免手工同步 |
| 把 ROM 镜像并入应用 hex | 目前是两个 hex 分别烧录；也可以生成一个"ROM + 应用"的合并 hex，一次烧完 |
| 支持多套 ROM 变体 | 仿照官方 `ROM` / `ROMx` / `ROM_PERI`，做几个不同基址/不同裁剪的变体 |
| 上板验证 | 烧录 `CH572BLE_ROM.hex` + `ble_uart-rom.hex`，用手机 App 验证广播/连接/读写/Notify |
| OTA 集成 | 结合 EVT 包里的 `BackupUpgrade_OTA` 示例，做"只升级应用段"的 OTA |

---

## 八点五、构建工具链

本工程用 **MounRiver Studio 2 自带的 WCH 定制工具链**，脚本会自动探测：

```
D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\
    components\WCH\Toolchain\RISC-V Embedded GCC12\bin\riscv-wch-elf-gcc.exe
    →  xPack GNU RISC-V Embedded GCC 12.2.0
```

### 为什么选它

协议栈源码里用到了两个 WCH 扩展，只有 WCH 定制工具链原生支持：

| 扩展 | 用在哪 | 通用工具链 | WCH 工具链 |
|---|---|---|---|
| `mcpy` 青稞私有指令 | `core_riscv.h` 的 `__MCPY()` | 报 `unrecognized opcode` | 实测生成 3 处 `mcpy` |
| `WCH-Interrupt-fast` | `CH57x_common.h` 的 `__INTERRUPT` | 降级为普通中断 | 中断用硬件压栈，`mret` 前无寄存器保存 |

通用工具链也能构建（脚本自动加 `-DINT_SOFT -DUSE_STD_MCPY` 降级），
但体积与性能略差：

| | 通用工具链 | **WCH 工具链** |
|---|---|---|
| ROM 镜像 bin | 89,900 字节 | **89,316 字节** |
| 应用 text | 6,360 字节 | **6,140 字节** |
| 可用栈 | 1,232 字节 | **1,384 字节** |

### 一个交叉验证证据

反汇编协议栈自带的 `tmos.o`、`ll_connect.o`，里面**也含 `mcpy` 指令** ——
说明 WCH 当初编译这个库用的就是自己的工具链，与本工程现在用的完全一致。

---

## 九、复现命令速查

```powershell
# 构建 ROM 镜像 + ROM 方式示例
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target rom

# 换 ROM 基址（记得同步改 src/Ld/Link_rom.ld 的 FLASH_ROM_END）
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 -RomBase 0x00020000

# 查看镜像结构
python tools\rom_inspect.py lib\CH572BLE_ROM.hex 0x20000 156

# 逐项核对跳转表
python tools\rom_verify.py --bin lib\CH572BLE_ROM.bin --syms lib\CH572BLE_ROM_SYMS.txt `
    --base 0x20000 --jt-json build\rom\jt_map.json --hdr include\CH572BLE_ROM.h

# 从官方头文件抽取跳转表 ABI
python tools\rom_jt_extract.py ..\CH592EVT-1.7\EVT\EXAM\BLE\LIB\CH59xBLE_ROM.h build\jt.json

# 抽取纯类型头文件
python tools\rom_hdr_extract.py include\CH572BLEPeri_LIB.h --out include\CH572BLE_ROM_types.h

# 扫描并生成兼容桩
python tools\rom_stub_gen.py --lib lib\libCH572BLE_PERI.a --keep build\rom\keep.txt `
    --out rom\rom_stub.c --report build\rom\stub_report.md

# 全部自检
powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
```
