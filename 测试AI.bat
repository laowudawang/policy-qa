@echo off
chcp 936 >nul
title 测试 AI 连接
cd /d "%~dp0"

set "PY="
if exist "C:\Users\19145\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=C:\Users\19145\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY if exist "C:\Users\19145\miniconda3\python.exe" set "PY=C:\Users\19145\miniconda3\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"

if not defined PY (
  echo.
  echo   没找到 Python，先装 Python 再测。
  echo.
  pause
  exit /b 1
)

"%PY%" tools\ping_llm.py
echo.
pause
