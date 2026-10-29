@echo off
chcp 936 >nul
title 配置 AI
cd /d "%~dp0"

if exist ".env.local" goto open

rem 注释用英文：bat 的 echo 写中文会变成 GBK，程序按 UTF-8 读会乱码
> ".env.local" echo # line2 = your key, line3 = endpoint ending with /chat/completions
>> ".env.local" echo LLM_KEY=
>> ".env.local" echo LLM_URL=https://maas.qianwenaiapi.com/compatible-mode/v1
>> ".env.local" echo LLM_MODEL=qwen3.8-flash
>> ".env.local" echo LLM_EXTRA={"enable_thinking": false}

echo.
echo   已生成 .env.local，只有 5 行，记事本马上打开。
echo.
echo   要改的是第 2、3 行：
echo.
echo     第2行  LLM_KEY=     等号后面粘你的 key
echo     第3行  LLM_URL=     粘贴控制台给的 Base URL，原样粘
echo.
echo   地址去哪抄：百炼控制台里的「API-KEY / 调用示例」，复制 OpenAI 兼容地址，
echo   一般长这样（结尾不用加 /chat/completions，程序会自己补）：
echo      https://ws-xxxxxxxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
echo   如果账号给的是老式公共地址（dashscope.aliyuncs.com），
echo   就整行换成那个，同样原样粘。
echo.
echo   第 4 行模型名、第 5 行千问参数，不用动。
echo.
echo   改完 Ctrl+S 保存，关掉记事本，双击「测试AI.bat」。
echo   它会直接告诉你通没通，通了再双击「启动.bat」。
echo.
pause

:open
notepad ".env.local"
