# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""账号与会话：零依赖实现，密码 pbkdf2 加盐，身份走 HttpOnly cookie。

存储（全部落在 data_private/，已 gitignore，不进仓库、不进交接包）：
  users.json                     账号：工号 / 姓名 / 角色 / 盐 / 密码哈希
  sessions.json                  会话：token → 用户，重启不丢
  chats/<user_id>/<conv_id>.json 每个人的对话，按用户分目录隔离

安全边界：
  · 只有两档：普通用户 / 系统管理员。第一个注册的自动是管理员，其余是普通用户。
    不按部门/岗位分级——制度库当前 120 份全部未定密（都是"内部公开"），
    分得更细也拦不住任何东西；等办公室逐份定密之后再引入业务角色。
  · 角色只在注册时确定，前端改不了，也没有接口能改（只能命令行 tools/admin.py）
  · 接口一律从 cookie 取身份，不再接受 ?role= 参数
  · 访问别人的会话 id 一律 403，不做"能猜到就能看"
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
# AUTH_DIR 可把账号库挪到别处：换机器、跑测试、两个演示实例各用一套时用得上
_d = os.environ.get("AUTH_DIR", "").strip()
PRIV = _d if os.path.isabs(_d) else (os.path.join(BASE, _d) if _d else os.path.join(BASE, "data_private"))
USERS_F = os.path.join(PRIV, "users.json")
SESS_F = os.path.join(PRIV, "sessions.json")
CHATS = os.path.join(PRIV, "chats")

SESSION_DAYS = 30
ITER = 120000
_LOCK = threading.RLock()

ROLES = {"user": "普通用户", "admin": "系统管理员"}


def _ensure():
    os.makedirs(CHATS, exist_ok=True)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    """先写临时文件再改名，避免断电/崩溃留下半个 json。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------- 账号 ----------
def norm_empno(s):
    """工号/账号归一：去掉首尾空白和常见分隔符，统一小写。"""
    return re.sub(r"[\s\-_/]+", "", (s or "")).lower()


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                               bytes.fromhex(salt), ITER).hex()


def user_count():
    return len(_read(USERS_F, {}))


def register(empno, name, pw, invite="", invite_code=""):
    """注册。返回 (用户对象, 错误信息)。

    第一个注册的账号自动成为管理员（省掉引导配置）；其余都是普通用户。
    设了 INVITE_CODE 就必须填对，否则不许注册。
    """
    empno = norm_empno(empno)
    name = (name or "").strip()
    if not re.fullmatch(r"[a-z0-9]{3,20}", empno):
        return None, "工号只能是 3-20 位字母或数字"
    if not name or len(name) > 20:
        return None, "请填姓名（20 字以内）"
    if len(pw or "") < 6:
        return None, "密码至少 6 位"
    if invite_code and (invite or "").strip() != invite_code:
        return None, "邀请码不对"

    with _LOCK:
        _ensure()
        users = _read(USERS_F, {})
        if empno in users:
            return None, "这个工号已经注册过了，直接登录"
        role = "admin" if not users else "user"
        salt = secrets.token_hex(16)
        u = {"empno": empno, "name": name, "role": role, "salt": salt,
             "hash": hash_pw(pw, salt), "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        users[empno] = u
        _write(USERS_F, users)
        os.makedirs(os.path.join(CHATS, empno), exist_ok=True)
        return u, ""


def login(empno, pw):
    """校验密码，成功返回用户对象。"""
    empno = norm_empno(empno)
    with _LOCK:
        u = _read(USERS_F, {}).get(empno)
        if not u:
            return None
        if hash_pw((pw or ""), u["salt"]) != u["hash"]:
            return None
        return u


def get_user(empno):
    u = _read(USERS_F, {}).get(norm_empno(empno))
    if u and u.get("role") not in ROLES:
        u["role"] = "user"      # 兼容早期数据里的 staff / hr
    return u


def set_role(empno, role):
    if role not in ROLES:
        return False
    with _LOCK:
        users = _read(USERS_F, {})
        u = users.get(norm_empno(empno))
        if not u:
            return False
        u["role"] = role
        _write(USERS_F, users)
        return True


# ---------- 会话 ----------
def new_session(empno):
    empno = norm_empno(empno)
    with _LOCK:
        _ensure()
        s = _read(SESS_F, {})
        tok = secrets.token_urlsafe(32)
        s[tok] = {"empno": empno, "last": int(time.time())}
        # 顺手清掉过期会话，别让文件无限长
        cut = int(time.time()) - SESSION_DAYS * 86400
        s = {k: v for k, v in s.items() if v.get("last", 0) > cut}
        _write(SESS_F, s)
        return tok


def who(token):
    """token → 用户对象；无效或过期返回 None。"""
    if not token:
        return None
    with _LOCK:
        s = _read(SESS_F, {})
        rec = s.get(token)
        if not rec:
            return None
        now = int(time.time())
        if rec.get("last", 0) < now - SESSION_DAYS * 86400:
            s.pop(token, None)
            _write(SESS_F, s)
            return None
        # 一小时才刷新一次「最后活跃」，不然每个请求都要重写整个文件；
        # 两个演示实例共用一个账号库时，频繁重写会互相覆盖
        if now - rec.get("last", 0) > 3600:
            rec["last"] = now
            s[token] = rec
            _write(SESS_F, s)
        return get_user(rec["empno"])


def drop_session(token):
    with _LOCK:
        s = _read(SESS_F, {})
        if s.pop(token, None) is not None:
            _write(SESS_F, s)


# ---------- 对话 ----------
def _conv_path(empno, cid):
    empno = norm_empno(empno)
    if not re.fullmatch(r"[a-z0-9_\-]{6,40}", cid or ""):
        return None
    return os.path.join(CHATS, empno, cid + ".json")


def list_convs(empno):
    """该用户的会话列表，最近更新的在前。只返回摘要，不带消息正文。"""
    empno = norm_empno(empno)
    d = os.path.join(CHATS, empno)
    out = []
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if not fn.endswith(".json"):
            continue
        c = _read(os.path.join(d, fn), None)
        if not c:
            continue
        msgs = c.get("messages") or []
        out.append({"id": c.get("id"), "title": c.get("title") or "新对话",
                    "updated": c.get("updated", ""),
                    "count": sum(1 for m in msgs if m.get("role") == "user")})
    out.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return out


def get_conv(empno, cid):
    p = _conv_path(empno, cid)
    return _read(p, None) if p else None


def new_conv(empno, title="新对话"):
    empno = norm_empno(empno)
    cid = "c" + secrets.token_hex(6)
    c = {"id": cid, "title": title, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
         "updated": time.strftime("%Y-%m-%d %H:%M:%S"), "messages": []}
    _ensure()
    os.makedirs(os.path.join(CHATS, empno), exist_ok=True)
    _write(_conv_path(empno, cid), c)
    return c


def append_msg(empno, cid, role, content, extra=None):
    """往会话里追加一条消息。会话不存在返回 None（不隐式创建，避免写错人）。"""
    p = _conv_path(empno, cid)
    if not p or not os.path.isfile(p):
        return None
    with _LOCK:
        c = _read(p, None)
        if not c:
            return None
        m = {"role": role, "content": content,
             "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        if extra:
            m.update(extra)
        c.setdefault("messages", []).append(m)
        c["updated"] = m["ts"]
        # 第一句用户提问当标题，截断到 20 字
        if role == "user" and (c.get("title") in (None, "", "新对话")):
            c["title"] = re.sub(r"\s+", " ", content).strip()[:20] or "新对话"
        _write(p, c)
        return c


def rename_conv(empno, cid, title):
    p = _conv_path(empno, cid)
    if not p or not os.path.isfile(p):
        return False
    with _LOCK:
        c = _read(p, None)
        if not c:
            return False
        c["title"] = (title or "新对话").strip()[:40]
        _write(p, c)
        return True


def del_conv(empno, cid):
    p = _conv_path(empno, cid)
    if p and os.path.isfile(p):
        os.remove(p)
        return True
    return False
