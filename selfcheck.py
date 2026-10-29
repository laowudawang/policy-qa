# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""
PolicyQA · 冒烟自测
跑一组样例问题，打印每个问题的 top 相关度分、命中来源与治理动作判定。
用法：python selfcheck.py
（可作为 K3 输出准确率 / K4 引用命中率的日常自测手段）
不写入审计日志。
"""
import app

CASES = [
    # (问题, 角色, 期望动作)
    # 注：MIN_SCORE 为原型默认值，仅保证以下样例可复现；
    #     接入真实制度库后应按 K3/K4 指标重新校准（或换 jieba/句向量检索）。
    ("因公出国住宿费标准是多少？", "user", "answered"),
    ("差旅报销需要哪些材料？",     "user", "answered"),
    ("公务用车怎么审批？",         "user", "answered"),
    ("绩效怎么考核？",             "user", "denied"),
    ("绩效怎么考核？",             "admin",    "answered"),
    ("公务接待用餐标准是多少？",   "user", "abolished"),
    ("信访举报怎么提交？",         "user", "secret"),
    ("园区停车位怎么申请？",       "user", "rejected"),
]


def main():
    app.build()
    passed = 0
    for q, role, expect in CASES:
        b = app.retrieve_all(q, role)
        top = max([s for arr in b.values() for s, _ in arr], default=0.0)
        r = app.answer(q, role, log=False)
        mark = "PASS" if r["action"] == expect else "FAIL"
        passed += r["action"] == expect
        srcs = "、".join(dict.fromkeys(s["title"] for s in r["sources"])) or "-"
        print("[%s] %-14s role=%-5s top=%5.2f  action=%-9s expect=%-9s 命中=%s"
              % (mark, q[:12], role, top, r["action"], expect, srcs))
    print("-" * 64)
    print("通过 %d/%d   MIN_SCORE=" % (passed, len(CASES)), app.MIN_SCORE)


if __name__ == "__main__":
    main()
