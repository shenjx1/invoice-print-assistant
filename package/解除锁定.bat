@echo off
chcp 936 >nul
title 解除文件锁定
echo.
echo   这个工具去掉从网上/聊天软件下载的文件上的「锁定」标记。
echo   加了锁的程序第一次运行会被系统拦下来，解一下就好。
echo   从 U 盘拷来的通常不用管，直接双击运行即可。
echo.
echo   正在处理当前目录（%~dp0）
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$n=0; Get-ChildItem -Path '%~dp0*' -Recurse -File -Include *.exe,*.dll -ErrorAction SilentlyContinue | ForEach-Object { Unblock-File -LiteralPath $_.FullName; $n++ }; Write-Host ('  已处理 ' + $n + ' 个文件') -ForegroundColor Green"
echo.
echo   完成，现在可以双击 发票打印助手.exe 了。
echo.
pause
