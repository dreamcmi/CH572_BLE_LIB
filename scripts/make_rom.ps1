# =============================================================================
#  make_rom.ps1 —— 一键生成 CH572 BLE 的 ROM 库镜像与应用侧头文件
#
#  这是本工程唯一的 ROM 产物生成入口。它只做两件事：
#
#      ① 把协议栈库编译成固定地址的 ROM 库镜像（放在 lib/ 下）
#           CH572BLE_ROM.hex        固定地址的 BLE 协议栈镜像（Intel HEX，可直接烧录）
#           CH572BLE_ROM.bin        同上，纯二进制
#           CH572BLE_ROM.map        链接映射（含每个符号的绝对地址）
#           CH572BLE_ROM_SYMS.txt   符号地址清单
#           CH572BLE_ROM_JT.md      跳转表对照报告（哪个下标对应哪个 API）
#
#      ② 生成应用侧头文件
#           include/CH572BLE_ROM.h  应用代码 include 这个即可调用协议栈
#
#  不做的事：不编译示例工程、不重编应用固件、不跑自检套件、不碰静态库。
#
#  原理（与 WCH 官方 CH59xBLE_ROM.hex 完全一致的做法）：
#      1. 把协议栈全部 .o 链接到一个**固定绝对地址** ROM_BASE；
#      2. ROM_BASE+0x00 放镜像标识，+0x08 放 UUID 表，+0x34 放函数跳转表；
#      3. 应用侧只包含 CH572BLE_ROM.h，头文件里把 API 定义成对绝对地址的
#         间接调用，因此**应用不需要链接任何 BLE 库**，代码体积显著下降；
#      4. ROM 里需要"应用提供"的回调（中断处理等）通过函数指针表间接调用。
#
#  三趟链接的原因：
#      跳转表内容（每个 API 的绝对地址）依赖链接结果，而链接又需要跳转表存在，
#      所以先做一趟"占位链接"拿到符号地址 -> 生成跳转表 -> 再正式链接两趟
#      （让跳转表自身的占位影响收敛），最后输出镜像。
#
#  工具链：
#      只支持 MounRiver Studio 自带的 WCH 定制工具链（会自动探测）
#          riscv-wch-elf-gcc      MounRiver Studio 2
#          riscv-none-embed-gcc   MounRiver Studio 1
#      协议栈源码用到了 mcpy 私有指令与 WCH-Interrupt-fast 中断属性，
#      只有官方工具链支持。
#
#  用法：
#      # 用工程自带的 lib\libCH572BLE_PERI.a 生成
#      powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1
#
#      # 换一个协议栈库来生成
#      powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 `
#          -Lib "D:\CH572EVT-1.5\EVT\EXAM\BLE\LIB\libCH572BLE_PERI.a"
#
#      # 换个 ROM 基址（改了要同步改 src\Ld\Link_rom.ld 的 ROM_APP_FLASH_END）
#      powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 -RomBase 0x00008000
#
#      # 指定工具链目录（自动探测失败时）
#      powershell -ExecutionPolicy Bypass -File scripts\make_rom.ps1 -Toolchain "<工具链目录>"
# =============================================================================

[CmdletBinding()]
param(
    # 要编译的协议栈库。默认用工程自带的 lib\libCH572BLE_PERI.a。
    # 指定别的路径时会先备份工程内原库，再替换。
    [string]$Lib = '',

    # ROM 基址。默认 0x00020000。
    #
    # 本工程的排布原则是「协议栈靠后、app 占前面一整块连续空间」：
    #   0x00000 - 0x1EFFF  124 KB  app 主体（连续，随便长）
    #   0x1F000 - 0x1FFFF    4 KB  SNV 绑定信息存储区
    #   0x20000 - 0x372CB  ~93 KB  BLE 协议栈 ROM 镜像  ← 就是这个基址
    #   0x372CC - 0x3BFFF  ~19 KB  余量（ROM 将来长大了可以往后扩）
    #   0x3C000 - 0x3FFFF   16 KB  芯片固定占用
    #
    # 上面两行是当前实测值，随库版本会变；精确边界以 lib\CH572BLE_ROM_JT.md
    # 和 build\out\*-combined.json 为准，别凭这里记。
    #
    # 换基址的话，必须同步改 src\Ld\Link_rom.ld 里的 ROM_APP_FLASH_END
    # （= 新基址 - SNV 大小，把 SNV 那 4K 留出来）。
    [string]$RomBase = '0x00020000',

    # 优化级别
    [string]$Opt = '-Os',

    # 工具链目录。不给则自动探测 MounRiver Studio 的安装位置。
    [string]$Toolchain = '',

    # CH59xBLE_ROM.h 的位置（跳转表 ABI 参考，用于保持下标与官方一致）
    [string]$Ch592RomHdr = '',

    # SNV 区大小（字节）。默认 4 KB，紧贴在 ROM 之前。
    #
    # 说明：绑定信息实际只用 BLE_SNV_BLOCK × BLE_SNV_NUM = 256 × 1 = 256 字节，
    # 但 CH572 的 Flash **物理擦除粒度是 4 KB**（见 ISP572.h、HAL.c 的
    # FLASH_BLOCK_SIZE），所以 SNV 必须独占一整个 4 KB 扇区，不能按 256 字节算。
    # 头文件里用它标注 Flash 分区；改这里也要同步改应用侧 Link_rom.ld。
    # 想让 app 多拿 3 KB 可以传 -SnvSize 1024，但要同步改 Link_rom.ld。
    # 另外注意：缩小 SNV 会让 ROM 基址前移，已烧录的 ROM 镜像要重新烧。
    [int]$SnvSize = 4096,

    # 打开串口调试输出（定义 DEBUG 宏）。注意参数名不能叫 Debug ——。
    #
    # 为什么需要它：PRINT 宏在 CH57x_common.h 里是 `#ifdef DEBUG` 才展开成
    # printf 的，默认是空宏；printf 的底层 _write 也被同样条件包着。
    # 所以不开这个开关时，固件在串口上什么都不会打印。
    #
    # 打开后：
    #   * 应用会初始化 UART（PA3 = TX，PA2 = RX，115200 8N1）
    #   * main() 会打印分步进度，可以定位卡在哪一步
    #   * HAL 里的协议栈报错也会打印出来
    [switch]$DebugBuild,

    # 只探测：链接一次量出 ROM 的实际大小，算出"协议栈贴末尾"时 app 能拿到的
    # 最大连续空间，并打印推荐基址，然后退出。不产出 hex/bin/头文件。
    #
    # 什么时候用它：ROM 基址决定 app 能用的 Flash 大小，而 ROM 最终大小又受基址
    # 影响（循环依赖）。改过 -SnvSize 或想让 app 空间最大时，先跑一次这个模式
    # 量出结果，再把推荐的 -RomBase 填回来。
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$LogDir    = Join-Path $Root 'build\logs'
$BuildDir  = Join-Path $Root 'build'
$RomBuild  = Join-Path $BuildDir 'rom'
$StageDir  = Join-Path $RomBuild 'stage'
$ObjDir    = Join-Path $RomBuild 'obj'
$LibDir    = Join-Path $Root 'lib'
$IncDir    = Join-Path $Root 'include'
$ToolsDir  = Join-Path $Root 'tools'

function Write-Step([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Info([string]$m) { Write-Host "    $m" }
function Write-Ok([string]$m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn2([string]$m){ Write-Host "    [警告] $m" -ForegroundColor Yellow }
function Write-Err([string]$m)  { Write-Host "    [错误] $m" -ForegroundColor Red }

# ---------------------------------------------------------------------------
# 工具链探测
# ---------------------------------------------------------------------------
function Find-Gcc {
    param([string]$Hint)

    # 本工程只支持 WCH 官方工具链：
    #   riscv-wch-elf-gcc   MounRiver Studio 2 自带（xPack GNU RISC-V Embedded GCC 12.2）
    #   riscv-none-embed-gcc  MounRiver Studio 1 自带
    # 它们原生支持协议栈用到的两个 WCH 扩展（mcpy 指令、WCH-Interrupt-fast 中断属性），
    # 通用 RISC-V 工具链不支持，因此不在支持范围内。
    $names = @(
        'riscv-wch-elf-gcc.exe',
        'riscv-none-embed-gcc.exe'
    )

    # 1) 命令行 -Toolchain 指定的目录
    if ($Hint) {
        if (Test-Path $Hint) {
            $p = Get-ChildItem -Path $Hint -Recurse -Depth 6 -Filter '*-gcc.exe' -ErrorAction SilentlyContinue |
                 Where-Object { $names -contains $_.Name } |
                 Sort-Object { $names.IndexOf($_.Name) } | Select-Object -First 1
            if ($p) { return $p.FullName }
            Write-Warn2 "指定的目录里没有 WCH 工具链：$Hint"
            Write-Warn2 "该目录下应存在 bin\riscv-wch-elf-gcc.exe 或 bin\riscv-none-embed-gcc.exe"
        } else {
            Write-Warn2 "指定的工具链目录不存在：$Hint"
        }
    }

    # 2) PATH 环境变量
    foreach ($n in $names) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }

    # 3) MounRiver Studio 的默认安装位置
    #    MRS2 的工具链藏得比较深，这里给出完整路径。
    $cands = @(
        'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12',
        'C:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12',
        'C:\MounRiver\MounRiver_Studio2\toolchain',
        'D:\MounRiver\MounRiver_Studio2\toolchain',
        'C:\MounRiver\MounRiver_Studio\toolchain',
        'D:\MounRiver\MounRiver_Studio\toolchain'
    )
    foreach ($c in $cands) {
        if (Test-Path $c) {
            $p = Get-ChildItem -Path $c -Recurse -Depth 6 -Filter '*-gcc.exe' -ErrorAction SilentlyContinue |
                 Where-Object { $names -contains $_.Name } |
                 Sort-Object { $names.IndexOf($_.Name) } | Select-Object -First 1
            if ($p) { return $p.FullName }
        }
    }
    return $null
}

$Gcc = Find-Gcc -Hint $Toolchain
if (-not $Gcc) {
    Write-Err '找不到 WCH 官方 RISC-V 工具链。'
    Write-Err ''
    Write-Err '本工程只支持 MounRiver Studio 自带的 WCH 定制工具链：'
    Write-Err '    riscv-wch-elf-gcc      （MounRiver Studio 2，推荐）'
    Write-Err '    riscv-none-embed-gcc   （MounRiver Studio 1）'
    Write-Err ''
    Write-Err '原因：协议栈源码用到了两个 WCH 扩展，只有官方工具链支持 ——'
    Write-Err '    mcpy                  青稞内核的快速内存拷贝私有指令'
    Write-Err '    WCH-Interrupt-fast    中断硬件压栈属性'
    Write-Err '通用 RISC-V 工具链（如 riscv32-esp-elf-gcc、riscv64-unknown-elf-gcc）'
    Write-Err '不支持这两个扩展，无法构建本工程。'
    Write-Err ''
    Write-Err '解决办法（任选其一）：'
    Write-Err '  1) 安装 MounRiver Studio 2，然后直接重跑本脚本（会自动探测）'
    Write-Err '  2) 用 -Toolchain 指定工具链目录，例如：'
    Write-Err '     -Toolchain "D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12"'
    exit 1
}
$BinDir  = Split-Path -Parent $Gcc
$Prefix  = (Get-Item $Gcc).BaseName -replace '-gcc$', ''
$Ar      = Join-Path $BinDir "$Prefix-ar.exe"
$Objcopy = Join-Path $BinDir "$Prefix-objcopy.exe"
$Size    = Join-Path $BinDir "$Prefix-size.exe"
$Nm      = Join-Path $BinDir "$Prefix-nm.exe"
# WCH 定制工具链有两代命名：riscv-none-embed-*（MRS1）与 riscv-wch-elf-*（MRS2）。
# 它们都认识 mcpy 指令与 WCH-Interrupt-fast 中断属性，不需要降级宏。

$romBaseInt = [Convert]::ToUInt32($RomBase.Substring(2), 16)
$romBaseHex = '0x{0:X8}' -f $romBaseInt
if (($romBaseInt % 4096) -ne 0 -or $SnvSize -lt 4096 -or ($SnvSize % 4096) -ne 0 -or
    $romBaseInt -le $SnvSize -or $romBaseInt -ge 0x3C000) {
    Write-Err 'ROM base and SNV reservation must use 4KB Flash erase boundaries'; exit 1
}

# 从 .map 里读出 ROM 镜像的结束地址。
# 不同版本的 ld 打印的地址宽度不同（8 位或 16 位），所以统一取后 8 位解析。
function Get-RomEndFromMap {
    param([string]$MapPath)
    $line = Select-String -Path $MapPath -Pattern '_rom_end_addr\s*=' |
            Select-Object -First 1
    if (-not $line) { return 0 }
    if ($line.Line -match '0x([0-9a-fA-F]+)') {
        $h = $Matches[1]
        if ($h.Length -gt 8) { $h = $h.Substring($h.Length - 8) }
        return [Convert]::ToUInt32($h, 16)
    }
    return 0
}

# ---------------------------------------------------------------------------
# 0. 解析协议栈库（-Lib 指定外部库时先备份再替换）
# ---------------------------------------------------------------------------
$stackLib = Join-Path $LibDir 'libCH572BLE_PERI.a'

# -Lib 指定了外部协议栈库时：先备份工程内原库，再用外部库替换。
# 这样后续所有步骤都可以统一从 lib\libCH572BLE_PERI.a 取库，逻辑最简单。
if ($Lib) {
    if (-not (Test-Path $Lib)) { Write-Err "找不到指定的协议栈库：$Lib"; exit 1 }
    $extFull = (Get-Item $Lib).FullName
    $curFull = if (Test-Path $stackLib) { (Get-Item $stackLib).FullName } else { '' }
    if ($extFull -ne $curFull) {
        Write-Info "使用外部协议栈库：$extFull"
        $backupDir = Join-Path $LibDir 'backup'
        if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Force -Path $backupDir | Out-Null }
        if (Test-Path $stackLib) {
            $old = Get-Item $stackLib
            $bk  = Join-Path $backupDir ("libCH572BLE_PERI.{0}.{1}.a" -f
                       (Get-Date).ToString('yyyyMMdd-HHmmss'), $old.Length)
            Copy-Item -LiteralPath $stackLib -Destination $bk -Force
            Write-Info ("工程内原库已备份: lib\backup\{0}" -f (Split-Path -Leaf $bk))
        }
        Copy-Item -LiteralPath $Lib -Destination $stackLib -Force
        Write-Ok '外部库已就位'
    }
}

if (-not (Test-Path $stackLib)) {
    Write-Err "找不到协议栈库：$stackLib"
    Write-Err '请把 WCH 的 libCH572BLE_PERI.a 放到 lib\ 目录下，或用 -Lib 指定它的位置。'
    exit 1
}

Write-Step '输入与工具链'
$li = Get-Item $stackLib
Write-Info ("协议栈库 : {0}" -f $stackLib)
Write-Info ("           {0:N0} 字节，修改于 {1}" -f
            $li.Length, $li.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
Write-Info "GCC      : $Gcc"
Write-Info "ROM 基址 : $romBaseHex"

$ArchFlags = @('-march=rv32imac_zicsr_zifencei','-mabi=ilp32','-mcmodel=medany','-msmall-data-limit=8')
# Compile granular sections. The real jump table's symbol relocations are GC roots.
$RomCfgFlags = @($Opt,'-ffunction-sections','-fdata-sections','-fno-common','-Wall','-Wno-attributes',
                 '-finput-charset=UTF-8','-fexec-charset=UTF-8')
# 打开串口调试（-DebugBuild 开关）
if ($DebugBuild) { $RomCfgFlags += '-DDEBUG' }
$IncFlags  = @("-I$IncDir",
               "-I$(Join-Path $Root 'src\StdPeriphDriver')",
               "-I$(Join-Path $Root 'src\RVMSIS')",
               "-I$(Join-Path $Root 'rom')")

function Invoke-Tool {
    param(
        [string]$Exe,
        [string[]]$Arguments,
        [string]$Caption = '',
        [switch]$Quiet,
        # 工作目录。给了就切过去再执行，这样传给工具的路径可以写成
        # 不含空格的相对路径，减少踩到 cmd 引号问题的机会。
        [string]$WorkDir = ''
    )

    if ($Caption -and -not $Quiet) { Write-Info $Caption }
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    $id      = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $logFile = Join-Path $LogDir "_t_$id.log"
    $batFile = Join-Path $LogDir "_t_$id.bat"

    # 把参数拼成一行，每个参数单独加引号。
    #
    # 为什么不用 GCC 的 @响应文件：它的 @file 语法会把 Windows 路径里的
    # 反斜杠当转义符吃掉（C:\a\b.c 变成 C:ab.c），导致"找不到源文件"。
    # 直接把完整命令行写进 .bat 交给 cmd 逐字读取就没有这个问题。
    # 注意末尾的反斜杠会让后续的引号失去转义作用，先把它替换成 \\ 规避。
    $argLine = ($Arguments | Where-Object { $_ -ne $null -and "$_".Trim() -ne '' } | ForEach-Object {
        $a = "$_" -replace '\\+$', '\\'
        '"' + ($a -replace '"', '\"') + '"'
    }) -join ' '

    # 生成临时 .bat 再执行。
    # 为什么不直接拼 cmd 命令串：cmd 在解析
    #     "带空格路径\工具.exe" 参数 > 日志 2>&1
    # 这种组合时会切错参数（实测 ar 会一个文件都解不出来）。
    # 写进 .bat 由 cmd 自己去读，就完全正常了。
    $bat = '@echo off' + "`r`n" + ('"{0}" {1} > "{2}" 2>&1') -f $Exe, $argLine, $logFile
    # 用 GBK 写 bat：这次每个参数都加了引号，末尾也补了换行 ——
    # 缺末尾换行符时，cmd 有可能把最后一行读成 "@echo off<命令>" 的拼接体，
    # 表现成屏幕上多打一行 "off ..."。
    [System.IO.File]::WriteAllText($batFile, $bat + "`r`n",
                                   [System.Text.Encoding]::GetEncoding('GBK'))

    $code = 0
    try {
        if ($WorkDir -and (Test-Path $WorkDir)) {
            Push-Location $WorkDir
            try   { & "$env:ComSpec" /c "`"$batFile`"" | Out-Null; $code = $LASTEXITCODE }
            finally { Pop-Location }
        } else {
            & "$env:ComSpec" /c "`"$batFile`"" | Out-Null
            $code = $LASTEXITCODE
        }
    } finally {
        Remove-Item -LiteralPath $batFile -Force -ErrorAction SilentlyContinue
    }

    # 日志编码自动判别（GCC 的中文提示可能是 UTF-8，也可能随系统区域变成 GBK）
    if ((Test-Path $logFile) -and -not $Quiet) {
        $text = ''
        try {
            $bytes = [System.IO.File]::ReadAllBytes($logFile)
            if ($bytes.Length -gt 0) {
                try {
                    $strict = New-Object System.Text.UTF8Encoding($false, $true)
                    $text = $strict.GetString($bytes)
                } catch {
                    $text = [System.Text.Encoding]::GetEncoding('GBK').GetString($bytes)
                }
            }
        } catch { $text = '' }

        foreach ($line in ($text -split "`r?`n")) {
            if     ($line -match 'error:|Error:|错误')     { Write-Err  $line }
            elseif ($line -match 'warning:|Warning:|警告') { Write-Warn2 $line }
            elseif ($line.Trim() -eq '')                    { }
            else                                           { Write-Info $line }
        }
    }
    Remove-Item -LiteralPath $logFile -Force -ErrorAction SilentlyContinue
    return $code
}

# ---------------------------------------------------------------------------
# 1. 解包协议栈 + 编译支撑层
# ---------------------------------------------------------------------------
Write-Step '准备 ROM 对象'

if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null


# 解包协议栈对象。
# 这里刻意"先把库复制成不含空格的名字（stack.a），再切到目标目录用相对路径解包"：
#   MounRiver Studio 2 的工具链路径里带空格
#   （...\RISC-V Embedded GCC12\bin\riscv-wch-elf-ar.exe），
#   cmd 处理带空格路径的引号时会反复消费引号，
#   导致 ar 收到错误的参数、一个文件都解不出来（但退出码仍是 0）。
#   用相对路径 + -WorkDir 就彻底避开了这个问题。
Copy-Item -LiteralPath $stackLib -Destination (Join-Path $StageDir 'stack.a') -Force
if ((Invoke-Tool -Exe $Ar -Arguments @('x','stack.a') -WorkDir $StageDir -Quiet) -ne 0) {
    Write-Err 'ar 解包失败'; exit 1
}
Remove-Item -LiteralPath (Join-Path $StageDir 'stack.a') -Force -ErrorAction SilentlyContinue
$stackObjs = @(Get-ChildItem -Path $StageDir -Filter '*.o' -File | Sort-Object Name)
Write-Ok "协议栈对象 $($stackObjs.Count) 个"

if (Test-Path $ObjDir) { Remove-Item $ObjDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $ObjDir | Out-Null

# 1.1 ROM 胶水层（rom_support.c 提供中断转接与应用钩子表）
$romObjs = @()
foreach ($s in @(Get-ChildItem -Path (Join-Path $Root 'rom') -Filter '*.c' -File |
                 Where-Object { $_.Name -ne 'rom_entry.c' } | Sort-Object Name)) {
    $o = Join-Path $ObjDir ($s.BaseName + '.o')
    if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + $RomCfgFlags + $IncFlags +
            @('-c','-o',$o,$s.FullName)) -Caption "编译 rom/$($s.Name)") -ne 0) {
        Write-Err "编译失败: $($s.Name)"; exit 1
    }
    $romObjs += $o
}

# 生成 rom/rom_version.h —— ROM 里的版本串（内容 = 协议栈的 VER_FILE）
#
# 为什么用"生成头文件"而不是命令行 -D 传字符串宏：
#   "-DXXX=\"字符串\"" 的引号会被 shell 吃掉，导致 V1.3 里的点号被编译器
#   当成小数，报错 "'CH572_BLE_PERI_LIB_V1' undeclared here"，
#   结果是版本串变空、协议栈启动校验失败。生成头文件没有引号问题。
#
# 为什么版本串必须与 VER_FILE 一致：
#   应用启动时 CH57x_BLEInit() 做校验
#       tmos_memcmp(VER_LIB, VER_FILE, tmos_strlen(VER_FILE))
#   不一致会打印 "head file error" 并停在 while(1)，BLE 完全起不来。
& python (Join-Path $ToolsDir 'rom_version_gen.py') `
    (Join-Path $IncDir 'CH572BLE_ROM_types.h') `
    (Join-Path $Root 'rom\rom_version.h') 2>&1 | ForEach-Object { Write-Info $_ }
if ($LASTEXITCODE -ne 0) { Write-Err '生成 rom_version.h 失败'; exit 1 }


# rom_entry.c 单独编译：它负责在 .rom_header 段里放镜像标识、UUID 表与版本串
$entryObj = Join-Path $ObjDir 'rom_entry.o'
if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + $RomCfgFlags + $IncFlags +
        @('-c','-o',$entryObj,(Join-Path $Root 'rom\rom_entry.c'))) -Caption '编译 rom/rom_entry.c') -ne 0) {
    Write-Err '编译失败: rom_entry.c'; exit 1
}

# 1.2 协议栈依赖的外设驱动（ROM 要自包含才不会被调用到无效地址）
$drvNeed = @('CH57x_clk.c','CH57x_gpio.c','CH57x_sys.c','CH57x_pwr.c','CH57x_uart.c')
foreach ($n in $drvNeed) {
    $p = Join-Path $Root "src\StdPeriphDriver\$n"
    if (-not (Test-Path $p)) { continue }
    $o = Join-Path $ObjDir ((Get-Item $p).BaseName + '.o')
    if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + $RomCfgFlags + $IncFlags +
            @('-c','-o',$o,$p)) -Caption "编译驱动 $n") -ne 0) {
        Write-Err "编译失败: $n"; exit 1
    }
    $romObjs += $o
}
Write-Ok "支撑层对象 $($romObjs.Count) 个"

# ---------------------------------------------------------------------------
# 1.3 生成"协议栈引用了但本平台不提供"的符号兼容桩
#
#     libCH572BLE_PERI.a 是从全功能协议栈裁剪来的：主机角色 / GATT 客户端 /
#     BLE 5.x 的**实现**被删了，但 gatt.o、att.o、api_c.o 里还残留对它们的调用。
#     那些调用所在的分支在从机角色下永远不会执行，所以为它们提供弱桩：
#       * 让 ROM 镜像能链接成功；
#       * 万一真被误调用，会停在桩里（便于定位），而不是跳到无效地址跑飞。
# ---------------------------------------------------------------------------
Write-Info '生成兼容桩 rom/rom_stub.c'
$keepFile = Join-Path $RomBuild 'keep.txt'
if (-not (Test-Path $keepFile)) {
    Set-Content -LiteralPath $keepFile -Encoding UTF8 -Value @(
        '# 本文件列出"必须使用协议栈真实实现"的符号，每行一个。',
        '# 留空即可：桩生成脚本会自行判断哪些符号在归档里已有定义',
        '# （包括 LOCAL 绑定的 .bss/.sbss 数据），只对彻底没有的符号生成桩。'
    )
}
$stubSrc    = Join-Path $Root 'rom\rom_stub.c'
$stubReport = Join-Path $RomBuild 'stub_report.md'
& python (Join-Path $ToolsDir 'rom_stub_gen.py') --lib $stackLib --keep $keepFile `
    --out $stubSrc --report $stubReport 2>&1 | ForEach-Object { Write-Info $_ }
if ($LASTEXITCODE -ne 0) { Write-Err '生成兼容桩失败'; exit 1 }

$stubObj = Join-Path $ObjDir 'rom_stub.o'
if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + $RomCfgFlags + $IncFlags +
        @('-c','-o',$stubObj,$stubSrc)) -Caption '编译 rom/rom_stub.c') -ne 0) {
    Write-Err '编译 rom_stub.c 失败'; exit 1
}
$romObjs += $stubObj

# ---------------------------------------------------------------------------
# 2. 链接函数（供多趟调用）
#
#    关于 --gc-sections 的两阶段策略：
#      * 第 1 趟**不开** --gc-sections：让归档里每一个对象、每一个函数都被保留，
#        这样我们才能从 map 里拿到"全部 API 的地址"来生成跳转表。
#      * 第 2 趟起**开启** --gc-sections：此时跳转表已经真实引用了所有要暴露的
#        API，它们成了 GC 根；而协议栈里那些"只有主机角色才会用到"的死代码
#        （gatt.c 的客户端读、att.c 的回请求等）会被裁掉，
#        连带解决它们引用的、PERI 库里根本不存在的符号。
# ---------------------------------------------------------------------------
$ld        = Join-Path $Root 'rom\rom_link.ld'
$stackOnly = @($stackObjs | ForEach-Object { $_.FullName })
$ispLib    = Join-Path $LibDir 'libISP572.a'

function Link-Rom {
    param(
        [string]$Elf,
        [string]$Map,
        [string]$JtObj,
        [int]$JtEntries,
        [switch]$GcSections,
        # 覆盖 ROM 基址。探测模式用它做一次"低基址试探"，平时留空即用 $romBaseHex。
        [string]$BaseOverride = ''
    )

    $base = if ($BaseOverride) { $BaseOverride } else { $romBaseHex }

    $objs = @($entryObj) + $romObjs + $stackOnly
    if ($JtObj -and (Test-Path $JtObj)) { $objs += $JtObj }

    $flags = @('-nostartfiles','-nostdlib',"-T$ld",
               "-Wl,--defsym=ROM_BASE=$base",
               "-Wl,--defsym=ROM_JT_ENTRIES=$JtEntries", '-Wl,--no-relax')
    if ($GcSections) { $flags += '-Wl,--gc-sections' }
    $flags += @("-Wl,-Map=$Map",
                '--specs=nano.specs','--specs=nosys.specs',
                '-o', $Elf)

    # libISP572.a 提供 FLASH_EEPROM_CMD（Flash 擦写命令），
    # 协议栈的 SNV 与 tmos_snv_* 依赖它，必须一起进 ROM。
    $a = @() + $ArchFlags + $flags + $objs + @($ispLib) + @('-lgcc','-lc','-lm')
    return (Invoke-Tool -Exe $Gcc -Arguments $a -Caption '链接')
}

# ---------------------------------------------------------------------------
# 从协议栈头文件里读出 VER_FILE，作为 ROM 版本串的内容。
#
# 为什么必须一致：应用的 CH57x_BLEInit() 会做
#     tmos_memcmp(VER_LIB, VER_FILE, tmos_strlen(VER_FILE))
# 校验，VER_LIB 指向 ROM 里的版本串、VER_FILE 来自头文件。
# 若两者前缀不同会直接停在 while(1) 里，表现为"协议栈初始化失败、BLE 起不来"。
#
# 头文件里的定义形如：  #define VER_FILE  "CH572_BLE_PERI_LIB_V1.3"
# ---------------------------------------------------------------------------
function Get-StackVersion {
    $hdrFile = Join-Path $IncDir 'CH572BLE_ROM_types.h'
    if (-not (Test-Path $hdrFile)) {
        Write-Warn2 "找不到 $hdrFile，ROM 版本串用兜底默认值"
        return ''
    }
    $m = Select-String -Path $hdrFile -Pattern '#define\s+VER_FILE\s+"([^"]+)"' |
         Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    Write-Warn2 'CH572BLE_ROM_types.h 里没找到 VER_FILE 定义'
    return ''
}

$stackVer = Get-StackVersion
if ($stackVer) {
    Write-Info "协议栈版本串 : $stackVer（将写入 ROM 的 VER_LIB）"
} else {
    $stackVer = 'CH572_BLE_PERI_LIB_V1.3'
    Write-Warn2 "改用兜底版本串 : $stackVer"
}

# ---------------------------------------------------------------------------
# 3. 第 1 趟：占位跳转表 + 链接，采集符号地址
# ---------------------------------------------------------------------------
Write-Step '第 1 趟：占位链接（采集符号地址）'

# 占位跳转表长度。
#
# 为什么要有它：第 1 趟链接时真实跳转表还没生成（表内容要靠这一趟的符号
# 地址才能算出来），所以先用一张同样长度的全 0 表占位，让后面的符号地址
# 落在接近最终的位置上。
#
# 为什么写死而不是精确推算：真实项数由 rom_gen.py 决定 —— 它以参考头文件
# 的最大下标 +1 为起点做 4 项对齐，再为 5 个扩展 API 扩容并重新对齐。
# 参考头文件本身有重复下标（见 rom_jt_extract.py 的处理），条目数（137）
# 与最大下标（140）对不上，算不准，所以这里直接取当前实测的真实项数 164。
# 下面第 4 节会用刚抽出的 JSON 估一个"下界"来兜底检查，防止这张表太小
# 导致撞上 rom_link.ld 的 ASSERT(SIZEOF(.rom_jt) == ROM_JT_ENTRIES*4)。
# 换参考头文件/协议栈库后如果最终链接报 rom_jt size mismatch，就调大这个数。
$JT_PLACEHOLDER_ENTRIES = 164

$placeholder = Join-Path $RomBuild 'rom_jt_placeholder.S'
$phLines = @('    .section .rom_jt,"a",@progbits','    .align 2',
             '    .globl _rom_jump_table','_rom_jump_table:')
1..$JT_PLACEHOLDER_ENTRIES | ForEach-Object { $phLines += '    .word 0' }
$phLines += '    .globl _rom_jt_end'
$phLines += '_rom_jt_end:'
$phLines += ''
Set-Content -LiteralPath $placeholder -Value $phLines -Encoding UTF8

$phObj = Join-Path $RomBuild 'rom_jt_placeholder.o'
if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + @('-x','assembler-with-cpp','-c',
        '-o',$phObj,$placeholder)) -Caption '汇编占位跳转表') -ne 0) {
    Write-Err '占位跳转表汇编失败'; exit 1
}

$map1 = Join-Path $RomBuild 'rom_pass1.map'
$elf1 = Join-Path $RomBuild 'rom_pass1.elf'

# 探测模式用一个很低的基址链接，保证一定放得下 ——
# 我们要的是"镜像有多大"，跟它落在哪无关。
$linkBaseHex = if ($Probe) { '0x00001000' } else { $romBaseHex }
$linkBaseInt = if ($Probe) { 0x1000 } else { $romBaseInt }

if ((Link-Rom -Elf $elf1 -Map $map1 -JtObj $phObj -JtEntries $JT_PLACEHOLDER_ENTRIES `
              -BaseOverride $linkBaseHex) -ne 0) {
    Write-Err '第 1 趟链接失败'
    Write-Host ''
    Write-Host '常见原因（见上方 undefined reference 清单）：' -ForegroundColor Yellow
    Write-Host '  * 缺少外设驱动 -> 把对应 CH57x_xxx.c 加进本脚本的 $drvNeed 列表' -ForegroundColor Yellow
    Write-Host '  * 缺少中断处理 -> 在 rom/rom_support.c 里补 ROM_IRQ_STUB' -ForegroundColor Yellow
    exit 1
}
Write-Ok '占位链接成功'

# ---------------------------------------------------------------------------
# 3.5 探测模式：量出"协议栈贴末尾"时 app 能拿到的最大连续空间
#
#     为什么需要探测：ROM 的最终大小受基址影响（基址不同，各函数绝对地址不同，
#     长跳转的指令编码会变），而基址又取决于 ROM 大小 —— 循环依赖。
#     这里直接用第 1 趟（未开 gc，尺寸最大）的结果做保守估计：
#     只要它放得下，开启 gc 后的正式链接一定也放得下。
# ---------------------------------------------------------------------------
if ($Probe) {
    $probeEnd  = Get-RomEndFromMap -MapPath $map1
    if ($probeEnd -le $linkBaseInt) {
        Write-Err "无法从 map 读出 ROM 结束地址（_rom_end_addr）"; exit 1
    }
    $probeSize = $probeEnd - $linkBaseInt

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ' 探测结果：ROM 尺寸与推荐布局' -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host '  （ROM 落在哪不影响它的大小，所以这里用低基址 0x00001000 试探）'
    Write-Host ("  ROM 尺寸（趟1，未开 gc） : {0:N0} 字节 ({1:N1} KB)   ← 保守上界" -f `
                $probeSize, ($probeSize / 1024))
    Write-Host '  正式链接开 --gc-sections 后会更小，本工程实测约小 10~12 KB'
    Write-Host ''

    # 推荐基址：让 ROM 紧贴用户 Flash 末尾，同时给 SNV 留空间，4K 对齐
    $recBase = (0x3C000 - $probeSize) -band (-bnot 0xFFF)
    $recSnv  = $recBase - $SnvSize
    Write-Host ("  推荐 -RomBase            : 0x{0:X8}" -f $recBase) -ForegroundColor Green
    Write-Host ("  对应 app 连续空间        : 0x00000000 - 0x{0:X8}  ({1:N1} KB)" -f `
                ($recSnv - 1), ($recSnv / 1024)) -ForegroundColor Green
    Write-Host ("  对应 SNV 区              : 0x{0:X8} - 0x{1:X8}  ({2:N1} KB)" -f `
                $recSnv, ($recBase - 1), ($SnvSize / 1024)) -ForegroundColor Green
    Write-Host ''
    Write-Host '  下一步：' -ForegroundColor Yellow
    Write-Host ("    1) scripts\make_rom.ps1 -RomBase 0x{0:X8}" -f $recBase) -ForegroundColor Yellow
    Write-Host '    2) rom_ram_info.ld 自动生成 app/SNV 边界，无需手改 Link_rom.ld' -ForegroundColor Yellow
    Write-Host  '    3) scripts\build.ps1 -Target example   （重新编译应用）' -ForegroundColor Yellow
    Write-Host ''
    exit 0
}

# ---------------------------------------------------------------------------
# 4. 生成真实跳转表 + 应用侧头文件
# ---------------------------------------------------------------------------
Write-Step '生成跳转表与应用侧头文件'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Err '需要 Python 3 来生成跳转表与头文件'; exit 1
}

$jtJson = Join-Path $RomBuild 'jt_map.json'
$hdr592 = $Ch592RomHdr
if (-not $hdr592 -or -not (Test-Path $hdr592)) {
    $cands = @(
        (Join-Path (Split-Path -Parent $Root) 'CH592EVT-1.7\EVT\EXAM\BLE\LIB\CH59xBLE_ROM.h'),
        'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH592EVT-1.7\EVT\EXAM\BLE\LIB\CH59xBLE_ROM.h'
    )
    foreach ($c in $cands) { if (Test-Path $c) { $hdr592 = $c; break } }
}
if (-not $hdr592 -or -not (Test-Path $hdr592)) {
    Write-Err '找不到 CH59xBLE_ROM.h（跳转表 ABI 参考），请用 -Ch592RomHdr 指定'
    exit 1
}
Write-Info "跳转表 ABI 参考: $hdr592"

& python (Join-Path $ToolsDir 'rom_jt_extract.py') $hdr592 $jtJson *> (Join-Path $RomBuild 'jt_extract.log')
if ($LASTEXITCODE -ne 0) {
    Get-Content (Join-Path $RomBuild 'jt_extract.log') | ForEach-Object { Write-Err $_ }
    exit 1
}

$jtAsm  = Join-Path $RomBuild 'rom_jt.S'
$outHdr = Join-Path $IncDir 'CH572BLE_ROM.h'
$report = Join-Path $LibDir 'CH572BLE_ROM_JT.md'

# ---------------------------------------------------------------------------
# 占位表长度校验
#
# 第 1 趟用的占位跳转表长度是写死的（见文件头 $JT_PLACEHOLDER_ENTRIES）。
# 这里用刚抽出来的 ABI 参考数目估一个"真实项数下界"来兜底：
#   rom_gen.py 从 max_idx+1 起 4 项对齐，再为 5 个扩展 API 扩容并重新对齐：
#       n_min = ((max_idx + 1) + 5 + 3) / 4 * 4
# 实测 max_idx=140（参考头文件里有重复下标，条目只有 137）→ n_min=148，
# 而真实表是 164 项，所以这里只能做"下界"检查：真实项数 < 占位长度会撞上
# rom_link.ld 的 ASSERT(SIZEOF(.rom_jt) == ROM_JT_ENTRIES*4) 而硬失败。
# 反过来，真实项数 > 占位长度只会让第 1 趟的地址偏低，第 2/3 趟会收敛，
# 所以这里只在"明显不够"时警告，不阻断构建。
# ---------------------------------------------------------------------------
$jtFuncs = (Get-Content -LiteralPath $jtJson -Raw | ConvertFrom-Json).functions
$maxIdx = 0
foreach ($p in $jtFuncs.PSObject.Properties) {
    $i = [int]$p.Value.index
    if ($i -gt $maxIdx) { $maxIdx = $i }
}
$minEntries = [int]([math]::Ceiling((($maxIdx + 1) + 5) / 4.0) * 4)
Write-Info "ABI 参考: 最大下标 $maxIdx，真实项数至少 $minEntries 项"
if ($minEntries -gt $JT_PLACEHOLDER_ENTRIES) {
    Write-Warn2 "占位表只有 $JT_PLACEHOLDER_ENTRIES 项，小于下界 $minEntries 项；" +
                "第 1 趟地址会偏低，若最终链接报 rom_jt size mismatch 请调大 `$JT_PLACEHOLDER_ENTRIES"
}

# 这一趟只需要跳转表汇编，**不要**生成头文件 ——
# 头文件里要写 ROM 镜像的实际大小，而大小得等最终链接完才知道。
& python (Join-Path $ToolsDir 'rom_gen.py') --map $map1 --jt $jtJson --base $romBaseHex `
    --out-asm $jtAsm --out-hdr $outHdr --report $report --no-header 2>&1 |
    ForEach-Object { Write-Info $_ }
if ($LASTEXITCODE -ne 0) { Write-Err '生成跳转表失败'; exit 1 }

$jtObj = Join-Path $RomBuild 'rom_jt.o'
if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + @('-x','assembler-with-cpp','-c',
        '-o',$jtObj,$jtAsm)) -Caption '汇编真实跳转表') -ne 0) {
    Write-Err '跳转表汇编失败'; exit 1
}

# ---------------------------------------------------------------------------
# 5. 第 2 / 3 趟：迭代收敛
#    跳转表本身占了空间，会让后面所有符号地址后移；重新生成一次即可收敛。
# ---------------------------------------------------------------------------
$entries = [int]((Select-String -Path $jtAsm -Pattern '^\s*\.word' -AllMatches).Count)
Write-Info "跳转表项数 = $entries"

foreach ($pass in 2,3) {
    Write-Step "第 $pass 趟：迭代链接"
    $mapN = Join-Path $RomBuild "rom_pass$pass.map"
    $elfN = Join-Path $RomBuild "rom_pass$pass.elf"
    if ((Link-Rom -Elf $elfN -Map $mapN -JtObj $jtObj -JtEntries $entries -GcSections `
              -BaseOverride $linkBaseHex) -ne 0) {
        Write-Err "第 $pass 趟链接失败"; exit 1
    }
    # 同样只重算跳转表汇编，头文件留到最后统一生成
    & python (Join-Path $ToolsDir 'rom_gen.py') --map $mapN --jt $jtJson --base $romBaseHex `
        --out-asm $jtAsm --out-hdr $outHdr --report $report --no-header 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Err "第 $pass 趟生成跳转表失败"; exit 1 }
    if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + @('-x','assembler-with-cpp','-c',
            '-o',$jtObj,$jtAsm)) -Quiet) -ne 0) {
        Write-Err "第 $pass 趟跳转表汇编失败"; exit 1
    }
    Write-Ok "第 $pass 趟完成"
}

# 最终链接：不再重算跳转表，保证"表内容"与"镜像布局"严格一致
Write-Step '输出最终镜像'
$elfFinal = Join-Path $RomBuild 'CH572BLE_ROM.elf'
$mapFinal = Join-Path $RomBuild 'CH572BLE_ROM.map'
if ((Link-Rom -Elf $elfFinal -Map $mapFinal -JtObj $jtObj -JtEntries $entries -GcSections `
              -BaseOverride $linkBaseHex) -ne 0) {
    Write-Err '最终链接失败'; exit 1
}

# ---------------------------------------------------------------------------
# 6. 读取最终镜像大小 -> 生成应用侧头文件
#
#    头文件里要写 ROM 的地址空间（CODE: base - end，多少字节），
#    这些值只有最终链接完才能确定，所以放在这里生成。
#    生成头文件不会再改动镜像 —— 跳转表内容在最终链接时已经定死了。
# ---------------------------------------------------------------------------
$romEnd = 0
$m = Select-String -Path $mapFinal -Pattern '_rom_end_addr\s*=' | Select-Object -First 1
if ($m -and ($m.Line -match '0x([0-9a-fA-F]{8,16})')) {
    $h = $Matches[1]
    $romEnd = [Convert]::ToUInt32($h.Substring($h.Length - 8), 16)
}
if ($romEnd -le $romBaseInt) {
    Write-Err "无法从 map 读出 ROM 结束地址（_rom_end_addr = 0x$('{0:X}' -f $romEnd)）"
    exit 1
}
$romSize  = $romEnd - $romBaseInt
$snvStart = $romBaseInt - $SnvSize          # SNV 区起点 = app 可用空间结束

Write-Step '生成应用侧头文件'
& python (Join-Path $ToolsDir 'rom_gen.py') --map $mapFinal --jt $jtJson --base $romBaseHex `
    --out-asm $jtAsm --out-hdr $outHdr --report $report `
    --rom-size $romSize --app-flash-end $snvStart --snv-addr $snvStart --snv-size $SnvSize 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Err '生成头文件失败'; exit 1 }
Write-Ok "生成应用侧头文件 $outHdr"

# ---------------------------------------------------------------------------
# 7. 生成 hex / bin
# ---------------------------------------------------------------------------
$hex = Join-Path $LibDir 'CH572BLE_ROM.hex'
$bin = Join-Path $LibDir 'CH572BLE_ROM.bin'
# 生成 rom/rom_ram_info.h —— ROM 占用的 RAM 区范围
#
# 为什么需要：ROM 的 .data/.bss 在 RAM 里，但 ROM 没有上电入口，
# 没人替它搬运 .data、清零 .bss。应用必须补上这两件事，
# 并且把这段 RAM 预留出来不被自己的变量占用。
# 地址由 tools/gen_rom_ram_info.py 从链接好的 ROM ELF 里读出，永远准确。
if (Test-Path $elfFinal) {
    & python (Join-Path $ToolsDir 'gen_rom_ram_info.py') `
        $elfFinal (Join-Path $Root 'rom\rom_ram_info.h') --app-flash-end $snvStart --snv-size $SnvSize 2>&1 | ForEach-Object { Write-Info $_ }
    if ($LASTEXITCODE -ne 0) { Write-Err 'RAM metadata generation failed'; exit 1 }
} else {
    Write-Warn2 "找不到 $elfFinal，跳过 rom_ram_info.h 生成"
}

if ((Invoke-Tool -Exe $Objcopy -Arguments @('-O','ihex',$elfFinal,$hex) -Quiet) -ne 0) { exit 1 }
if ((Invoke-Tool -Exe $Objcopy -Arguments @('-O','binary',$elfFinal,$bin) -Quiet) -ne 0) { exit 1 }
Copy-Item $mapFinal (Join-Path $LibDir 'CH572BLE_ROM.map') -Force
# 用 cmd 重定向导出符号表。
# 注意不能写 "*> 文件"：PowerShell 会按 UTF-16LE 落盘，导致后续 Python 工具解析失败。
$symFile = Join-Path $LibDir 'CH572BLE_ROM_SYMS.txt'
# Invoke-Tool 的重定向走 cmd，这里直接把输出重定向到文件更直接：
$symLines = & $Nm --defined-only --extern-only --numeric-sort $elfFinal 2>&1
if ($LASTEXITCODE -ne 0) { Write-Err 'Symbol export failed'; exit 1 }
[System.IO.File]::WriteAllLines($symFile, [string[]]$symLines, (New-Object System.Text.UTF8Encoding($false)))

Write-Ok "生成 $hex"
Write-Ok "生成 $bin"

Write-Host ''
Write-Host '    ---------- ROM 镜像体积 ----------' -ForegroundColor Cyan
& $Size $elfFinal 2>&1 | ForEach-Object { Write-Host "    $_" }
Write-Host ("    hex 文件: {0,10:N0} 字节" -f (Get-Item $hex).Length) -ForegroundColor Cyan
Write-Host ("    bin 文件: {0,10:N0} 字节" -f (Get-Item $bin).Length) -ForegroundColor Cyan

Write-Host ''
Write-Host '    ---------- Flash 分区（协议栈靠后，app 占前面连续空间）----------' -ForegroundColor Cyan
Write-Host ("    app 主体       : 0x00000000 - 0x{0:X8}  （{1:N1} KB，连续一整块）" -f `
            ($snvStart - 1), ($snvStart / 1024)) -ForegroundColor Cyan
Write-Host ("    SNV 绑定信息   : 0x{0:X8} - 0x{1:X8}  （{2:N1} KB，运行期写，不烧录）" -f `
            $snvStart, ($romBaseInt - 1), ($SnvSize / 1024)) -ForegroundColor Cyan
Write-Host ("    ROM 镜像       : 0x{0:X8} - 0x{1:X8}  （{2:N0} 字节 / {3:N1} KB）" -f `
            $romBaseInt, ($romEnd - 1), $romSize, ($romSize / 1024)) -ForegroundColor Cyan
Write-Host  '    芯片固定占用   : 0x0003C000 - 0x0003FFFF  （16 KB，BootLoader + INFO）' -ForegroundColor Cyan
Write-Host ''
Write-Host '    app/SNV/RAM 边界已写入 rom\rom_ram_info.ld；请用配套头文件重编应用。'

# ---------------------------------------------------------------------------
# 7. 镜像头部自检
# ---------------------------------------------------------------------------
$inspect = Join-Path $ToolsDir 'rom_inspect.py'
if (Test-Path $inspect) {
    Write-Host ''
    Write-Host '    ---------- ROM 镜像头部自检 ----------' -ForegroundColor Cyan
    $out = & python $inspect $hex $romBaseHex $entries 2>&1 | Out-String
    $inspectCode = $LASTEXITCODE
    Write-Host $out
    if ($inspectCode -ne 0) { Write-Err 'ROM structure verification failed'; exit 1 }
}

# ---------------------------------------------------------------------------
# 8. 跳转表 / 镜像 / 头文件 三方一致性自检 ★ 必须在 make_rom 里跑 ★
#
# 为什么放在这里而不是只靠 run_tests.ps1：
#   本脚本自称"唯一的 ROM 产物入口"（见文件头）。跳转表错位（表里写的地址
#   与镜像里符号真实地址不一致）是 ROM 方案最容易犯、也最难查的错 —— 它不报错，
#   只是跳到一个合法但错误的地址。rom_inspect.py 只看头部结构，看不到这个。
#   所以生成的镜像必须在这里就地验一遍，不能等到跑测试的时候。
#
# 注意：以前 rom_verify.py 只覆盖到 JT(141)，表尾那 5 个扩展 API
# （TMOS_SystemProcess / CH572BLE_ROM_Init / GetHandles / FlashCommand /
#  TMOS_TimerSet）恰恰是应用真正调用的，却从不核对。现已改为以镜像为基准
# 遍历全部 $entries 项，并用头文件反查"空槽是否被暴露成可调用 API"。
# ---------------------------------------------------------------------------
$verify = Join-Path $ToolsDir 'rom_verify.py'
if (Test-Path $verify) {
    Write-Host ''
    Write-Host '    ---------- 跳转表 / 镜像 / 头文件 一致性 ----------' -ForegroundColor Cyan
    $symFileV = Join-Path $LibDir 'CH572BLE_ROM_SYMS.txt'
    $reportV  = Join-Path $RomBuild 'rom_verify_report.md'
    $out = & python $verify --bin $bin --syms $symFileV --base $romBaseHex `
        --jt-json $jtJson --hdr $outHdr --count $entries --report $reportV 2>&1 | Out-String
    $verifyCode = $LASTEXITCODE
    Write-Host $out
    if ($verifyCode -ne 0) {
        Write-Err 'ROM 跳转表一致性验证失败（详见上面的不匹配明细）'
        exit 1
    }
} else {
    Write-Warn2 "找不到 tools\rom_verify.py，跳过跳转表一致性验证"
}

Write-Host ''
Write-Host 'ROM 镜像构建完成。' -ForegroundColor Green
Write-Host "  镜像    : $hex"
Write-Host "  二进制  : $bin"
Write-Host "  头文件  : $outHdr"
Write-Host "  跳转表  : $report"
Write-Host "  符号表  : $(Join-Path $LibDir 'CH572BLE_ROM_SYMS.txt')"
