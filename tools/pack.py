# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""打包成可以直接发给别人的 zip。

做法是白名单：只打进下面列出来的文件，凡是不在名单里的（.env.local、
logs/、__pycache__、.git）都不可能混进去。打完包还会把 zip 重新读一遍，
逐个文件找有没有漏出去的密钥，确认干净才算成功。

用法（PolicyQA目录下）：
    python tools/pack.py            # 打含 120 份制度的内网交接包
    python tools/pack.py --nodb     # 打不含 data_group 的公开演示包

产物：dist/PolicyQA-YYYYMMDD.zip
"""
import argparse
import datetime
import os
import re
import sys
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.basename(BASE)
DIST = os.path.join(BASE, "dist")

FILES = [
    "先看这个.txt", "app.py", "auth.py", "start_demo.py", "index.html", "login.html", "audit.html",
    "selfcheck.py", "启动.bat", "停止.bat", "配置AI.bat", "测试AI.bat",
    ".env.example",
]
DIRS = ["data", "data_group", "data_pub"]
TOOLS = ["ingest_one.py", "ingest_pdf.py", "eval_group.py", "goldcheck.py",
         "webtest_group.py", "llm_test.py", "fetch_pub.py", "admin.py", "e2e.py",
         "ping_llm.py"]

# 兜底黑名单：就算哪天白名单写错了，这些也不许进
NEVER_DIRS = ("logs/", "__pycache__/", ".git/", "dist/", ".venv/", "data_private/")
NEVER_NAMES = (".env", ".env.local")

# 打包时把本机专用路径从 启动.bat 里抹掉（对方机器上没有这些路径）
# 正斜杠、反斜杠都算：bat 里两种写法都出现过，漏一种就把用户名带进包里了
PERSONAL = re.compile(rb"[Cc]:[\\/]+Users[\\/]+19145")

# 真密钥长这样：sk- 后面跟一长串字母数字。文档里的 sk-xxxx 不算
KEY_RE = re.compile(r"sk-[0-9A-Za-z_\-]{20,}")


def keep(rel):
    low = rel.replace("\\", "/").lower()
    if any(d in low for d in NEVER_DIRS):
        return False
    name = low.rsplit("/", 1)[-1]
    if name in NEVER_NAMES:
        return False
    if name.startswith(".env.") and name != ".env.example":
        return False
    return True


def collect(with_db=True):
    """返回 [(绝对路径, zip 内相对路径)]。"""
    out = []
    for fn in FILES:
        p = os.path.join(BASE, fn)
        if os.path.isfile(p):
            out.append((p, fn))
        else:
            print("  跳过（不存在）:", fn)
    dirs = [d for d in DIRS if with_db or d != "data_group"]
    for d in dirs:
        for dirpath, dirnames, filenames in os.walk(os.path.join(BASE, d)):
            dirnames[:] = [x for x in dirnames if not x.startswith(".")]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, BASE).replace("\\", "/")
                if keep(rel):
                    out.append((p, rel))
    for fn in TOOLS:
        p = os.path.join(BASE, "tools", fn)
        if os.path.isfile(p):
            out.append((p, "tools/" + fn))
    return sorted(out, key=lambda x: x[1])


def read_for_zip(path, rel):
    """启动.bat 要抹掉本机路径，其余原样返回字节。"""
    with open(path, "rb") as f:
        data = f.read()
    if rel.lower().endswith(".bat"):
        lines = [ln for ln in data.split(b"\r\n") if not PERSONAL.search(ln)]
        data = b"\r\n".join(lines)
        if not data.endswith(b"\r\n"):
            data += b"\r\n"
    return data


def audit(zip_path):
    """重新读一遍产物，找密钥和不该出现的文件。"""
    bad = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for n in names:
            if not keep(n):
                bad.append("不该出现的文件: " + n)
                continue
            if n.lower().endswith((".md", ".py", ".html", ".txt", ".bat", ".example")):
                txt = z.read(n).decode("utf-8", "ignore")
                m = KEY_RE.search(txt)
                if m:
                    bad.append("疑似密钥: %s -> %s" % (n, m.group(0)[:12] + "..."))
    return names, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodb", action="store_true", help="不含 data_group（公开演示包）")
    a = ap.parse_args()

    items = collect(with_db=not a.nodb)
    os.makedirs(DIST, exist_ok=True)
    stamp = datetime.date.today().strftime("%Y%m%d")
    tag = "公开演示" if a.nodb else "内网交接"
    out = os.path.join(DIST, "PolicyQA-%s-%s.zip" % (tag, stamp))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p, rel in items:
            z.writestr(ROOT + "/" + rel, read_for_zip(p, rel))

    names, bad = audit(out)
    size = os.path.getsize(out) / 1024.0

    print("=" * 56)
    print("  产物 :", out)
    print("  大小 : %.0f KB（原始 %.1f MB）" % (
        size, sum(os.path.getsize(p) for p, _ in items) / 1048576.0))
    print("  文件 : %d 个" % len(names))
    print("  制度 : %d 份" % len([n for n in names if "/data_group/" in n and n.endswith(".md")]))
    print("=" * 56)
    if bad:
        print("  校验未通过：")
        for b in bad:
            print("   -", b)
        print("\n  已删除产物，请先修掉上面的问题再打包。")
        os.remove(out)
        return 1
    print("  校验通过：无密钥、无日志、无本机私有文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
