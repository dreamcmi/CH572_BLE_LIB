# =============================================================================
#  build.ps1 —— CH572 BLE 静态库工程一键构建脚本
#
#  用法：
#     powershell -ExecutionPolicy Bypass -File scripts\build.ps1
#     powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target lib
#     powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target example -Example ble_uart
#     powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Toolchain <GCC 所在目录>
#
#  说明：
#     本脚本不依赖 make / cmake，只需要一个 RISC-V GCC 工具链即可完成
#     "静态库打包 + 示例工程编译 + hex 生成 + 体积统计" 的全过程。
#
#  工具链：
#     只支持 MounRiver Studio 自带的 WCH 定制工具链
#         riscv-wch-elf-gcc      （MounRiver Studio 2，推荐）
#         riscv-none-embed-gcc   （MounRiver Studio 1）
#     原因：协议栈源码用到了 mcpy 私有指令与 WCH-Interrupt-fast 中断属性，
#     只有官方工具链支持。
# =============================================================================

[CmdletBinding()]
param(
    # 构建目标：all / lib / example / rom / clean
    [ValidateSet('all', 'lib', 'example', 'rom', 'clean')]
    [string]$Target = 'all',

    # 要构建的示例名（examples 下的目录名），all 表示全部
    [string]$Example = 'all',

    # RISC-V GCC 工具链目录（含 *-gcc.exe），留空则自动探测
    [string]$Toolchain = '',

    # 优化等级
    [string]$Opt = '-Os',

    # 额外宏定义，例如 -Defines BLE_MAC=1
    [string[]]$Defines = @(),

    # 打开串口调试输出（定义 DEBUG 宏）。注意参数名不能叫 Debug ——。
    # PRINT 与 printf 的 _write 都靠这个宏启用，不开则串口无输出。
    [switch]$DebugBuild
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---------------------------------------------------------------------------
# 0. 路径准备
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$LogDir    = Join-Path $Root 'build\logs'
$BuildDir = Join-Path $Root 'build'
$ObjDir = Join-Path $BuildDir 'obj'
$OutDir = Join-Path $BuildDir 'out'

$IncDirs = @(
    (Join-Path $Root 'include'),
    (Join-Path $Root 'src\StdPeriphDriver'),
    (Join-Path $Root 'src\RVMSIS'),
    # rom/ 里放着构建时生成的头文件（rom_version.h / rom_ram_info.h），
    # ROM 方式的应用需要包含它们
    (Join-Path $Root 'rom')
) | Where-Object { Test-Path $_ }

function Write-Step([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok([string]$m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn2([string]$m){ Write-Host "    [警告] $m" -ForegroundColor Yellow }
function Write-Err([string]$m)  { Write-Host "    [错误] $m" -ForegroundColor Red }
function Write-Info([string]$m) { Write-Host "    $m" }

# ---------------------------------------------------------------------------
# 1. 探测工具链
# ---------------------------------------------------------------------------
function Find-Toolchain {
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

$Gcc = Find-Toolchain -Hint $Toolchain
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

Write-Step '工具链'
Write-Info "GCC     : $Gcc"
Write-Info "AR      : $Ar"
Write-Info "OBJCOPY : $Objcopy"

# ---------------------------------------------------------------------------
# 2. 编译选项
#
#    -march=rv32imac_zicsr_zifencei
#        CH572 内核为青稞 RISC-V3C（RV32IMBC + 自定义扩展）。
#        rv32imac 是它指令集的超集；zicsr 必须开（启动代码与协议栈大量用 CSR 指令）。
# ---------------------------------------------------------------------------
$ArchFlags = @(
    '-march=rv32imac_zicsr_zifencei',
    '-mabi=ilp32',
    '-mcmodel=medany',
    '-msmall-data-limit=8'
)
$CommonFlags = @(
    $Opt,
    '-ffunction-sections',
    '-fdata-sections',
    '-fno-common',
    '-Wall',
    '-Wno-attributes',
    # 显式声明源文件与执行字符集为 UTF-8：本工程头文件含中文注释与中文报错串，
    # 中文 Windows 上不指定的话 GCC 可能按 GBK 猜测，导致中文提示乱码。
    '-finput-charset=UTF-8',
    '-fexec-charset=UTF-8',
    '-g'
)
foreach ($d in $Defines) { if ($d) { $CommonFlags += "-D$d" } }
if ($DebugBuild) { $CommonFlags += '-DDEBUG' }

$IncludeFlags = @()
foreach ($i in $IncDirs) { $IncludeFlags += "-I$i" }

# ---------------------------------------------------------------------------
# 3. 统一的"运行外部工具并判定成败"函数
#
#    要点：PowerShell 里若直接用管道接住原生命令的输出，$LASTEXITCODE 会被管道
#          命令覆盖，导致误判成败。所以这里先把输出重定向到临时文件，拿到真实
#          退出码后再读文件、按 error/warning 分类着色输出。
# ---------------------------------------------------------------------------
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
# 4. 构建静态库
# ---------------------------------------------------------------------------
function Build-StaticLib {
    Write-Step '构建静态库 libCH572BLE.a'

    $libObjDir = Join-Path $ObjDir 'lib'
    if (Test-Path $libObjDir) { Remove-Item $libObjDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $libObjDir | Out-Null

    # ---- 4.1 编译 HAL 层与调试诊断层（本工程新增的适配代码） ----
    #
    # hal/   : HAL 实现（CH57x_BLEInit、SysTick_Handler 等）
    # debug/ : 运行时诊断（心跳打印、HardFault 现场），只在 DEBUG 下编译
    #
    # 两者都编进来，因为 ROM 方式下协议栈在 ROM 里，
    # 这些应用侧代码必须自己提供。
    $halSrcs = @()
    foreach ($dir in @('hal', 'debug')) {
        $d = Join-Path $Root $dir
        if (Test-Path $d) {
            $halSrcs += Get-ChildItem -Path $d -Filter '*.c' -File | Sort-Object Name
        }
    }
    $halObjs = @()
    foreach ($s in $halSrcs) {
        $o = Join-Path $libObjDir ($s.BaseName + '.o')
        $a = @() + $ArchFlags + $CommonFlags + $IncludeFlags + @('-c', '-o', $o, $s.FullName)
        if ((Invoke-Tool -Exe $Gcc -Arguments $a -Caption "编译 HAL: $($s.Name)") -ne 0) {
            Write-Err "HAL 编译失败：$($s.Name)"; return $false
        }
        $halObjs += ($s.BaseName + '.o')
    }

    # ---- 4.2 解包官方协议栈归档 ----
    #        把官方 .o 一起打进新库，上层只需要 -lCH572BLE 一个库即可。
    Write-Info '解包官方协议栈 libCH572BLE_PERI.a'
    $srcLib = Join-Path $Root 'lib\libCH572BLE_PERI.a'
    if (-not (Test-Path $srcLib)) { Write-Err "找不到官方库：$srcLib"; return $false }

    $stackDir = Join-Path $libObjDir 'stack'
    if (Test-Path $stackDir) { Remove-Item $stackDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $stackDir | Out-Null
    # 先把库复制成不含空格的名字，再切到目标目录用相对路径解包。
    # 这样调用 ar 的命令行里完全没有空格，彻底避开 cmd 的引号处理问题。
    Copy-Item -LiteralPath $srcLib -Destination (Join-Path $stackDir 'stack.a') -Force
    if ((Invoke-Tool -Exe $Ar -Arguments @('x', 'stack.a') -WorkDir $stackDir -Caption 'ar x') -ne 0) {
        Write-Err 'ar 解包失败'; return $false
    }
    Remove-Item -LiteralPath (Join-Path $stackDir 'stack.a') -Force -ErrorAction SilentlyContinue

    $stackObjs = Get-ChildItem -Path $stackDir -Filter '*.o' -File
    Write-Info "协议栈对象文件：$($stackObjs.Count) 个"

    # ---- 4.3 打包 ----
    $outLib = Join-Path $Root 'lib\libCH572BLE.a'
    if (Test-Path $outLib) { Remove-Item $outLib -Force }

    Push-Location $libObjDir
    try {
        $members = @($halObjs) + @($stackObjs | ForEach-Object { 'stack/' + $_.Name })
        if ((Invoke-Tool -Exe $Ar -Arguments (@('rcs', $outLib) + $members) -Caption 'ar rcs（打包）') -ne 0) {
            Write-Err 'ar 打包失败'; return $false
        }
    } finally { Pop-Location }

    Write-Ok ('生成 {0}（{1:N0} 字节）' -f $outLib, (Get-Item $outLib).Length)

    # ---- 4.4 导出符号清单，便于排查链接问题 ----
    $symFile = Join-Path $BuildDir 'libCH572BLE_symbols.txt'
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    & $Nm --defined-only --extern-only $outLib *> $symFile
    $n = (Get-Content -LiteralPath $symFile -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Ok "导出符号表：$symFile （$n 行）"

    return $true
}

# ---------------------------------------------------------------------------
# 5. 构建示例工程
# ---------------------------------------------------------------------------
function Build-Example {
    param([string]$Name, [switch]$RomMode)

    $exRoot = Join-Path $Root "examples\$Name"
    if (-not (Test-Path $exRoot)) { Write-Err "示例不存在：$Name"; return $false }

    $suffix = if ($DebugBuild) { '-debug' } else { '' }
    $tag = if ($RomMode) { "$Name-rom$suffix" } else { "$Name$suffix" }
    if ($RomMode) {
        Write-Step "构建示例：$Name（ROM 镜像方式）"
    } else {
        Write-Step "构建示例：$Name（静态库方式）"
    }

    # 两种方式的对象分开放，避免互相覆盖
    $exObjDir = Join-Path $ObjDir $tag
    if (Test-Path $exObjDir) { Remove-Item $exObjDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $exObjDir | Out-Null

    $exInc = @( (Join-Path $exRoot 'include') ) | Where-Object { Test-Path $_ }
    $exIncludeFlags = @() + $IncludeFlags + ($exInc | ForEach-Object { "-I$_" })

    # ROM 方式需要定义 CH572BLE_ROM=1，CONFIG.h 据此切换到 ROM 头文件
    $exDefines = @()
    if ($RomMode) { $exDefines += '-DCH572BLE_ROM=1' }
    if ($DebugBuild)   { $exDefines += '-DDEBUG' }

    # ---- 5.1 启动文件 ----
    $startupObj = Join-Path $exObjDir 'startup_CH572.o'
    $a = @() + $ArchFlags + $exDefines + @('-x', 'assembler-with-cpp', '-c', '-o', $startupObj,
                              (Join-Path $Root 'src\Startup\startup_CH572.S'))
    if ((Invoke-Tool -Exe $Gcc -Arguments $a -Caption '汇编启动文件 startup_CH572.S') -ne 0) {
        Write-Err '启动文件编译失败'; return $false
    }
    $objs = @($startupObj)

    # ---- 5.2 示例源码 ----
    #     两种方式各有一个 main：静态库用 main.c，ROM 用 main_rom.c。
    #     两者都定义 MEM_BUF，不能同时参与链接，所以要按方式挑一个。
    $allSrcs = @( Get-ChildItem -Path (Join-Path $exRoot 'src') -Filter '*.c' -File -ErrorAction SilentlyContinue )
    $srcs = @()
    foreach ($s in $allSrcs) {
        if ($RomMode) {
            if ($s.BaseName -eq 'main') { continue }
        } else {
            if ($s.BaseName -eq 'main_rom') { continue }
        }
        $srcs += $s
    }

    # ---- 5.3 外设驱动 ----
    if (-not (Test-Path (Join-Path $exRoot '.nostdperiph'))) {
        foreach ($n in @('CH57x_clk.c','CH57x_flash.c','CH57x_gpio.c','CH57x_sys.c','CH57x_uart.c','CH57x_pwr.c')) {
            $p = Join-Path $Root "src\StdPeriphDriver\$n"
            if (Test-Path $p) { $srcs += Get-Item $p }
        }
    }

    # ---- 5.4 HAL 层与调试诊断层 ----
    #     静态库方式下 HAL 已经打包进 libCH572BLE.a；
    #     ROM 方式下 HAL 要留在应用里（它负责填 bleConfig_t 并注册中断钩子）。
    #
    #     debug/ 里的诊断代码（心跳打印、HardFault 现场）**两种方式都编进应用**，
    #     不受 ROM_MODE 影响 —— 它们是排查工具，跟协议栈放在哪无关。
    $extraDirs = @()
    if ($RomMode) { $extraDirs += 'hal' }
    $extraDirs += 'debug'

    foreach ($dir in $extraDirs) {
        $d = Join-Path $Root $dir
        if (Test-Path $d) {
            $srcs += @( Get-ChildItem -Path $d -Filter '*.c' -File | Sort-Object Name )
        }
    }

    foreach ($s in $srcs) {
        $o = Join-Path $exObjDir ($s.BaseName + '.o')
        $a = @() + $ArchFlags + $CommonFlags + $exDefines + $exIncludeFlags + @('-c', '-o', $o, $s.FullName)
        if ((Invoke-Tool -Exe $Gcc -Arguments $a -Caption "编译 $($s.Name)") -ne 0) {
            Write-Err "编译失败：$($s.Name)"; return $false
        }
        $objs += $o
    }

    # ---- 5.5 链接 ----
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $elf = Join-Path $OutDir "$tag.elf"
    $map = Join-Path $OutDir "$tag.map"

    if ($RomMode) {
        # ROM 方式：协议栈代码已经在 ROM 镜像里，**不能再链接 -lCH572BLE**，
        # 否则等于把静态库版协议栈又链一份进来，体积白涨还可能符号冲突。
        $ld = Join-Path $Root 'src\Ld\Link_rom.ld'
        if (-not (Test-Path $ld)) {
            Write-Warn2 '找不到 src\Ld\Link_rom.ld，暂用普通 Link.ld'
            $ld = Join-Path $Root 'src\Ld\Link.ld'
        }
    } else {
        $ld = Join-Path $Root 'src\Ld\Link.ld'
    }

    $linkArgs = @() + $ArchFlags + @(
        '-nostartfiles',
        "-T$ld",
        # 让链接脚本里的 INCLUDE 能找到 rom/ 下生成的文件
        # （Link_rom.ld 会 INCLUDE rom_ram_info.ld，那里写着 ROM 占用的 RAM 区）
        "-L$(Join-Path $Root 'rom')",
        '-Wl,--gc-sections',
        "-Wl,-Map=$map",
        '--specs=nano.specs',
        '--specs=nosys.specs',
        '-o', $elf
    ) + $objs + @( "-L$(Join-Path $Root 'lib')" )

    if (-not $RomMode) {
        $linkArgs += @('-lCH572BLE', '-lISP572')
    }

    if ((Invoke-Tool -Exe $Gcc -Arguments $linkArgs -Caption '链接') -ne 0) {
        Write-Err '链接失败'; return $false
    }

    # ---- 5.6 生成 hex 与体积报告 ----
    #    文件名用 $tag（ROM 方式带 -rom 后缀），否则两种方式会互相覆盖
    $hex = Join-Path $OutDir "$tag.hex"
    if ((Invoke-Tool -Exe $Objcopy -Arguments @('-O', 'ihex', $elf, $hex)) -ne 0) {
        Write-Err 'HEX export failed'; return $false
    }
    if ($RomMode) {
        $combined = Join-Path $OutDir "$tag-combined.hex"
        $verifyArgs = @((Join-Path $Root 'tools\verify_fixed_image.py'),
            '--rom', (Join-Path $Root 'build\rom\CH572BLE_ROM.elf'), '--app', $elf,
            '--ram-header', (Join-Path $Root 'rom\rom_ram_info.h'),
            '--objdump', (Join-Path $BinDir "$Prefix-objdump.exe"), '--out', $combined)
        if ((Invoke-Tool -Exe 'python' -Arguments $verifyArgs -Caption 'Verify fixed image and create combined HEX') -ne 0) {
            Write-Err 'Fixed image verification failed'; return $false
        }
        Write-Ok "Combined firmware: $combined"
    }
    Write-Ok "生成固件 $hex"

    Write-Host ''
    Write-Host '    ---------- 固件体积 ----------' -ForegroundColor Cyan
    & $Size $elf 2>&1 | ForEach-Object { Write-Host "    $_" }

    Write-Host ''
    Write-Host '    ---------- 占用最大的 20 个符号 ----------' -ForegroundColor Cyan
    & $Nm --print-size --size-sort --radix=d $elf 2>&1 |
        Select-Object -Last 20 | ForEach-Object { Write-Host "    $_" }

    Write-Host ''
    Write-Host ('    提示：CH572 的 RAM 只有 12KB，请在 build\out\{0}.map 里确认 _ebss 与 .stack 之间仍有余量。' -f $Name) -ForegroundColor Yellow

    # ---- 5.7 指令集兼容性自检 ----
    #    CH572 内核是 RV32IMBC：既没有 A 扩展（原子指令）也没有浮点单元。
    #    用通用 RISC-V 工具链编译时，必须确认产物里没混进这两类指令，
    #    否则烧进芯片会直接跑飞。这里调用工程自带的检查脚本。
    $isaScript = Join-Path $Root 'tools\isa_check.py'
    if ((Test-Path $isaScript) -and (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host ''
        Write-Host '    ---------- 指令集兼容性自检 ----------' -ForegroundColor Cyan
        # 用 Out-String 收拢输出再写回，避免 Python 的多行 stdout
        # 在 PowerShell 的格式化管道里被吞掉。
        $isaOut  = & python $isaScript $elf 2>&1 | Out-String
        $isaCode = $LASTEXITCODE
        Write-Host $isaOut
        if ($isaCode -ne 0) {
            Write-Warn2 '指令集自检发现可疑指令，请人工确认后再烧录！'
        }
    }

    return $true
}

function Get-ExampleNames {
    param([string]$Which)
    if ($Which -ne 'all') { return @($Which) }
    $d = Join-Path $Root 'examples'
    if (-not (Test-Path $d)) { return @() }
    # 带 .rfonly 标记的目录不是 BLE 示例（例如 examples\rf_rom_app 走纯 RF
    # 固定地址镜像路线，不链接 RF/BLE 库且刻意不包含 BLE 头），本脚本不处理它们，
    # 由 scripts\make_rf_rom.ps1 + scripts\build_rf_rom_app.ps1 负责。
    # 详见 examples\rf_rom_app\.rfonly
    return @( Get-ChildItem -Path $d -Directory |
              Where-Object { -not (Test-Path (Join-Path $_.FullName '.rfonly')) } |
              Select-Object -ExpandProperty Name )
}

# ---------------------------------------------------------------------------
# 6. 主流程
# ---------------------------------------------------------------------------
switch ($Target) {
    'clean' {
        Write-Step '清理构建产物'
        if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }
        Write-Ok '已清理 build 目录'
    }

    'lib' {
        if (-not (Build-StaticLib)) { exit 1 }
        Write-Host "`n静态库构建完成：$Root\lib\libCH572BLE.a" -ForegroundColor Green
    }

    'example' {
        if (-not (Test-Path (Join-Path $Root 'lib\libCH572BLE.a'))) {
            Write-Warn2 '静态库尚不存在，先构建静态库'
            if (-not (Build-StaticLib)) { exit 1 }
        }
        $ok = $true
        foreach ($n in (Get-ExampleNames -Which $Example)) {
            if (-not (Build-Example -Name $n)) { $ok = $false }
            # 同一份示例再编一遍 ROM 方式，方便直接对比两种方式的体积
            if (-not (Build-Example -Name $n -RomMode)) { $ok = $false }
        }
        if (-not $ok) { exit 1 }
        Write-Host "`n示例构建完成（静态库方式 + ROM 镜像方式），产物在 build\out 下。" -ForegroundColor Green
    }

    'rom' {
        # 只构建 ROM 镜像 + ROM 方式的示例
        # 注意：要把工具链参数透传给 make_rom.ps1，否则它自己探测可能失败
        $romScript = Join-Path $ScriptDir 'make_rom.ps1'
        $romArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $romScript)
        if ($Toolchain) { $romArgs += @('-Toolchain', $Toolchain) }
        if ((Invoke-Tool -Exe 'powershell' -Arguments $romArgs -Caption '构建 ROM 镜像') -ne 0) {
            Write-Err 'ROM 镜像构建失败'; exit 1
        }
        $ok = $true
        foreach ($n in (Get-ExampleNames -Which $Example)) {
            if (-not (Build-Example -Name $n -RomMode)) { $ok = $false }
        }
        if (-not $ok) { exit 1 }
        Write-Host "`nROM 方式示例构建完成。" -ForegroundColor Green
        Write-Host "  ROM 镜像: $(Join-Path $Root 'lib\CH572BLE_ROM.hex')"
        Write-Host "  应用固件: $OutDir\*-rom.hex"
    }

    'all' {
        # 静态库方式
        if (-not (Build-StaticLib)) { exit 1 }

        # ROM 镜像方式（协议栈固化到固定地址，应用只调用绝对地址）
        $romScript = Join-Path $ScriptDir 'make_rom.ps1'
        $romArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $romScript)
        if ($Toolchain) { $romArgs += @('-Toolchain', $Toolchain) }
        if ((Invoke-Tool -Exe 'powershell' -Arguments $romArgs -Caption '构建 ROM 镜像') -ne 0) {
            Write-Err 'ROM 镜像构建失败'; exit 1
        }

        $ok = $true
        foreach ($n in (Get-ExampleNames -Which $Example)) {
            # 同一份示例编两种方式，方便直接对比体积与验证一致性
            if (-not (Build-Example -Name $n))          { $ok = $false }
            if (-not (Build-Example -Name $n -RomMode)) { $ok = $false }
        }
        if (-not $ok) { exit 1 }

        Write-Host "`n全部构建完成。" -ForegroundColor Green
        Write-Host "  静态库      : $Root\lib\libCH572BLE.a"
        Write-Host "  ROM 库镜像  : $Root\lib\CH572BLE_ROM.hex / .bin"
        Write-Host "  ROM 应用头  : $Root\include\CH572BLE_ROM.h"
        Write-Host "  应用固件    : $OutDir\*.hex"
        Write-Host "                  （ble_uart.hex 为静态库方式，ble_uart-rom.hex 为 ROM 方式）"
    }
}
