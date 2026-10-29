@echo off
chcp 936 >nul
title 停止东知道
cd /d "%~dp0"
setlocal enabledelayedexpansion

set "K=0"
for %%P in (8765 8766) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
    set /a K+=1
  )
)

if "!K!"=="0" (
  echo.
  echo   东知道没在跑。
) else (
  echo.
  echo   已停止。
)
ping -n 3 127.0.0.1 >nul 2>&1
exit /b 0
