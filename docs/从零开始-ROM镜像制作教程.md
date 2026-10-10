> **历史记录（2026-10-09 已更新）**：下文含旧布局、旧烧录步骤和旧故障结论。当前修复、内存预算及测试步骤请看 [CH572 固化协议栈修复与测试](CH572固化协议栈修复与测试.md)。发布/调试镜像已通过软件检查，实机验证由用户进行。

# 零基础教程：CH572 BLE ROM 库（hex）是怎么做出来的

> 这份文档假设你**没写过嵌入式、也没见过链接脚本**。
> 从「工具链在哪」一路讲到「hex 是怎么一步步生成的」，每一步都说明**在干什么、为什么这么干**。
>
> 已经会用了想查参数？直接看 [`使用说明.md`](使用说明.md)。
> 想看设计推导？看 [`ROM镜像方案说明.md`](ROM镜像方案说明.md)。

---

## 目录

- [第 0 章 先搞清楚：我们要做的东西长什么样](#第-0-章-先搞清楚我们要做的东西长什么样)
- [第 1 章 工具链：用的是哪一个](#第-1-章-工具链用的是哪一个)
- [第 2 章 三个关键概念（不懂这三个后面看不懂）](#第-2-章-三个关键概念不懂这三个后面看不懂)
- [第 3 章 hex 是怎么生成的：完整流程](#第-3-章-hex-是怎么生成的完整流程)
- [第 4 章 亲手跑一遍](#第-4-章-亲手跑一遍)
- [第 5 章 生成出来的 hex 长什么样、怎么验证](#第-5-章-生成出来的-hex-长什么样怎么验证)
- [第 6 章 烧到芯片上是怎么运行的](#第-6-章-烧到芯片上是怎么运行的)
- [第 7 章 常见疑问](#第-7-章-常见疑问)

---

## 第 0 章 先搞清楚：我们要做的东西长什么样

### 0.1 一句话说明

**把 WCH 给 CH572 的 BLE 蓝牙协议栈（原本是一个 `.a` 库文件），转换成一个固定地址的 `.hex` 文件，烧进芯片一次就不用再动了。应用代码以后只管调用它。**

### 0.2 为什么要这么做

WCH 给 CH592 提供了这种「固定地址 hex」形式的库：

```
CH592EVT-1.7\EVT\EXAM\BLE\LIB\
    CH59xBLE_ROM.hex           ← 全功能版（192 KB）
    CH59xBLE_ROMx.hex          ← 同上，备用地址段
    CH59xBLE_ROM_PERI.hex      ← 仅从机版（136 KB）
    CH59xBLE_ROM.h             ← 配套头文件
```

但给 CH572 只给了一个普通的静态库：

```
CH572EVT-1.4\EVT\EXAM\BLE\LIB\
    libCH572BLE_PERI.a         ← 只是个 .a 库文件
    CH572BLEPeri_LIB.h         ← 配套头文件
```

**两者差别在哪？**

| | `.a` 静态库 | `.hex` 固定地址库 |
|---|---|---|
| 本质 | 一堆待链接的目标文件 | 一份已经算好地址、可以直接烧录的镜像 |
| 使用 | 应用编译时把它**链进自己的固件** | 应用**不链接它**，只按绝对地址调用 |
| 应用固件大小 | 229 KB（协议栈全在里面） | **19 KB**（协议栈在芯片里，不在固件里） |
| 升级 | 升级要重传整个 229 KB | 只传 19 KB，而且不动协议栈，更安全 |

所以本工程做的事，就是**给 CH572 补上那份 `.hex`**。

### 0.3 最终产物一览

```
CH572_BLE_LIB\lib\
    CH572BLE_ROM.hex          ★ 你要的东西（251,288 字节的文本文件，数据 87.2 KB）
    CH572BLE_ROM.bin          ★ 同上，纯二进制 89,316 字节
    CH572BLE_ROM.map            链接映射（每个函数的绝对地址都在这）
    CH572BLE_ROM_JT.md          跳转表对照表（哪个编号对应哪个 API）
```

配套的应用侧头文件：

```
CH572_BLE_LIB\include\
    CH572BLE_ROM.h            ★ 应用包含这个，94 个 API 都能用
    CH572BLE_ROM_types.h        协议栈的类型定义（被上面那个引用）
```

---

## 第 1 章 工具链：用的是哪一个

### 1.1 答案

**是这一个，你给的路径完全正确：**

```
D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12
```

里面的编译器：

```
bin\riscv-wch-elf-gcc.exe      xPack GNU RISC-V Embedded GCC 12.2.0
```

### 1.2 本工程只支持 WCH 官方工具链

协议栈源码用到了两个 WCH 扩展，**只有 MounRiver Studio 自带的定制工具链支持**：

| 扩展 | 用在哪 | 通用 RISC-V 工具链 |
|---|---|---|
| `mcpy` 青稞内核私有指令 | `core_riscv.h` 的 `__MCPY()` | ✗ 报 `unrecognized opcode` |
| `WCH-Interrupt-fast` 中断硬件压栈 | `CH57x_common.h` 的 `__INTERRUPT` | ✗ 不认识该属性 |

所以工程里没有"降级宏"这类开关 —— 直接用官方工具链，两个扩展原生生效。

| | 本工程使用 |
|---|---|
| 工具链 | **`riscv-wch-elf-gcc`（MounRiver Studio 2 自带，GCC 12.2）** |
| 备用 | `riscv-none-embed-gcc`（MounRiver Studio 1 自带） |
| 降级宏 | 不需要 |
| ROM 镜像 bin | **89,316 字节** |
| 应用固件 text | **6,140 字节** |

脚本找不到工具链时会直接报错退出，并提示安装 MounRiver Studio 或用
`-Toolchain` 指定目录 —— 不会悄悄退回通用工具链去编一个有问题的产物。

### 1.3 官方工具链带来的两个好处

WCH 的工具链是**定制版**，认识两个通用工具链不认识的东西：

#### ① `mcpy` —— 青稞内核的私有指令

协议栈源码里有这么一段（`core_riscv.h`）：

```c
__attribute__((always_inline)) RV_STATIC_INLINE void __MCPY(void *dst, void *start, void *end)
{
    __asm volatile("mcpy %2, %0, %1" : "+r"(start), "+r"(dst) : "r"(end) : "memory");
}
```

`mcpy` 是沁恒青稞内核的一条"快速内存拷贝"指令，**通用工具链不认识它**，会报：

```
Error: unrecognized opcode `mcpy a2,a1,a5'
```

通用工具链不认识它。用 WCH 官方工具链可以直接汇编通过，实测编译产物里真的生成了 `mcpy` 指令：

```
$ riscv-wch-elf-objdump -d build\rom\obj\CH57x_sys.o | findstr mcpy
   ...  mcpy  ...              ← 真的用上了
```

> 顺便一个有意思的发现：协议栈自己的 `tmos.o`、`ll_connect.o` 里**也含 `mcpy`** ——
> 说明 WCH 当初编译这个库用的就是自己的工具链，现在我们也用同一个，完全对齐。

#### ② `WCH-Interrupt-fast` —— 硬件压栈中断

协议栈头文件里中断函数是这么声明的（`CH57x_common.h`）：

```c
#define __INTERRUPT   __attribute__((interrupt("WCH-Interrupt-fast")))
```

青稞内核有「中断硬件压栈」功能：进中断时 CPU 自动把寄存器压栈，出中断自动恢复。
用这个属性编出来的中断函数**没有寄存器保存/恢复代码**，又快又小。
通用工具链不认识这个属性，会降级成普通中断（要手动保存所有寄存器）。

实测对比 `RTC_IRQHandler` 的汇编：

**官方工具链（用硬件压栈）：**
```asm
00000026 <RTC_IRQHandler>:
    26:  lui    a5,0x40001
    2a:  li     a4,48
    2e:  sb     a4,48(a5)
    32:  li     a5,1
    34:  auipc  a4,0x0
    38:  sw     a5,0(a4)
    3c:  mret                    ← 只有 mret，没有任何寄存器保存！
```

**通用工具链（不认识该属性）：** 会降级成普通中断，多出一大段 `sw x1,4(sp)` / `sw x4,8(sp)` ... 的寄存器保存代码。

### 1.4 构建脚本现在会自动找到它

不需要你手工指定路径，脚本会自己按这个顺序找：

```
1) 命令行 -Toolchain 参数指定的目录
2) PATH 环境变量里的 riscv-wch-elf-gcc / riscv-none-embed-gcc / ...
3) 常见安装位置，第一个就是：
   D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12
```

实测输出：

```
==> 工具链
    GCC     : D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\
              components\WCH\Toolchain\RISC-V Embedded GCC12\bin\riscv-wch-elf-gcc.exe
```

> 如果自动探测失败（比如装在别的盘），用 `-Toolchain` 手工指定：
> ```powershell
> powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target all `
>     -Toolchain "D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12"
> ```

### 1.5 找不到工具链时会怎样

脚本按这个顺序找，找不到就**直接报错退出**（不会退回通用工具链）：

```
1) 命令行 -Toolchain 指定的目录
2) PATH 环境变量里的 riscv-wch-elf-gcc / riscv-none-embed-gcc
3) MounRiver Studio 的默认安装位置
```

报错信息会列出不支持的工具链名称，以及两种解决办法。

---

## 第 2 章 三个关键概念（不懂这三个后面看不懂）

### 2.1 概念一：目标文件 与 静态库

**目标文件（.o）**：一个 `.c` 源文件编译出来的半成品。
里面的代码还不能直接跑，因为**地址还没定**。比如它只知道「我要调用一个叫 `tmos_start_task` 的函数」，
但不知道这个函数最终在内存的哪个地址。

**静态库（.a）**：一堆 `.o` 打包成一个文件。
本质就是"装着很多 `.o` 的压缩包"（可以用 `ar x` 解包出来）。

我们手上的 `libCH572BLE_PERI.a` 就是这样一个包：

```
$ ar t libCH572BLE_PERI.a
aes.o
api_c.o
att.o
gap.o
gattservapp.o
ll_advertise.o
...
（一共 54 个 .o）
```

### 2.2 概念二：链接 —— 把地址定下来

**链接**就是"把所有 `.o` 拼在一起，给每个函数和数据分配一个确定地址"的过程。

平时做应用开发时：

```
你的代码.o  +  libCH572BLE_PERI.a 里的.o  →  链接  →  应用.hex（含协议栈）
```

问题来了：这样每次编应用都要把 89 KB 的协议栈重新拼进去，
应用固件就变成了 229 KB。

**我们的目标**是反过来做：

```
libCH572BLE_PERI.a 里的.o  +  ROM 自己的一点点代码  →  链接到固定地址  →  CH572BLE_ROM.hex
                                                                         （协议栈的镜像，单独烧）
应用.o  +  （不链接协议栈！）                       →  链接  →  应用.hex（只有 19 KB）
```

应用通过**绝对地址**去调用协议栈里的函数。地址从哪来？见下一个概念。

### 2.3 概念三：函数跳转表（这是整个方案的核心）

应用怎么知道 `tmos_start_task` 在哪个地址？

**不能写死**，因为协议栈的位置可能变。WCH 的解决办法是在 ROM 镜像的**固定位置**放一张"目录表"：

```
ROM_BASE + 0x00   镜像标识（魔数 "CH57"）
ROM_BASE + 0x08   UUID 表（GATT 标准 UUID，22 项 × 2 字节）
ROM_BASE + 0x34   ★ 函数跳转表 ★
                  第 0 项 → 版本字符串的地址
                  第 1 项 → tmos_rand 函数的地址
                  第 2 项 → tmos_memcmp 函数的地址
                  ...
                  第 9 项 → tmos_start_task 函数的地址
                  ...
之后              真正的代码与只读数据
```

应用侧头文件里就这么定义：

```c
#define FUNCITON_BASE_ADDRESSS   (LIB_FLASH_BASE_ADDRESSS + 0x34)

/* 「读内存地址 (FUNCITON_BASE_ADDRESSS + n*4) 处的 32 位值」= 第 n 个函数的地址 */
#define BLE_LIB_JT(n)            (*(uint32_t*)( FUNCITON_BASE_ADDRESSS + (n) * 4 ))

/* 于是 tmos_start_task 就变成了：把第 9 项当地址，转成函数指针，然后调用 */
#define tmos_start_task  (( BOOL (*)(tmosTaskID, tmosEvents, tmosTimer) ) BLE_LIB_JT(9))
```

**这样应用编译时完全不需要协议栈的代码**，只要知道"第 9 项是 `tmos_start_task`"就够了。

> **为什么编号要沿用 CH592 的？**
> 因为 CH592 官方头文件 `CH59xBLE_ROM.h` 已经把这套编号定死了
> （`tmos_start_task` = 9、`GAPRole_PeripheralInit` = 46 ...）。
> 我们沿用同一套编号，好处是从 CH592 ROM 工程迁移过来的代码不用改一行。

---

## 第 3 章 hex 是怎么生成的：完整流程

这是你最想知道的。下面按**实际执行顺序**一步步讲，每一步都说清"输入什么、做了什么、输出什么"。

先给个总览图：

```
┌─────────────────────────────────────────────────────────────────┐
│ 输入                                                            │
│   libCH572BLE_PERI.a          WCH 官方协议栈（54 个 .o）        │
│   rom/rom_entry.c             本工程新增：镜像头部              │
│   rom/rom_support.c           本工程新增：中断转接 + 钩子表      │
│   rom/rom_stub.c              本工程新增：自动生成的兼容桩      │
│   src/StdPeriphDriver/*.c     官方外设驱动（编进镜像，做到自包含）│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────┐
        │ 趟 1：占位链接（跳转表先放 156 个 0）      │
        │ 目的：拿到「全部符号的绝对地址」           │
        │ 产出：rom_pass1.elf / rom_pass1.map        │
        └───────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────┐
        │ 趟 2：生成跳转表 + 应用头文件              │
        │ 工具：tools/rom_gen.py                     │
        │ 产出：build/rom/rom_jt.S（跳转表汇编）      │
        │       include/CH572BLE_ROM.h（应用头文件）  │
        └───────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────┐
        │ 趟 3 / 4：开 --gc-sections 迭代链接两次    │
        │ 目的：裁掉主机角色死代码，让跳转表地址收敛 │
        └───────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────┐
        │ 趟 5：最终链接（不再重算跳转表）           │
        │ 产出：CH572BLE_ROM.elf                     │
        └───────────────────────────────────────────┘
                              │
                              ▼
        ┌───────────────────────────────────────────┐
        │ objcopy -O ihex  →  CH572BLE_ROM.hex       │
        │ objcopy -O binary →  CH572BLE_ROM.bin      │
        └───────────────────────────────────────────┘
```

下面展开讲每一步。

---

### 步骤 0：准备阶段 —— 把材料凑齐

#### 0-a. 解包官方协议栈

```powershell
ar x libCH572BLE_PERI.a      # 解出 54 个 .o
```

> **实现要点**：MounRiver Studio 2 的工具链路径里有空格
> （`...\RISC-V Embedded GCC12\bin\`），用 `cmd` 调用 `ar` 时引号会被反复消费，
> 导致 `ar` 一个文件都解不出来（而退出码仍是 0，不容易发现）。
>
> 所以这里先把库复制成不带空格的名字 `stack.a`，切到目标目录，用相对路径解包：
> ```powershell
> Copy-Item libCH572BLE_PERI.a  build\rom\stage\stack.a
> Push-Location build\rom\stage
> ar x stack.a
> ```

#### 0-b. 编译「镜像头部」

文件：`rom/rom_entry.c`

这个文件负责在 ROM 镜像的**最开头**放上标识和 UUID 表：

```c
/* ROM_BASE + 0x00，8 字节：魔数 + 保留 */
const uint8_t _rom_magic[8] __attribute__((section(".rom_magic"), used, aligned(8))) = {
    0x37, 0x35, 0x48, 0x43,     /* '7','5','H','C'，小端读作 0x43483537 = "CH57" */
    0x00, 0x00, 0x00, 0x00,
};

/* ROM_BASE + 0x08，44 字节：GATT 标准 UUID，下标与 BLE_LIB_UUID(n) 对应 */
const uint8_t _rom_uuid_table[22][2] __attribute__((section(".rom_uuid"), used)) = {
    {0x00, 0x18},   /*  0  Generic Access     0x1800 */
    {0x01, 0x18},   /*  1  Generic Attribute  0x1801 */
    {0x00, 0x28},   /*  2  Primary Service    0x2800 */
    ...（共 22 项）
};
```

> **为什么必须用 `__attribute__((section(...)))` 指定单独的段？**
>
> GNU 链接器在拼接同一个输出段时，会按**输入段名的字母序**排。
> 如果 `_rom_magic` 和 `_rom_uuid_table` 都放进 `.rom_header`，
> 字母序会让 `_rom_uuid` 排在 `_rom_magic` 前面 —— 头部布局就全错了。
> 拆成两个段，就能在链接脚本里**按我指定的顺序**拼。
>
> **实现要点**：`_rom_magic` 必须写成**一个 8 字节数组**，不能写成两个 `uint32_t` 变量 ——
> 同一段内变量的顺序由编译器后端决定，`-O` 下不保证等于源码声明顺序，
> 保留字可能跑到魔数前面。合成一个对象后地址才完全确定。

#### 0-c. 编译「中断转接层」

文件：`rom/rom_support.c`

BLE 工作时有硬件中断（基带中断 BB、链路层中断 LLE），
这两个中断的处理函数**在协议栈里**。应用必须能接管它们。

```c
/* 只有这两个中断需要 ROM 转接 */
void BB_IRQHandler(void)  { g_romAppHooks.ble_irq_handler(); }   /* 基带中断 */
void LLE_IRQHandler(void) { g_romAppHooks.lle_irq_handler(); }   /* 链路层中断 */

/* 应用调用这个来注册自己的钩子，同时拿到 ROM 版本号 */
const char *CH572BLE_ROM_Init(rom_app_hooks_t *hooks)
{
    if (hooks && hooks->magic == ROM_HOOK_MAGIC) { g_romAppHooks = *hooks; }
    return _rom_version_str;
}

/* 应用调用这个拿到 BB / LLE 中断的入口地址 */
const rom_handles_t *CH572BLE_ROM_GetHandles(void) { return &g_romHandles; }
```

> **为什么需要这一层？**
> ROM 里的协议栈不能直接调用应用代码（两边独立编译、独立链接，互相不知道对方存在）。
> 所以用一张"函数指针表"做间接调用 —— 这就是钩子表。

#### 0-d. 生成「兼容桩」—— 这一步很有意思

```powershell
python tools\rom_stub_gen.py --lib lib\libCH572BLE_PERI.a --out rom\rom_stub.c
```

**背景**：`libCH572BLE_PERI.a` 是从全功能协议栈裁剪出来的。
裁剪时删掉了主机角色的**实现**，但 `gatt.o`、`att.o` 这些文件里
**还残留着对主机侧函数的调用**（那些调用所在的分支在从机模式下永远不会执行）。

这会导致链接时报一堆 `undefined reference`。所以脚本自动扫出这些符号，生成"弱桩"：

```c
/* 空实现，返回 0 */
__attribute__((weak)) uint32_t ATT_ReadReq(void) { return 0; }
__attribute__((weak)) uint32_t LL_SetPrivacyMode(void) { return 0; }
...
```

**它做了什么检查**（这里踩过一个致命坑）：

| 检查项 | 说明 |
|---|---|
| 收集**所有**已定义符号 | ⚠️ 必须包含 LOCAL 绑定的 `.bss`/`.sbss` 数据。`gapRole_state`、`gBleHostPara` 这些在 `.o` 里是 local 的，只收集 GLOBAL 会把它们误判为"缺失"，生成的桩会**覆盖真实数据**，直接导致 BLE 不工作 |
| 跳过 libc | `memcpy` / `memset` / `strlen` / `__udivdi3` 由 `-lc` / `-lgcc` 提供，不能生成同名桩 |
| 区分函数与变量 | 按命名习惯判断（`l2capFindPsm` 是函数，`l2capChannels` 是变量） |

实测结果：只需要 **8 个函数桩 + 2 个变量桩**。

#### 0-e. 编译外设驱动

`CH57x_clk.c`、`CH57x_gpio.c`、`CH57x_sys.c`、`CH57x_flash.c`、`CH57x_pwr.c`、`CH57x_uart.c`
一起编进镜像 —— 这样 ROM 才是**自包含**的，不会因为缺一个驱动函数就跳到无效地址。

---

### 步骤 1：趟 1 —— 占位链接，采集符号地址

**目的**：先不管跳转表内容，把整包东西链一遍，从 `.map` 文件里**读出每个函数的地址**。

```powershell
riscv-wch-elf-gcc -march=rv32imac_zicsr_zifencei -mabi=ilp32 -mcmodel=medany \
    -nostartfiles -nostdlib \
    -T rom/rom_link.ld \
    -Wl,--defsym=ROM_BASE=0x00020000 \
    -Wl,-Map=build/rom/rom_pass1.map \
    -o build/rom/rom_pass1.elf \
    <所有 .o> libISP572.a -lgcc -lc -lm
```

`build/rom/rom_pass1.map` 里就会有这样的内容：

```
0x00004123   BLE_LibInit
0x00008567   GAPRole_PeripheralInit
0x00009123   tmos_start_task
...
```

#### ⚠️ 关键：趟 1 **必须关掉** `--gc-sections`

这是最容易搞错的地方，我第一次就踩了。

`--gc-sections` 是"垃圾回收"：从"根"出发找出哪些代码会被用到，没用到就删掉。
趟 1 的跳转表是 156 个 `0`，**不引用任何函数**，
所以链接器会认为"整个协议栈都没人用"，把它全删了 —— `.map` 里就只剩头部几个符号。

**实测**：开 `--gc-sections` 时，可用 API 只剩 **14 个**（正确应该是 94 个）。

---

### 步骤 2：趟 2 —— 生成跳转表和应用头文件

用上一步的 `.map` 文件，跑：

```powershell
python tools\rom_gen.py `
    --map build/rom/rom_pass1.map `          # 上一步的符号地址表
    --jt build/rom/jt_map.json `             # CH592 的跳转表编号基准
    --base 0x00020000 `                      # ROM 基址
    --out-asm build/rom/rom_jt.S `           # 输出：跳转表汇编
    --out-hdr include/CH572BLE_ROM.h         # 输出：应用侧头文件
```

#### 2-a. 生成跳转表汇编

为什么用汇编而不用 C？因为跳转表的每一项都是**函数的绝对地址**，
必须由**链接器**填进去（写死在 C 里做不到）。

生成的 `rom_jt.S` 长这样：

```asm
    .section .rom_jt,"a",@progbits
    .align 2
    .globl _rom_jump_table
_rom_jump_table:
    .word _rom_version_str    /* JT(  0) VER_LIB */
    .word tmos_rand           /* JT(  1) tmos_rand */
    .word tmos_memcmp         /* JT(  2) tmos_memcmp */
    .word tmos_isbufset       /* JT(  3) tmos_isbufset */
    ...
    .word GAPRole_CentralInit /* JT( 47) GAPRole_CentralInit —— 本平台无此 API */
    .word 0
    ...
```

**注意**：CH572 没有的 API（43 个，比如主机角色的）会被填成 `0`。
这样应用误用它们时不会跳到随机地址，而且头文件里根本不定义这些宏 ——
**编译期就会报错**，比运行时跑飞好得多。

#### 2-b. 生成应用侧头文件

`include/CH572BLE_ROM.h`，核心就这几行：

```c
#define LIB_FLASH_BASE_ADDRESSS    0x00020000
#define UUID_BASE_ADDRESSS         (LIB_FLASH_BASE_ADDRESSS + 0x08)
#define FUNCITON_BASE_ADDRESSS     (LIB_FLASH_BASE_ADDRESSS + 0x34)

#define BLE_LIB_UUID(n)   ( UUID_BASE_ADDRESSS + (n) * 2 )
#define BLE_LIB_JT(n)     (*(uint32_t*)( FUNCITON_BASE_ADDRESSS + (n) * 4 ))
#define VER_LIB           ((const uint8_t*) BLE_LIB_JT(0))

/* GATT UUID 直接指向 ROM 里的 UUID 表 */
#define primaryServiceUUID   ((const uint8_t*) BLE_LIB_UUID(2))
#define characterUUID        ((const uint8_t*) BLE_LIB_UUID(5))
...

/* 94 个可用 API，每个都变成"从跳转表第 n 项取地址再调用" */
/* JT(  9) */
#define tmos_start_task   (( BOOL (*)(tmosTaskID, tmosEvents, tmosTimer) ) BLE_LIB_JT(9))
/* JT( 46) */
#define GAPRole_PeripheralInit (( bStatus_t (*)(void) ) BLE_LIB_JT(46))
...
```

#### 2-c. 顺带生成一个「纯类型头文件」

文件：`include/CH572BLE_ROM_types.h`

因为 ROM 方式下应用**不能**再包含 `CH572BLEPeri_LIB.h` ——
那会把 API 声明成普通函数符号，跟 `BLE_LIB_JT` 宏打架。
但协议栈的类型定义（`bleConfig_t`、`gapRoleEvent_t`...）应用还得用，
所以从官方头文件里把"纯类型"抽出来单独成文：

| 内容 | 处理 |
|---|---|
| `typedef ...;`（含函数指针类型） | 保留 |
| `struct` / `enum` / `union` 定义 | 保留 |
| `#define`（含续行的多行宏） | 保留 |
| `extern` 变量与函数声明 | 丢弃 |
| 普通函数原型 | 丢弃 |

实测抽出 **107 个 typedef + 691 个 #define**。

> 这里也踩了两个坑：
> 1. 源文件是 Windows 换行，只按 `'\n'` 切分会残留 `'\r'`，
>    拼回去时会在 `#define ... \` 续行宏中间插入空行，把宏拆断；
> 2. 官方头文件里有些函数宏的原型是跨行的，抽取脚本必须**先把续行折叠成单行**再匹配。

---

### 步骤 3：趟 3 / 4 —— 开垃圾回收，迭代收敛

```powershell
# 用真实的跳转表重新链接，这次开 --gc-sections
riscv-wch-elf-gcc ... -Wl,--gc-sections \
    -T rom/rom_link.ld -Wl,--defsym=ROM_BASE=0x00020000 \
    -o build/rom/rom_pass2.elf <所有 .o> rom_jt.o libISP572.a -lgcc -lc -lm

# 再用新的 map 重新生成跳转表，消除"表自身占位"带来的地址偏移
python tools/rom_gen.py --map build/rom/rom_pass2.map ...
```

#### 为什么要迭代两次

跳转表本身占了 `156 × 4 = 624` 字节。
第一次生成时，符号地址是按"跳转表占 152 项"算的；
真实表变成 156 项后，后面所有符号的地址都会往后挪 16 字节。
用新 map 重新生成一次，地址就收敛了。做两轮保险。

#### 开了 `--gc-sections` 就不需要那些桩了吗

不是不需要，而是**大部分桩会被自动裁掉**：

跳转表现在真实引用了所有要暴露的 API，它们成了"GC 根"。
协议栈里"只有主机角色才会走到"的代码从根出发不可达 → 被裁掉 →
它们引用的未定义符号也就不再是问题。

**实测**：趟 1（无 gc）全量链接 text 约 103 KB；最终（有 gc）约 89 KB，
**裁掉约 14 KB 死代码**。

---

### 步骤 4：最终链接

```powershell
# 不再重算跳转表，保证「表内容」与「镜像布局」严格一致
riscv-wch-elf-gcc ... -o build/rom/CH572BLE_ROM.elf
```

得到 `CH572BLE_ROM.elf`。

#### 链接脚本 `rom/rom_link.ld` 干的事

```ld
ENTRY( _rom_entry )
ROM_BASE = DEFINED(ROM_BASE) ? ROM_BASE : 0x00020000;

MEMORY
{
    ROM (rx)  : ORIGIN = ROM_BASE, LENGTH = 208K
    RAM (xrw) : ORIGIN = 0x20000000, LENGTH = 12K
}

SECTIONS
{
    /* +0x00：8 字节镜像标识 */
    .rom_header ROM_BASE :
    {
        KEEP(*(.rom_magic))
        . = ALIGN(4);
        KEEP(*(.rom_uuid))          /* +0x08：44 字节 UUID 表 */
        . = ALIGN(4);
    } >ROM

    /* 头部布局强制校验：必须是 0x34 字节，写错直接链接失败 */
    ASSERT(. == ROM_BASE + 0x34,
           "ROM 头部布局错误：.rom_header 必须正好 0x34 字节")

    /* +0x34：函数跳转表 */
    .rom_jt (ROM_BASE + 0x34) :
    {
        KEEP(*(.rom_jt))
        . = ALIGN(4);
    } >ROM

    /* 版本字符串 */
    .rom_version : { KEEP(*(.rom_version)) . = ALIGN(4); } >ROM

    /* 代码与只读数据 */
    .text : { ... } >ROM

    /* 可写数据：运行期在 RAM，初值存在 ROM */
    .data : { ... } >RAM AT>ROM
    .bss (NOLOAD) : { ... } >RAM
}
```

> **`ASSERT` 是干嘛的？**
> 它让链接器检查"`.rom_header` 是不是正好 0x34 字节"。
> 如果有人改坏了魔数或 UUID 表的长度，链接会直接失败并打印中文提示 ——
> 而不是等你烧进芯片才发现头部错位。

> **`.highcode` 段为什么不搬到 RAM？**
> 协议栈里很多函数标了 `.highcode`（原设计是上电搬到 RAM 里跑，为了快）。
> ROM 方案下我们**不搬** —— 直接在 Flash 上就地执行（XIP）。
> 好处是省下宝贵的 12 KB RAM，而且镜像才是"烧一次就不动"。

---

### 步骤 5：转成 hex / bin

```powershell
riscv-wch-elf-objcopy -O ihex   build/rom/CH572BLE_ROM.elf  lib/CH572BLE_ROM.hex
riscv-wch-elf-objcopy -O binary build/rom/CH572BLE_ROM.elf  lib/CH572BLE_ROM.bin
```

**`objcopy -O ihex` 做了什么？**

`.elf` 里面有地址信息、调试信息、符号表等等，芯片烧录器不认识。
`objcopy` 把"真正要烧进 Flash 的字节"按地址抽出来，编码成 **Intel HEX 文本格式**：

```
:020000040000FA          ← 扩展线性地址记录：基地址 = 0x0000
:1000000037354843...     ← 从 0x0000 开始写 16 字节
:10001000...             ← 从 0x0010 开始写 16 字节
...
:00000001FF              ← 文件结束
```

每行格式：`:` + 字节数 + 地址 + 记录类型 + 数据 + 校验和。

**`objcopy -O binary`** 则直接输出纯二进制（没有地址信息，按最小地址连续排列）。
两种都给，是因为不同烧录工具/产线流程偏好不同。

实测产物：

```
lib\CH572BLE_ROM.hex   251,288 字节  （HEX 文本，含地址与校验）
lib\CH572BLE_ROM.bin    89,316 字节  （纯二进制，= 实际 Flash 占用）
地址范围：0x00020000 - 0x00035CE3
```

> **hex 比 bin 大很多是正常的**：HEX 每 16 字节数据要配 11 个字符的行头 + 校验和，
> 所以大概是二进制的 2.8 倍。

---

## 第 4 章 亲手跑一遍

### 4.1 三条命令

在 `CH572_BLE_LIB` 目录下：

```powershell
# ① 构建 ROM 镜像（你要的 hex 就是这一步产出的）
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1
```

或者一次把两种方式都编出来：

```powershell
# ② 全量构建：静态库 + ROM 镜像 + 两种方式的应用固件
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target all
```

跑完自检：

```powershell
# ③ 26 项自检
powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
```

双击用户也可以直接用 `build.bat`。

### 4.2 期望看到的输出

```
==> 工具链与参数
    GCC      : D:\MounRiver\MounRiver_Studio2\...\RISC-V Embedded GCC12\bin\riscv-wch-elf-gcc.exe
    ROM 基址 : 0x00020000

==> 准备 ROM 对象
    [OK] 协议栈对象 54 个
    编译 rom/rom_stub.c
    编译 rom/rom_support.c
    编译 rom/rom_entry.c
    编译驱动 CH57x_clk.c
    ...
    [OK] 支撑层对象 8 个
    生成兼容桩 rom/rom_stub.c
    协议栈已定义符号 : 1648
    生成数据桩       : 2
    生成函数桩       : 8

==> 第 1 趟：占位链接（采集符号地址）
    [OK] 占位链接成功

==> 生成跳转表与应用侧头文件
    跳转表项数       : 156
    可用 API         : 94
    本平台缺失的 API : 43
    [OK] 生成应用侧头文件 include\CH572BLE_ROM.h

==> 第 2 趟：迭代链接
    [OK] 第 2 趟完成
==> 第 3 趟：迭代链接
    [OK] 第 3 趟完成

==> 输出最终镜像
    [OK] 生成 lib\CH572BLE_ROM.hex
    [OK] 生成 lib\CH572BLE_ROM.bin

    ---------- ROM 镜像体积 ----------
       text    data     bss     dec     hex   filename
      89256      60    1136   90452   16154   build\rom\CH572BLE_ROM.elf
    hex 文件:    251,288 字节
    bin 文件:     89,316 字节
    ROM 占用: 0x00020000 - 0x00035CE3  (89,316 字节, 87.2 KB)

    ---------- ROM 镜像头部自检 ----------
    镜像地址范围 : 0x00020000 - 0x00035CE3   数据 89316 字节 (87.2 KB)
    +0x00 魔数 : 0x43483537  [OK] "CH57"
    UUID 表：识别为已知 GATT UUID 的项数: 22 / 22
    跳转表：项数 156，指向 ROM 内部 98 项，空项 58 项，指向镜像外部 0 项 [OK]
    结论: [OK] 镜像结构符合 WCH ROM 约定
```

看到最后那句 `[OK] 镜像结构符合 WCH ROM 约定` 就成了。

---

## 第 5 章 生成出来的 hex 长什么样、怎么验证

### 5.1 用文本编辑器打开看看

`lib\CH572BLE_ROM.hex` 用记事本就能打开（会很大，建议只看看开头结尾）：

```
:020000040000FA
:10000000373548430000000000180118002801284B
:10001000022803280029012902290329042905294C
...
:00000001FF
```

第 2 行 `37 35 48 43` 就是魔数 `"CH57"`（小端存储）。
第 3 行开始 `00 18 01 18 00 28 01 28 ...` 就是 UUID 表。

### 5.2 三道自动校验

#### ① 看镜像结构

```powershell
python tools\rom_inspect.py lib\CH572BLE_ROM.hex 0x20000 156
```

检查：魔数对不对、UUID 表能不能识别、跳转表有没有指向镜像外面的无效地址。

#### ② 逐项核对跳转表（**最关键**）

```powershell
python tools\rom_verify.py --bin lib\CH572BLE_ROM.bin `
    --syms lib\CH572BLE_ROM_SYMS.txt `
    --base 0x20000 --jt-json build\rom\jt_map.json --hdr include\CH572BLE_ROM.h
```

**为什么这项最重要**：跳转表里的地址和镜像里符号的真实地址
是**两个不同步骤产生的**，差一轮迭代就全线错位，而且**只有烧进芯片才会暴露**。
所以这里逐项比对：

```
地址正确          : 94 项
本平台无此 API(0) : 43 项
地址不匹配        : 0 项
[通过] 跳转表与镜像完全一致
结论: [通过] ROM 镜像、跳转表、头文件三者一致，可以烧录
```

#### ③ 指令集兼容性

```powershell
python tools\isa_check.py build\out\ble_uart-rom.elf
```

CH572 内核是 **RV32IMBC**：没有 A 扩展（原子指令）、没有浮点单元。
用 `rv32imac` 编译**理论上**可能生成 `amo*` / `lr.w` / `sc.w`，
这些指令在 CH572 上会触发非法指令异常**直接跑飞**。所以必须查一遍。

脚本只对 ELF 里**真实函数区间**解码 —— 直接扫整个 `.text` 会把字符串和
跳转表当成指令，产生大量假阳性（会误报出十几条"浮点指令"，实际全是数据）。

### 5.3 一条命令跑完所有检查

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
```

---

## 第 6 章 烧到芯片上是怎么运行的

### 6.1 烧录地址

```
① lib\CH572BLE_ROM.hex         →  烧到 0x00020000 起
② build\out\ble_uart-rom.hex   →  烧到 0x00000000 起
```

Flash 分区：

```
0x00000000 - 0x0001EFFF  124 KB   app 主体（连续一整块）← 以后升级只更新这里
0x0001F000 - 0x0001FFFF    4 KB   SNV 绑定信息存储区（运行期写，不烧录）
0x00020000 - 0x00035CE3  ~88 KB   BLE 协议栈 ROM 镜像 ← 先烧一次，不再改动
0x00035CE4 - 0x0003BFFF  ~25 KB   余量（ROM 将来长大了可往后扩）
0x0003C000 - 0x0003FFFF   16 KB   芯片固定占用（BootLoader + INFO）
0x0001A000 - 0x0003AFFF  132 KB   应用主体               ← 以后升级只更新这里
0x0001F000 - 0x0001FFFF    4 KB   SNV 绑定信息存储区
```

### 6.2 上电后发生什么

```
上电复位
  │
  ├─ 启动文件 startup_CH572.S
  │    清 bss → 把 .highcode 从 Flash 搬到 RAM → 设栈指针 → 跳 main()
  │
  ├─ main() 里配置时钟
  │    HSECFG_Capacitance(HSECap_18p);
  │    SetSysClock(CLK_SOURCE_HSE_PLL_100MHz);
  │
  ├─ ★ InstallRomIrqHandlers()   ← ROM 方式必须做这一步
  │    把 ROM 里的 BB / LLE 中断入口注册给钩子表
  │
  ├─ CH57x_BLEInit()             ← 填 bleConfig_t、注册 6 个回调、启动协议栈
  │    注意：这一步调用的 BLE_LibInit 在 ROM 里（通过跳转表第 n 项）
  │
  ├─ HAL_Init() → GAPRole_PeripheralInit() → 你的 GATT 服务初始化
  │
  └─ while(1) { TMOS_SystemProcess(); }   ← ROM 里的调度器
```

### 6.3 应用侧必须做的两件事

#### ① 装 ROM 中断钩子（漏了 BLE 完全不工作）

```c
static void InstallRomIrqHandlers(void)
{
    static rom_app_hooks_t hooks;

    tmos_memset(&hooks, 0, sizeof(hooks));
    hooks.magic = ROM_HOOK_MAGIC;

    const rom_handles_t *h = CH572BLE_ROM_GetHandles();  /* 取 ROM 中断入口 */
    if (h && h->magic == ROM_HANDLE_MAGIC)
    {
        hooks.ble_irq_handler = h->bb_irq;    /* 基带中断 → ROM 实现 */
        hooks.lle_irq_handler = h->lle_irq;   /* 链路层中断 → ROM 实现 */
    }
    hooks.rtc_irq_handler = RTC_IRQHandler;   /* RTC 由应用处理 */

    PRINT("ROM 版本: %s\n", CH572BLE_ROM_Init(&hooks));   /* 注册并取版本号 */
}
```

> **为什么必须做**：BLE 基带/链路层中断的处理函数在 ROM 里。
> 应用启动文件里这两个中断是"弱定义 + 死循环"。
> 不接管的话，射频中断一触发就停在死循环 —— 表现为"能广播但一连接就死"或"完全连不上"。

#### ② 链接时**不要**加 `-lCH572BLE`

```make
# 正确
LIBS    = -Llib -lISP572 -lm
LDFLAGS = -nostartfiles -Tsrc/Ld/Link_rom.ld -Wl,--gc-sections \
          --specs=nano.specs --specs=nosys.specs
```

加了 `-lCH572BLE` 等于把静态库版协议栈又链一份进来，体积白涨还可能符号冲突。

---

## 第 7 章 常见疑问

### Q0：换了协议栈库（lib）之后怎么办？

一条命令重新生成 hex 与头文件：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 `
    -Lib "新库路径\libCH572BLE_PERI.a"
```

为什么不能只换 hex：新库可能新增/删除 API（跳转表编号会变）、
或者所有地址整体位移 —— 所以 hex 和头文件必须**成对重建**，
而且**头文件变了必须重编应用**，否则应用会按旧编号调用。

详见 [`lib更新后如何重新生成.md`](lib更新后如何重新生成.md)。

### Q0.5：两种方式在体积和内存上差多少？

有一条完整对比（含 Flash/RAM 布局图与 OTA 规划）：
[`内存布局与OTA规划.html`](内存布局与OTA规划.html)。

简版：

| | 静态协议栈 | ROM 镜像 |
|---|---|---|
| 应用 Flash 占用 | 80,864 B | **6,616 B** |
| 可用栈空间 | 1,384 B | **4,560 B** |
| 芯片里总代码量 | 80.9 KB | 93.8 KB（含 ROM 87.2 KB） |
| OTA 升级包 | ≈ 81 KB | **≈ 7 KB** |

本工程的 `tools/mem_layout.py` 可以直接算出任意 ELF 的布局：

```powershell
python tools\mem_layout.py build\out\ble_uart-rom.elf
```

### Q1：我能不能不用 ROM 方式，就用静态库？

当然可以，而且**开发阶段推荐用静态库方式**（一个 hex 烧进去就能跑，最省事）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target lib
```

产出的 `lib\libCH572BLE.a` 用法和普通库一样，链接时加 `-lCH572BLE -lISP572` 即可。

两种方式对比：

| | 静态库 | ROM 镜像 |
|---|---|---|
| 应用 text | 80,276 B | **6,140 B** |
| 应用 hex | 225 KB | **19 KB** |
| 可用栈 | 1,384 B | 1,712 B |
| 烧录 | 一个 hex | ROM 88 KB + 应用 19 KB |
| 适合 | 开发调试 / 小批量 / 不做 OTA | **量产 / 需要 OTA** |

### Q2：ROM 方式比静态库还多烧 88 KB，划算吗？

看场景：

- **只有一台设备、不升级** → 静态库更省事。
- **要 OTA 升级** → ROM 明显划算：
  - 升级包从 229 KB 降到 19 KB（省 92% 流量与时间）
  - 升级**不动协议栈**，变砖风险低
  - 应用区只有几十 KB，回滚 / 双备份都更容易安排
- **量产** → ROM 烧一次，之后只刷小固件，产线更快。

### Q3：为什么 hex 比 bin 大那么多？

HEX 是**文本格式**，每 16 字节数据要配 11 个字符的行头 + 2 字节校验和，
所以文件大小 ≈ 数据量的 2.8 倍。烧录时按"数据字节"算，不会真的占那么多 Flash。

### Q4：能改 ROM 的起始地址吗？

可以，但要**同步改两处**：

1. 构建时：`scripts\make_rom.ps1 -RomBase 0x00008000`
2. `src\Ld\Link_rom.ld` 里的 `FLASH_ROM_END`（改成 ROM 实际结束位置）

ROM 实际结束位置在构建日志里能看到：

```
ROM 占用: 0x00020000 - 0x00035CE3  (89,316 字节, 87.2 KB)
```

把 `FLASH_ROM_END` 设成「ROM 起始地址 − SNV 大小」（当前 ROM 在 0x20000、SNV 4 KB，
所以是 `0x1F000`）。也可以直接跑 `make_rom.ps1 -Probe` 让它自动算出推荐值。
对不上会导致应用和 ROM 抢同一段 Flash。

### Q5：CH572 能不能做主机（Central）？

**不能。** 逐符号比对的结果：

`libCH59xBLE.a` 里有、`libCH572BLE_PERI.a` 里没有的 **649 个符号**，
全部集中在主机侧（HCI 命令层、链路层扫描/连接状态机、GATT 客户端、BLE 5.x 高级特性）。

这不是"库没编译进去"，而是**芯片固件里就没有这部分代码**。
再加上 CH572 只有 12 KB RAM / 没有 BLE ROM 区，
硬做也铺不开。要主机功能请换 CH592。

本工程用编译期能力宏把这条边界固化了 —— 误用主机 API 直接编译报错：

```
error: call to '_ch572_ble_missing_api' declared with attribute error:
       CH572 BLE 固件库不提供该 API，请检查平台能力宏 CH572_BLE_HAS_xxx
```

### Q6：ROM 镜像能在实机跑吗？验证过了吗？

**PC 侧验证完备**：

- 全量编译 + 链接成功
- 跳转表 94 项地址逐项核对，0 项不匹配
- 镜像头部结构符合 WCH 约定（魔数、UUID 表 22/22 识别）
- 指令集兼容性检查通过（无原子指令、无浮点指令）
- 内存布局检查（可用栈 1,712 字节）
- 26 项自检全部通过

**但实机烧录没做**（当前环境没有 CH572 EVT 板）。
首次上板建议先跑 `examples/ble_uart`：用手机 App 搜到 `CH572-BLE-UART`，
走一遍「读 0xFFE1 → 写 0xFFE3 → 收 0xFFE4 通知」的回环，
确认无误后再移植到产品工程。

### Q7：为什么构建脚本要用 PowerShell 还要加 BOM？

Windows PowerShell 5.1 读 `.ps1` 时，**如果没有 BOM，会按系统 ANSI（中文系统是 GBK）解析**。
本工程脚本里有大量中文注释和中文提示，按 GBK 解析会直接语法报错。
但如果**多**了一个 BOM（重复的 U+FEFF），同样会报莫名其妙的语法错误。

所以有个工具专门管这件事：

```powershell
python tools\fix_ps1_encoding.py
```

它会确保每个 `.ps1` 有且仅有一个 UTF-8 BOM。
如果你用编辑器改脚本后报语法错误，先跑一下这个。

---

## 附：本教程涉及的脚本速查

| 脚本 | 干什么 |
|---|---|
| `scripts/make_rom.ps1` | ★ 一键生成 ROM 库镜像（hex / bin）+ 应用侧头文件，本教程的主角 |
| `scripts/build.ps1` | 总构建入口：`-Target lib` / `all` / `rom` / `clean` |
| `scripts/run_tests.ps1` | 26 项自检 |
| `tools/rom_gen.py` | 生成跳转表汇编 + 应用侧头文件 |
| `tools/rom_jt_extract.py` | 从官方 `CH59xBLE_ROM.h` 抽取跳转表编号基准 |
| `tools/rom_hdr_extract.py` | 抽取协议栈"纯类型"头文件 |
| `tools/rom_stub_gen.py` | 为裁剪残留符号生成兼容桩 |
| `tools/rom_inspect.py` | 查看镜像头部结构 |
| `tools/rom_verify.py` | 逐项核对跳转表地址 |
| `tools/isa_check.py` | 指令集兼容性检查 |
| `tools/fix_ps1_encoding.py` | 修正脚本 BOM（脚本报语法错误时用） |
