# =============================================================================
#  make_mrs_project.ps1 —— 为示例工程生成 MounRiver Studio ROM/static 工程文件
#
#  背景：
#      本工程的主构建方式是 scripts\build.ps1（命令行，可完全复现、可脚本化）。
#      但很多同事习惯在 MounRiver Studio（MRS）里点"编译/下载"，所以这里以
#      WCH 官方 CH572 Peripheral 示例的 .cproject / .project 为模板，把
#      「源文件选择 / 搜索路径 / 库名 / 链接脚本 / 宏定义 / 输出名」替换成
#      本工程的配置。默认生成 ROM release；用 -StaticMode 选择静态库模式，
#      用 -DebugBuild 打开 DEBUG 串口诊断。
#
#  用法：
#      powershell -ExecutionPolicy Bypass -File scripts\make_mrs_project.ps1
#      powershell -ExecutionPolicy Bypass -File scripts\make_mrs_project.ps1 -SdkDir "D:\CH572EVT-1.4"
#      powershell -ExecutionPolicy Bypass -File scripts\make_mrs_project.ps1 -Force
#      powershell -ExecutionPolicy Bypass -File scripts\make_mrs_project.ps1 -DebugBuild -Force
#      powershell -ExecutionPolicy Bypass -File scripts\make_mrs_project.ps1 -StaticMode -Force
#
#  产物（放在 examples\<示例名>\ 下）：.project / .cproject
#
#  目录关系（很重要）：
#      <ROOT>\                      （本工程根目录）
#        |-- include\  hal\  lib\  src\
#        |-- examples\ble_uart\      （MRS 工程所在位置）
#      工程里用 PARENT-2-PROJECT_LOC 指回 <ROOT>，
#      整个 CH572_BLE_LIB 目录可以整体拷贝到任何路径，工程依然有效。
#
#  实现说明：
#      刻意不用正则替换 option 内容 —— 那段 XML 里同时存在
#      "c.compiler.include.paths" 和 "c.compiler.include.files" 两个相似 id，
#      正则容易打错目标。这里改成"定位 id 字符串 -> 找到该 option 的起止 ->
#      整段替换"，逻辑直白且可验证。ROM 原生 MRS 构建不自动合并镜像：先用
#      scripts\build.ps1 -Target rom 生成元数据并执行 fixed-image 校验。
# =============================================================================

[CmdletBinding()]
param(
    [string]$Example = 'ble_uart',
    [string]$SdkDir = '',
    [switch]$Force,
    [switch]$RomMode,
    [switch]$StaticMode,
    [switch]$DebugBuild
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$ExDir     = Join-Path $Root "examples\$Example"
if ($RomMode -and $StaticMode) { throw '不能同时指定 -RomMode 与 -StaticMode。' }
$RomTarget = -not $StaticMode
$BuildTag  = if ($RomTarget) { "$Example-rom" } else { $Example }
if ($DebugBuild) { $BuildTag += '-debug' }

if (-not (Test-Path $ExDir)) {
    Write-Host "[错误] 示例目录不存在: $ExDir" -ForegroundColor Red
    exit 1
}

if ($RomTarget) {
    $romRamHeader = Join-Path $Root 'rom\rom_ram_info.h'
    $romRamLinker = Join-Path $Root 'rom\rom_ram_info.ld'
    if ((-not (Test-Path $romRamHeader)) -or (-not (Test-Path $romRamLinker))) {
        $romBuilder = Join-Path $ScriptDir 'make_rom.ps1'
        if (-not (Test-Path $romBuilder)) {
            throw "缺少 ROM RAM 元数据，且找不到 ROM 构建脚本：$romBuilder"
        }
        Write-Host '[ROM] 缺少 RAM 元数据，先调用 make_rom.ps1 生成固定地址镜像和元数据。' -ForegroundColor Cyan
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $romBuilder
        if ($LASTEXITCODE -ne 0) { throw 'make_rom.ps1 执行失败，未生成 MRS 工程。' }
    }
    if ((-not (Test-Path $romRamHeader)) -or (-not (Test-Path $romRamLinker))) {
        throw 'ROM RAM 元数据仍然缺失；请先运行 scripts\make_rom.ps1。'
    }
}

function LV([string]$v) {
    # 生成一个 listOptionValue；XML 属性里引号要写成 &quot;
    return '<listOptionValue builtIn="false" value="&quot;' + $v + '&quot;"/>'
}

function List-Values([string[]]$Values) {
    return (($Values | ForEach-Object { LV $_ }) -join '')
}

# ---------------------------------------------------------------------------
# 把 .cproject 中指定 id 所在的整个 <option ...>...</option> 内容替换掉。
# $IdKeyword 用完整 id 前缀（含选项名与编号）以唯一定位。
# ---------------------------------------------------------------------------
function Set-OptionContent {
    param(
        [string]$Text,
        [string]$IdKeyword,
        [string]$NewBody
    )

    $keyPos = $Text.IndexOf($IdKeyword)
    if ($keyPos -lt 0) {
        throw "未找到 option id: $IdKeyword"
    }

    # 向左找到该 option 起始的 '<'
    $start = $Text.LastIndexOf('<', $keyPos)
    if ($start -lt 0) { $start = $keyPos }

    # option 可能是自闭合 <option .../>，也可能是成对 <option ...>...</option>
    $gtPos = $Text.IndexOf('>', $keyPos)
    if ($gtPos -lt 0) {
        throw "option 起始标签不完整: $IdKeyword"
    }

    $selfClosed = ($Text[$gtPos - 1] -eq '/')

    if ($selfClosed) {
        $head = $Text.Substring($start, $gtPos - $start - 1)      # 去掉结尾的 '/'
        $new  = $head + '>' + $NewBody + '</option>'
        $out  = $Text.Substring(0, $start) + $new + $Text.Substring($gtPos + 1)
    } else {
        $endTag = $Text.IndexOf('</option>', $gtPos)
        if ($endTag -lt 0) {
            throw "找不到 </option>: $IdKeyword"
        }
        $head = $Text.Substring($start, $gtPos - $start + 1)
        $out  = $Text.Substring(0, $start) + $head + $NewBody + $Text.Substring($endTag)
    }

    $emptyValue = if ($NewBody.Length -eq 0) { 'true' } else { 'false' }
    $outEnd = $out.IndexOf('>', $start)
    $outTag = $out.Substring($start, $outEnd - $start + 1)
    if ([regex]::IsMatch($outTag, 'IS_VALUE_EMPTY="(true|false)"')) {
        $outTag = [regex]::Replace($outTag, 'IS_VALUE_EMPTY="(true|false)"', ('IS_VALUE_EMPTY="' + $emptyValue + '"'), 1)
        $out = $out.Substring(0, $start) + $outTag + $out.Substring($outEnd + 1)
    }

    Write-Host "  [补丁] $IdKeyword"
    return $out
}

function Set-SourceEntries {
    param([string]$Text, [string]$Entries)
    $start = $Text.IndexOf('<sourceEntries>', [System.StringComparison]::Ordinal)
    $end = $Text.IndexOf('</sourceEntries>', $start, [System.StringComparison]::Ordinal)
    if ($start -lt 0 -or $end -lt 0) { throw '模板中找不到完整的 <sourceEntries>。' }
    $end += '</sourceEntries>'.Length
    return $Text.Substring(0, $start) + '<sourceEntries>' + $Entries + '</sourceEntries>' + $Text.Substring($end)
}

function Set-FirstTagAttribute {
    param([string]$Text, [string]$TagName, [string]$Attribute, [string]$Value)
    $start = $Text.IndexOf("<$TagName ", [System.StringComparison]::Ordinal)
    if ($start -lt 0) { throw "模板中找不到 <$TagName>。" }
    $end = $Text.IndexOf('>', $start)
    if ($end -lt 0) { throw "<$TagName> 起始标签不完整。" }
    $tag = $Text.Substring($start, $end - $start + 1)
    $pattern = '\b' + [regex]::Escape($Attribute) + '="[^"]*"'
    if (-not [regex]::IsMatch($tag, $pattern)) { throw "<$TagName> 缺少 $Attribute 属性。" }
    $escaped = [System.Security.SecurityElement]::Escape($Value)
    $tag = [regex]::Replace($tag, $pattern, ($Attribute + '="' + $escaped + '"'), 1)
    return $Text.Substring(0, $start) + $tag + $Text.Substring($end + 1)
}

function Set-AssemblerDefines {
    param([string]$Text, [string]$Body)
    $id = 'option.assembler.defs.181380015'
    $isEmpty = if ($Body.Length -eq 0) { 'true' } else { 'false' }
    if ($Text.IndexOf($id, [System.StringComparison]::Ordinal) -lt 0) {
        $anchor = '<inputType id="ilg.gnumcueclipse.managedbuild.cross.riscv.tool.assembler.input.126366858"'
        $pos = $Text.IndexOf($anchor, [System.StringComparison]::Ordinal)
        if ($pos -lt 0) { throw '模板汇编器缺少 inputType，无法添加汇编宏选项。' }
        $option = '<option IS_BUILTIN_EMPTY="false" IS_VALUE_EMPTY="' + $isEmpty + '" id="ilg.gnumcueclipse.managedbuild.cross.riscv.option.assembler.defs.181380015" name="Defined symbols (-D)" superClass="ilg.gnumcueclipse.managedbuild.cross.riscv.option.assembler.defs" useByScannerDiscovery="false" valueType="definedSymbols"/>' + "`r`n`t`t`t`t`t`t`t`t"
        $Text = $Text.Insert($pos, $option)
    }
    $Text = Set-OptionContent -Text $Text -IdKeyword $id -NewBody $Body
    $idPos = $Text.IndexOf($id, [System.StringComparison]::Ordinal)
    $start = $Text.LastIndexOf('<option', $idPos, [System.StringComparison]::Ordinal)
    $end = $Text.IndexOf('>', $start)
    $tag = $Text.Substring($start, $end - $start + 1)
    $tag = [regex]::Replace($tag, 'IS_VALUE_EMPTY="(true|false)"', ('IS_VALUE_EMPTY="' + $isEmpty + '"'), 1)
    return $Text.Substring(0, $start) + $tag + $Text.Substring($end + 1)
}

# ---------------------------------------------------------------------------
# 1. 找官方 .cproject 模板
# ---------------------------------------------------------------------------
function Find-Template {
    param([string]$Hint)
    if ($Hint -and (Test-Path $Hint)) {
        $p = Join-Path $Hint 'EVT\EXAM\BLE\Peripheral\.cproject'
        if (Test-Path $p) { return $p }
    }
    $cands = @(
        (Join-Path (Split-Path -Parent $Root) 'CH572EVT-1.4\EVT\EXAM\BLE\Peripheral\.cproject'),
        'C:\Users\DarrenCheng\Desktop\CH570StaicBle\CH572EVT-1.4\EVT\EXAM\BLE\Peripheral\.cproject',
        (Join-Path $ExDir '.cproject')
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    return $null
}

$template = Find-Template -Hint $SdkDir
if (-not $template) {
    Write-Host '[错误] 找不到 .cproject 模板，请用 -SdkDir 指定 CH572EVT 解压目录。' -ForegroundColor Red
    exit 1
}
Write-Host "使用模板: $template"

$rawTemplate = [System.IO.File]::ReadAllBytes($template)
try {
    $inputEncoding = New-Object System.Text.UTF8Encoding($false, $true)
    $cproj = $inputEncoding.GetString($rawTemplate)
} catch {
    $inputEncoding = [System.Text.Encoding]::GetEncoding('GBK')
    $cproj = $inputEncoding.GetString($rawTemplate)
}
$outputEncoding = New-Object System.Text.UTF8Encoding($false)

# ---------------------------------------------------------------------------
# 2. 生成 .project
# ---------------------------------------------------------------------------
$xmlExample = [System.Security.SecurityElement]::Escape($Example)
$modeLabel = if ($RomTarget) { 'ROM' } else { 'static library' }
$project = @"
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<projectDescription>
	<name>$xmlExample</name>
	<comment>CH572 BLE $modeLabel 工程示例（由 make_mrs_project.ps1 生成）</comment>
	<projects>
	</projects>
	<buildSpec>
		<buildCommand>
			<name>org.eclipse.cdt.managedbuilder.core.genmakebuilder</name>
			<triggers>clean,full,incremental,</triggers>
			<arguments>
			</arguments>
		</buildCommand>
		<buildCommand>
			<name>org.eclipse.cdt.managedbuilder.core.ScannerConfigBuilder</name>
			<triggers>full,incremental,</triggers>
			<arguments>
			</arguments>
		</buildCommand>
	</buildSpec>
	<natures>
		<nature>org.eclipse.cdt.core.cnature</nature>
		<nature>org.eclipse.cdt.managedbuilder.core.managedBuildNature</nature>
		<nature>org.eclipse.cdt.managedbuilder.core.ScannerConfigNature</nature>
	</natures>
	<linkedResources>
		<link>
			<name>ble_include</name>
			<type>2</type>
			<locationURI>PARENT-2-PROJECT_LOC/include</locationURI>
		</link>
		<link>
			<name>ble_hal</name>
			<type>2</type>
			<locationURI>PARENT-2-PROJECT_LOC/hal</locationURI>
		</link>
		<link>
			<name>ble_lib</name>
			<type>2</type>
			<locationURI>PARENT-2-PROJECT_LOC/lib</locationURI>
		</link>
			<link>
				<name>ble_src</name>
				<type>2</type>
				<locationURI>PARENT-2-PROJECT_LOC/src</locationURI>
			</link>
			<link>
				<name>ble_debug</name>
				<type>2</type>
				<locationURI>PARENT-2-PROJECT_LOC/debug</locationURI>
			</link>
			<link>
				<name>ble_rom</name>
				<type>2</type>
				<locationURI>PARENT-2-PROJECT_LOC/rom</locationURI>
			</link>
	</linkedResources>
	<filteredResources>
		<filter>
			<id>1602468250279</id>
			<name/>
			<type>22</type>
			<matcher>
				<id>org.eclipse.ui.ide.multiFilter</id>
				<arguments>1.0-name-matches-false-false-*.wvproj</arguments>
			</matcher>
		</filter>
	</filteredResources>
</projectDescription>
"@

# ---------------------------------------------------------------------------
# 3. 修补 .cproject（每个 option 用完整 id 精确定位）
# ---------------------------------------------------------------------------
$patched = $cproj

# ---- 3.1 Include paths: mirror build.ps1, including the example's local headers ----
$incPaths = @(
    '${workspace_loc:/${ProjName}}',
    '${workspace_loc:/${ProjName}/include}',
    '${workspace_loc:/${ProjName}/ble_include}',
    '${workspace_loc:/${ProjName}/ble_src/StdPeriphDriver}',
    '${workspace_loc:/${ProjName}/ble_src/RVMSIS}'
)
if ($RomTarget) { $incPaths += '${workspace_loc:/${ProjName}/ble_rom}' }
$patched = Set-OptionContent -Text $patched `
    -IdKeyword 'option.c.compiler.include.paths.1567947810' `
    -NewBody (List-Values $incPaths)

# Startup assembly is preprocessed too, so give it the same include roots and ROM macro.
$patched = Set-OptionContent -Text $patched `
    -IdKeyword 'option.assembler.include.paths.1034038285' `
    -NewBody (List-Values $incPaths)

$defines = @()
if ($RomTarget) { $defines += 'CH572BLE_ROM=1' }
if ($DebugBuild) { $defines += 'DEBUG' }
$defineBody = ($defines | ForEach-Object {
    '<listOptionValue builtIn="false" value="' + [System.Security.SecurityElement]::Escape($_) + '"/>'
}) -join ''
$patched = Set-OptionContent -Text $patched `
    -IdKeyword 'option.c.compiler.defs.177116515' -NewBody $defineBody
$patched = Set-AssemblerDefines -Text $patched -Body $defineBody

# ---- 3.2 Linker search paths and libraries ----
# Link_rom.ld uses INCLUDE rom_ram_info.ld; ble_rom must be a linker search path.
$linkPaths = @(
    '${workspace_loc:/${ProjName}/ble_lib}',
    '${workspace_loc:/${ProjName}/ble_rom}'
)
$patched = Set-OptionContent -Text $patched `
    -IdKeyword 'option.c.linker.paths.2057340378' -NewBody (List-Values $linkPaths)

# Static mode matches build.ps1's two libraries. ROM mode has no BLE/ISP archive,
# so the fixed image cannot be pulled into the application a second time.
$libBody = ''
if (-not $RomTarget) {
    $libBody = '<listOptionValue builtIn="false" value="CH572BLE"/>' +
               '<listOptionValue builtIn="false" value="ISP572"/>'
}
$patched = Set-OptionContent -Text $patched `
    -IdKeyword 'option.c.linker.libs.1350862782' -NewBody $libBody

# ---- 3.3 Select linker script, artifact path, and real application entry sources ----
$linkerScript = if ($RomTarget) {
    '${workspace_loc:/${ProjName}/ble_src/Ld/Link_rom.ld}'
} else {
    '${workspace_loc:/${ProjName}/ble_src/Ld/Link.ld}'
}
$patched = Set-OptionContent -Text $patched `
    -IdKeyword 'option.c.linker.scriptfile.1390103472' -NewBody (LV $linkerScript)
$patched = Set-FirstTagAttribute -Text $patched -TagName 'configuration' `
    -Attribute 'artifactName' -Value $BuildTag
$patched = Set-FirstTagAttribute -Text $patched -TagName 'builder' `
    -Attribute 'buildPath' -Value '${workspace_loc:/${ProjName}/obj}'

$sourceEntries = @()
$mainToExclude = if ($RomTarget) { 'main.c' } else { 'main_rom.c' }
$sourceEntries += '<entry excluding="' + $mainToExclude + '" flags="VALUE_WORKSPACE_PATH|RESOLVED" kind="sourcePath" name="src"/>'
$sourceEntries += '<entry flags="VALUE_WORKSPACE_PATH|RESOLVED" kind="sourcePath" name="ble_src/Startup"/>'

if (-not (Test-Path (Join-Path $ExDir '.nostdperiph'))) {
    $driverDir = Join-Path $Root 'src\StdPeriphDriver'
    $driverAllow = @('CH57x_clk.c','CH57x_flash.c','CH57x_gpio.c','CH57x_sys.c','CH57x_uart.c','CH57x_pwr.c')
    $driverFiles = @(Get-ChildItem -Path $driverDir -Filter '*.c' -File -ErrorAction SilentlyContinue)
    $presentDrivers = @($driverAllow | Where-Object { Test-Path (Join-Path $driverDir $_) })
    if ($presentDrivers.Count -ne $driverAllow.Count) {
        $missingDrivers = @($driverAllow | Where-Object { -not (Test-Path (Join-Path $driverDir $_)) })
        throw ('MRS 与 build.ps1 的外设驱动列表不一致，缺少：' + ($missingDrivers -join ', '))
    }
    $excludedDrivers = @($driverFiles | Where-Object { $driverAllow -notcontains $_.Name } | ForEach-Object { $_.Name })
    if ($excludedDrivers.Count -gt 0) {
        $excludeAttr = ' excluding="' + ($excludedDrivers -join '|') + '"'
    } else {
        $excludeAttr = ''
    }
    $sourceEntries += '<entry' + $excludeAttr + ' flags="VALUE_WORKSPACE_PATH|RESOLVED" kind="sourcePath" name="ble_src/StdPeriphDriver"/>'
}
if ($RomTarget) {
    $sourceEntries += '<entry flags="VALUE_WORKSPACE_PATH|RESOLVED" kind="sourcePath" name="ble_hal"/>'
}
$sourceEntries += '<entry flags="VALUE_WORKSPACE_PATH|RESOLVED" kind="sourcePath" name="ble_debug"/>'
$sourceBody = "`r`n`t`t`t`t`t`t`t" + ($sourceEntries -join "`r`n`t`t`t`t`t`t`t") + "`r`n`t`t`t`t`t`t"
$patched = Set-SourceEntries -Text $patched -Entries $sourceBody

# ---------------------------------------------------------------------------
# 4. 写文件
# ---------------------------------------------------------------------------
$pProject  = Join-Path $ExDir '.project'
$pCproj    = Join-Path $ExDir '.cproject'

$skip = $false
if ((-not $Force) -and (Test-Path $pProject) -and (Test-Path $pCproj)) {
    Write-Host ''
    Write-Host '[提示] 工程文件已存在，未覆盖（需要重建请加 -Force）' -ForegroundColor Yellow
    $skip = $true
}

if (-not $skip) {
    [System.IO.File]::WriteAllText($pProject, $project, (New-Object System.Text.UTF8Encoding($false)))
    [System.IO.File]::WriteAllBytes($pCproj, $outputEncoding.GetBytes($patched))

    Write-Host ''
    Write-Host '[OK] 已生成 MounRiver Studio 工程文件：' -ForegroundColor Green
    Write-Host "     $pProject"
    Write-Host "     $pCproj"
}

Write-Host ''
Write-Host '在 MRS 中的使用步骤：' -ForegroundColor Cyan
Write-Host '  1. File -> Import -> General -> Existing Projects into Workspace'
Write-Host "  2. Select root directory 选择: $ExDir"
Write-Host '  3. 勾选工程 -> Finish'
Write-Host "  4. MRS ELF 预期路径: $(Join-Path $ExDir ('obj\' + $BuildTag + '.elf'))"
$psBuildCommand = "powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target rom -Example $Example"
if ($DebugBuild) { $psBuildCommand += ' -DebugBuild' }
if ($RomTarget) {
    $combinedName = $BuildTag + '-combined.hex'
    Write-Host "  5. MRS 只生成应用 ELF/HEX；ROM 首次请运行: $psBuildCommand"
    Write-Host "     完整镜像由该脚本在 build\out\$combinedName 生成并完成 fixed-image 校验/合并。"
} else {
    Write-Host '  5. 静态库模式请先运行: powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Target lib'
}
Write-Host ''
Write-Host '校验配置是否正确：' -ForegroundColor Cyan
Write-Host "  python tools\check_cproject.py `"$pCproj`" --project `"$pProject`" --mode $(if ($RomTarget) { 'rom' } else { 'static' })$(if ($DebugBuild) { ' --debug' })"
