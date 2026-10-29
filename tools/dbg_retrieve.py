# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""调试检索：看某问题命中的 chunk 和分数。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATA_DIR", "data_pub")

import app  # noqa: E402

app.build()

Q = sys.argv[1] if len(sys.argv) > 1 else "带薪年休假可以休几天？"

# 不打日志直接调 retrieve_all
b = app.retrieve_all(Q, "user", k=5)
for key in ("allowed", "denied", "abolished", "secret"):
    items = b.get(key) or []
    if not items:
        continue
    print("### 桶:", key)
    for c in items:
        s, c = c
        print("  [%.2f] %s | %s | %s" % (s, c["title"], c["wh"], c.get("clause", "")))
        txt = (c.get("text") or "").replace("\n", " ")
        print("       ", txt[:160])
print("done")
