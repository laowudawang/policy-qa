# PolicyQA · 制度问答智能体

> Policy Q&A with clause-level citations — zero dependencies, pure Python stdlib.

把一堆散落的规章制度，变成一个能问、能溯源、查不到就说不知道的问答库。

用大白话问"这事该怎么办"，它先给结论，再列依据的制度名和条号，点开能看原文；**检索不到依据就明说没有，不猜不编**。

---

## 特点

- **主程序零依赖**：纯 Python 标准库，`python app.py` 直接跑，不需要装任何包
- **可选依赖只有一个**：`tools/ingest_pdf.py`（从 PDF 批量导入）需要 `pip install pypdf`，其余脚本全用标准库
- **可溯源**：每条结论都标注《制度名》第 X 条，无依据则拒答并转人工
- **治理层**：废止制度自动停答、审计留痕、账号与会话隔离、受限/涉密制度只给题录
- **多轮追问**：同一会话可以问"那 2500 万的呢"，只携带用户前几轮**问话**，不携带历史回答
- **表格解析**（可选）：把制度附件里的权限表、期限表还原成 Markdown 表，保留行列关系
- **两种模式**：不配模型时走"检索 + 摘原文"；配了 OpenAI 兼容接口就切成生成式

## 快速开始

```bash
# 需要 Python 3.9+
python app.py
```

浏览器打开 http://127.0.0.1:8765 ，**第一个注册的账号自动是管理员**。

局域网共享：`HOST=0.0.0.0 python app.py`

## 放自己的语料

在 `data/` 下放 `.md` 或 `.txt`，一个文件一份制度即可。文件头建议带元信息：

```
# 差旅费管理办法
文号：某单位字〔20XX〕X号
生效日期：2026年5月9日
密级：内部公开
状态：现行
```

正文按"第一条 / 第二条"分段，检索和引用都靠它来定位。

> **本仓库不包含任何制度原文。** `data/`、`data_group/`、`data_pub/`、`data_private/`、`logs/` 都已写进 `.gitignore`。

想立刻试跑又手头没有语料，可以抓几份**政府公开**制度当示例（脚本里是财政部、中办等公开发布的文件）：

```bash
python tools/fetch_pub.py
```

它会写入 `data_pub/`，再用 `DATA_DIR=data_pub python app.py` 启动即可。

## 接大模型（可选）

复制 `.env.example` 为 `.env.local`，填三项：

```
LLM_URL=https://你的接口地址/compatible-mode/v1
LLM_MODEL=模型名
LLM_KEY=你的key
```

任何 OpenAI 兼容接口都行（Ollama、vLLM、各家云服务）。不填也能用，只是回到"只检索 + 摘原文"。

## 目录

```
app.py            主程序：语料加载、切块、BM25 检索、生成、治理判定、HTTP 服务
auth.py           账号与会话（pbkdf2 加盐、HttpOnly cookie）
index.html        聊天界面（单文件，无框架、无 CDN）
login.html        登录/注册
audit.html        审计日志（仅管理员）
selfcheck.py      冒烟自测
tools/            运维与评测脚本（导入 PDF、批量检索校验等）
```

## 已知限制

- **分档条款容易串档**：一句话里并列多个金额/身份档位时，小模型可能把相邻档位的机构混进来。缓解办法是这类问题在回答开头标注"需核对"，并说明原文未明确的口径。
- **表格需预解析**：仓库不附带表格解析结果，需自己用 `tools/` 里的脚本从原始 PDF 抽取。
- **不做裁量**：只指引到原文，不判断"这样做合不合规"。

## 许可

MIT，见 [LICENSE](LICENSE)。
