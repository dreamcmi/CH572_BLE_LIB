@echo off
REM ============================================================================
REM  build.bat —— 给不熟悉 PowerShell 的同事用的双击入口
REM
REM  说明：实际构建逻辑都在 scripts\build.ps1 里，这里只是转调一层。
REM        双击本文件即可完成"静态库 + 全部示例"的构建。
REM
REM  常用用法（在命令行里）：
REM     build.bat                 构建静态库 + 全部示例
REM     build.bat lib             只构建静态库
REM     build.bat clean           清理 build 目录
REM     build.bat example ble_uart
REM ============================================================================
setlocal

set SCRIPT_DIR=%~dp0
set PS1=%SCRIPT_DIR%scripts\build.ps1

if not exist "%PS1%" (
    echo [错误] 找不到 %PS1%
    exit /b 1
)

REM 允许通过环境变量 RISCV_TOOLCHAIN 指定工具链目录
set TOOL_ARG=
if not "%RISCV_TOOLCHAIN%"=="" set TOOL_ARG=-Toolchain "%RISCV_TOOLCHAIN%"

if /i "%~1"=="clean" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Target clean %TOOL_ARG%
    goto :end
)

if /i "%~1"=="lib" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Target lib %TOOL_ARG%
    goto :end
)

if /i "%~1"=="example" (
    set EXNAME=all
    if not "%~2"=="" set EXNAME=%~2
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Target example -Example %EXNAME% %TOOL_ARG%
    goto :end
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Target all %TOOL_ARG%

:end
echo.
pause
endlocal
