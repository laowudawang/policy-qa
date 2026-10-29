# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""
公开制度语料抓取脚本（测试用）
从政府公开网站抓取制度全文 → 清洗为 md → 存到 data_pub/
抓取对象均为公开发布的政策文件（政府网/部委网站），仅用于本地检索链路测试。

用法：
    python tools/fetch_pub.py           # 抓取所有未下载的条目
    python tools/fetch_pub.py 差旅      # 只抓文件名含"差旅"的条目（可覆盖重抓）
"""
import os, re, sys, ssl, urllib.request
from html.parser import HTMLParser

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data_pub")

# 目标清单：url + 元数据（标题/文号/部门/生效日期以公开发布信息为准）
DOCS = [
    {
        "file": "01-中央和国家机关差旅费管理办法.md",
        "title": "中央和国家机关差旅费管理办法",
        "wh": "财行〔2013〕531号", "dept": "财政部", "eff": "2014年1月1日",
        "url": "https://www.mof.gov.cn/gkml/caizhengwengao/wg2014/wg201402/201407/t20140728_1118944.htm",
    },
    {
        "file": "02-党政机关国内公务接待管理规定.md",
        "title": "党政机关国内公务接待管理规定",
        "wh": "中办发〔2013〕22号", "dept": "中共中央办公厅、国务院办公厅", "eff": "2013年12月",
        "url": "https://www.gov.cn/zhengce/2013-12/08/content_5407873.htm",
    },
    {
        "file": "03-党政机关公务用车管理办法.md",
        "title": "党政机关公务用车管理办法",
        "wh": "中办发〔2017〕71号", "dept": "中共中央办公厅、国务院办公厅", "eff": "2017年12月5日",
        "url": "https://www.gov.cn/zhengce/2017-12/11/content_5246032.htm",
    },
    {
        "file": "04-中央和国家机关会议费管理办法.md",
        "title": "中央和国家机关会议费管理办法",
        "wh": "财行〔2016〕214号", "dept": "财政部等三部门", "eff": "2016年7月1日",
        "url": "https://www.mof.gov.cn/zcsjtsgb/gfxwj/201606/t20160629_3583432.htm",
    },
    {
        "file": "05-因公临时出国经费管理办法.md",
        "title": "因公临时出国经费管理办法",
        "wh": "财行〔2013〕516号", "dept": "财政部、外交部", "eff": "2014年1月20日",
        "url": "https://m.mof.gov.cn/zcfb/201312/t20131227_1030433.htm",
    },
    {
        "file": "06-党政机关厉行节约反对浪费条例.md",
        "title": "党政机关厉行节约反对浪费条例（2025年修订）",
        "wh": "2025年5月2日中共中央、国务院发布", "dept": "中共中央、国务院", "eff": "2025年5月2日",
        "url": "https://www.zyshgzb.gov.cn/n1/2025/0519/c460493-40482907.html",
    },
    {
        "file": "07-企业国有资产交易监督管理办法.md",
        "title": "企业国有资产交易监督管理办法",
        "wh": "国资委、财政部令第32号", "dept": "国务院国资委、财政部", "eff": "2016年6月24日",
        "url": "https://www.gov.cn/gongbao/content/2016/content_5115848.htm",
    },
    {
        "file": "08-中央企业合规管理办法.md",
        "title": "中央企业合规管理办法",
        "wh": "国资委令第42号", "dept": "国务院国资委", "eff": "2022年10月1日",
        "url": "https://www.gov.cn/zhengce/zhengceku/2022-09/19/content_5710633.htm",
    },
    {
        "file": "09-职工带薪年休假条例.md",
        "title": "职工带薪年休假条例",
        "wh": "国务院令第514号", "dept": "国务院", "eff": "2008年1月1日",
        "url": "https://www.gov.cn/govweb/zwgk/2007-12/16/content_835228.htm",
        "mirrors": ["https://www.xm.gov.cn/zwgk/flfg/sfbwj/200806/t20080626_236169.htm"],
    },
    {
        "file": "10-国有企业管理人员处分条例.md",
        "title": "国有企业管理人员处分条例",
        "wh": "国务院令第781号", "dept": "国务院", "eff": "2024年9月1日",
        "url": "https://www.gov.cn/gongbao/2024/issue_11386/202406/content_6955760.html",
    },
    {
        "file": "11-中华人民共和国招标投标法.md",
        "title": "中华人民共和国招标投标法（2017年修正）",
        "wh": "主席令第86号（修正）", "dept": "全国人大常委会", "eff": "2017年12月28日",
        "url": "http://www.npc.gov.cn/zgrdw//npc////////xinwen/2018-01/04/content_2036284.htm",
        "mirrors": ["https://www.gaoming.gov.cn/fsgmzsj/gkmlpt/content/4/4337/post_4337113.html"],
    },
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

BLOCK_TAGS = {"p", "div", "td", "tr", "li", "h1", "h2", "h3", "h4", "table", "br"}
SKIP_TAGS = {"script", "style", "noscript"}

# 行级噪声（页面导航/工具条残留）
NOISE = re.compile(
    r"(扫一扫|分享到|责任编辑|网站地图|版权所有|ICP备|京公网安备|打印此页|关闭窗口|"
    r"字体[:：]|【字号|上一篇|下一篇|返回顶部|相关链接|相关文章|政策解读|大中小|"
    r"附件下载|主办单位|技术支持|视频号|抖音|Android下载|iphone下载|移动客户端|"
    r"网站纠错|网站标识码|国务院客户端|中国政府网运行中心|登录注册|退出注册)")

# 尾部截断标记：正文之后的网站模板从这里开始
TAIL_MARKERS = ["附件下载", "【大中小】", "扫一扫在手机", "网站标识码", "主办单位：",
                "版权所有", "中国政府网运行中心", "无标题文档", "责编", "分享让",
                "相关稿件", "驻外机构", "E-mail推荐", "返回首页", "链接："]


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.buf = []
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.flush()

    def handle_startendtag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        elif tag in BLOCK_TAGS:
            self.flush()

    def handle_data(self, data):
        if self.skip_depth:
            return
        t = data.strip()
        if t:
            self.buf.append(t)

    def flush(self):
        if not self.buf:
            return
        s = ""
        for part in self.buf:
            if s and s[-1].isascii() and s[-1].isalnum() \
                    and part[0].isascii() and part[0].isalnum():
                s += " " + part
            else:
                s += part
        self.chunks.append(s)
        self.buf = []

    def result(self):
        self.flush()
        return self.chunks


def fetch_html(url):
    last_err = None
    for ctx in (None, ssl._create_unverified_context()):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
                raw = r.read()
            break
        except Exception as e:
            last_err = e
    else:
        raise last_err
    enc = "utf-8"
    m = re.search(rb'charset=["\']?([a-zA-Z0-9_-]+)', raw[:3000])
    if m:
        enc = m.group(1).decode("ascii", "ignore").lower()
        if enc in ("gb2312", "gbk"):
            enc = "gb18030"
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def cut_tail(chunks):
    """正文之后第一处网站模板标记起，全部丢弃（只扫描后 40% 区域防止误切正文）"""
    start = int(len(chunks) * 0.6)
    for i in range(start, len(chunks)):
        if any(mk in chunks[i] for mk in TAIL_MARKERS):
            return chunks[:i]
    return chunks


def clean_chunks(chunks):
    out = []
    for c in chunks:
        c = re.sub(r"\s+", " ", c).strip().replace("\u3000", " ").strip()
        if not c or len(c) == 1:
            continue
        if NOISE.search(c):
            continue
        out.append(c)
    return out


def slice_body(text):
    """从全文里切出正文：从"第一章"或"第一条"开始"""
    m = re.search(r"(第[一二三四五六七八九十]+章|第一条)", text)
    if m and m.start() < len(text) - 200:
        return text[m.start():]
    return text


def build_md(doc, text):
    head = [
        "# " + doc["title"], "",
        "文号：" + doc["wh"],
        "发文部门：" + doc["dept"],
        "生效日期：" + doc["eff"],
        "密级：内部公开",
        "状态：现行",
        "来源：" + doc["url"] + "（公开发布，抓取于本地测试）",
        "",
    ]
    return "\n".join(head) + "\n" + text + "\n"


def run(filters=None):
    force_all = bool(filters) and "all" in filters
    real = [f for f in (filters or []) if f != "all"]
    os.makedirs(OUT, exist_ok=True)
    ok = skipped = 0
    for doc in DOCS:
        if real and not any(f in doc["file"] or f in doc["title"] for f in real):
            continue
        dst = os.path.join(OUT, doc["file"])
        if os.path.isfile(dst) and not force_all and not real:
            print("[跳过]", doc["file"], "（已存在）")
            skipped += 1
            continue
        print("[抓取]", doc["title"], doc["url"])
        try:
            html = None
            for u in [doc["url"]] + doc.get("mirrors", []):
                try:
                    html = fetch_html(u)
                    break
                except Exception as e:
                    print("  [!] 来源失败 %s (%s)" % (u, e))
            if html is None:
                print("  [!] 该条目全部来源均失败，跳过")
                continue
            parser = TextExtractor()
            parser.feed(html)
            chunks = cut_tail(parser.result())
            chunks = clean_chunks(chunks)
            text = "\n\n".join(chunks)
            text = slice_body(text)
            md = build_md(doc, text)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(md)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            print("  → %s | %d 字 | %d 段" % (doc["file"], len(text), len(lines)))
            print("  → 开头: %s" % (lines[0][:50] if lines else "??"))
            print("  → 结尾: %s" % (lines[-1][:50] if lines else "??"))
            ok += 1
        except Exception as e:
            print("  [!] 失败:", e)
    print("完成：成功 %d，跳过 %d" % (ok, skipped))


if __name__ == "__main__":
    run(sys.argv[1:] or None)
