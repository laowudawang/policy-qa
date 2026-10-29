# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""
生成式联调脚本：把"PolicyQA"接到大模型（需要 API Key）跑一组全链路测试。
先配好环境变量（以下示例为 DeepSeek，其他 OpenAI 兼容服务同理）：
    Git Bash:
        export LLM_URL=https://api.deepseek.com/chat/completions
        export LLM_MODEL=deepseek-chat
        export LLM_KEY=sk-xxxxxxxx
        export DATA_DIR=data_pub        # 可选：用公开测试语料库（默认 data/）
        export MIN_SCORE=8              # 可选：公开库建议 8，样例库默认 4
    Windows CMD：把 export 换成 set
用法：cd PolicyQA && python tools/llm_test.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

CASES = [
    ("因公出国住宿费标准是多少？", "user"),
    ("公务接待陪餐人数最多几人？", "user"),
    ("年休假有几天？", "user"),
    ("招标公告发布期多久？", "user"),
    ("合规审查意见谁签字？", "user"),
    ("园区停车位怎么申请？", "user"),
]


def main():
    if not app.LLM_URL:
        print("[!] 未设置 LLM_URL —— 当前是抽取式回退，无需 Key。")
        print("    要跑生成式联调，请先按脚本头部说明配置三个环境变量。")
        return
    if not app.LLM_KEY:
        print("[提醒] 未设置 LLM_KEY（若该服务无需 Key 可忽略）")
    app.build()
    print("模型    :", app.LLM_MODEL)
    print("端点    :", app.LLM_URL)
    print("语料目录:", app.DATA_DIR, "| 切片:", len(app.CORPUS), "| 拒答阈值:", app.MIN_SCORE)
    print("=" * 68)
    errors = 0
    for q, role in CASES:
        t0 = time.time()
        try:
            r = app.answer(q, role, log=False)
        except Exception as e:
            errors += 1
            print("问:", q, "-> 异常:", e)
            continue
        dt = time.time() - t0
        print("问:", q, "（身份 %s · 耗时 %.1fs）" % (role, dt))
        print(r["answer"][:500])
        src = "、".join(("/".join(filter(None, [s["title"], s["clause"]]))) for s in r["sources"])
        print("  动作: %s | 来源: %s" % (r["action"], src or "-"))
        print("-" * 68)
    print("完成。异常数:", errors)


if __name__ == "__main__":
    main()
