@echo off
chcp 936 >nul
setlocal
title 发票打印助手 - 安装到本机

set "APP_EXE=发票打印助手.exe"
set "APP_DESC=发票打印助手"
set "LNK_NAME=发票打印助手.lnk"
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

echo.
echo   ============================================================
echo      发票打印助手  ·  安装到本机
echo   ============================================================
echo.

if not exist "%SRC%\%APP_EXE%" (
    echo   [x] 这个文件夹里找不到 %APP_EXE%
    echo       先把压缩包完整解压，再运行本脚本。
    echo.
    pause
    exit /b 1
)
if not exist "%SRC%\_internal" (
    echo   [x] 缺少 _internal 文件夹（运行库）
    echo       先把压缩包完整解压；本脚本需要整个文件夹，不能只拿 exe。
    echo.
    pause
    exit /b 1
)

rem 目标目录：优先 D 盘，其次当前用户的 LocalAppData（都不需要管理员权限）
if exist "D:\" (
    set "INSTALL_DIR=D:\发票打印助手"
) else (
    set "INSTALL_DIR=%LOCALAPPDATA%\Programs\InvoicePrintAssistant"
)

echo   安装位置：%INSTALL_DIR%
echo.

rem 覆盖安装时先关掉正在跑的老版本，不然 exe 被占用复制不过去
taskkill /IM "%APP_EXE%" /F >nul 2>&1
if not errorlevel 1 (
    echo   已关闭正在运行的程序，准备覆盖安装。
    timeout /t 2 /nobreak >nul
)

if /i "%SRC%"=="%INSTALL_DIR%" (
    echo   程序已经在本机目录里，跳过复制。
) else (
    echo   正在复制程序文件……
    robocopy "%SRC%" "%INSTALL_DIR%" /E /NFL /NDL /NJH /NJS /NP /XF "使用说明.txt" "安装到本机.bat" "解除锁定.bat" >nul
    if errorlevel 8 (
        echo.
        echo   [x] 复制失败：目标目录被占用或没有写入权限。
        echo       关掉资源管理器里打开的该目录再试；仍然失败就把压缩包换个地方解压。
        echo.
        pause
        exit /b 1
    )
)

echo   正在创建桌面快捷方式……
rem 用系统自带的 WScript.Shell 建 .lnk，不依赖任何第三方工具
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=$env:INSTALL_DIR;$exe=Join-Path $d $env:APP_EXE;$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) $env:LNK_NAME));$s.TargetPath=$exe;$s.WorkingDirectory=$d;$s.IconLocation=$exe+',0';$s.Description=$env:APP_DESC;$s.Save()"
if errorlevel 1 (
    echo.
    echo   [ ] 桌面快捷方式没建成（不影响使用，可以手动建）
    echo       到 %INSTALL_DIR%，右键 %APP_EXE% - 发送到 - 桌面快捷方式
    echo.
) else (
    echo   桌面图标已创建：%LNK_NAME%
)

echo   启动一次程序，顺便登记右键菜单……
start "" "%INSTALL_DIR%\%APP_EXE%"

echo.
echo   ============================================================
echo      装好了
echo.
echo      桌面图标：发票打印助手
echo      程序目录：%INSTALL_DIR%
echo.
echo      以后把发票 PDF 拖到桌面图标上，就开始打印。
echo      程序目录不能删，也不能只留下 exe（旁边还要有 _internal）。
echo   ============================================================
echo.
pause
endlocal
