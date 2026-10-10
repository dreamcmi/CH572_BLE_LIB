# =============================================================================
#  build_rf_rom_app.ps1 —— 构建「通过固定地址镜像调用纯 RF 库」的应用
#
#  本脚本构建的应用 **不链接任何 RF / BLE 库**：
#    射频功能全部由 lib\CH572RF_ROM.hex（固定地址镜像）提供，
#    应用只通过 include\CH572RF_ROM.h 里的跳转表宏调用它。
#    所以应用自身极小（.text 1,660 B），可独立于镜像更新。
#
#  前置：必须先跑 scripts\make_rf_rom.ps1 生成镜像与头文件
#
#  用法：
#    powershell -ExecutionPolicy Bypass -File scripts\build_rf_rom_app.ps1 -Role both -DebugBuild
#    powershell -ExecutionPolicy Bypass -File scripts\build_rf_rom_app.ps1 -Role TX
# =============================================================================

[CmdletBinding()]
param(
    [ValidateSet('TX', 'RX', 'both')]
    [string]$Role = 'both',

    [switch]$DebugBuild,

    [string]$Toolchain = '',

    [string]$Opt = '-Os'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$ExDir     = Join-Path $Root 'examples\rf_rom_app'
$OutDir    = Join-Path $Root 'build\out'
$ObjDir    = Join-Path $Root 'build\obj\rf_rom_app'
$LogDir    = Join-Path $Root 'build\logs'

function Write-Step([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok([string]$m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn2([string]$m){ Write-Host "    [警告] $m" -ForegroundColor Yellow }
function Write-Err([string]$m)  { Write-Host "    [错误] $m" -ForegroundColor Red }
function Write-Info([string]$m) { Write-Host "    $m" }

# ---------------------------------------------------------------------------
# 1. 工具链
# ---------------------------------------------------------------------------
function Find-Toolchain {
    param([string]$Hint)
    $names = @('riscv-wch-elf-gcc.exe', 'riscv-none-embed-gcc.exe')
    function Pick([string]$dir) {
        if (-not (Test-Path $dir)) { return $null }
        $p = Get-ChildItem -Path $dir -Recurse -Depth 6 -Filter '*-gcc.exe' `
                 -ErrorAction SilentlyContinue |
             Where-Object { $names -contains $_.Name } |
             Sort-Object { $names.IndexOf($_.Name) } | Select-Object -First 1
        if ($p) { return $p.FullName }
        return $null
    }
    if ($Hint) { $r = Pick $Hint; if ($r) { return $r }; Write-Warn2 "指定目录无工具链：$Hint" }
    foreach ($base in @(
        'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12',
        'C:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12',
        'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32',
        'C:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
    )) { $r = Pick $base; if ($r) { return $r } }
    foreach ($n in $names) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return ''
}

Write-Step '探测工具链'
$Gcc = Find-Toolchain -Hint $Toolchain
if (-not $Gcc) { Write-Err '找不到 WCH RISC-V 工具链，请用 -Toolchain 指定'; exit 1 }
$BinDir  = Split-Path -Parent $Gcc
$Prefix  = (Split-Path -Leaf $Gcc) -replace '-gcc\.exe$', ''
$Objcopy = Join-Path $BinDir "$Prefix-objcopy.exe"
$Size    = Join-Path $BinDir "$Prefix-size.exe"
$Nm      = Join-Path $BinDir "$Prefix-nm.exe"
Write-Ok $Gcc
$env:Path = "$BinDir;$env:Path"

# ---------------------------------------------------------------------------
# 2. 依赖检查
# ---------------------------------------------------------------------------
Write-Step '检查依赖'

$RomHex = Join-Path $Root 'lib\CH572RF_ROM.hex'
$RomHdr = Join-Path $Root 'include\CH572RF_ROM.h'
$RamHdr = Join-Path $Root 'rom_rf\rom_rf_ram_info.h'
$RamLd  = Join-Path $Root 'rom_rf\rom_rf_ram_info.ld'
$LdScript = Join-Path $Root 'src\Ld\Link_rf_rom.ld'
$IspLib = Join-Path $Root 'lib\libISP572.a'

foreach ($p in @($RomHex, $RomHdr, $RamHdr, $RamLd, $LdScript)) {
    if (-not (Test-Path $p)) {
        Write-Err "缺少 $p"
        if ($p -like '*RF_ROM*' -or $p -like '*ram_info*') {
            Write-Info '先跑：powershell -ExecutionPolicy Bypass -File scripts\make_rf_rom.ps1'
        }
        exit 1
    }
}
Write-Ok 'RF 镜像 / 镜像头 / RAM 信息 / 链接脚本 齐备'

# ---------------------------------------------------------------------------
# 3. 编译参数
#
#    include 顺序：先 include\rf（CH572rf.h），再 include（CH57x_*.h）。
#    ★ 绝不加 include 里的 BLE 头 ★ —— 它们与 CH572rf.h 的 LL_TX_PWR_* 冲突。
# ---------------------------------------------------------------------------
$ArchFlags = @('-march=rv32imac_zicsr_zifencei', '-mabi=ilp32',
               '-mcmodel=medany', '-msmall-data-limit=8')
$CommonFlags = @($Opt, '-ffunction-sections', '-fdata-sections', '-fno-common',
                 '-Wall', '-Wno-attributes', '-finput-charset=UTF-8',
                 '-fexec-charset=UTF-8', '-g')
if ($DebugBuild) { $CommonFlags += '-DDEBUG' }

$IncFlags = @(
    "-I$(Join-Path $Root 'include\rf')",
    "-I$(Join-Path $Root 'include')",
    "-I$(Join-Path $Root 'rom_rf')",
    "-I$(Join-Path $ExDir 'include')",
    "-I$(Join-Path $Root 'src\StdPeriphDriver')",
    "-I$(Join-Path $Root 'src\RVMSIS')"
)

function Invoke-Tool {
    param([string]$Exe, [string[]]$Arguments, [string]$Caption = '')
    if ($Caption) { Write-Info $Caption }
    $id = [guid]::NewGuid().ToString('N').Substring(0, 8)
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $bat = Join-Path $LogDir "_rfa_$id.bat"
    $log = Join-Path $LogDir "_rfa_$id.log"
    $line = '"' + $Exe + '"'
    foreach ($a in $Arguments) {
        if ($a -match '[\s]') { $line += ' "' + $a + '"' } else { $line += ' ' + $a }
    }
    [System.IO.File]::WriteAllText($bat, "@echo off`r`n$line > `"$log`" 2>&1`r`n",
                                   [System.Text.Encoding]::GetEncoding('GBK'))
    & "$env:ComSpec" /c "`"$bat`"" | Out-Null
    $code = $LASTEXITCODE
    if (Test-Path $log) {
        Get-Content $log -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_ -match 'error|错误')       { Write-Host "    [错误] $_" -ForegroundColor Red }
            elseif ($_ -match 'warning|警告') { Write-Host "    [警告] $_" -ForegroundColor Yellow }
        }
    }
    Remove-Item $bat, $log -Force -ErrorAction SilentlyContinue
    return $code
}

# ---------------------------------------------------------------------------
# 4. 构建一个角色
# ---------------------------------------------------------------------------
function Build-Role {
    param([string]$R, [int]$RoleVal)

    $tag = "rf_rom_app-$($R.ToLower())"
    if ($DebugBuild) { $tag += "-debug" }

    Write-Step "构建 $R 应用（RF_APP_ROLE=$RoleVal）"

    if (Test-Path $ObjDir) { Remove-Item $ObjDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $ObjDir | Out-Null
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

    $defFlags = @("-DRF_APP_ROLE=$RoleVal")
    $objs = @()

    $startupSrc = Join-Path $Root 'src\Startup\startup_CH572.S'
    $startupObj = Join-Path $ObjDir 'startup_CH572.o'
    if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags +
            @('-x','assembler-with-cpp','-c','-o',$startupObj,$startupSrc)) `
            -Caption '汇编启动文件') -ne 0) {
        Write-Err '启动文件编译失败'; return $false
    }
    $objs += $startupObj

    $src = Join-Path $ExDir 'src\rf_rom_app.c'
    $obj = Join-Path $ObjDir 'rf_rom_app.o'
    if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + $CommonFlags + $defFlags +
            $IncFlags + @('-c','-o',$obj,$src)) -Caption '编译 rf_rom_app.c') -ne 0) {
        Write-Err '编译失败：rf_rom_app.c'; return $false
    }
    $objs += $obj

    foreach ($n in @('CH57x_clk.c','CH57x_flash.c','CH57x_gpio.c',
                     'CH57x_sys.c','CH57x_uart.c','CH57x_pwr.c')) {
        $p = Join-Path $Root "src\StdPeriphDriver\$n"
        if (-not (Test-Path $p)) { continue }
        $o = Join-Path $ObjDir (([IO.Path]::GetFileNameWithoutExtension($n)) + '.o')
        if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + $CommonFlags +
                $IncFlags + @('-c','-o',$o,$p)) -Caption "编译 $n") -ne 0) {
            Write-Err "编译失败：$n"; return $false
        }
        $objs += $o
    }

    $elf = Join-Path $OutDir "$tag.elf"
    $map = Join-Path $OutDir "$tag.map"
    $hex = Join-Path $OutDir "$tag.hex"

    # ★ 注意：链接时**不**加 -lCH57xRF ★
    #   这正是"应用不依赖 RF 库"的体现 —— 所有 RF 功能都通过跳转表调用。
    $a = @() + $ArchFlags + @(
        '-nostartfiles',
        "-T$LdScript",
        "-L$(Join-Path $Root 'rom_rf')",
        '-Wl,--gc-sections',
        "-Wl,-Map=$map",
        '--specs=nano.specs', '--specs=nosys.specs',
        '-o', $elf
    ) + $objs + @("-L$(Join-Path $Root 'lib')", '-lISP572')

    if ((Invoke-Tool -Exe $Gcc -Arguments $a -Caption '链接') -ne 0) {
        Write-Err '链接失败'; return $false
    }
    if ((Invoke-Tool -Exe $Objcopy -Arguments @('-O','ihex',$elf,$hex)) -ne 0) {
        Write-Err 'HEX 导出失败'; return $false
    }

    # ---- 关键自检：应用里不允许出现 RF 库的实现符号 ----
    $nmOut = & $Nm $elf 2>&1 | Out-String
    $leak = @()
    foreach ($s in @('RFRole_BasicInit','RFIP_StartTx','RFIP_SetRx','RFEND_DevInit',
                     'BB_LibIRQHandler','LLE_LibIRQHandler')) {
        if ($nmOut -match "(?m)\s[TRD]\s+$s`$") { $leak += $s }
    }

    Write-Host ''
    Write-Host '    ---------- 应用体积 ----------' -ForegroundColor Cyan
    & $Size $elf 2>&1 | ForEach-Object { Write-Host "    $_" }
    Write-Host ("    hex : {0,8:N0} 字节" -f (Get-Item $hex).Length) -ForegroundColor Cyan

    if ($leak.Count -gt 0) {
        Write-Err ("应用里出现了 RF 库实现符号：{0} —— 说明误链接了 libCH57xRF.a" -f ($leak -join ', '))
        return $false
    }
    Write-Ok '自检通过：应用未包含 RF 库实现（全部走跳转表）'
    Write-Ok "生成 $hex"
    return $true
}

# ---------------------------------------------------------------------------
# 5. 执行
# ---------------------------------------------------------------------------
$ok = $true
switch ($Role) {
    'TX'   { $ok = Build-Role -R 'TX' -RoleVal 1 }
    'RX'   { $ok = Build-Role -R 'RX' -RoleVal 0 }
    'both' {
        $ok = (Build-Role -R 'TX' -RoleVal 1)
        if ($ok) { $ok = (Build-Role -R 'RX' -RoleVal 0) }
    }
}
if (-not $ok) { exit 1 }

Write-Host ''
Write-Host 'RF 镜像方式应用构建完成。' -ForegroundColor Green
Write-Host ''
Write-Host '烧录（两个文件！）：' -ForegroundColor Yellow
Write-Host '  ① lib\CH572RF_ROM.hex          → 0x00010000 起   （只烧一次）'
if ($Role -eq 'TX' -or $Role -eq 'both') {
    Write-Host '  ② build\out\rf_rom_app-tx[-debug].hex → 0x00000000 起'
}
if ($Role -eq 'RX' -or $Role -eq 'both') {
    Write-Host '  ② build\out\rf_rom_app-rx[-debug].hex → 0x00000000 起'
}
Write-Host ''
Write-Host '以后只更新应用时，只需重烧 ②，镜像 ① 保持不变。' -ForegroundColor Cyan
