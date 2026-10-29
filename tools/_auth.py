# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""联调脚本用：拿一个能用的登录态。不是业务代码，只在本机跑。

做法：在用户库里准备一个专用账号（默认 tooluser），按需要调好角色，
再走 /api/login 换一个 cookie 出来。服务在跑就能用，改完下一个请求生效。

用法：
    from _auth import login_cookie
    ck = login_cookie("http://127.0.0.1:8765", "user")
    ... headers={"Cookie": ck} ...
"""
import json
import os
import sys
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import auth   # noqa: E402

EMPNO = "tooluser"
NAME = "联调脚本"
PW = "tool-2026"


def login_cookie(base, role=None, empno=EMPNO):
    """返回 'dz_sid=xxx'。账号不存在就建，角色不对就改。"""
    if not auth.get_user(empno):
        u, err = auth.register(empno, NAME, PW)
        if err:
            raise SystemExit("建联调账号失败：%s" % err)
    if role and auth.get_user(empno).get("role") != role:
        auth.set_role(empno, role)

    body = json.dumps({"empno": empno, "pw": PW}).encode("utf-8")
    req = urllib.request.Request(base + "/api/login", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            sc = r.headers.get("Set-Cookie") or ""
    except urllib.error.HTTPError as e:
        raise SystemExit("登录被拒（%s）：检查密码或先删掉 data_private/users.json 里的 %s"
                         % (e.code, empno))
    except urllib.error.URLError as e:
        raise SystemExit("连不上 %s：服务没起？%s" % (base, e))
    if not sc:
        raise SystemExit("服务没返回登录 cookie，检查 app.py 是否已更新")
    return sc.split(";")[0]
