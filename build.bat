@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] 安装依赖 ...
python -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo [2/3] 生成打印机图标 ...
python tools\make_icon.py
if errorlevel 1 goto fail

echo [3/3] 打包文件夹版 ...
python -m PyInstaller --noconfirm --clean build_onedir.spec
if errorlevel 1 goto fail

echo.
echo 打包完成： dist_onedir\发票打印助手\发票打印助手.exe
explorer "%~dp0dist_onedir"
exit /b 0

:fail
echo.
echo 打包失败，请查看上面的报错信息。
pause
