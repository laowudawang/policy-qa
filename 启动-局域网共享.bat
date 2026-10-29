@echo off
chcp 936 >nul
title 东知道 · 局域网共享
cd /d "%~dp0"

set "PY=C:\Users\19145\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=C:\Users\19145\miniconda3\python.exe"

echo.
echo   局域网共享模式：同网段的同事可以用本机地址访问。
echo   首次运行如弹出防火墙提示，请勾选「专用网络」并允许。
echo   这台电脑不能休眠，黑窗口别关。
echo.
"%PY%" start_demo.py --lan

echo.
echo   服务已停止。
pause
