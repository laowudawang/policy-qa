# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""单份制度入库：一份 PDF / TXT / MD → data_group 里一个可被检索的制度

和 ingest_pdf.py 的分工：
  ingest_pdf.py  吃"整册制度汇编"，靠目录页把 120 份制度切开，只对那一套版式有效
  ingest_one.py  吃"一份文件就是一份制度"，不要目录，版式宽松，随便什么都能进

用法（在PolicyQA目录下）：
  python tools/ingest_one.py 新办法.pdf
  python tools/ingest_one.py 新办法.pdf --title "集团XX管理办法" --mi 内部受限
  python tools/ingest_one.py 新办法.pdf --replace "集团总部员工出差管理细则"
  python tools/ingest_one.py 新办法.pdf --dry          # 只看解析结果，不写文件

--replace 是"修订"场景：旧制度标记为已废止（文件保留，可追溯），新文件写成现行。
不做这一步，新旧两份会同时进作答池，同一件事能答出两套互相矛盾的规矩。

入库后不用重启服务：app.py 自带热加载，下一次提问会自动重建索引。

依赖：只有读 PDF 才需要 pypdf（pip install pypdf）；TXT/MD 不需要任何依赖。
"""
import argparse
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "data_group")

PAGE_NO = re.compile(r"^\s*-?\s*\d{1,4}\s*-?\s*$")
CLAUSE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+条")
CHAPTER = re.compile(r"^第[一二三四五六七八九十]+[章节]\s*\S")
WH_RE = re.compile(r"[\u4e00-\u9fa5]{2,14}[〔\[]\d{4}[〕\]]\s*\d+\s*号")


def die(msg):
    print("\n[中止] " + msg + "\n")
    sys.exit(1)


# ---------- 读文件 ----------
def read_text(path):
    low = path.lower()
    if low.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            die("读 PDF 需要 pypdf：pip install pypdf\n"
                "（如果手上是 Word，先另存为 PDF 或 TXT 再跑）")
        r = PdfReader(path)
        pages = [(p.extract_text() or "") for p in r.pages]
        print("  页数：%d" % len(pages))
        return "\n".join(pages)
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    die("文本编码认不出来，试试另存为 UTF-8")


def clean_lines(text):
    out = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s.strip() or PAGE_NO.match(s):
            continue
        if s.strip() in ("目  录", "目 录"):
            continue
        out.append(s.strip())
    return out


def build_body(lines):
    """行首第X条开新段；第X章/节加 ##；● 单独成段；其余折行直接拼接。"""
    paras, cur = [], []

    def flush():
        if cur:
            t = "".join(cur).strip()
            if t:
                paras.append(t)
            cur.clear()

    for s in lines:
        if CLAUSE.match(s) or s.startswith("●"):
            flush()
            cur.append(s)
        elif CHAPTER.match(s):
            flush()
            cur.append("## " + re.sub(r"\s+", " ", s))
        else:
            cur.append(s)
    flush()
    return paras


def guess_title(lines, stem):
    """取开头第一行像标题的。按原始行判断，不能跨行拼（否则会把文号行吞进来）。"""
    for s in lines[:6]:
        t = s.strip()
        if not t or t.startswith(("●", "##", "第")):
            continue
        if WH_RE.search(t) or t.endswith("号") or "印发" in t or "审议" in t:
            continue
        if 2 <= len(t) <= 40 and "。" not in t and not t.isdigit():
            return t
    return re.sub(r"\.(pdf|txt|md)$", "", os.path.basename(stem), flags=re.I)


def extract_meta(head):
    """从开头几行里找文号 / 生效日期。找不到就留空，不猜。"""
    wh = ""
    for ln in head.splitlines():          # 逐行找，避免跨行匹配出半句话
        m = WH_RE.search(ln)
        if m:
            wh = re.sub(r"\s+", "", m.group(0))
            if wh.startswith("日"):
                wh = wh[1:]
            break
    eff = ""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起施行", head)
    if m:
        eff = "%s年%s月%s日" % m.group(1, 2, 3)
    else:
        for ln in head.splitlines():
            if "印发" in ln or "颁布" in ln:
                m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", ln)
                if m:
                    eff = "%s年%s月%s日" % m.group(1, 2, 3)
                    break
    return wh, eff


def existing_files():
    """返回 {标题: 路径}，用来发现重名/修订。"""
    out = {}
    if not os.path.isdir(OUT_DIR):
        return out
    for fn in os.listdir(OUT_DIR):
        if not fn.lower().endswith(".md"):
            continue
        p = os.path.join(OUT_DIR, fn)
        try:
            head = open(p, encoding="utf-8").read(400)
        except OSError:
            continue
        m = re.search(r"^#\s+(.+)$", head, re.M)
        if m:
            out[m.group(1).strip()] = p
    return out


def drop_header_residue(paras, title, wh):
    """开头常是"标题行＋文号行"拼成的一段，和文件头重复。留着会变成一条
    没有条款编号的"依据"，专门抢标题类问题的分数，去掉。"""
    limit = len(title) + len(wh or "") + 20
    while paras:
        p = paras[0]
        if p.startswith(("●", "##", "第")) or len(p) > limit:
            break
        if title in p or (wh and wh in p):
            paras.pop(0)
            continue
        break
    return paras


def pick_name(title):
    """给新制度取文件名。同名已存在就加日期/序号 —— 绝不允许覆盖旧文件，
    否则"修订"会把刚写进去的新版盖成旧版，再标成已废止。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "", title)[:40]
    base = "新增-%s" % safe
    cand = os.path.join(OUT_DIR, base + ".md")
    if not os.path.exists(cand):
        return cand
    stamp = time.strftime("%Y%m%d")
    cand = os.path.join(OUT_DIR, "%s-%s.md" % (base, stamp))
    n = 2
    while os.path.exists(cand):
        cand = os.path.join(OUT_DIR, "%s-%s-%d.md" % (base, stamp, n))
        n += 1
    return cand


def mark_abolished(path, new_title):
    """把旧制度标成已废止，并记下被谁替代。文件不删，保留追溯。"""
    with open(path, encoding="utf-8") as f:
        s = f.read()
    if re.search(r"^状态[:：]", s, re.M):
        s = re.sub(r"^状态[:：].*$", "状态：已废止", s, count=1, flags=re.M)
    else:
        s = s.replace("\n\n", "\n状态：已废止\n\n", 1)
    note = "废止说明：已被《%s》替代" % new_title
    if "废止说明" not in s:
        s = re.sub(r"^(状态[:：].*)$", r"\1\n" + note, s, count=1, flags=re.M)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="待入库的文件（pdf / txt / md）")
    ap.add_argument("--title", default="", help="制度全称，留空则从正文首行猜")
    ap.add_argument("--mi", default="内部公开",
                    choices=["内部公开", "内部受限", "涉密"], help="密级")
    ap.add_argument("--status", default="现行", choices=["现行", "已废止"])
    ap.add_argument("--wh", default="", help="文号，留空则自动抓")
    ap.add_argument("--eff", default="", help="生效日期，留空则自动抓")
    ap.add_argument("--domain", default="其他", help="领域，只用于归类展示")
    ap.add_argument("--replace", default="",
                    help="被替代的旧制度全称（修订时用，旧文件标为已废止）")
    ap.add_argument("--dry", action="store_true", help="只看解析结果，不写文件")
    a = ap.parse_args()

    if not os.path.isfile(a.src):
        die("找不到文件：" + a.src)

    print("读取：", os.path.basename(a.src))
    text = read_text(a.src)
    lines = clean_lines(text)
    paras = build_body(lines)
    if not paras:
        die("没抽到正文。扫描件（图片型 PDF）需要先做 OCR，本脚本不做 OCR。")

    title = a.title.strip() or guess_title(lines, a.src)
    wh, eff = extract_meta("\n".join(lines[:12]))
    wh = a.wh or wh
    eff = a.eff or eff
    paras = drop_header_residue(paras, title, wh)
    n_clause = sum(1 for p in paras if CLAUSE.match(p))
    chars = sum(len(p) for p in paras)

    print("\n解析结果，请逐项核对：")
    print("  标题    :", title)
    print("  文号    :", wh or "（没抓到 → 留空，需要就 --wh 补）")
    print("  生效日期:", eff or "（没抓到 → 留空，需要就 --eff 补）")
    print("  条款数  : %d 条" % n_clause)
    print("  字数    : %d 字" % chars)
    print("  密级    :", a.mi, "（错了就是泄密或该看的看不到，务必确认）")
    print("  状态    :", a.status)

    if n_clause == 0:
        print("\n  警告：一条「第X条」都没认出来。可能是版式不含条款编号，"
              "或 PDF 是扫描件。入库后检索效果会很差，建议先看原文。")
    if a.mi == "涉密":
        print("\n  注意：密级=涉密 的正文不会进作答池，只保留一条题录指引。")

    old = existing_files()
    if title in old and not a.replace:
        die("已存在同名制度：%s\n"
            "如果这是修订版，请加 --replace \"%s\" 让旧版退出作答。"
            % (os.path.basename(old[title]), title))

    fname = None
    head = ["# %s" % title, "", "领域：%s" % a.domain]
    if wh:
        head.append("文号：%s" % wh)
    if eff:
        head.append("生效日期：%s" % eff)
    head += ["密级：%s" % a.mi, "状态：%s" % a.status, ""]
    content = "\n".join(head) + "\n\n".join(paras) + "\n"

    if a.dry:
        print("\n[--dry] 没有写文件。确认无误后去掉 --dry 再跑一次。")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    dst = pick_name(title)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)
    print("\n已写入：", os.path.basename(dst))

    if a.replace:
        if a.replace not in old:
            print("  警告：没找到要替代的《%s》，旧制度可能仍会参与作答，请手工核对。" % a.replace)
        else:
            mark_abolished(old[a.replace], title)
            print("  已废止：", os.path.basename(old[a.replace]))

    print("\n下一步：")
    print("  1) 打开页面直接问一句相关的，确认能答到这份新制度（不用重启服务）")
    print("  2) 如果答不到，看阈值：%s 里 MIN_SCORE 是否偏高" % "环境变量/启动脚本")
    print("  3) 制度全文属内部资料，别把 data_group/ 传出去")


if __name__ == "__main__":
    main()
