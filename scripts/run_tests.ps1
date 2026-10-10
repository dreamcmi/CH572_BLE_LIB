# =============================================================================
#  run_tests.ps1 —— CH572 BLE 静态库工程自检
#
#  跑下面这些检查，全部通过才算工程健康：
#     1. 静态库存在且符号表合理（能查到关键 API）
#     2. 能力拦截宏有效：误用主机 API 必须编译失败
#     3. 正向用例：只用从机 API 必须编译通过
#     4. 示例固件存在，且指令集与 CH572 兼容
#     5. RAM 余量足够（读 .map 里的 _ebss 与栈顶）
#
#  用法：
#     powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
#     powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1 -Toolchain <目录>
# =============================================================================

[CmdletBinding()]
param(
    [string]$Toolchain = '',
    [string]$Example = 'ble_uart'
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir

$pass = 0
$fail = 0

function Check {
    param([string]$Title, [bool]$Ok, [string]$Detail = '')
    if ($Ok) {
        Write-Host ("  [通过] {0}" -f $Title) -ForegroundColor Green
        $script:pass++
    } else {
        Write-Host ("  [失败] {0}" -f $Title) -ForegroundColor Red
        if ($Detail) { Write-Host ("         {0}" -f $Detail) -ForegroundColor DarkGray }
        $script:fail++
    }
}

function Section([string]$t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

<#
.SYNOPSIS
    读取外部工具（GCC 等）写到文件里的输出，自动判断编码。
.DESCRIPTION
    本工程的头文件里有中文报错串，GCC 在不同环境下可能输出 UTF-8 或 GBK：
      * 加了 -fexec-charset=UTF-8（本工程默认）→ 输出 UTF-8
      * 中文 Windows 上未指定时 → 可能输出 GBK
    这里先按"严格 UTF-8"试解，失败再退回 GBK，两种情况都能正确读出中文。
#>
# ---------------------------------------------------------------------------
# 用「生成 .bat 再执行」的方式调用编译器
#
# 为什么不直接拼 cmd 命令串：MounRiver Studio 2 的工具链路径里带空格
# （...\RISC-V Embedded GCC12\bin\），cmd 解析
#     "带空格路径\gcc.exe" 参数 > 日志 2>&1
# 这种组合时会切错参数（实测会导致编译找不到输入文件）。
# 写进 .bat 交给 cmd 自己读就完全正常。
# ---------------------------------------------------------------------------
function Invoke-Compile {
    param(
        [string]$Src,
        [string]$Obj,
        [string[]]$Flags,
        # 日志路径由调用方指定：调用方之后要用同一个路径去读日志
        [string]$Log = ''
    )

    $id   = [guid]::NewGuid().ToString('N').Substring(0, 8)
    if (-not $Log) { $Log = Join-Path $TmpDir "_c_$id.log" }
    $log  = $Log
    $argF = Join-Path $TmpDir "_c_$id.txt"
    $batF = Join-Path $TmpDir "_c_$id.bat"

    # 直接把完整命令行写进 .bat，不用 GCC 的 @响应文件
    # （它的 @file 语法会把 Windows 路径的反斜杠当转义符吃掉）。
    $all = @() + $Flags + @('-c', '-o', $Obj, $Src)
    $argLine = ($all | Where-Object { "$_".Trim() -ne '' } | ForEach-Object {
        $a = "$_" -replace '\\+$', '\\'
        '"' + ($a -replace '"', '\"') + '"'
    }) -join ' '
    $bat = '@echo off' + "`r`n" + ('"{0}" {1} > "{2}" 2>&1') -f $Gcc, $argLine, $log
    [System.IO.File]::WriteAllText($batF, $bat + "`r`n",
                                   [System.Text.Encoding]::GetEncoding('GBK'))

    & "$env:ComSpec" /c "`"$batF`"" | Out-Null
    $code = $LASTEXITCODE
    Remove-Item -LiteralPath $batF, $argF -Force -ErrorAction SilentlyContinue
    return $code
}

# 读日志文本，自动判别 UTF-8 / GBK
function Read-LogText {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return '' }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0) { return '' }
    try {
        $strict = New-Object System.Text.UTF8Encoding($false, $true)
        return $strict.GetString($bytes)
    } catch {
        return [System.Text.Encoding]::GetEncoding('GBK').GetString($bytes)
    }
}

function Read-ToolLog {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return '' }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0) { return '' }
    try {
        $strict = New-Object System.Text.UTF8Encoding($false, $true)
        return $strict.GetString($bytes)
    } catch {
        return [System.Text.Encoding]::GetEncoding('GBK').GetString($bytes)
    }
}

# ---------------------------------------------------------------------------
# 定位工具链
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
$BinDir = Split-Path -Parent $Gcc
$Prefix = (Get-Item $Gcc).BaseName -replace '-gcc$', ''

Write-Host "工具链: $Gcc" -ForegroundColor Cyan
Write-Host "示例  : $Example" -ForegroundColor Cyan

$ArchFlags = @('-march=rv32imac_zicsr_zifencei','-mabi=ilp32','-mcmodel=medany','-msmall-data-limit=8')
# -finput-charset / -fexec-charset：显式声明"源文件与执行字符集都是 UTF-8"。
# 本工程的头文件里有大量中文注释和中文报错串，在中文 Windows 上如果不指定，
# GCC 会按系统区域猜测字符集，导致 __attribute__((error("中文"))) 的提示乱码。
$CfgFlags  = @('-Os','-ffunction-sections','-fdata-sections','-fno-common','-Wall','-Wno-attributes',
               '-finput-charset=UTF-8','-fexec-charset=UTF-8')
$IncFlags  = @("-I$(Join-Path $Root 'include')",
               "-I$(Join-Path $Root 'src\StdPeriphDriver')",
               "-I$(Join-Path $Root 'src\RVMSIS')")

$TmpDir = Join-Path $Root 'build\test'
New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null

# ---------------------------------------------------------------------------
# 1. 静态库检查
# ---------------------------------------------------------------------------
Section '1. 静态库 libCH572BLE.a'

$lib = Join-Path $Root 'lib\libCH572BLE.a'
Check -Title 'libCH572BLE.a 存在' -Ok (Test-Path $lib) -Detail "路径: $lib"

if (Test-Path $lib) {
    $size = (Get-Item $lib).Length
    Check -Title ("库体积合理（{0:N0} 字节）" -f $size) -Ok ($size -gt 400000) `
          -Detail '小于 400KB 说明可能只打包了 HAL，没把官方协议栈打进去'

    $nmExe = Join-Path $BinDir "$Prefix-nm.exe"
    $syms  = & $nmExe --defined-only --extern-only $lib 2>&1 | Out-String
    foreach ($s in @('BLE_LibInit','TMOS_SystemProcess','GAPRole_PeripheralInit',
                     'CH57x_BLEInit','HAL_Init','GATTServApp_RegisterService')) {
        Check -Title "库里能找到符号 $s" -Ok ($syms -match ("\b" + $s + "\b"))
    }
}

# ---------------------------------------------------------------------------
# 2. 能力拦截：必须编译失败
# ---------------------------------------------------------------------------
Section '2. 能力拦截宏（误用主机 API 必须编译失败）'

$failSrc = Join-Path $Root 'tests\test_should_fail.c'
$failLog = Join-Path $TmpDir 'fail.log'
$failCode = Invoke-Compile -Src $failSrc -Obj (Join-Path $TmpDir 'fail.o') `
                            -Flags (@($ArchFlags + $CfgFlags + $IncFlags)) -Log $failLog
$failText = Read-LogText -Path $failLog

Check -Title 'test_should_fail.c 编译失败（预期）' -Ok ($failCode -ne 0) `
      -Detail '如果编译通过，说明 ch572_ble.h 的能力拦截宏失效了'
Check -Title '错误信息是可读的中文提示' `
      -Ok ($failText -match '_ch572_ble_missing_api' -and $failText -match 'CH572 BLE 固件库不提供该 API') `
      -Detail '期望看到 __attribute__((error(...))) 抛出的中文说明'

if ($failText -match 'GAPRole_CentralInit') {
    Write-Host '         ↑ 报错已正确定位到误用的 API：GAPRole_CentralInit' -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 3. 正向用例：必须编译通过
# ---------------------------------------------------------------------------
Section '3. 正向用例（只用从机 API 必须编译通过）'

$okSrc = Join-Path $Root 'tests\test_should_pass.c'
$okLog = Join-Path $TmpDir 'pass.log'
$okCode = Invoke-Compile -Src $okSrc -Obj (Join-Path $TmpDir 'pass.o') `
                          -Flags (@($ArchFlags + $CfgFlags + $IncFlags)) -Log $okLog
$okText = Read-LogText -Path $okLog

Check -Title 'test_should_pass.c 编译通过' -Ok ($okCode -eq 0) `
      -Detail ((($okText -split "`n" | Where-Object { $_ -match 'error' } | Select-Object -First 3) -join ' | '))
Check -Title '没产生 warning' -Ok ($okText -notmatch 'warning:') `
      -Detail '有 warning 说明头文件或声明有问题'

# ---------------------------------------------------------------------------
# 4. 示例固件 + 指令集
# ---------------------------------------------------------------------------
Section "4. 示例固件 $Example"

$elf = Join-Path $Root "build\out\$Example.elf"
$hex = Join-Path $Root "build\out\$Example.hex"
Check -Title "$Example.elf 存在" -Ok (Test-Path $elf) -Detail '先跑 build.ps1 构建示例'
Check -Title "$Example.hex 存在" -Ok (Test-Path $hex)

if ((Test-Path $elf) -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $isa = Join-Path $Root 'tools\isa_check.py'
    if (Test-Path $isa) {
        $isaOut = & python $isa $elf 2>&1 | Out-String
        $isaCode = $LASTEXITCODE
        Check -Title '指令集与 CH572 兼容（无原子指令/浮点指令）' -Ok ($isaCode -eq 0) `
              -Detail '用 python tools\isa_check.py 手工查看详情'
    }
}

# ---------------------------------------------------------------------------
# 4b. ROM 镜像（hex / bin 形式）
#
#     ROM 方案最容易出错的地方是"跳转表里的地址"与"镜像里符号的真实地址"
#     不一致 —— 两者由不同步骤产生，差一轮迭代就全线错位，而且烧进去才发现。
#     所以这里用 tools/rom_verify.py 做逐项核对。
# ---------------------------------------------------------------------------
Section '4b. ROM 镜像（CH572BLE_ROM.hex / .bin）'

$romHex = Join-Path $Root 'lib\CH572BLE_ROM.hex'
$romBin = Join-Path $Root 'lib\CH572BLE_ROM.bin'
$romHdr = Join-Path $Root 'include\CH572BLE_ROM.h'
$romSym = Join-Path $Root 'lib\CH572BLE_ROM_SYMS.txt'
$jtJson = Join-Path $Root 'build\rom\jt_map.json'

# ROM 基址不再硬编码 —— 从生成的头文件里读，这样改 -RomBase 后自检自动跟上。
# 以前这里写死 0x4000，换布局后就会拿错地址去核对，误报失败。
$RomBase = 0x00004000
if (Test-Path $romHdr) {
    $hm = Select-String -Path $romHdr -Pattern '#define LIB_FLASH_BASE_ADDRESSS\s+0x([0-9A-Fa-f]+)' |
          Select-Object -First 1
    if ($hm) { $RomBase = [Convert]::ToUInt32($hm.Matches[0].Groups[1].Value, 16) }
}
$RomBaseHex = '0x{0:X8}' -f $RomBase
$romSym = Join-Path $Root 'lib\CH572BLE_ROM_SYMS.txt'
$jtJson = Join-Path $Root 'build\rom\jt_map.json'

Check -Title 'CH572BLE_ROM.hex 存在' -Ok (Test-Path $romHex) -Detail '先跑 build.ps1 -Target rom'
Check -Title 'CH572BLE_ROM.bin 存在' -Ok (Test-Path $romBin)
Check -Title 'CH572BLE_ROM.h 存在'   -Ok (Test-Path $romHdr)

$romImage = Join-Path $Root 'build\rom\CH572BLE_ROM.elf'
if (Test-Path $romImage) {
    $romIsa = & python (Join-Path $Root 'tools\isa_check.py') $romImage 2>&1 | Out-String
    Check -Title 'ROM 协议栈镜像指令集与 CH572 兼容' -Ok ($LASTEXITCODE -eq 0) -Detail $romIsa
}

if ((Test-Path $romHex) -and (Get-Command python -ErrorAction SilentlyContinue)) {
    # 镜像头部结构（魔数 / UUID 表 / 跳转表越界项）
    $inspect = Join-Path $Root 'tools\rom_inspect.py'
    if (Test-Path $inspect) {
        $out = & python $inspect $romHex $RomBaseHex 2>&1 | Out-String
        $code = $LASTEXITCODE
        Check -Title 'ROM 镜像头部结构符合 WCH 约定' -Ok ($code -eq 0) `
              -Detail '用 python tools\rom_inspect.py 手工查看详情'
    }

    # 跳转表与符号表逐项核对
    if ((Test-Path $romBin) -and (Test-Path $romSym) -and (Test-Path $jtJson)) {
        $verify = Join-Path $Root 'tools\rom_verify.py'
        if (Test-Path $verify) {
            $args = @($verify, '--bin', $romBin, '--syms', $romSym,
                      '--base', $RomBaseHex, '--jt-json', $jtJson, '--hdr', $romHdr)
            $out = & python @args 2>&1 | Out-String
            $code = $LASTEXITCODE
            # 只把结论行打出来，避免刷屏
            ($out -split "`n") | Where-Object { $_ -match '地址正确|不匹配|结论' } |
                ForEach-Object { Write-Host ("         " + $_.Trim()) -ForegroundColor DarkGray }
            Check -Title '跳转表内地址与镜像符号地址完全一致' -Ok ($code -eq 0) `
                  -Detail 'python tools\rom_verify.py 可查看不匹配明细'
        }
    }
}

# ---------------------------------------------------------------------------
# 4c. ROM 方式的应用固件
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 4b-2. Flash 分区一致性（协议栈靠后、app 占前面连续空间）
#
#   ROM 挪到高位以后，最危险的错误是"SNV 区落在 ROM 里" ——
#   运行期写绑定信息会把协议栈代码擦掉，而且只有上板才会发现。
#   这里把分区关系在编译期就验一遍。
# ---------------------------------------------------------------------------
Section '4b-2. Flash 分区一致性'

# nm 工具（这一节用到了，但原定义点在后面，这里先确保有）
$nmExe = Join-Path $BinDir "$Prefix-nm.exe"

if (Test-Path $romHdr) {
    $hText = Get-Content -LiteralPath $romHdr -Raw

    $mSize = [regex]::Match($hText, '#define LIB_FLASH_SIZE\s+0x([0-9A-Fa-f]+)')
    $mSnv  = [regex]::Match($hText, '#define BLE_SNV_ADDR\s+\(0x([0-9A-Fa-f]+)\)')

    $hdrSize = if ($mSize.Success) { [Convert]::ToUInt32($mSize.Groups[1].Value, 16) } else { 0 }
    $snvAddr = if ($mSnv.Success)  { [Convert]::ToUInt32($mSnv.Groups[1].Value, 16) } else { 0 }
    $romBase = $RomBase

    if ($hdrSize -gt 0 -and (Test-Path $romBin)) {
        $binSize = (Get-Item $romBin).Length
        Check -Title ("头文件 LIB_FLASH_SIZE（{0:N0}）与 .bin 实际大小（{1:N0}）一致" -f $hdrSize, $binSize) `
              -Ok ([Math]::Abs($hdrSize - $binSize) -le 64) `
              -Detail '差异应只是对齐余量；差太多说明头文件不是按当前镜像生成的'
    }

    if ($snvAddr -gt 0) {
        # SNV 大小从链接脚本读，不要写死 ——
        # 之前这里固定 0x1000，SNV 缩到 1 KB 后就误判成与 ROM 重叠。
        $snvSize = 0x1000
        $ldFile = Join-Path $Root 'rom\rom_ram_info.ld'
        if (Test-Path $ldFile) {
            $lm = Select-String -Path $ldFile -Pattern 'SNV_SIZE\s*=\s*0x([0-9A-Fa-f]+)' |
                  Select-Object -First 1
            if ($lm) { $snvSize = [Convert]::ToUInt32($lm.Matches[0].Groups[1].Value, 16) }
        }

        # SNV 是运行期可擦写区，绝不能和 ROM 重叠
        $snvEnd = $snvAddr + $snvSize - 1
        Check -Title ("SNV 区（0x{0:X8}-0x{1:X8}）不与 ROM 区（0x{2:X8} 起）重叠" -f $snvAddr, $snvEnd, $romBase) `
              -Ok ($snvEnd -lt $romBase) `
              -Detail '重叠会导致写绑定信息时擦掉协议栈代码 —— 必须让 SNV 落在 ROM 之前'
        Check -Title ("SNV 区（0x{0:X8}）落在用户 Flash 范围内" -f $snvAddr) `
              -Ok ($snvAddr -ge 0 -and $snvEnd -lt 0x3C000)
        Check -Title ("app 可用区间 0x00000000-0x{0:X8} 非空" -f ($snvAddr - 1)) `
              -Ok ($snvAddr -gt 0x4000) `
              -Detail 'app 可用空间太小，不合理'
    }

    # app 固件不能长到 SNV 区里去
    $appRomElf = Join-Path $Root "build\out\$Example-rom.elf"
    if ((Test-Path $appRomElf) -and $snvAddr -gt 0) {
        $nmTxt = & $nmExe --defined-only $appRomElf 2>&1 | Out-String
        $mEt = [regex]::Match($nmTxt, '([0-9a-fA-F]{8})\s+\S\s+_etext')
        if ($mEt.Success) {
            $etext = [Convert]::ToUInt32($mEt.Groups[1].Value, 16)
            Check -Title ("app 代码结束（0x{0:X8}）未越过 SNV 区（0x{1:X8}）" -f $etext, $snvAddr) `
                  -Ok ($etext -lt $snvAddr) `
                  -Detail 'app 太长，需要缩小或把 ROM/SNV 往后挪'
        }
    }
}

Section "4c. ROM 方式应用固件 $Example-rom"

$romElf = Join-Path $Root "build\out\$Example-rom.elf"
$romFw  = Join-Path $Root "build\out\$Example-rom.hex"
Check -Title "$Example-rom.elf 存在" -Ok (Test-Path $romElf)
Check -Title "$Example-rom.hex 存在" -Ok (Test-Path $romFw)

if (Test-Path $romElf) {
    # ROM 方式下应用不应再包含协议栈代码，体积应该远小于静态库方式
    $nmExe = Join-Path $BinDir "$Prefix-nm.exe"
    $sizeExe = Join-Path $BinDir "$Prefix-size.exe"
    $sizes = & $sizeExe $romElf 2>&1 | Out-String
    $m = [regex]::Match($sizes, '^\s*(\d+)\s+(\d+)\s+(\d+)', 'Multiline')
    if ($m.Success) {
        $text = [int]$m.Groups[1].Value
        Write-Host ("         ROM 方式应用 text = {0:N0} 字节" -f $text) -ForegroundColor DarkGray
        Check -Title 'ROM 方式应用体积显著小于静态库方式（应 < 30KB）' -Ok ($text -lt 30000) `
              -Detail '若超过 30KB，检查是否误链接了 -lCH572BLE'
    }
    # 关键：ROM 方式下不应再把协议栈符号链进来
    $syms = & $nmExe --defined-only $romElf 2>&1 | Out-String
    Check -Title 'ROM 方式应用未重复包含协议栈实现（无 BLE_LibInit）' `
          -Ok ($syms -notmatch '\bBLE_LibInit\b') `
          -Detail '出现 BLE_LibInit 说明又链了一份静态库，体积会白涨'

    # 指令集自检
    $isa = Join-Path $Root 'tools\isa_check.py'
    if (Test-Path $isa) {
        $null = & python $isa $romElf 2>&1 | Out-String
        Check -Title 'ROM 方式应用指令集与 CH572 兼容' -Ok ($LASTEXITCODE -eq 0)
    }
}

# ---------------------------------------------------------------------------
# 5. RAM 余量
# ---------------------------------------------------------------------------
Section '5. RAM 余量（CH572 只有 12 KB）'

$map = Join-Path $Root "build\out\$Example.map"
if (Test-Path $map) {
    $mapText = Get-Content -LiteralPath $map -Raw
    # 注意：不同版本的 ld 在 .map 里打出的地址宽度不一样 ——
    #   binutils 2.3x（MounRiver 的 GCC 12）是 16 位：0x0000000020002A98
    #   更早的版本是  8 位：0x20002A98
    # 所以这里两种都接受，并用 Substring 取后 8 位来解析。
    $ebss  = [regex]::Match($mapText, '(0x[0-9a-fA-F]{8,16})\s+PROVIDE \(_ebss')
    $stack = [regex]::Match($mapText, '(0x[0-9a-fA-F]{8,16})\s+PROVIDE \(_eusrstack')
    if ($ebss.Success -and $stack.Success) {
        # 取后 8 位十六进制解析（兼容 8 位与 16 位两种写法）
        $aHex = $ebss.Groups[1].Value.Substring(2)
        $bHex = $stack.Groups[1].Value.Substring(2)
        $a = [Convert]::ToUInt32($aHex.Substring($aHex.Length - 8), 16)
        $b = [Convert]::ToUInt32($bHex.Substring($bHex.Length - 8), 16)
        $free = [int]$b - [int]$a
        $bssSize = [int]$a - 0x20000000
        Write-Host ("         _ebss      = 0x{0:X8}  （bss 共 {1:N0} 字节）" -f $a, $bssSize) -ForegroundColor DarkGray
        Write-Host ("         _eusrstack = 0x{0:X8}  （RAM 顶端）" -f $b) -ForegroundColor DarkGray
        Check -Title ("链接预留栈空间 {0:N0} 字节" -f $free) -Ok ($free -ge 1024) `
              -Detail '这是链接余量；实际最大栈深度仍需上板测量'
    } else {
        Check -Title '能从 .map 里读出 RAM 布局' -Ok $false -Detail '正则没匹配到 _ebss / _eusrstack'
    }
} else {
    Write-Host '  [跳过] 没有 .map 文件（先构建示例）' -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# ROM release/debug 的启动、RAM、IRQ、ISP 与所有跳转表项
# ---------------------------------------------------------------------------
Section '6. 固化镜像完整性（release / debug）'
$objdump = Join-Path $BinDir "$Prefix-objdump.exe"
foreach ($variant in @("$Example-rom", "$Example-rom-debug")) {
    $appImage = Join-Path $Root "build\out\$variant.elf"
    $mergedHex = Join-Path $Root "build\out\$variant-combined.hex"
    Check -Title "$variant 合并 HEX 存在" -Ok (Test-Path $mergedHex)
    if (Test-Path $appImage) {
        $verifyArgs = @((Join-Path $Root 'tools\verify_fixed_image.py'),
            '--rom', (Join-Path $Root 'build\rom\CH572BLE_ROM.elf'),
            '--app', $appImage, '--ram-header', (Join-Path $Root 'rom\rom_ram_info.h'),
            '--objdump', $objdump, '--check-hex', $mergedHex)
        $verifyOutput = & python @verifyArgs 2>&1 | Out-String
        $verifyCode = $LASTEXITCODE
        Write-Host $verifyOutput.Trim()
        Check -Title "$variant 启动复制、中断、ISP、内存分区有效" -Ok ($verifyCode -eq 0) -Detail $verifyOutput
    } else {
        Check -Title "$variant ELF 存在" -Ok $false -Detail '先分别构建 release 和 -DebugBuild'
    }
}

# ---------------------------------------------------------------------------
# 7. 自检工具自身的回归测试
#
#    为什么要有这一组：make_rom.ps1 现在会在生成镜像后就地调用 rom_verify.py
#    做"跳转表 / 镜像 / 头文件"三方一致性校验。如果 rom_verify.py 自己失灵
#    （遍历范围写错、判定太宽），构建照样绿，错位就会一路烧进芯片。
#    所以这里反向验证：故意改坏槽位，要求它必须报错。
# ---------------------------------------------------------------------------
Section '7. 自检工具回归（防止校验本身失灵）'
$negTest = Join-Path $Root 'tests\test_rom_verify_negative.py'
if (Test-Path $negTest) {
    $negOut = & python $negTest 2>&1 | Out-String
    $negCode = $LASTEXITCODE
    Write-Host $negOut.Trim()
    Check -Title 'rom_verify.py 能抓到 4 类跳转表错位' -Ok ($negCode -eq 0) -Detail $negOut
} else {
    Check -Title 'tests\test_rom_verify_negative.py 存在' -Ok $false
}

$ps1Syntax = Join-Path $Root 'tools\check_ps1_syntax.py'
if (Test-Path $ps1Syntax) {
    $synOut = & python $ps1Syntax 2>&1 | Out-String
    $synCode = $LASTEXITCODE
    Check -Title '构造脚本语法正确（BOM 未被破坏）' -Ok ($synCode -eq 0) -Detail $synOut
} else {
    Check -Title 'tools\check_ps1_syntax.py 存在' -Ok $false
}

# 文档也是产物的一部分：链接失效、标签错配、SVG 缺 viewBox 都算回归。
$htmlCheck = Join-Path $Root 'tools\check_html_docs.py'
if (Test-Path $htmlCheck) {
    $htmlOut = & python $htmlCheck 2>&1 | Out-String
    $htmlCode = $LASTEXITCODE
    Check -Title 'HTML 文档有效（标签/链接/SVG）' -Ok ($htmlCode -eq 0) -Detail $htmlOut
} else {
    Check -Title 'tools\check_html_docs.py 存在' -Ok $false
}

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host ('=' * 60)
if ($fail -eq 0) {
    Write-Host ("全部自检通过（{0} 项）" -f $pass) -ForegroundColor Green
    exit 0
} else {
    Write-Host ("自检结果：通过 {0} 项，失败 {1} 项" -f $pass, $fail) -ForegroundColor Red
    exit 1
}
