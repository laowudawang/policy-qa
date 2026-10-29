# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""Web 端到端测试：向本地服务发问题，打印动作/回答/来源。"""
import json
import sys
import urllib.request

from _auth import login_cookie

BASE = "http://127.0.0.1:8765"

CASES = [
    ("带薪年休假可以休几天？", "user"),
    ("园区停车位怎么申请？", "user"),
    ("公务接待陪餐人数最多几人？", "user"),
    ("招标公告应当通过什么媒介发布？", "user"),
    ("企业国有资产转让在什么平台进行？", "user"),
]


def ask(q, role="user"):
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
    for q, role in CASES:
        print("=" * 60)
        print("问：", q, "| role:", role)
        try:
            d = ask(q, role)
            print("动作：", d.get("action"))
            ans = (d.get("answer") or "").strip()
            print("回答：", ans[:300].replace("\n", " / "))
            srcs = d.get("sources") or []
            print("来源：", [(s.get("title"), s.get("wh"), s.get("mi")) for s in srcs[:3]])
        except Exception as e:
            print("异常：", repr(e))
    print("=" * 60)
    print("完成")
