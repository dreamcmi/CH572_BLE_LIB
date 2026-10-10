# =============================================================================
#  make_rf_rom.ps1 —— 生成「纯 RF 库」的固定地址镜像
#
#  与 scripts\make_rom.ps1（BLE 版）的关系：
#    同一套思路（占位链接 → 生成跳转表 → 迭代收敛 → 最终链接 → 出 hex/bin），
#    但**刻意简化**掉 BLE 专有的四件事：
#      1. 没有 UUID 表（那是 BLE GATT 的标准 UUID）
#      2. 不解析 WCH 官方 ABI 头文件（RF 库没有官方 ABI 头）
#      3. 不自造版本串（直接引库自己的 VER_RF_LIB，天然一致）
#      4. 不生成兼容桩（RF 库符号自足，nm -u 为空）
#
#  产物：
#    lib\CH572RF_ROM.hex / .bin / .map / _SYMS.txt    固定地址镜像
#    include\CH572RF_ROM.h                            应用侧头文件（自动生成）
#    rom_rf\rom_rf_ram_info.h / .ld                   镜像占用的 RAM 区
#    build\rom_rf\rom_rf_jt.S                         跳转表汇编（中间产物）
#
#  用法：
#    powershell -ExecutionPolicy Bypass -File scripts\make_rf_rom.ps1
#    powershell -ExecutionPolicy Bypass -File scripts\make_rf_rom.ps1 -RomBase 0x10000
# =============================================================================

[CmdletBinding()]
param(
    # 镜像基址。默认 0x00010000（64 KB 处）
    #   app 应用  : 0x00000 - 0x0FFFF  （64 KB，绰绰有余）
    #   RF 镜像   : 0x10000 - ...
    #   芯片固定  : 0x3C000 - 0x3FFFF
    # 纯 RF 没有 SNV 需求，所以不像 BLE 那样必须留擦除扇区。
    [string]$RomBase = '0x00010000',

    # 优化等级
    [string]$Opt = '-Os',

    # 工具链目录，空则自动探测
    [string]$Toolchain = '',

    # 只探测推荐基址，不生成产物
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---------------------------------------------------------------------------
# 0. 路径
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$ToolsDir  = Join-Path $Root 'tools'
$LibDir    = Join-Path $Root 'lib'
$IncDir    = Join-Path $Root 'include'
$RfIncDir  = Join-Path $IncDir 'rf'
$RfRomDir  = Join-Path $Root 'rom_rf'
$RfBuild   = Join-Path $Root 'build\rom_rf'
$LogDir    = Join-Path $Root 'build\logs'

function Write-Step([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok([string]$m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn2([string]$m){ Write-Host "    [警告] $m" -ForegroundColor Yellow }
function Write-Err([string]$m)  { Write-Host "    [错误] $m" -ForegroundColor Red }
function Write-Info([string]$m) { Write-Host "    $m" }

# ---------------------------------------------------------------------------
# 1. 工具链（优先 MRS2 的 GCC12 精确路径，PATH 只作兜底）
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

    if ($Hint) {
        $r = Pick $Hint
        if ($r) { return $r }
        Write-Warn2 "指定的目录里没有 WCH 工具链：$Hint"
    }
    foreach ($base in @(
        'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12',
        'C:\MounRiver\MounRiver_Studio2\resources\app\resources\win32\components\WCH\Toolchain\RISC-V Embedded GCC12',
        'D:\MounRiver\MounRiver_Studio2\resources\app\resources\win32',
        'C:\MounRiver\MounRiver_Studio2\resources\app\resources\win32'
    )) {
        $r = Pick $base
        if ($r) { return $r }
    }
    foreach ($n in $names) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return ''
}

Write-Step '探测工具链'
$Gcc = Find-Toolchain -Hint $Toolchain
if (-not $Gcc) {
    Write-Err '找不到 WCH RISC-V 工具链。请用 -Toolchain 指定目录。'
    exit 1
}
$BinDir  = Split-Path -Parent $Gcc
$Prefix  = (Split-Path -Leaf $Gcc) -replace '-gcc\.exe$', ''
$Objcopy = Join-Path $BinDir "$Prefix-objcopy.exe"
$Size    = Join-Path $BinDir "$Prefix-size.exe"
$Nm      = Join-Path $BinDir "$Prefix-nm.exe"
$Ar      = Join-Path $BinDir "$Prefix-ar.exe"
Write-Ok $Gcc
$env:Path = "$BinDir;$env:Path"

# ---------------------------------------------------------------------------
# 2. 依赖检查
# ---------------------------------------------------------------------------
Write-Step '检查依赖'
$RfLib = Join-Path $LibDir 'libCH57xRF.a'
foreach ($p in @($RfLib, (Join-Path $RfIncDir 'CH572rf.h'),
                 (Join-Path $RfRomDir 'rom_rf_link.ld'),
                 (Join-Path $RfRomDir 'rom_rf_entry.c'))) {
    if (-not (Test-Path $p)) { Write-Err "缺少 $p"; exit 1 }
}
Write-Ok 'RF 库 / RF 头 / 镜像链接脚本 / 镜像入口 齐备'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Err '需要 Python 3 来生成跳转表与头文件'; exit 1
}

# ---------------------------------------------------------------------------
# 3. 调外部工具
# ---------------------------------------------------------------------------
$env:Path = "$BinDir;$env:Path"

function Invoke-Tool {
    param([string]$Exe, [string[]]$Arguments, [string]$Caption = '')
    if ($Caption) { Write-Info $Caption }
    $id = [guid]::NewGuid().ToString('N').Substring(0, 8)
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $bat = Join-Path $LogDir "_rfr_$id.bat"
    $log = Join-Path $LogDir "_rfr_$id.log"
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
# 4. 编译参数
# ---------------------------------------------------------------------------
$ArchFlags = @(
    '-march=rv32imac_zicsr_zifencei',
    '-mabi=ilp32',
    '-mcmodel=medany',
    '-msmall-data-limit=8'
)
$CommonFlags = @(
    $Opt, '-ffunction-sections', '-fdata-sections', '-fno-common',
    '-Wall', '-Wno-attributes', '-g'
)
# ★ 只加 include\rf，不加 include\ 根目录 ★
#   根目录下的 ch572_ble.h 与 CH572rf.h 对 LL_TX_PWR_* 给了不同数值，
#   同时可见会取到错的值（详见 docs\纯RF收发固件.md 第五节）
$IncFlags = @("-I$RfIncDir", "-I$RfRomDir")

New-Item -ItemType Directory -Force -Path $RfBuild | Out-Null

# ---------------------------------------------------------------------------
# 5. 编译镜像入口 + 解包 RF 库
# ---------------------------------------------------------------------------
Write-Step '编译镜像入口与解包 RF 库'

$entryObj = Join-Path $RfBuild 'rom_rf_entry.o'
if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + $CommonFlags + $IncFlags +
        @('-c', '-o', $entryObj, (Join-Path $RfRomDir 'rom_rf_entry.c'))) -Caption '编译 rom_rf_entry.c') -ne 0) {
    Write-Err '镜像入口编译失败'; exit 1
}

$stageDir = Join-Path $RfBuild 'stage'
if (Test-Path $stageDir) { Remove-Item $stageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stageDir | Out-Null
Copy-Item $RfLib (Join-Path $stageDir 'stack.a') -Force
Push-Location $stageDir
& $Ar x stack.a 2>&1 | Out-Null
Pop-Location
$stackObjs = @(Get-ChildItem $stageDir -Filter '*.o' | ForEach-Object { $_.FullName })
if ($stackObjs.Count -eq 0) { Write-Err 'RF 库解包失败（没有 .o）'; exit 1 }
Write-Ok "RF 库对象 $($stackObjs.Count) 个"

# ---------------------------------------------------------------------------
# 6. 五趟链接
# ---------------------------------------------------------------------------
$romBaseInt = [Convert]::ToUInt32($RomBase, 16)
$romBaseHex = '0x{0:X8}' -f $romBaseInt
$linkBaseHex = $romBaseHex

function Link-Rom {
    param([string]$Elf, [string]$Map, [string]$JtObj, [int]$JtEntries,
          [switch]$GcSections, [string]$BaseOverride)
    $a = @() + $ArchFlags + @(
        '-nostartfiles',
        "-T$(Join-Path $RfRomDir 'rom_rf_link.ld')",
        "-Wl,--defsym=ROM_RF_BASE=$BaseOverride",
        "-Wl,--defsym=ROM_RF_JT_ENTRIES=$JtEntries",
        '-Wl,--no-relax',
        "-Wl,-Map=$Map",
        '--specs=nano.specs', '--specs=nosys.specs',
        '-o', $Elf,
        $entryObj, $JtObj
    ) + $stackObjs
    if ($GcSections) { $a += '-Wl,--gc-sections' }
    return (Invoke-Tool -Exe $Gcc -Arguments $a -Caption '链接')
}

function Get-RomEndFromMap {
    param([string]$MapPath)
    $m = Select-String -Path $MapPath -Pattern '_rom_rf_end_addr\s*=' | Select-Object -First 1
    if ($m -and ($m.Line -match '0x([0-9a-fA-F]{8,16})')) {
        $h = $Matches[1]
        return [Convert]::ToUInt32($h.Substring($h.Length - 8), 16)
    }
    return 0
}

# ---- 趟 1：占位跳转表（跳转表长度先按已知项数，脚本末尾会用真实值交叉校验）----
Write-Step '趟 1：占位链接'
# 占位项数从生成器直接读，避免两处硬编码不同步
# （早先这里手写 14 而生成器实际是 13，占位表长度对不上，跳转表被截断）
$JT_ENTRIES = [int](& python -c "import sys; sys.path.insert(0,r'$ToolsDir'); import gen_rf_jt as g; print(len(g.JT_ENTRIES))")
if ($JT_ENTRIES -le 0) { Write-Err '读不出跳转表项数'; exit 1 }
Write-Info "跳转表占位项数 = $JT_ENTRIES（来自 tools\gen_rf_jt.py）"
$phSrc = Join-Path $RfBuild 'rom_rf_jt_placeholder.S'
$ph = @('    .section .rom_rf_jt,"a",@progbits', '    .align 2',
        '    .globl _rom_rf_jump_table', '_rom_rf_jump_table:')
1..$JT_ENTRIES | ForEach-Object { $ph += '    .word 0' }
$ph += '    .globl _rom_rf_jt_end', '_rom_rf_jt_end:', ''
[IO.File]::WriteAllText($phSrc, ($ph -join "`r`n"), (New-Object Text.UTF8Encoding($false)))

$phObj = Join-Path $RfBuild 'rom_rf_jt_placeholder.o'
if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + @('-x','assembler-with-cpp','-c',
        '-o',$phObj,$phSrc)) -Caption '汇编占位跳转表') -ne 0) {
    Write-Err '占位跳转表汇编失败'; exit 1
}

$map1 = Join-Path $RfBuild 'rom_rf_pass1.map'
$elf1 = Join-Path $RfBuild 'rom_rf_pass1.elf'
if ((Link-Rom -Elf $elf1 -Map $map1 -JtObj $phObj -JtEntries $JT_ENTRIES `
          -BaseOverride $linkBaseHex) -ne 0) {
    Write-Err '趟 1 链接失败'; exit 1
}
Write-Ok '趟 1 完成'

if ($Probe) {
    $probeEnd = Get-RomEndFromMap -MapPath $map1
    $probeSize = $probeEnd - $romBaseInt
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host ' 探测结果' -ForegroundColor Cyan
    Write-Host '============================================================' -ForegroundColor Cyan
    Write-Host (" 趟1（未开 gc，保守上界）: {0:N0} 字节 ({1:N1} KB)" -f $probeSize, ($probeSize / 1024))
    Write-Host ' 开 --gc-sections 后会更小'
    Write-Host ''
    exit 0
}

# ---- 趟 2：导出符号 → 生成真实跳转表 ----
Write-Step '趟 2：生成跳转表与应用侧头文件'

$symFile = Join-Path $LibDir 'CH572RF_ROM_SYMS.txt'
$symLines = & $Nm --defined-only --extern-only --numeric-sort $elf1 2>&1
if ($LASTEXITCODE -ne 0) { Write-Err '符号导出失败'; exit 1 }
[System.IO.File]::WriteAllLines($symFile, [string[]]$symLines,
                                (New-Object System.Text.UTF8Encoding($false)))

$jtAsm  = Join-Path $RfBuild 'rom_rf_jt.S'
$outHdr = Join-Path $IncDir 'CH572RF_ROM.h'
$report = Join-Path $LibDir 'CH572RF_ROM_JT.md'

& python (Join-Path $ToolsDir 'gen_rf_jt.py') --syms $symFile --base $romBaseHex `
    --out-asm $jtAsm --out-hdr $outHdr --report $report 2>&1 |
    ForEach-Object { Write-Info $_ }
if ($LASTEXITCODE -ne 0) { Write-Err '生成跳转表失败'; exit 1 }

$jtObj = Join-Path $RfBuild 'rom_rf_jt.o'
if ((Invoke-Tool -Exe $Gcc -Arguments (@() + $ArchFlags + @('-x','assembler-with-cpp','-c',
        '-o',$jtObj,$jtAsm)) -Caption '汇编真实跳转表') -ne 0) {
    Write-Err '跳转表汇编失败'; exit 1
}

$entries = [int]((Select-String -Path $jtAsm -Pattern '^\s*\.word' -AllMatches).Count)
Write-Info "跳转表项数 = $entries"

# ---- 趟 3：开 gc 链接（跳转表已定，重跑一次让地址收敛）----
Write-Step '趟 3：开 gc 的迭代链接'
$map3 = Join-Path $RfBuild 'rom_rf_pass3.map'
$elf3 = Join-Path $RfBuild 'rom_rf_pass3.elf'
if ((Link-Rom -Elf $elf3 -Map $map3 -JtObj $jtObj -JtEntries $entries -GcSections `
          -BaseOverride $linkBaseHex) -ne 0) {
    Write-Err '趟 3 链接失败'; exit 1
}
Write-Ok '趟 3 完成'

# ---- 趟 4：最终链接 ----
Write-Step '趟 4：最终链接'
$elfFinal = Join-Path $RfBuild 'CH572RF_ROM.elf'
$mapFinal = Join-Path $RfBuild 'CH572RF_ROM.map'
if ((Link-Rom -Elf $elfFinal -Map $mapFinal -JtObj $jtObj -JtEntries $entries -GcSections `
          -BaseOverride $linkBaseHex) -ne 0) {
    Write-Err '最终链接失败'; exit 1
}
$romEnd = Get-RomEndFromMap -MapPath $mapFinal
if ($romEnd -le $romBaseInt) { Write-Err '读不出镜像结束地址'; exit 1 }
$romSize = $romEnd - $romBaseInt

# ---- 出 hex / bin / map / 符号表 ----
$hex = Join-Path $LibDir 'CH572RF_ROM.hex'
$bin = Join-Path $LibDir 'CH572RF_ROM.bin'
if ((Invoke-Tool -Exe $Objcopy -Arguments @('-O','ihex',$elfFinal,$hex)) -ne 0) { exit 1 }
if ((Invoke-Tool -Exe $Objcopy -Arguments @('-O','binary',$elfFinal,$bin)) -ne 0) { exit 1 }
Copy-Item $mapFinal (Join-Path $LibDir 'CH572RF_ROM.map') -Force
$symFinal = & $Nm --defined-only --extern-only --numeric-sort $elfFinal 2>&1
[System.IO.File]::WriteAllLines($symFile, [string[]]$symFinal,
                                (New-Object System.Text.UTF8Encoding($false)))

# ---------------------------------------------------------------------------
# 7. 生成应用侧的 RAM 预留信息
# ---------------------------------------------------------------------------
Write-Step '生成应用侧 RAM 预留信息'

$ramInfoH  = Join-Path $RfRomDir 'rom_rf_ram_info.h'
$ramInfoLd = Join-Path $RfRomDir 'rom_rf_ram_info.ld'
$ramArgs = @((Join-Path $ToolsDir 'gen_rf_ram_info.py'), $elfFinal,
             $ramInfoH, $ramInfoLd, '--app-flash-end', $romBaseHex)
& python @ramArgs 2>&1 | ForEach-Object { Write-Info $_ }
if ($LASTEXITCODE -ne 0) { Write-Err 'RAM 信息生成失败'; exit 1 }

# ---------------------------------------------------------------------------
# 8. 镜像 / 跳转表 / 头文件 三方一致性自检 ★ 就地校验，不等跑测试 ★
#
#    跳转表错位是这类方案最容易犯、也最难查的错 —— 它不报错，
#    只是跳到一个合法但错误的地址。所以生成完必须在这里验一遍。
# ---------------------------------------------------------------------------
$verify = Join-Path $ToolsDir 'verify_rf_rom.py'
if (Test-Path $verify) {
    Write-Host ''
    Write-Host '    ---------- 镜像 / 跳转表 / 头文件 一致性 ----------' -ForegroundColor Cyan
    $vArgs = @($verify, '--bin', $bin, '--syms', $symFile, '--base', $romBaseHex,
               '--hdr', $outHdr, '--ram-hdr', $ramInfoH)
    $vOut = & python @vArgs 2>&1 | Out-String
    $vCode = $LASTEXITCODE
    Write-Host $vOut
    if ($vCode -ne 0) {
        Write-Err 'RF 镜像一致性验证失败（详见上面的不匹配明细）'
        exit 1
    }
} else {
    Write-Warn2 '找不到 tools\verify_rf_rom.py，跳过一致性验证'
}

# ---------------------------------------------------------------------------
# 9. 汇总
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '    ---------- RF 镜像体积 ----------' -ForegroundColor Cyan
& $Size $elfFinal 2>&1 | ForEach-Object { Write-Host "    $_" }
Write-Host ("    hex : {0,10:N0} 字节" -f (Get-Item $hex).Length) -ForegroundColor Cyan
Write-Host ("    bin : {0,10:N0} 字节" -f (Get-Item $bin).Length) -ForegroundColor Cyan

Write-Host ''
Write-Host '    ---------- Flash 分区 ----------' -ForegroundColor Cyan
Write-Host ("    应用可用      : 0x00000000 - 0x{0:X8}  ({1:N1} KB)" -f `
            ($romBaseInt - 1), ($romBaseInt / 1024)) -ForegroundColor Cyan
Write-Host ("    RF 镜像       : 0x{0:X8} - 0x{1:X8}  ({2:N0} 字节)" -f `
            $romBaseInt, ($romEnd - 1), $romSize) -ForegroundColor Cyan
Write-Host ("    余量          : 0x{0:X8} - 0x0003BFFF  ({1:N1} KB)" -f `
            $romEnd, ((0x3C000 - $romEnd) / 1024)) -ForegroundColor Cyan
Write-Host  '    芯片固定      : 0x0003C000 - 0x0003FFFF  (16 KB)' -ForegroundColor Cyan

Write-Host ''
Write-Host 'RF 镜像构建完成。' -ForegroundColor Green
Write-Host "  镜像    : $hex"
Write-Host "  二进制  : $bin"
Write-Host "  头文件  : $outHdr"
Write-Host "  跳转表  : $report"
Write-Host "  RAM 信息: $ramInfoH / $ramInfoLd"
Write-Host ''
Write-Host '下一步：scripts\build_rf_rom_app.ps1  （编译用跳转表的应用）' -ForegroundColor Yellow
