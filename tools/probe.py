# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""快速探问：验证措辞对作答的影响（8766）。"""
import json
import urllib.request

from _auth import login_cookie

BASE = "http://127.0.0.1:8766"
QS = [
    ("绩效考核多长时间进行一次？", "admin"),
    ("调薪需要什么条件？", "admin"),
    ("绩效工资怎么考核？", "admin"),
]


def ask(q, role):
    req = urllib.request.Request(
        BASE + "/api/ask",
        data=json.dumps({"query": q}).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Cookie": login_cookie(BASE, role)}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


for q, role in QS:
    d = ask(q, role)
    ans = (d.get("answer") or "").strip().replace("\n", " / ")
    print("[%s] %s → %s" % (role, q, d.get("action")))
    print("   ", ans[:220])
