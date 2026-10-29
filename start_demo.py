# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""PolicyQA · 一键启动两个演示实例
  8765 = 主实例（指向 data/ 或 data_group/ 里的语料）
  8766 = 治理样例库（受限/废止/涉密样例，适合"治理能力"演示）

用法（在 PolicyQA 目录下）：
  python start_demo.py --key sk-xxxx            # 接大模型
  set LLM_KEY=sk-xxxx && python start_demo.py   # 或从环境变量读
  python start_demo.py                          # 不传 Key → 抽取式回退模式
可用环境变量覆盖：LLM_URL / LLM_MODEL / LLM_KEY
"""
import argparse
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# 用 pythonw 启动时没有控制台，sys.stdout 是 None，print 一句就崩。
# 把标准输出接到日志文件，既救活 print，也留下排查线索。
if sys.stdout is None:
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    _logf = open(os.path.join(BASE, "logs", "start.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = _logf
    sys.stderr = _logf

INSTANCES = [
    {"PORT": "8765", "DATA_DIR": "data_group", "MIN_SCORE": "14"},
    {"PORT": "8766", "DATA_DIR": "data", "MIN_SCORE": "4"},
]


def load_env_local():
    """读同目录下的 .env.local（已在 .gitignore 里，不会进仓库）。

    用 utf-8-sig 读：记事本另存时可能带 BOM，带 BOM 的话第一行 key
    会变成 "\\ufeffLLM_URL"，看起来填了却不生效——这个坑太隐蔽。
    """
    p = os.path.join(BASE, ".env.local")
    if not os.path.isfile(p):
        return {}
    out = {}
    with open(p, encoding="utf-8-sig") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def port_busy(port):
    """端口是不是已经有人占了——最常见的原因是上一次的黑窗口没关。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--one", action="store_true",
                    help="只起主实例（8765），不起样例实例（8766）")
    ap.add_argument("--lan", action="store_true",
                    help="开放给局域网同事访问（绑 0.0.0.0，仅限可信内网）")
    a = ap.parse_args()
    insts = INSTANCES[:1] if a.one else INSTANCES

    env_local = load_env_local()
    key = a.key or os.environ.get("LLM_KEY") or env_local.get("LLM_KEY", "")
    url = (a.url or os.environ.get("LLM_URL") or env_local.get("LLM_URL")
           or "https://api.deepseek.com/chat/completions")
    model = (a.model or os.environ.get("LLM_MODEL") or env_local.get("LLM_MODEL")
             or "deepseek-flash")
    # 厂商专有参数（如千问 qwen3.8 的 enable_thinking）和答案长度上限，原样透传
    extra = os.environ.get("LLM_EXTRA") or env_local.get("LLM_EXTRA", "")
    max_tok = os.environ.get("LLM_MAX_TOKENS") or env_local.get("LLM_MAX_TOKENS", "")

    procs = []
    # 子进程的输出统一写进 logs/server.log，出问题时能倒查；
    # 用 pythonw 启动时没有控制台，这也是唯一的排查入口
    log_path = os.path.join(BASE, "logs", "server.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logf = open(log_path, "a", encoding="utf-8", buffering=1)
    logf.write("\n===== %s 启动 =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
    for inst in insts:
        if port_busy(inst["PORT"]):
            print("端口 %s 已被占用，跳过这个实例。" % inst["PORT"])
            print("  多半是上一次没关干净（那个黑窗口），或者别的程序占着。")
            print("  想继续用：先关掉那个窗口，或在命令行执行 netstat -ano | findstr :%s 看是谁。"
                  % inst["PORT"])
            continue
        env = dict(os.environ, PYTHONIOENCODING="utf-8", **inst)
        if a.lan:
            env["HOST"] = "0.0.0.0"
        if key:
            env.update({"LLM_URL": url, "LLM_MODEL": model, "LLM_KEY": key})
        if extra:
            env["LLM_EXTRA"] = extra
        if max_tok:
            env["LLM_MAX_TOKENS"] = max_tok
        p = subprocess.Popen([PY, os.path.join(BASE, "app.py")], env=env, cwd=BASE,
                             stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=flags)
        procs.append((p, inst["PORT"]))
        print("已启动: http://127.0.0.1:%s  (DATA_DIR=%s, MIN_SCORE=%s)"
              % (inst["PORT"], inst["DATA_DIR"], inst["MIN_SCORE"]))
    if not procs:
        print("两个端口都占着，没东西可启动。先关掉旧的窗口再试。")
        return 1
    if key:
        print("大模型：%s（生成式回答）%s" % (model, ("  额外参数 " + extra) if extra else ""))
    else:
        print("提示：未提供 Key，当前为抽取式回退模式（只检索+摘录原文）。"
              "可在同目录建 .env.local 写 LLM_KEY=sk-xxx")
    print("服务在后台运行。要停：双击「停止.bat」，或关掉这个窗口")
    if a.lan:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        print("局域网访问：http://%s:8765   （同事同网段直接打开）" % ip)
        print("提醒：同事各自注册账号，登录后才能提问；对话记录互相看不到。"
              "制度属内部资料，别开给不可信网络")

    # 稍等服务起来，自动开浏览器（双击 启动.bat 时不用再手动输网址）
    if not a.no_browser:
        import threading
        import webbrowser
        threading.Timer(2.0, lambda: webbrowser.open(
            "http://127.0.0.1:%s" % INSTANCES[0]["PORT"])).start()

    try:
        for p, _ in procs:
            p.wait()
    except KeyboardInterrupt:
        for p, _ in procs:
            p.terminate()
        print("\n已全部停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
