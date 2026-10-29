# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""治理层 Web 测试（8766 样例语料）：权限 / 废止 / 涉密 / 正常作答。"""
import json
import urllib.request

from _auth import login_cookie

BASE = "http://127.0.0.1:8766"

CASES = [
    ("绩效工资怎么考核？", "user", "受限-普通用户应被拒"),
    ("绩效工资怎么考核？", "admin", "受限-管理员应可答"),
    ("公务接待用餐标准是多少？", "user", "废止-应停答"),
    ("信访举报怎么提交？", "user", "涉密-应给题录指引"),
    ("差旅费住宿标准是多少？", "user", "正常-应作答"),
]


def ask(q, role):
    req = urllib.request.Request(
        BASE + "/api/ask",
        data=json.dumps({"query": q}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Cookie": login_cookie(BASE, role)},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    for q, role, tag in CASES:
        print("=" * 64)
        print("[%s] 问：%s | role: %s" % (tag, q, role))
        try:
            d = ask(q, role)
            print("  动作：", d.get("action"))
            ans = (d.get("answer") or "").strip().replace("\n", " / ")
            print("  回答：", ans[:260])
        except Exception as e:
            print("  异常：", repr(e))
    print("=" * 64)
    print("完成")
