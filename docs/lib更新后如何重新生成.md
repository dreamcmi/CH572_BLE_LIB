> **历史记录（2026-10-09 已更新）**：下文含旧布局、旧烧录步骤和旧故障结论。当前修复、内存预算及测试步骤请看 [CH572 固化协议栈修复与测试](CH572固化协议栈修复与测试.md)。发布/调试镜像已通过软件检查，实机验证由用户进行。

# lib 更新后怎么办：重新生成 hex 与头文件

> 场景：WCH 发布了新的 CH572EVT，或者你自己替换/重新编译了协议栈库
> `lib\libCH572BLE_PERI.a`，需要重新生成 ROM 镜像（hex/bin）和应用侧头文件。
>
> **一句话答案**：跑 `scripts\make_rom.ps1`。
> 它是本工程**唯一的 ROM 产物生成入口**，只做"库编译 + 头文件生成"两件事。

---

## 一、先搞清楚：lib 更新分两种情况

**两种情况用的是同一个脚本**，区别只在于要不要传 `-Lib`。

| | 情况 A：协议栈库变了 | 情况 B：只改了 HAL / ROM 胶水层 |
|---|---|---|
| 什么变了 | `lib\libCH572BLE_PERI.a` | `hal\*.c`、`rom\*.c`、`rom_link.ld` |
| 典型来源 | WCH 发了新 EVT 版本 | 你自己改了 HAL 的初始化逻辑 |
| 跳转表编号会变吗 | **会**（新 API 可能加进来，编号扩容） | 不会 |
| 头文件要重建吗 | **要** | 要（地址会变） |
| 怎么跑 | `make_rom.ps1 -Lib <新库路径>` | `make_rom.ps1`（库没换，不传 `-Lib`） |

判断方法很简单 —— **看有没有动 `lib\libCH572BLE_PERI.a`**。

---

## 二、一键生成

```
scripts\make_rom.ps1
```

### 只做两件事

| 产出 | 说明 |
|---|---|
| `lib\CH572BLE_ROM.hex` | ROM 库镜像（Intel HEX，烧录用） |
| `lib\CH572BLE_ROM.bin` | 同上，纯二进制 |
| `include\CH572BLE_ROM.h` | 应用侧头文件（94 个 API 通过跳转表调用 ROM） |

顺带还会生成几个诊断文件（都在 `lib\` 下，供排查用）：
`CH572BLE_ROM.map`（符号绝对地址）、`CH572BLE_ROM_SYMS.txt`（符号地址清单）、
`CH572BLE_ROM_JT.md`（跳转表对照表）。

### 不做的事

不编译示例工程、不重编应用固件、不跑自检套件、不碰静态库 `libCH572BLE.a`。
这些各有各的入口（见文末[速查表](#八命令速查)）。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `-Lib` | 空（用工程自带的 `lib\libCH572BLE_PERI.a`） | 指定别的协议栈库。会先备份工程内原库到 `lib\backup\`，再替换 |
| `-RomBase` | `0x00020000` | ROM 基址。改了要同步改 `src\Ld\Link_rom.ld` 的 `FLASH_ROM_END` |
| `-Opt` | `-Os` | 优化级别 |
| `-Toolchain` | 空（自动探测） | 工具链目录 |
| `-Ch592RomHdr` | 空（自动探测） | `CH59xBLE_ROM.h` 的位置，作为跳转表 ABI 基准 |

---

## 三、情况 A：协议栈库变了

### 步骤

```powershell
cd CH572_BLE_LIB

# 一条命令：备份原库 -> 替换 -> 重新生成 hex 与头文件
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 `
    -Lib "D:\CH572EVT-1.5\EVT\EXAM\BLE\LIB\libCH572BLE_PERI.a"
```

### 期望输出

```
============================================================
 CH572 BLE ROM 库镜像 + 应用侧头文件  一键生成
============================================================

==> 输入
    协议栈库 : D:\CH572EVT-1.5\...\libCH572BLE_PERI.a
               大小 613,468 字节，修改于 2026-10-08 14:20:11
    ROM 基址 : 0x00020000
    使用外部协议栈库：D:\CH572EVT-1.5\...\libCH572BLE_PERI.a
    工程内原库已备份: lib\backup\libCH572BLE_PERI.20261008-015321.613468.a
    [OK] 外部库已就位

==> 生成 ROM 库镜像与头文件
    [OK] 协议栈对象 54 个
    [OK] 支撑层对象 8 个
    [OK] 占位链接成功
    [OK] 生成应用侧头文件 include\CH572BLE_ROM.h
    [OK] 第 2 趟完成
    [OK] 第 3 趟完成
    [OK] 生成 lib\CH572BLE_ROM.hex
    [OK] 生成 lib\CH572BLE_ROM.bin
结论: [OK] 镜像结构符合 WCH ROM 约定

 生成完成
  ROM 库镜像 (HEX)     251,288 字节   lib\CH572BLE_ROM.hex
  ROM 库镜像 (BIN)      89,316 字节   lib\CH572BLE_ROM.bin
  应用侧头文件           23,783 字节   include\CH572BLE_ROM.h
```

### 如果想自己控制换库过程

`-Lib` 会自动备份并替换工程内的库。如果你想手工来：

```powershell
# ① 自己备份 + 换库
Copy-Item lib\libCH572BLE_PERI.a "lib\backup\libCH572BLE_PERI.$(Get-Date -Format yyyyMMdd).a"
Copy-Item "新库路径\libCH572BLE_PERI.a" lib\libCH572BLE_PERI.a -Force

# ② 重新生成（不传 -Lib，脚本直接用工程内的库）
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1
```

### 别忘了重编应用

头文件变了，应用必须重编：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target example
```

### 为什么必须重新生成（而不是只拷 hex）

新库可能带来：

| 变化 | 影响 |
|---|---|
| 新增了 API | **跳转表要扩容**，编号可能重排 → 头文件必须重建 |
| 删除了 API | 原本可用的宏可能变成不可用 → 头文件必须重建 |
| 代码实现改了 | 各 API 的绝对地址全变 → hex 必须重建 |
| 库变小/变大 | 镜像结束地址变了 → `src\Ld\Link_rom.ld` 的 `FLASH_ROM_END` 可能要调 |

所以**必须重新链接生成**，不能只替换 hex。

> ⚠️ **hex 和头文件必须成对使用**。hex 里跳转表第 n 项是什么地址，
> 头文件里就得按同一个 n 去调用。只换一个会导致应用按错的编号调用，
> 轻则功能异常，重则跳到无效地址跑飞。

### 换库后建议检查这两件事

**① ROM 基址还够不够用**

看构建日志里这行：

```
ROM 占用: 0x00020000 - 0x00035CE3  (89,316 字节, 87.2 KB)
```

如果新库大很多，ROM 结束地址可能超过 `0x00035CE3`（当前镜像末尾），需要调整：

- `src\Ld\Link_rom.ld` 里的 `FLASH_ROM_END`
- 必要时改 ROM 基址（`-RomBase`）

**② 跳转表编号有没有变**

对比 `lib\CH572BLE_ROM_JT.md` 的「可用项」数量。数量变多说明新库带了新 API。

---

## 四、情况 B：只改了 HAL / ROM 胶水层

比如你改了 `hal\HAL.c` 里的初始化逻辑，或者调整了 `rom\rom_support.c`。
协议栈库没变，但：

- HAL 层是**编进 ROM 镜像**的（ROM 要自包含），所以 hex 会变；
- 各符号的地址可能整体位移，所以头文件要一起重建。

```powershell
cd CH572_BLE_LIB

# 不传 -Lib，脚本用工程自带的协议栈库
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1

# 然后重编应用
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target example
```

---

## 五、想彻底重来（最保险）

如果怀疑中间状态脏了，先清掉派生产物再生成：

```powershell
cd CH572_BLE_LIB

# 清掉 ROM 镜像、头文件、静态库
Remove-Item lib\CH572BLE_ROM.*, lib\libCH572BLE.a, include\CH572BLE_ROM*.h -Force

# 重新生成
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1
```

> 注意 `CH572BLE_ROM_types.h` 也会被一起删掉，但脚本会自动重新抽取，不用手工恢复。

清构建中间产物（`build\` 目录）用总构建脚本：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target clean
```

---

## 六、构建脚本会不会"以为已经生成过就跳过"？

**不会。** 已经实测确认：

- `make_rom.ps1` 每次都会重新解包、重新编译、重新生成跳转表、重新链接
  （构建前会先删除 `build\rom\stage` 与 `build\rom\obj`）；
- 实测**连续跑两次产物 SHA256 完全一致**（确定性构建），
  且每次都会刷新产物时间戳。

所以只要跑一次就会重新生成，不存在增量缓存导致用旧产物的问题。

---

## 七、常见问题

### Q1：我只想换 hex，不想要新头文件，行不行？

**不行。** 见上面「为什么必须重新生成」里的说明 —— hex 和头文件是一对，
必须成对重建。

### Q2：换库后应用编译报 `implicit declaration of function 'xxx'`

说明新库里**没有**这个 API 了（被 WCH 裁掉了）。
查 `lib\CH572BLE_ROM_JT.md` 确认该项状态；如果确实是新库删掉的，
应用侧要改用别的 API 或者换方案。

反过来，如果新库**新增**了 API，`lib\CH572BLE_ROM_JT.md` 里的「可用」项会变多。

### Q3：换库后 ROM 镜像放不下了

说明新库大了。两个办法：

1. 把 ROM 基址往前挪（让 ROM 区变大），比如 `-RomBase 0x0001C000`，
   同时把 `src\Ld\Link_rom.ld` 的 `FLASH_ROM_END` 改成 `新基址 − 0x1000`；
   直接跑 `scripts\make_rom.ps1 -Probe` 会给出推荐值；
2. 如果应用区被压缩得太厉害，考虑退回**静态库方式**
   （不烧 ROM，协议栈编进应用固件）—— 代价是应用固件大。

### Q4：`lib\backup\` 里的备份越来越多怎么办？

可以随时删。它只是给你留个后悔的机会：

```powershell
Remove-Item lib\backup\* -Force
```

### Q5：我自己重新编译了协议栈（不是 WCH 给的），能用吗？

可以，但要求：

- 编译参数要与本工程一致（`-march=rv32imac_zicsr_zifencei -mabi=ilp32 -mcmodel=medany`）
- 导出符号集合要与 `CH572BLEPeri_LIB.h` 里的声明对得上
- 必须是 RV32IMBC 兼容的指令集（不能含 A 扩展原子指令、不能含浮点）
- 必须用 **WCH 官方工具链**编译（协议栈源码用到 `mcpy` 私有指令）

换完跑一次 `make_rom.ps1`，再用 `scripts\run_tests.ps1` 做一次完整自检。

### Q6：报"找不到 WCH 官方 RISC-V 工具链"

本工程只支持 MounRiver Studio 自带的 WCH 定制工具链。两种解决办法：

```powershell
# 办法 1：安装 MounRiver Studio 2，脚本会自动探测
# 办法 2：用 -Toolchain 指定目录
powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 `
    -Toolchain "D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12"
```

**不支持**通用 RISC-V 工具链（`riscv32-esp-elf-gcc`、`riscv64-unknown-elf-gcc` 等）——
协议栈源码用到了 `mcpy` 私有指令与 `WCH-Interrupt-fast` 中断属性，只有官方工具链认识。

### Q7：换库之后要不要重编 MounRiver Studio 工程？

要。MRS 工程里引用的就是 `include\CH572BLE_ROM.h`，
头文件变了之后在 MRS 里点一次 Rebuild 即可。
如果 `.cproject` 里的库名/路径也变了，重新跑一次
`scripts\make_mrs_project.ps1 -Force`。

---

## 八、命令速查

| 想做什么 | 命令 |
|---|---|
| **生成 ROM 镜像 + 头文件** | `scripts\make_rom.ps1` |
| 用指定的新库生成 | `scripts\make_rom.ps1 -Lib <新库路径>` |
| 换 ROM 基址生成 | `scripts\make_rom.ps1 -RomBase 0x00008000` |
| 编译应用固件 | `scripts\build.ps1 -Target example` |
| 打包静态库（另一种方式） | `scripts\build.ps1 -Target lib` |
| 全部一起做 | `scripts\build.ps1 -Target all` |
| 清构建中间产物 | `scripts\build.ps1 -Target clean` |
| 完整自检（26 项） | `scripts\run_tests.ps1` |
| 生成 MounRiver Studio 工程 | `scripts\make_mrs_project.ps1` |
