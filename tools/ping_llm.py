# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""测一下 .env.local 里配的大模型到底通不通。填完 key 双击「测试AI.bat」即可。

只发一句话、只要几个 token，花不了钱。出问题会把原因说人话：
  · key 不对 / 过期        -> 401
  · 地址写错 / 少了路径    -> 404 或连不上
  · 没开通这个模型         -> 403 或 400
  · 思考模式没关，正文为空 -> 单列一条提示
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    p = os.path.join(BASE, ".env.local")
    if not os.path.isfile(p):
        return None
    out = {}
    with open(p, encoding="utf-8-sig") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def say(msg=""):
    print(msg, flush=True)


def normalize_url(u):
    """控制台给的是 Base URL（给 SDK 用），我们直接发 HTTP，得补全路径。"""
    u = (u or "").strip().rstrip("/")
    if not u:
        return u
    if u.endswith(("/chat/completions", "/completions", "/api/chat")):
        return u
    return u + "/chat/completions"


def main():
    cfg = load_env()
    if cfg is None:
        say("没有找到 .env.local。先双击「配置AI.bat」生成一份，再填 key。")
        return 1
    url = cfg.get("LLM_URL", "")
    model = cfg.get("LLM_MODEL", "")
    key = cfg.get("LLM_KEY", "")
    extra = cfg.get("LLM_EXTRA", "")

    say("=" * 60)
    say("  地址 : " + (url or "(空)"))
    say("  模型 : " + (model or "(空)"))
    say("  密钥 : " + (("已填 %d 位" % len(key)) if key else "(空)"))
    say("  参数 : " + (extra or "(无)"))
    say("=" * 60)

    if not url:
        say("[×] 没填 LLM_URL")
        return 1
    full = normalize_url(url)
    if full != url:
        say("[i] 你填的是 Base URL，已自动补成完整地址：")
        say("    " + full)
        say("")
    url = full
    if not model:
        say("[×] 没填 LLM_MODEL")
        return 1
    if not key:
        say("[!] 没填 LLM_KEY（内网模型不需要鉴权的可以忽略）")

    body = {"model": model,
            "messages": [{"role": "user", "content": "只回复两个字：收到"}],
            "stream": False, "max_tokens": 16}
    if extra:
        try:
            e = json.loads(extra)
            if isinstance(e, dict):
                body.update(e)
        except Exception:
            say("[!] LLM_EXTRA 不是合法 JSON，已忽略：" + extra)

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key

    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    t0 = time.time()
    try:
        # 不走系统代理：公司电脑常设全局代理，会把本地/内网请求也劫走
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=40) as f:
            raw = f.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        say("[×] 接口返回 %d" % e.code)
        say("    " + detail.replace("\n", " "))
        if e.code == 401:
            say("\n  → key 不对、复制不完整，或者已经失效。重新去控制台复制一次。")
            say("  → 还有一种情况：百炼分「团队版 Plan / 个人版 / Coding Plan / 按量计费」,")
            say("    这几种的 key 和 Base URL 互不相通，配错组合也会报 401。")
            say("    以控制台为准：找到你这个 key，抄它旁边那条 Base URL。")
        elif e.code == 404:
            say("\n  → 地址和 key 不配对。百炼几种套餐的 Base URL 各不相同：")
            say("    千问AI平台     https://maas.qianwenaiapi.com/compatible-mode/v1")
            say("    百炼团队版     https://ws-xxxxxxxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
            say("    百炼个人/按量  https://dashscope.aliyuncs.com/compatible-mode/v1")
            say("    混用必然报错，也不会计入你买的额度。")
            say("    去控制台找到你这个 key 对应的那条 Base URL，原样粘过来。")
        elif e.code == 403:
            say("\n  → 这个 key 没有调用该模型的权限，去控制台确认模型已开通。")
        elif e.code == 400:
            say("\n  → 参数不被接受。常见原因：LLM_EXTRA 里的字段这家不认，"
                "把这一行删掉再试。")
        return 1
    except Exception as e:
        say("[×] 连不上：%s" % e)
        say("    → 检查地址拼写、网络是否要连 VPN/内网。")
        return 1

    cost = time.time() - t0
    try:
        data = json.loads(raw)
    except Exception:
        say("[×] 返回的不是 JSON，地址可能指向了网页而不是接口：")
        say("    " + raw[:300])
        return 1

    text = ""
    if "choices" in data:
        text = ((data["choices"][0].get("message") or {}).get("content") or "").strip()
    elif "message" in data:
        text = (data["message"].get("content") or "").strip()

    if not text:
        say("[!] 接口通了（HTTP 200），但正文是空的，耗时 %.1fs" % cost)
        say("    → 多半是模型先花 token 思考，把 max_tokens 用光了。")
        say('      千问加一行 LLM_EXTRA={"enable_thinking": false} 再试。')
        say("    原始返回：" + raw[:300])
        return 1

    say("[√] 通了！耗时 %.1fs" % cost)
    say("    模型回了一句：" + text[:80])
    say("    真正返回的模型名：" + str(data.get("model", "(未给)")))
    say("")
    say("现在双击「停止.bat」再双击「启动.bat」，就可以用 AI 回答了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
