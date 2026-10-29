# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""
PolicyQA · 集团制度问答智能体（原型 MVP + 治理层）
纯 Python 标准库实现，零第三方依赖。运行：
    python app.py
浏览器打开 http://127.0.0.1:8765 → 注册登录（第一个注册的账号自动成为系统管理员）

每个人有自己的账号，左侧是本人的历史对话，对话记录存在 data_private/chats/<工号>/，
互相看不到。身份只认 HttpOnly cookie，前端改不了角色（不再有 ?role= 后门）。

默认"抽取式回退"：只检索 + 溯源原文（这正是立项卡要求的"指引到原文"）。
要启用生成式回答（接本地或 API 大模型）：
    # Ollama 示例（先 ollama pull qwen2.5:7b）
    set LLM_URL=http://localhost:11434/api/chat
    set LLM_MODEL=qwen2.5:7b
LLM_URL 留空时自动回退到抽取式，原型始终可演示。

已内置 4 项治理能力（集团级使用的关键差别）：
  1. 审计留痕 : 每次问答写入 logs/audit.jsonl（时间/工号/问题/命中/动作/外发字数）。
  2. 拒答转人工: 相关度低于阈值时不猜、不编，明确引导咨询责任部门。
  3. 时效停答 : 制度带"状态/废止日期"字段，已废止制度停止作答并提示。
  4. 反馈闭环 : 答后点选"有用/有问题"，写入 logs/feedback.jsonl，
                作为 K5 人工修改率与知识库优化的数据来源。

账号：登录后才能提问，只有 普通用户 / 系统管理员 两档（管理员能看审计日志）。
制度"密级"字段和按密级过滤的代码还在，但 120 份语料目前全部未定密（都是
"内部公开"），所以它现在拦不到任何东西——等办公室逐份定密之后才会生效。
阈值可用环境变量 MIN_SCORE 覆盖。
"""
import os, re, json, math, time, uuid, urllib.request, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, parse_qs, quote

import auth          # 账号 / 会话 / 个人对话记录（同目录 auth.py，零依赖）

BASE = os.path.dirname(os.path.abspath(__file__))
_dd = os.environ.get("DATA_DIR", "").strip()
DATA_DIR = _dd if os.path.isabs(_dd) else (os.path.join(BASE, _dd) if _dd else os.path.join(BASE, "data"))
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"   # 0.0.0.0 = 开放给局域网
def normalize_llm_url(u):
    """把各家控制台给的 Base URL 补成能直接 POST 的完整地址。

    控制台（百炼、硅基流动等）页面上的通常只到 /compatible-mode/v1，
    那是给 OpenAI SDK 用的，SDK 自己会拼 /chat/completions；
    我们这里是直接发 HTTP 请求，得自己补。用户粘哪个都能用。
    """
    u = (u or "").strip().rstrip("/")
    if not u:
        return u
    if u.endswith(("/chat/completions", "/completions", "/api/chat")):
        return u
    return u + "/chat/completions"


LLM_URL = normalize_llm_url(os.environ.get("LLM_URL", ""))
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()
LLM_KEY = os.environ.get("LLM_KEY", "").strip()      # 可选：API Key（Bearer）
MIN_SCORE = float(os.environ.get("MIN_SCORE", "4.0"))   # 拒答阈值（BM25 相关度）

# 厂商专有参数，JSON 形式塞进请求体。比如千问 qwen3.8 系列默认开着思考模式，
# 必须写 {"enable_thinking": false} 才不浪费 token 去想；深度思考可以配
# {"reasoning_effort": "low"}。写错了不影响启动，只是不用额外参数。
try:
    LLM_EXTRA = json.loads(os.environ.get("LLM_EXTRA", "") or "{}")
    if not isinstance(LLM_EXTRA, dict):
        LLM_EXTRA = {}
except Exception:
    LLM_EXTRA = {}
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "900"))   # 答案上限，省等待时间

# ---------- 账号 / 会话配置 ----------
COOKIE_NAME = "dz_sid"                 # 登录态 cookie（HttpOnly，前端 JS 读不到）
INVITE_CODE = os.environ.get("INVITE_CODE", "").strip()   # 非空则注册必须填对
MAX_Q = int(os.environ.get("MAX_Q", "500"))              # 单次提问字数上限

# ---------- 治理层配置 ----------
# 只有两档：user=普通用户，admin=系统管理员。不设部门角色——制度库当前无一
# 份定密，分得更细也拦不住任何东西，等逐份定密后再按岗位扩。
ROLES = auth.ROLES
RESTRICTED_ROLES = {"admin"}            # 假如今后有"内部受限"制度，仅管理员可读
LOG_DIR = os.path.join(BASE, "logs")
AUDIT_LOG = os.path.join(LOG_DIR, "audit.jsonl")
FEEDBACK_LOG = os.path.join(LOG_DIR, "feedback.jsonl")
os.makedirs(LOG_DIR, exist_ok=True)

# 索引读写锁：HTTP 服务是多线程的，重建索引时不能让别的线程正在扫旧索引
_INDEX_LOCK = threading.RLock()

PROMPT = """你是"PolicyQA"，某集团制度问答助手。员工问你的是"这事该怎么办"，不是要你自己去翻文件。

回答按这个顺序写，全文控制在 500 字以内：
1. 第一句先给结论，分两种问法：
   - 问"怎么办/流程"：一句话说清能不能办、找谁办、分几步；
   - 问"是什么/包括哪些/标准多少"：直接把定义、清单或数字摆出来（有几项就列几项），不要绕到"怎么办"上去。
2. 然后最多 6 点，每点一句话讲清"谁办、找谁、交什么、多久"。同类人合并成一点，不要逐条照抄原文。
3. 每点句末用括号标出处：（《制度名》第X条）。
4. 最后另起一行写"依据："，一行一条，只写《制度名》第X条。

硬约束：
- 只能依据下面给出的制度原文，不得编造，不得引入原文之外的常识或法律条文。
- 原文按金额或身份分档列出的，先在原文里找到问题落入的那一行，只照那一行回答；其他档位提到的机构一个字都不许写。
- 原文没写的（时限、金额、材料、数量、统计口径），一律写"制度未明确"，不要推断、不要圆场。
- 问"几个会议""几次""几道程序"而原文只写了谁审批、谁前置时，就是"没写数量"这种情况：
  直说制度未规定会议数量，再把原文规定的环节列出来，不要自己折算成数字。
- 数字、金额、天数、部门名称必须与原文完全一致，不许改写。
- 只做"指引到原文"，不做政策解释与裁量，不替员工判断"这样做合不合规"。
- 不复述原文长段，用员工听得懂的话概括；不要用 #、*、** 这类 Markdown 符号。

制度原文：
{ctx}

问题：{q}
回答："""


# ---------- 1. 语料加载与切块 ----------
def parse_meta(text, fname):
    """解析制度元数据：文号 / 密级 / 状态 / 生效日期 / 废止信息。"""
    def field(k):
        m = re.search(k + r'[:：]\s*([^\n]+)', text)
        return m.group(1).strip() if m else ""
    title = fname
    m = re.search(r'^#\s+(.+)$', text, re.M)
    if m:
        title = m.group(1).strip()
    wh = field("文号")
    meta = {
        "mi": field("密级") or "内部公开",        # 内部公开 / 内部受限 / 涉密
        "status": field("状态") or "现行",        # 现行 / 已废止
        "eff": field("生效日期"),
        "abdate": field("废止日期"),
        "note": field("废止说明"),
    }
    return title, wh, meta


def _split_long(p, max_len=400):
    """超长条款：优先按（一）（二）项切，每片带「第X条 首句」前缀，
    避免长块被 BM25 长度归一化压分（实测第二十一条"审批程序"因此排不进前列）。"""
    if len(p) <= max_len:
        return [p]
    head = p[:30].strip()
    segs = re.split(r'(?=[（(][一二三四五六七八九十百零〇]+[）)])', p)
    pieces, buf = [], ""
    for seg in segs:
        if buf and len(buf) + len(seg) > max_len:
            pieces.append(buf)
            buf = seg
        else:
            buf += seg
    if buf:
        pieces.append(buf)
    # 仍超长（无项标记或单项过长）→ 按句号切；再不行硬切
    final = []
    for piece in pieces:
        if len(piece) <= max_len:
            final.append(piece)
            continue
        b2 = ""
        for sent in re.split(r'(?<=[。；])', piece):
            if b2 and len(b2) + len(sent) > max_len:
                final.append(b2)
                b2 = sent
            else:
                b2 += sent
        if b2:
            final.append(b2)
    hard = []
    for piece in final:
        while len(piece) > max_len:
            hard.append(piece[:max_len])
            piece = piece[max_len:]
        if piece:
            hard.append(piece)
    # 非首片补上下文前缀
    return [hard[0]] + [head + "……" + x for x in hard[1:]]


def chunk_doc(text, title, wh, fname, meta):
    chunks = []
    # 条只在行首才算新条款（正文中"依据第十条"之类引用不切）
    parts, carry = [], ""
    for seg in re.split(r'(?m)(?=^第[一二三四五六七八九十百零〇\d]+条)', text):
        seg = carry + seg
        carry = ""
        m = re.search(r'\n(##\s*第[一二三四五六七八九十]+[章节][^\n]*)\s*$', seg)
        if m:
            carry = "\n" + m.group(1) + "\n"
            seg = seg[:m.start()].rstrip() + "\n"
        parts.append(seg)
    for p in parts:
        p = p.strip()
        if len(p) < 12:
            continue
        clause = ""
        # 块首可能带"## 第X章 …"章标题（章标题归位产生），先跳过再识别条款号
        qh = re.match(r'##\s*第[一二三四五六七八九十]+[章节][^\n]*\n?', p)
        core = p[qh.end():] if qh else p
        mc = re.match(r'第([一二三四五六七八九十百零〇\d]+)条', core)
        if mc:
            clause = "第" + mc.group(1) + "条"
        pieces = _split_long(p)
        for n, piece in enumerate(pieces):
            # 同一条被切成多块时，后续块标注「（续）」，避免来源里出现两个同名条款
            cl = clause + ("（续）" if n else "")
            chunks.append({"title": title, "wh": wh, "clause": cl,
                           "fname": fname, "text": piece, **meta})
    return chunks


def load_corpus(d):
    chunks = []
    if not os.path.isdir(d):
        # 首次运行：自动建目录，并告诉用户下一步做什么，而不是直接报错
        try:
            os.makedirs(d, exist_ok=True)
            print("[i] 已创建语料目录：%s" % d)
            print("    把制度的 .md / .txt 文件放进去，保存后自动生效，无需重启。")
        except OSError as e:
            print("[!] 语料目录无法创建：%s（%s）" % (d, e))
        return chunks
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith((".md", ".txt")):
            continue
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            text = f.read()
        title, wh, meta = parse_meta(text, fn)
        if meta["mi"] == "涉密":
            # 治理：涉密文件正文不入库，仅生成一条题录，供检索时给出指引
            chunks.append({
                "title": title, "wh": wh, "clause": "题录", "fname": fn,
                "text": "《%s》（%s）为涉密文件，正文不录入知识库，仅提供题录，"
                        "不提供在线查阅。" % (title, wh),
                **meta})
            continue
        chunks.extend(chunk_doc(text, title, wh, fn, meta))
    return chunks


# ---------- 2. 分词（中文双字 bigram + 英文词）与 BM25 ----------
# 常见问法同义词扩展：解决"问词"与"制度用词"不对桥的问题
# （问"住宿标准"要能命中"住宿费限额"，问"休几天"要能命中"5天"）
QUERY_SYN = {
    "标准": ["限额", "额度", "上限"],
    "限额": ["标准", "额度"],
    "几天": ["日内"], "多少天": ["日内"], "多久": ["日内", "工作日"],
    "多少钱": ["金额", "费用"],
    "材料": ["提交", "票据", "发票", "审批单"],
    "手续": ["审批", "办理"],
    "流程": ["程序", "步骤"], "程序": ["流程"],
    "出国": ["出访"], "出访": ["出国"],
    "报销": ["费用", "凭据"],
    # 制度里很少写"员工培训"，统一叫"教育培训/学习"（120 份制度里"员工培训"一次没出现）
    "培训": ["教育", "学习"],
    "丢了": ["遗失", "丢失"], "丢失": ["遗失"],
}


# 常见问句虚词（bigram），不参与"区分词"判断
STOP_BIGRAMS = {"怎么", "么办", "什么", "是指", "如何", "哪些", "多少", "可以",
                "需要", "是否", "有哪", "请问", "一下", "是多", "么申", "么考",
                "几点", "点开", "多久", "哪里", "哪个", "怎样", "我该", "应该",
                "么算", "咋办", "么样"}


def expand_query(q):
    extra = []
    for k, vs in QUERY_SYN.items():
        if k in q:
            extra.extend(vs)
    return q + (" " + " ".join(extra) if extra else "")


def tokenize(s):
    toks = []
    for run in re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9]+', s):
        if '\u4e00' <= run[0] <= '\u9fff':
            if len(run) == 1:
                toks.append(run)
            for i in range(len(run) - 1):
                toks.append(run[i:i + 2])
        else:
            toks.append(run.lower())
    return toks


class BM25:
    def __init__(self, docs):
        self.docs = docs
        self.N = len(docs)
        self.df = {}
        for d in docs:
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1
        self.idf = {t: math.log((self.N - n + 0.5) / (n + 0.5) + 1)
                    for t, n in self.df.items()}
        self.avgdl = (sum(len(d) for d in docs) / self.N) if self.N else 1
        self.k1, self.b = 1.5, 0.75

    def score(self, query, idx):
        d = self.docs[idx]
        tf = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for q in query:
            if q in tf:
                idf = self.idf.get(q, 0)
                s += idf * (tf[q] * (self.k1 + 1)) / \
                    (tf[q] + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
        return s


# ---------- 3. 检索（含治理过滤） ----------
_TITLE_TOKENS = {}


def title_bonus(c, qset):
    """标题命中加权：问题命中制度标题越多，该制度整体越相关
    （"公务接待"的问题应偏向接待办法，而不是同样出现"标准"二字的差旅办法）"""
    k = c["title"]
    tt = _TITLE_TOKENS.get(k)
    if tt is None:
        tt = _TITLE_TOKENS[k] = set(tokenize(k))
    hits = len(tt & qset)
    return min(2.0, 1 + 0.35 * hits) if hits else 1.0


def _rerank_in_doc(scored, qset):
    """制度内二级排序：文档选对了，但条款可能选错。

    同一份制度的每个条款几乎都会复述制度名（"因公临时出国……"），这部分
    token 在制度内部毫无区分度，却因为 tf 高把篇幅长的条款顶到前面。
    做法：认定首选制度后，用「查询词 - 该制度标题词」的剩余词在制度内部
    重排条款，把真正回答问题的条款（如"第二十一条 审批程序"）提到前排。
    只在首选制度领先明显时启用，避免误伤跨制度的问题。
    """
    best = {}
    for s, i in scored:
        if s <= 0:
            continue
        f = CORPUS[i]["fname"]
        if f not in best or s > best[f][0]:
            best[f] = (s, i)
    if not best:
        return
    top = sorted(best.items(), key=lambda kv: -kv[1][0])
    if len(top) > 1 and top[0][1][0] < top[1][1][0] * 1.35:
        return  # 两个制度咬得很近，不做制度内重排
    fname = top[0][0]
    title_tokens = _TITLE_TOKENS.get(CORPUS[top[0][1][1]]["title"]) or set()
    resid = [q for q in qset if q not in title_tokens]
    if len(resid) < 2:
        return  # 剩余词太少，说明问题信息主要在标题里，重排只会引入噪声
    for n, (s, i) in enumerate(scored):
        if CORPUS[i]["fname"] != fname or s <= 0:
            continue
        scored[n] = (s + 0.8 * bm.score(resid, i), i)
    scored.sort(reverse=True)


_CLAUSE_NO = re.compile(r"^第[一二三四五六七八九十百零〇\d]+条[　\s：:]*")


def clause_head(text, n=12):
    """取条款正文开头（去掉"## 第X章"与"第X条"），用于判断这条是不是流程类条款。"""
    t = text
    if t.startswith("##"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
    t = _CLAUSE_NO.sub("", t.lstrip())
    return t[:n]


def retrieve_all(query, role, k=3, floor=None):
    """按角色与制度状态分桶检索：
    allowed=可正常作答 / denied=受限无权限 / abolished=已废止 /
    secret=涉密题录。低于 MIN_SCORE 的一律不进入任何桶 → 触发拒答。
    floor 传 0 可绕过阈值（只给"问题是啥都没说清"时的澄清候选使用）。"""
    with _INDEX_LOCK:      # 服务是多线程的，重建索引时不许有人正在扫描
        return _retrieve_all(query, role, k, floor)


def _retrieve_all(query, role, k=3, floor=None):
    floor = MIN_SCORE if floor is None else floor
    buckets = {"allowed": [], "denied": [], "abolished": [], "secret": []}
    qt = tokenize(expand_query(query))
    # 单个汉字太泛（"天""人""管"到处都有），只留二元组和字母数字词，
    # 否则"年休假几天"能靠一个"天"字蹭上任何一份制度
    qt = [t for t in qt if len(t) > 1 or not ("\u4e00" <= t <= "\u9fff")]
    if not qt:
        return buckets
    qset = set(qt)
    ask_flow = any(w in query for w in ("流程", "程序", "步骤", "怎么办", "怎么走"))
    ask_def = any(w in query for w in ("包括", "哪些", "是什么", "范围", "含义", "定义"))
    scored = []
    for i in range(len(CORPUS)):
        c = CORPUS[i]
        s = bm.score(qt, i)
        if s > 0:
            tb = title_bonus(c, qset)
            s *= tb
            if ask_flow and tb > 1:
                # 问"流程"的题优先「审批程序/办理流程」类条款。要求问题先和
                # 制度标题沾边，否则会把不相干制度的"办事程序"条顶上来
                # （"招聘流程"曾被《子企业清理实施细则》的"工作程序"抢走）。
                head = clause_head(c["text"])
                if any(w in head for w in ("程序", "流程", "步骤")):
                    s *= 3.5
                elif "办理" in c["text"][:30]:
                    s *= 1.2
            if ask_def and tb >= 1.9:
                # 问"包括哪些/是什么"时，定义类条款（"相关内容""适用范围"）才是答案，
                # 但这类条款往往篇幅短、复述制度名少，容易被"决策监督"这种长条压住。
                # 要求问题与标题至少命中两个字（tb≥1.7），否则会飘到隔壁制度。
                head = clause_head(c["text"], 8)
                if any(w in head for w in ("适用", "内容", "范围", "原则", "定义", "是指")):
                    s *= 1.6
                elif "即" in c["text"][:60] or "包括" in c["text"][:60]:
                    s *= 1.5
            if not c.get("clause") and "文号" in c["text"][:300]:
                # 文头/元数据块降权：只含标题与发文信息，不是作答素材，
                # 但靠标题 bigram 容易抢占前排（如"年休假几天"挤掉含天数的条款）
                s *= 0.6
        scored.append((s, i))
    scored.sort(reverse=True)
    _rerank_in_doc(scored, qset)
    for s, i in scored:
        if s < floor:
            break
        c = CORPUS[i]
        if c["mi"] == "涉密":
            buckets["secret"].append((s, c))
        elif c["mi"] == "内部受限" and role not in RESTRICTED_ROLES:
            buckets["denied"].append((s, c))
        elif c["status"] != "现行":
            buckets["abolished"].append((s, c))
        else:
            buckets["allowed"].append((s, c))
    for key in buckets:
        buckets[key] = buckets[key][:k]
    return buckets


# ---------- 3.5 问题太模糊时，先反问再答 ----------
# 只说了个通用词（"审批""流程""报销"）的问句，既不该硬答一堆不相干条款，
# 也不该丢一句"未找到依据"——先问清他想办哪件事。
GENERIC_Q = ("审批", "流程", "程序", "步骤", "报销", "制度", "规定", "标准",
             "手续", "材料", "申请", "办理", "要求", "条件", "怎么办", "怎么弄",
             "什么", "多少", "几天", "多久", "如何", "范围", "区别")


def is_vague(q):
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", q or "")
    if len(s) <= 3:
        return True
    rest = s
    for w in GENERIC_Q:
        rest = rest.replace(w, "")
    return len(rest) == 0


def clarify_options(query, n=4):
    """通用词（"审批""报销"）时，按制度给方向——"您要问哪份制度里的事"。
    比拿条标题当方向靠谱：条标题常常是半句话（"变更、解除合同的…"）。"""
    b = retrieve_all(query, "user", k=48, floor=0.0)
    best = {}
    for s, c in (b["allowed"] + b["denied"] + b["abolished"]):
        if c["fname"] not in best or s > best[c["fname"]][0]:
            best[c["fname"]] = (s, c)
    opts = []
    for f, (s, c) in sorted(best.items(), key=lambda kv: -kv[1][0])[:n]:
        opts.append({"label": c["title"],
                     "query": c["title"] + " " + query.strip()})
    return opts


def suggest_topics(query, n=4):
    """拒答后的"你可能想问"：只推和问题**字面沾边**的制度，沾不上就不推。
    宁可只给一句"没找到"，也不要甩一堆"因公出国审批流程"糊人。"""
    q_tokens = {t for t in tokenize(query)
                if len(t) > 1 and t not in STOP_BIGRAMS
                # 满库都有的词（"公司""规定"）不算沾边依据
                and bm.df.get(t, 0) < 0.08 * len(CORPUS)}
    if not q_tokens:
        return []
    b = retrieve_all(query, "user", k=24, floor=0.0)
    opts, seen = [], set()
    for s, c in (b["allowed"] + b["denied"] + b["abolished"]):
        if c["fname"] in seen or s < MIN_SCORE * 0.45:
            continue
        # 至少命中三个实词才算沾边。只沾一两个多半是巧合
        #（"股票会涨吗"能蹭上某份制度里的"上市公司股票"，但那是两码事）
        if len(q_tokens & set(tokenize(c["text"]))) < 3:
            continue
        seen.add(c["fname"])
        opts.append({"label": "《%s》里怎么规定的？" % c["title"],
                     "query": c["title"] + " " + query.strip()})
        if len(opts) >= n:
            break
    return opts


# ---------- 4. 生成 ----------
_MD_HEAD = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)


def clean_md(t):
    """制度正文里混着 Markdown 章节标题（"## 第二章 审批流程"），
    只去掉 # 号，章节名留着——直接摆给员工看会更像原文。"""
    return _MD_HEAD.sub("", t or "").strip()


def extract(top):
    out = []
    for c in top:
        tag = " · ".join(x for x in (c["title"], c["wh"], c["clause"]) if x)
        out.append("【%s】\n%s" % (tag, clean_md(c["text"])))
    return "\n\n".join(out)


def call_llm(prompt):
    body = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": LLM_MAX_TOKENS,
    }
    body.update(LLM_EXTRA)      # 厂商专有参数（enable_thinking 等）盖在最上层
    payload = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if LLM_KEY:
        hdrs["Authorization"] = "Bearer " + LLM_KEY
    req = urllib.request.Request(LLM_URL, data=payload, headers=hdrs)
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))
    if "message" in data:           # Ollama
        return data["message"]["content"]
    if "choices" in data:           # OpenAI 兼容
        msg = data["choices"][0].get("message") or {}
        content = (msg.get("content") or "").strip()
        if not content:
            # 推理型模型（如 deepseek-flash、qwen3.8 默认开思考）会先花 token 思考，
            # max_tokens 给小了正文就是空的——这种情况宁可回退，也不返回空答案
            raise RuntimeError("模型未返回正文，可能被 max_tokens 截断，或思考模式没关"
                               "（qwen3.8 需 LLM_EXTRA={\"enable_thinking\": false}）")
        return content
    return json.dumps(data, ensure_ascii=False)


def generate(query, top, prev=None):
    """返回（答案, 外发字符数）。外发 0 表示没走模型、是本地抽取的。

    外发内容只有三样：指令 + 用户问题 + 命中的那几条条款原文，
    不发整库、不发员工身份、不发审计日志。
    """
    if LLM_URL:
        ctx = "\n\n".join("【%s·%s·%s】\n%s" % (c["title"], c["wh"], c["clause"], c["text"])
                          for c in top)
        head = ""
        if prev:
            head = ("前几轮用户问过（只用来判断这次在说什么，不得当作依据，也不得沿用其中的结论）：\n"
                    + "\n".join("%d. %s" % (i + 1, p[:60]) for i, p in enumerate(prev))
                    + "\n\n")
        prompt = PROMPT.format(ctx=ctx, q=query)
        if head:
            prompt = prompt.replace("问题：" + query, head + "问题：" + query, 1)
        try:
            return call_llm(prompt), len(prompt)
        except Exception as e:
            return ("（大模型调用失败，回退到抽取式：%s）\n\n" % e + extract(top)), 0
    return extract(top) + "\n\n_（当前为抽取式回退：配置 LLM_URL + LLM_MODEL 后启用生成式回答）_", 0


# ---------- 5. 问答主流程（治理判定在此发生） ----------
def answer(query, role="user", log=True, who="", prev=None):
    maybe_reload()          # 制度文件被替换/新增时，不重启也能生效
    query = (query or "").strip()
    qid = "q" + uuid.uuid4().hex[:8]
    role = role if role in ROLES else "user"
    prev = [p for p in (prev or []) if p][-3:]      # 只留最近 3 轮用户问过的话
    if not query:
        return {"answer": "请输入问题。", "sources": [], "action": "empty", "qid": qid}

    # 只说了个通用词（"审批""报销"）：不硬答一堆不相干条款，也不打发去人工，
    # 先反问要办哪件事，给出最相近的几个方向
    if is_vague(query):
        opts = clarify_options(query)
        if opts:
            ans = ("「%s」在制度里对应的不是一件事，我不确定您问的是哪一类。\n"
                   "在问题里补一句您要办的事，答案会具体很多"
                   "（比如「买设备怎么审批」「出差回来怎么报销」）。\n"
                   "也可以直接点下面的方向：" % re.sub(r"\s+", "", query))
            if log:
                audit_append({"qid": qid, "role": role, "empno": who or "",
                              "role_label": ROLES.get(role, role),
                              "question": query, "action": "clarify",
                              "sources": [o["label"] for o in opts][:3],
                              "answer_head": ans[:200], "mode": "extract", "egress": 0})
            return {"answer": ans, "sources": [], "action": "clarify",
                    "qid": qid, "options": opts, "mode": "extract"}

    keep = 5 if LLM_URL else 3   # LLM 模式多给几条上下文，提升引用命中率
    # 追问时拿前一轮问题补检索线索："那另一种情况呢"单看没有关键词，拼上上文才检索得到
    _q = query
    if prev and (len(query) <= 14 or re.search(r"^(那|这|它|其|该|此)|呢[？?]?$", query)):
        _q = prev[-1] + " " + query
    b = retrieve_all(_q, role, k=keep)
    allowed, denied = b["allowed"], b["denied"]
    abolished, secret = b["abolished"], b["secret"]
    sources, action, options = [], "answered", []
    egress = 0      # 本次外发给模型的字符数；0 = 没出网

    # 治理优先：废止/涉密/受限命中的强度超过现行命中时，按治理规则响应，
    # 避免同义词扩散把"涉已废止制度"的问题误答成现行制度
    governed = [x for x in (denied, abolished, secret) if x]
    if allowed and governed and max(x[0][0] for x in governed) > allowed[0][0]:
        allowed = []

    if allowed:
        top = [c for s, c in allowed[:keep]]
        # 同一条被切开时，「（续）」紧跟正段，读起来才连贯；其他保持相关度顺序
        base = lambda c: c["clause"].replace("（续）", "")
        rank = {}
        for n, c in enumerate(top):
            rank.setdefault((c["fname"], base(c)), n)
        top.sort(key=lambda c: (rank[(c["fname"], base(c))],
                                c["clause"].endswith("（续）")))
        ans, egress = generate(query, top, prev)
        sources = [{"title": c["title"], "wh": c["wh"], "clause": c["clause"],
                    "fname": c["fname"], "mi": c["mi"], "eff": c["eff"],
                    "snippet": clean_md(c["text"])[:140], "text": clean_md(c["text"])}
                   for c in top]
        if egress:
            # 只列模型真正引用的条款，检索到但没用上的不摆出来充数；
            # 文头块没有条款号（空串）不列，重复的续块只留一条
            cited, seen_k = [], set()
            for s in sources:
                base_c = s["clause"].replace("（续）", "")
                if not base_c or base_c not in ans:
                    continue
                k = (s["fname"], s["clause"])
                if k in seen_k:
                    continue
                seen_k.add(k)
                cited.append(s)
            if cited:
                sources = cited
        # 复杂题前置警示：写在最前面，因为首行是唯一一定会被看到的位置。
        # 判据用规则，不靠模型自评——模型自评"我确定"最不可信。
        _docs = {s["title"] for s in sources}
        _counting = re.search(r"几个(会议|程序|环节|层级|部门)|几道(程序|手续)|几次(审批|会议)|多少个会议", query)
        if _counting:
            ans = ("【需核对】本题涉及多份制度，且制度原文未明确「几个会议」的统计口径。\n"
                   "下面只是一种理解，请以下列条款原文为准。\n\n" + ans)
        elif len(_docs) >= 3:
            ans = ("【需核对】本题涉及 %d 份制度交叉，以下结论仅作指引，请以下列条款原文为准。\n\n"
                   % len(_docs) + ans)
    elif denied:
        action = "denied"
        names = "、".join(sorted({c["title"] for s, c in denied}))
        role_label = ROLES.get(role, role)
        ans = ("您询问的事项涉及内部受限制度（%s）。\n"
               "您当前访问身份为「%s」，暂无查阅权限。\n"
               "如需查阅，请联系人力资源部，或通过 OA 提交制度查阅申请。"
               % (names, role_label))
    elif abolished:
        action = "abolished"
        c = abolished[0][1]
        when = "已于 %s 废止" % c["abdate"] if c["abdate"] else "已废止"
        ans = ("检索到的相关制度《%s》（%s）%s。\n"
               "按「废止停答」规则，本系统不再依据已废止制度作答，"
               "请咨询集团办公室确认现行规定。") % (c["title"], c["wh"], when)
        if c["note"]:
            ans += "\n现行衔接：" + c["note"]
    elif secret:
        action = "secret"
        c = secret[0][1]
        ans = ("您查询的事项对应《%s》（%s）。\n"
               "该文件属涉密信息，系统仅保留题录，不提供在线查阅。\n"
               "请携本人工作证件，按集团保密规定到制度发文部门查阅。"
               % (c["title"], c["wh"]))
    else:
        action = "rejected"
        ans = ("未在现行制度库中找到明确依据。\n"
               "为避免误导，本系统不作猜测性回答。建议：\n"
               "① 咨询所在单位综合管理部门；\n"
               "② 通过 OA「制度咨询」流程提交集团办公室解答。")
        options = suggest_topics(query)

    mode = "llm" if egress else "extract"
    if log:
        audit_append({
            "qid": qid, "role": role, "empno": who or "",
            "role_label": ROLES.get(role, role),
            "question": query, "action": action,
            # 来源用「制度名#条款」，审计页可直接读；旧记录为「文件名#条款」，页面兼容
            "sources": ["%s#%s" % (c["title"], c["clause"]) for c in
                        [x[1] for x in (allowed or denied or abolished or secret)]][:3],
            "answer_head": ans[:200],
            # 数据出网留痕：mode=llm 表示真的调了外部模型，egress=本次外发字符数
            "mode": mode, "egress": egress,
        })
    return {"answer": ans, "sources": sources, "action": action, "qid": qid,
            "options": options, "mode": mode, "egress": egress}


# ---------- 6. 审计日志 / 反馈日志 ----------
def audit_append(entry):
    entry["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[!] 审计日志写入失败:", e)


def feedback_append(entry):
    entry["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(FEEDBACK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[!] 反馈日志写入失败:", e)


def read_jsonl(path, limit=None):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    if limit:
        lines = lines[-limit:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


# ---------- 7. Web 服务 ----------
def _lan_ip():
    """本机局域网 IP，用来把链接发给同事。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # 不会真的发包，只为拿到出口网卡地址
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def corpus_meta():
    """给前端用的语料概况：装的是哪套库、多少份制度、示例问题也跟着换。"""
    titles = {}
    for c in CORPUS:
        titles.setdefault(c["fname"], c["title"])
    # 示例问题取自语料里真实存在的前几份制度标题，不写死具体制度名
    pool = sorted(titles.values())
    ex = []
    for t in pool[:4]:
        short = re.sub(r"^(集团|公司|本单位)", "", t)
        short = re.sub(r"(管理办法|实施细则|管理规定|办法|细则|制度)$", "", short)
        if short:
            ex.append("%s是怎么规定的？" % short)
    if not ex:
        ex = ["这件事该怎么办？", "需要准备哪些材料？"]
    name = "制度库" if titles else "空语料（请把制度放进 data/）"
    # 附表（从 PDF 表格还原出来的）单独计数，别混进"多少份制度"里
    _tabs = {f for f in titles if f.startswith("附表")}
    return {"corpus": name, "docs": len(titles) - len(_tabs),
            "tables": len(_tabs), "chunks": len(CORPUS),
            "llm": bool(LLM_URL), "examples": ex}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---------- 身份：只认 HttpOnly cookie，不再接受 ?role= ----------
    def _token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                return unquote(v)
        return ""

    def _me(self):
        return auth.who(self._token())

    def _cookie(self, token, clear=False):
        if clear:
            return "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE_NAME
        return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
            COOKIE_NAME, token, auth.SESSION_DAYS * 86400)

    def do_GET(self):
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        me = self._me()

        # ---------- 页面 ----------
        if p in ("/", "/chat", "/index.html"):
            if not me:
                self._redirect("/login")
            else:
                self._file(os.path.join(BASE, "index.html"), "text/html")
            return
        if p in ("/login", "/register"):
            if me:
                self._redirect("/chat")
            else:
                self._file(os.path.join(BASE, "login.html"), "text/html")
            return
        if p == "/logout":
            auth.drop_session(self._token())
            self._redirect("/login", cookie=self._cookie("", clear=True))
            return
        if p == "/audit":
            # 页面本身放行，接口再按角色判；非管理员打开会看到提示
            if not me:
                self._redirect("/login")
            else:
                self._file(os.path.join(BASE, "audit.html"), "text/html")
            return

        # ---------- 制度原文：同样走密级 + 角色钩子 ----------
        if p.startswith("/data/"):
            if not me:
                self._json({"error": "请先登录。"}, 401)
                return
            self._serve_doc(unquote(p[len("/data/"):]), me["role"])
            return

        # ---------- 以下都是接口，一律要登录 ----------
        if p == "/api/handshake":          # 登录页用：判断是不是还没人注册
            self._json({"first": auth.user_count() == 0,
                        "invite": bool(INVITE_CODE)})
            return
        if not me:
            self._json({"error": "登录已过期，请重新登录。"}, 401)
            return

        if p == "/api/me":
            self._json({"empno": me["empno"], "name": me["name"], "role": me["role"],
                        "role_label": ROLES.get(me["role"], me["role"])})
        elif p == "/api/convs":
            self._json({"convs": auth.list_convs(me["empno"])})
        elif p == "/api/conv":
            cid = (qs.get("id", [""])[0] or "")
            c = auth.get_conv(me["empno"], cid)
            if not c:
                self._json({"error": "对话不存在或不属于当前账号。"}, 404)
            else:
                self._json({"conv": c})
        elif p == "/api/stats":
            maybe_reload()
            self._json(self._stats())
        elif p == "/api/meta":
            maybe_reload()
            self._json(corpus_meta())
        elif p == "/api/audit":
            if me["role"] != "admin":
                self._json({"error": "审计日志仅系统管理员可查看。"}, 403)
                return
            try:
                lim = int(qs.get("limit", ["100"])[0])
            except ValueError:
                lim = 100
            lim = max(1, min(lim, 2000))
            all_rows = read_jsonl(AUDIT_LOG)
            entries = read_jsonl(AUDIT_LOG, limit=lim)
            cnt = {}
            for r in all_rows:
                k = r.get("action") or "empty"
                cnt[k] = cnt.get(k, 0) + 1
            today = time.strftime("%Y-%m-%d")
            self._json({
                "total": len(all_rows),
                "entries": entries,
                "counts": cnt,
                "today": sum(1 for r in all_rows if (r.get("ts") or "").startswith(today)),
            })
        else:
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path
        data = self._body()

        # ---------- 登录 / 注册（不需要身份） ----------
        if p == "/api/login":
            u = auth.login(data.get("empno", ""), data.get("pw", ""))
            if not u:
                self._json({"error": "工号或密码不正确。"}, 401)
                return
            tok = auth.new_session(u["empno"])
            self._json({"ok": True, "user": self._pub(u)},
                       headers={"Set-Cookie": self._cookie(tok)})
            return
        if p == "/api/register":
            u, err = auth.register(data.get("empno", ""), data.get("name", ""),
                                   data.get("pw", ""), data.get("invite", ""),
                                   INVITE_CODE)
            if err:
                self._json({"error": err}, 400)
                return
            tok = auth.new_session(u["empno"])
            self._json({"ok": True, "user": self._pub(u),
                        "first": u["role"] == "admin"},
                       headers={"Set-Cookie": self._cookie(tok)})
            return

        me = self._me()
        if not me:
            self._json({"error": "登录已过期，请重新登录。"}, 401)
            return

        if p == "/api/ask":
            q = (data.get("query") or "").strip()
            if not q:
                self._json({"answer": "请输入问题。", "sources": [], "action": "empty",
                            "qid": "", "cid": data.get("cid") or ""})
                return
            if len(q) > MAX_Q:
                self._json({"error": "问题太长了（%d 字），请精简到 %d 字以内再问。"
                                     % (len(q), MAX_Q)}, 400)
                return
            # 会话不存在 / 不是自己的 → 开一个新的。给别人的 id 也只会在自己目录下找
            cid = data.get("cid") or ""
            conv = auth.get_conv(me["empno"], cid) if cid else None
            if not conv:
                conv = auth.new_conv(me["empno"])
                cid = conv["id"]
            # 只取前几轮"用户问的话"，回答一律不带：上一轮若答错，不能被当成下一轮的前提
            prev = [m.get("content", "") for m in conv.get("messages", [])
                    if m.get("role") == "user"][-3:]
            try:
                ans = answer(q, me["role"], who=me["empno"], prev=prev)
            except Exception as e:
                print("[!] 作答失败:", e, flush=True)
                self._json({"error": "服务内部错误，请稍后重试或联系管理员。"}, 500)
                return
            auth.append_msg(me["empno"], cid, "user", q)
            c = auth.append_msg(me["empno"], cid, "assistant", ans.get("answer", ""), {
                "qid": ans.get("qid", ""), "action": ans.get("action", ""),
                "sources": ans.get("sources", []), "options": ans.get("options", []),
                "mode": ans.get("mode", ""), "egress": ans.get("egress", 0)})
            ans["cid"] = cid
            ans["title"] = (c or {}).get("title") or "新对话"
            self._json(ans)
        elif p == "/api/feedback":
            verdict = data.get("verdict", "")
            if verdict not in ("up", "down"):
                self._json({"error": "verdict 必须为 up/down"}, 400)
                return
            feedback_append({
                "qid": data.get("qid", ""), "verdict": verdict,
                "empno": me["empno"], "role": me["role"],
                "name": me["name"],
                "question": data.get("question", ""),
                "comment": data.get("comment", ""),
            })
            self._json({"ok": True})
        elif p == "/api/convs":
            act = data.get("action", "")
            if act == "new":
                self._json({"conv": auth.new_conv(me["empno"])})
            elif act == "rename":
                ok = auth.rename_conv(me["empno"], data.get("id", ""),
                                      data.get("title", ""))
                self._json({"ok": ok})
            elif act == "delete":
                ok = auth.del_conv(me["empno"], data.get("id", ""))
                self._json({"ok": ok})
            else:
                self._json({"error": "action 必须为 new/rename/delete"}, 400)
        else:
            self.send_error(404)

    # ---------- 小工具 ----------
    def _pub(self, u):
        return {"empno": u["empno"], "name": u["name"], "role": u["role"],
                "role_label": ROLES.get(u["role"], u["role"])}

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        if n > 2 * 1024 * 1024:            # 2MB 上限，防着超大请求打满内存
            self.rfile.read(n)
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    def _redirect(self, to, cookie=None):
        self.send_response(302)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # 制度原文查阅：同样走权限钩子（密级 + 角色）
    def _serve_doc(self, name, role):
        name = name.replace("\\", "/")
        if "/" in name or ".." in name:
            self.send_error(404)
            return
        path = os.path.join(DATA_DIR, name)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            _, _, meta = parse_meta(text, name)
        except Exception:
            meta = {"mi": "内部公开"}
        if meta.get("mi") == "涉密":
            self._json({"error": "该文件为涉密文件，系统仅保留题录，不提供在线查阅。"}, 403)
        elif meta.get("mi") == "内部受限" and role not in RESTRICTED_ROLES:
            self._json({"error": "该制度为内部受限，当前访问身份无查阅权限。请联系人力资源部。"}, 403)
        else:
            self._file(path, "text/plain")

    def _stats(self):
        audits = read_jsonl(AUDIT_LOG)
        fbs = read_jsonl(FEEDBACK_LOG)
        today = time.strftime("%Y-%m-%d")
        docs = {}
        for c in CORPUS:
            docs.setdefault(c["fname"], c)
        return {
            "chunks": len(CORPUS), "docs": len(docs),
            "restricted": sum(1 for d in docs.values() if d["mi"] == "内部受限"),
            "secret": sum(1 for d in docs.values() if d["mi"] == "涉密"),
            "abolished": sum(1 for d in docs.values() if d["status"] != "现行"),
            "llm": bool(LLM_URL), "model": LLM_MODEL or "(抽取式回退)",
            "today_queries": sum(1 for a in audits if a.get("ts", "").startswith(today)),
            "feedback": {"up": sum(1 for f in fbs if f.get("verdict") == "up"),
                         "down": sum(1 for f in fbs if f.get("verdict") == "down")},
        }

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                b = f.read()
        except Exception:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _json(self, obj, code=200, headers=None):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)


def build():
    """重建索引。先在局部变量里建好，最后一次性换掉全局引用，
    这样重建过程中进来的查询要么全用旧索引、要么全用新索引，不会串。"""
    global CORPUS, CORPUS_TOKS, bm
    corpus = load_corpus(DATA_DIR)
    toks = [tokenize(c["text"]) for c in corpus]
    index = BM25(toks)
    with _INDEX_LOCK:
        CORPUS, CORPUS_TOKS, bm = corpus, toks, index
    return corpus


# ---------- 语料热加载：制度文件变了不用重启 ----------
_DATA_SIG = None
_LAST_CHECK = 0.0


def _data_sig():
    """语料目录里所有 md/txt 的（文件名, 修改时间, 大小）快照。"""
    out = []
    try:
        for fn in sorted(os.listdir(DATA_DIR)):
            if fn.lower().endswith((".md", ".txt")):
                try:
                    st = os.stat(os.path.join(DATA_DIR, fn))
                except OSError:
                    continue
                out.append((fn, int(st.st_mtime), st.st_size))
    except OSError:
        return ()
    return tuple(out)


def maybe_reload():
    """发现语料目录有变化就重建索引。最多 1.5 秒查一次，查询开销可忽略。"""
    global _DATA_SIG, _LAST_CHECK
    now = time.monotonic()
    if _DATA_SIG is not None and now - _LAST_CHECK < 1.5:
        return False
    _LAST_CHECK = now
    sig = _data_sig()
    if _DATA_SIG is None:          # 首次只是记下基线
        _DATA_SIG = sig
        return False
    if sig != _DATA_SIG:
        _DATA_SIG = sig
        build()
        print("[热加载] 语料有变化，已重建索引：%d 块" % len(CORPUS), flush=True)
        return True
    return False


def main():
    build()
    print("=" * 56)
    print("  PolicyQA · 集团制度问答智能体（原型 + 治理层）")
    print("=" * 56)
    print("  数据目录  :", DATA_DIR)
    print("  语料切块数:", len(CORPUS))
    print("  生成模式  :", ("大模型(%s)%s" % (LLM_MODEL, " + Key" if LLM_KEY else "")) if LLM_URL else "抽取式回退")
    print("  治理能力  : 审计留痕 · 拒答转人工 · 废止停答 · 反馈闭环 · 账号隔离")
    print("  拒答阈值  : MIN_SCORE =", MIN_SCORE)
    print("  审计日志  :", AUDIT_LOG)
    n_user = auth.user_count()
    print("  账号      : %d 个已注册%s" % (n_user, "" if n_user else "（第一个注册的自动成为管理员）"))
    print("  对话记录  :", os.path.join(auth.PRIV, "chats"))
    if INVITE_CODE:
        print("  邀请码    : 已开启（注册须填写）")
    print("  访问地址  : http://%s:%d" % (HOST if HOST != "0.0.0.0" else _lan_ip(), PORT))
    if HOST == "0.0.0.0":
        print("  局域网共享: 已开启（同网段同事用上面地址访问，各自注册各自的账号）")
    print("  Ctrl+C 退出")
    print("=" * 56)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
