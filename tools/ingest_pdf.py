# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""制度汇编 PDF → 结构化语料（data_group/*.md）

用法（需要 pypdf）：
  把汇编 PDF 放进 pdf/ 目录，然后
  python tools/ingest_pdf.py
  或先指定目录：set IN_DIR=D:\制度PDF  （Linux/macOS: export IN_DIR=~/制度PDF）
输出：data_group/ 下逐份 md 文件 + _manifest.json

要点：
- 从目录页解析各制度的标题与页码，按目录定位正文范围
- 页码偏移：上册 PDF 页 = 印刷页 + 4；下册 PDF 页 = 印刷页 + 6
- 清洗页脚「- 123 -」；正文折行直接拼接（中文无空格）
- 行首「第X条」= 新段；「第X章/节」= 章标题；「●」= 元数据行
- 元数据：文号、生效日期；密级默认「内部公开」（需人工复核）
"""
import json
import os
import re
import sys

from pypdf import PdfReader

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# PDF 目录：优先取环境变量 IN_DIR，否则用程序目录下的 pdf/
IN_DIR = os.environ.get("IN_DIR") or os.path.join(BASE_DIR, "pdf")
OUT_DIR = os.path.join(BASE_DIR, "data_group")

VOLS = [
    {"fn": "汇编（上）.pdf", "offset": 4, "name": "上册"},
    {"fn": "汇编（下）.pdf", "offset": 6, "name": "下册"},
]

PAGE_NO = re.compile(r"^\s*-?\s*\d{1,4}\s*-?\s*$")
TOC_LINE = re.compile(r"^(\d+)、(.*?)[…\.]{1,}\s*(\d+)$")
PART_LINE = re.compile(r"^第[一二三]部分\s+([一二三]级制度)")

DOMAIN_RULES = [
    ("党建纪检", ["党建", "党委", "党支部", "党总支", "巡察", "意识形态", "全面从严治党",
                 "纪委", "第一议题", "中心组", "党员", "一把手", "宣传"]),
    ("公司治理", ["章程", "三重一大", "议事规则", "前置事项", "董事会", "总裁", "外派董事", "授权管理"]),
    ("财务资金", ["财务", "资金", "会计", "融资", "发票", "税务", "支付", "应收", "债券", "理财", "出纳", "债务融资"]),
    ("人事薪酬", ["劳动用工", "薪酬", "领导人员", "招聘", "人事档案", "干部教育", "老干部",
                 "因私出国", "因公临时出国", "出差管理", "人力资源"]),
    ("投资资产", ["投资", "产权", "资产评估", "国有资产评估", "子企业", "不动产", "商标", "长期股权", "资产减值"]),
    ("审计追责", ["审计", "责任追究", "容错纠错"]),
    ("法务合规", ["法律", "合同", "合规", "法务", "商业秘密", "法治"]),
    ("采购行政", ["采购", "固定资产", "办公设备", "办公用品", "纪念品", "公务用车", "印章", "证照"]),
    ("信息保密", ["信息化", "信息安全", "主数据", "协同办公", "软件正版化", "客商主数据"]),
    ("安全稳定", ["安全生产", "信访", "稳定", "老娘舅"]),
    ("群团关怀", ["互助互济", "帮困", "职代会", "职工代表大会"]),
    ("制度管理", ["规章制度管理"]),
]
# 名称有歧义、手工归类的
DOMAIN_FIX = {
    "集团关于促进业务协同的指导意见（试行）": "战略运营",
    "集团战略规划管理办法": "战略运营",
    "集团关于进一步加强重点工作督办的意见": "战略运营",
    "集团人力资源信息化建设管理办法": "人事薪酬",
    "集团档案管理实施细则": "档案公文",
    "集团总部档案分类方案、文件材料归档范围和档案保管期限表": "档案公文",
    "集团总部公文处理工作细则": "档案公文",
    "集团机要文件管理细则": "档案公文",
    "集团信息公开管理细则": "档案公文",
    "集团新闻宣传实施细则": "党建纪检",
    "集团房地产权证集中管理工作细则": "投资资产",
}


def domain_of(title):
    if title in DOMAIN_FIX:
        return DOMAIN_FIX[title]
    for d, kws in DOMAIN_RULES:
        if any(k in title for k in kws):
            return d
    return "其他"


def parse_toc(pdf, toc_pages):
    items, part = [], ""
    for i in toc_pages:
        text = pdf.pages[i].extract_text() or ""
        for raw in text.splitlines():
            s = raw.strip()
            mp = PART_LINE.match(s)
            if mp:
                part = mp.group(1)
                continue
            m = TOC_LINE.match(s)
            if m and part:
                items.append({"part": part, "seq": int(m.group(1)),
                              "title": re.sub(r"\s+", "", m.group(2)),
                              "page": int(m.group(3))})
    return items


def clean_lines(text):
    out = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s.strip():
            continue
        if PAGE_NO.match(s):
            continue
        if s.strip() in ("目  录", "目 录"):
            continue
        out.append(s.strip())
    return out


def build_body(lines):
    """行首第X条开新段；第X章/节前加 ##；● 单独成段；其余折行直接拼接。"""
    paras = []
    cur = []

    def flush():
        if cur:
            t = "".join(cur).strip()
            if t:
                paras.append(t)
            cur.clear()

    for s in lines:
        if re.match(r"^第[一二三四五六七八九十百零〇\d]+条", s):
            flush()
            cur.append(s)
        elif re.match(r"^第[一二三四五六七八九十]+[章节]\s*\S", s):
            flush()
            cur.append("## " + re.sub(r"\s+", " ", s).strip())
        elif s.startswith("●"):
            flush()
            cur.append(s)
        else:
            cur.append(s)
    flush()
    return paras


def extract_meta(paras):
    head = "\n".join(paras[:6])
    wh = ""
    m = re.search(r"[\u4e00-\u9fa5]{2,14}[〔\[]\d{4}[〕\]]\s*\d+\s*号", head)
    if m:
        wh = re.sub(r"\s+", "", m.group(0))
        if wh.startswith("日"):        # "2026 年 1 月 29 日集团字〔…〕" 的"日"
            wh = wh[1:]
    eff = ""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起施行", head)
    if m:
        eff = "%s年%s月%s日" % (m.group(1), m.group(2), m.group(3))
    else:
        # "自颁布之日起实施" 之类：用印发日期兜底
        for ln in head.split("\n"):
            if "印发" in ln or "颁布" in ln:
                m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", ln)
                if m:
                    eff = "%s年%s月%s日" % (m.group(1), m.group(2), m.group(3))
                    break
    passed = ""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]{0,20}?审议通过", head)
    if m:
        passed = "%s年%s月%s日" % (m.group(1), m.group(2), m.group(3))
    return wh, eff, passed


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "", s)


def main():
    if not os.path.isdir(IN_DIR):
        print("找不到 PDF 目录：%s" % IN_DIR)
        print("把汇编 PDF 放进去，或先设环境变量 IN_DIR 指向 PDF 所在目录。")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = []
    total = 0
    for vol in VOLS:
        path = os.path.join(IN_DIR, vol["fn"])
        pdf = PdfReader(path)
        toc_items = parse_toc(pdf, [2, 3, 4, 5])
        print("[%s] 目录解析 %d 项" % (vol["name"], len(toc_items)))
        for k, it in enumerate(toc_items):
            start_pdf = it["page"] + vol["offset"] - 1          # PDF 索引（0 基）
            if k + 1 < len(toc_items):
                end_pdf = toc_items[k + 1]["page"] + vol["offset"] - 1
            else:
                end_pdf = len(pdf.pages)
            text = "\n".join((pdf.pages[i].extract_text() or "")
                             for i in range(start_pdf, min(end_pdf, len(pdf.pages))))
            paras = build_body(clean_lines(text))
            wh, eff, passed = extract_meta(paras)
            # 去掉正文里重复的标题行（首段若不是 ● / 第X章 / 第X条，视为标题残留）
            if paras and not paras[0].startswith(("●", "##", "第")):
                paras = paras[1:]
            dom = domain_of(it["title"])
            pref = {"一级制度": "一级", "二级制度": "二级", "三级制度": "三级"}[it["part"]]
            fname = "%s-%02d-%s.md" % (pref, it["seq"], safe_name(it["title"]))
            head = ["# %s" % it["title"], "", "层级：%s" % pref, "领域：%s" % dom]
            if wh:
                head.append("文号：%s" % wh)
            if eff:
                head.append("生效日期：%s" % eff)
            head += ["密级：内部公开", "状态：现行", ""]
            body = "\n\n".join(paras)
            with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
                f.write("\n".join(head) + body + "\n")
            manifest.append({"file": fname, "title": it["title"], "part": it["part"],
                             "seq": it["seq"], "domain": dom, "wh": wh, "eff": eff,
                             "passed": passed, "pages": [it["page"], end_pdf - vol["offset"]],
                             "chars": len(body)})
            total += 1
    with open(os.path.join(OUT_DIR, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("完成：%d 个制度文件 → %s" % (total, OUT_DIR))
    no_wh = [m["title"] for m in manifest if not m["wh"]]
    no_eff = [m["title"] for m in manifest if not m["eff"]]
    print("无文号 %d 项，无施行日期 %d 项" % (len(no_wh), len(no_eff)))
    if no_wh:
        print("  无文号示例:", no_wh[:5])


if __name__ == "__main__":
    main()
