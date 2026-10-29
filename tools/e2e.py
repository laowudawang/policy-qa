# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""端到端回归：一条命令把软件主路径全跑一遍。

    python tools/e2e.py

自己起一个临时实例（8899 端口、独立账号库、抽取式回答、7 份样例语料），
测完自动关掉。不碰你的 data_private，不调用大模型，不花钱。
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8899
ROOT = "http://127.0.0.1:%d" % PORT
TMP = os.path.join(tempfile.gettempdir(), "dz_e2e_auth")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append((name, detail))
        print("  [FAIL] " + name + ("   -> " + str(detail) if detail else ""))


class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    """一个浏览器：自带 cookie。"""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedir())

    def call(self, path, data=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        r = urllib.request.Request(ROOT + path, data=body,
                                   headers={"Content-Type": "application/json"})
        try:
            with self.op.open(r, timeout=60) as f:
                return f.status, f.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, path):
        code, b = self.call(path)
        return code, self._json(b)

    def post(self, path, obj=None):
        code, b = self.call(path, obj if obj is not None else {})
        return code, self._json(b)

    @staticmethod
    def _json(b):
        try:
            return json.loads(b.decode("utf-8"))
        except Exception:
            return {}


def wait_up(timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(ROOT + "/api/handshake", timeout=2) as f:
                if f.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def spawn():
    env = dict(os.environ, AUTH_DIR=TMP, PORT=str(PORT), DATA_DIR="data",
               MIN_SCORE="4", PYTHONIOENCODING="utf-8")
    for k in ("LLM_URL", "LLM_KEY", "LLM_MODEL", "LLM_EXTRA"):
        env.pop(k, None)          # 强制抽取式，快且不花钱
    log = open(os.path.join(tempfile.gettempdir(), "dz_e2e_server.log"), "a", encoding="utf-8")
    p = subprocess.Popen([sys.executable, os.path.join(BASE, "app.py")],
                         env=env, cwd=BASE, stdout=log, stderr=subprocess.STDOUT)
    return p, log


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    proc, log = spawn()
    try:
        if not wait_up():
            print("服务没起来，看日志：", log.name)
            return 1

        print("\n[1] 未登录时的门禁")
        anon = Client()
        code, _ = anon.get("/")
        check("/ 跳登录", code == 302, code)
        code, d = anon.post("/api/ask", {"query": "测试"})
        check("未登录提问被拒", code == 401, code)
        code, _ = anon.get("/api/convs")
        check("未登录读会话被拒", code == 401, code)
        code, _ = anon.get("/api/me")
        check("未登录读身份被拒", code == 401, code)
        code, _ = anon.get("/data/01-%E5%9B%A0%E5%85%AC%E5%87%BA%E5%9B%BD%E5%A2%83%E7%AE%A1%E7%90%86%E5%8A%9E%E6%B3%95_%E6%A0%B7%E4%BE%8B.md")
        check("未登录读制度原文被拒", code == 401, code)
        code, _ = anon.get("/data/..%2fapp.py")
        check("路径穿越挡住", code in (400, 401, 404), code)

        print("\n[2] 注册与登录")
        a = Client()
        code, d = a.post("/api/register", {"name": "测试甲", "empno": "e2etest", "pw": "pass123456"})
        check("第一个账号注册成功", code == 200, code)
        check("第一个账号是管理员", d.get("user", {}).get("role") == "admin", d)
        code, d = a.post("/api/register", {"name": "重复", "empno": "e2etest", "pw": "pass123456"})
        check("重复工号被拒", code == 400, code)
        code, d = a.post("/api/register", {"name": "太短", "empno": "e2e_x", "pw": "123"})
        check("密码太短被拒", code == 400, code)
        code, d = a.post("/api/login", {"empno": "e2etest", "pw": "wrongpass"})
        check("密码错误被拒", code == 401, code)
        code, d = a.get("/api/me")
        check("登录态可用", d.get("empno") == "e2etest", d)

        c = Client()
        code, d = c.post("/api/register", {"name": "带横线", "empno": "e2e-c", "pw": "pass123456"})
        code2, d2 = c.post("/api/login", {"empno": "e2e_c", "pw": "pass123456"})
        code3, d3 = c.get("/api/me")
        check("工号横线/下划线/大小写都能登进同一个号",
              code == 200 and code2 == 200 and d3.get("empno") == "e2ec", d3)

        print("\n[3] 问答主路径")
        t0 = time.time()
        code, d = a.post("/api/ask", {"query": "因公出国住宿费标准是多少？"})
        cost = time.time() - t0
        check("提问返回 200", code == 200, code)
        check("作答成功", d.get("action") == "answered", d.get("action"))
        check("带回条款", len(d.get("sources") or []) > 0, len(d.get("sources") or []))
        cid = d.get("cid")
        check("自动建会话并返回 cid", bool(cid), cid)
        check("抽取式 3 秒内", cost < 3, "%.2fs" % cost)
        print("         耗时 %.2fs，引用 %d 条" % (cost, len(d.get("sources") or [])))

        code, d2 = a.post("/api/ask", {"query": "差旅报销需要哪些材料？", "cid": cid})
        check("续接同一会话", d2.get("cid") == cid, d2.get("cid"))

        code, d = a.get("/api/convs")
        convs = d.get("convs") or []
        check("会话列表 1 条", len(convs) == 1, convs)
        check("轮数记到 2", convs and convs[0].get("count") == 2, convs[0] if convs else None)

        code, d = a.get("/api/conv?id=" + cid)
        msgs = (d.get("conv") or {}).get("messages") or []
        check("历史消息 4 条（2 问 2 答）", len(msgs) == 4, len(msgs))
        check("回答带出处", any(m.get("sources") for m in msgs), "无 sources")

        print("\n[4] 异常输入")
        code, d = a.post("/api/ask", {"query": ""})
        check("空问题退回 empty", d.get("action") == "empty", d)
        code, d = a.post("/api/ask", {"query": "啊" * 600})
        check("超长问题 400", code == 400, code)
        weird = '<script>alert(1)</script> "引号" \\反斜杠\n换行 😀 & < >'
        code, d = a.post("/api/ask", {"query": weird})
        check("特殊字符不崩", code == 200, code)
        check("特殊字符有回答", bool(d.get("answer")), d)
        code, d = a.post("/api/ask", {"query": "   "})
        check("纯空格退回 empty", d.get("action") == "empty", d)

        print("\n[5] 会话管理")
        code, d = a.post("/api/convs", {"action": "rename", "id": cid, "title": "改过的名字"})
        check("改名成功", code == 200 and d.get("ok"), d)
        code, d = a.get("/api/conv?id=" + cid)
        check("改名生效", (d.get("conv") or {}).get("title") == "改过的名字", d.get("conv", {}).get("title"))
        code, d = a.post("/api/convs", {"action": "new"})
        new_id = (d.get("conv") or {}).get("id")
        check("新建会话", bool(new_id), d)
        code, d = a.post("/api/convs", {"action": "delete", "id": new_id})
        check("删除会话", d.get("ok"), d)
        code, d = a.get("/api/conv?id=" + (new_id or "x"))
        check("删掉的会话读不到", code == 404, code)
        code, d = a.post("/api/convs", {"action": "delete", "id": "不存在的id"})
        check("删不存在的会话不报错", code == 200, code)

        print("\n[6] 账号隔离")
        b = Client()
        code, d = b.post("/api/register", {"name": "测试乙", "empno": "e2e_b", "pw": "pass123456"})
        check("第二个账号是普通用户", d.get("user", {}).get("role") == "user", d)
        code, d = b.get("/api/conv?id=" + cid)
        check("乙读甲的会话 404", code == 404, code)
        code, d = b.post("/api/ask", {"query": "因公出国住宿标准", "cid": cid})
        check("乙往甲的会话提问会另开一个", d.get("cid") and d.get("cid") != cid, d.get("cid"))
        code, d = b.get("/api/convs")
        mine = d.get("convs") or []
        check("乙只看到自己的会话", all(c.get("id") != cid for c in mine), mine)
        code, _ = b.get("/api/audit?limit=5")
        check("普通用户看审计 403", code == 403, code)
        code, _ = a.get("/api/audit?limit=5")
        check("管理员看审计 200", code == 200, code)

        print("\n[7] 权限与治理")
        code, d = b.post("/api/ask", {"query": "绩效怎么考核？"})
        check("受限制度对普通用户拒答", d.get("action") in ("denied", "rejected"), d.get("action"))
        code, d = a.post("/api/ask", {"query": "绩效怎么考核？"})
        check("受限制度对管理员作答", d.get("action") == "answered", d.get("action"))
        code, d = b.post("/api/ask", {"query": "公务接待用餐标准是多少？"})
        check("废止制度停答", d.get("action") == "abolished", d.get("action"))
        code, d = b.post("/api/ask", {"query": "信访举报怎么提交？"})
        check("涉密只给题录", d.get("action") == "secret", d.get("action"))

        print("\n[8] 退出与伪造")
        code, d = b.post("/api/feedback", {"qid": "x", "verdict": "up"})
        check("反馈可写", code == 200, code)
        code, _ = b.get("/logout")
        check("退出跳登录", code == 302, code)
        code, _ = b.get("/api/me")
        check("退出后会话失效", code == 401, code)
        fake = Client()
        code, _ = fake.call("/api/me")
        check("匿名访问仍被拒", code == 401, code)

        print("\n[9] 并发")
        errs = []

        def shoot(i):
            try:
                c, _ = a.post("/api/ask", {"query": "差旅报销需要哪些材料？"})
                if c != 200:
                    errs.append(c)
            except Exception as e:
                errs.append(repr(e))

        ths = [threading.Thread(target=shoot, args=(i,)) for i in range(6)]
        t0 = time.time()
        [t.start() for t in ths]
        [t.join() for t in ths]
        cost = time.time() - t0
        check("6 个并发提问全部 200", not errs, errs)
        check("并发 5 秒内完成", cost < 5, "%.2fs" % cost)
        print("         6 并发耗时 %.2fs" % cost)

        print("\n[10] 重启后登录态与数据还在")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        time.sleep(0.5)
        proc, log = spawn()
        if not wait_up():
            check("重启后服务可用", False, "起不来")
        else:
            code, d = a.get("/api/me")
            check("重启后不用重新登录", code == 200 and d.get("empno") == "e2etest", code)
            code, d = a.get("/api/convs")
            check("重启后会话还在", len(d.get("convs") or []) >= 1, d)
            code, d = a.get("/api/conv?id=" + cid)
            check("重启后对话内容还在", len((d.get("conv") or {}).get("messages") or []) == 4, d)

        print("\n" + "=" * 56)
        print("  通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
        if FAIL:
            for n, d in FAIL:
                print("   - %s  (%s)" % (n, d))
        print("=" * 56)
        return 0 if not FAIL else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log.close()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
