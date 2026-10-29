@echo off
chcp 936 >nul
title 东知道
cd /d "%~dp0"

set "PY="
if exist "C:\Users\19145\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=C:\Users\19145\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY if exist "C:\Users\19145\miniconda3\python.exe" set "PY=C:\Users\19145\miniconda3\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"

if not defined PY (
  echo.
  echo   没有找到 Python，先装一下再双击本文件。
  echo.
  echo   下载地址：https://www.python.org/downloads/
  echo   需要 3.9 或以上版本；安装界面第一屏务必勾选
  echo   "Add Python to PATH"，装完重新双击本文件即可。
  echo.
  echo   本程序不用装任何第三方库，装个干净的 Python 就能跑。
  echo.
  pause
  exit /b 1
)

rem 已经在后台跑着，就直接开浏览器，不重复启动
netstat -ano | findstr ":8765 " | findstr "LISTENING" >nul
if not errorlevel 1 (
  start "" http://127.0.0.1:8765
  exit /b 0
)

rem 用 pythonw 启动：没有黑窗口，服务在后台待着
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
start "" "%PYW%" start_demo.py --no-browser --one

set /a n=0
:wait
ping -n 2 127.0.0.1 >nul 2>&1
set /a n+=1
netstat -ano | findstr ":8765 " | findstr "LISTENING" >nul
if not errorlevel 1 goto ready
if %n% lss 20 goto wait
echo.
echo   启动超时了。双击「停止.bat」清一下，再看 logs\server.log 里的报错。
echo.
pause
exit /b 1

:ready
start "" http://127.0.0.1:8765
exit /b 0
