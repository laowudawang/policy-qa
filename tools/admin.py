# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""账号管理（命令行）。服务在跑也能用，改完下一个请求就生效。

用法（PolicyQA目录下）：
    python tools/admin.py list                  看所有账号
    python tools/admin.py passwd dhls001        重置某人密码（交互输入）
    python tools/admin.py role dhls001 admin    设为管理员（只有 user / admin 两档）
    python tools/admin.py del dhls001           删账号（连同他的所有对话）

说明：
  · 密码是 pbkdf2 加盐哈希，任何人都看不到明文，忘了只能重置
  · 删账号会一并删掉 data_private/chats/<工号>/ 下的对话，不可恢复
  · 审计日志 logs/audit.jsonl 不动，那是留痕，删不掉才对
"""
import getpass
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth   # noqa: E402


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = a[0]
    users = auth._read(auth.USERS_F, {})

    if cmd == "list":
        if not users:
            print("还没有任何账号。第一个人打开网页注册时自动成为管理员。")
            return 0
        print("%-14s %-10s %-12s %-20s %s" % ("工号", "姓名", "角色", "注册时间", "对话数"))
        for k, u in sorted(users.items()):
            d = os.path.join(auth.CHATS, k)
            n = len([f for f in os.listdir(d) if f.endswith(".json")]) if os.path.isdir(d) else 0
            print("%-14s %-10s %-12s %-20s %d" % (
                k, u.get("name", ""), auth.ROLES.get(u.get("role"), u.get("role")),
                u.get("created", ""), n))
        return 0

    if len(a) < 2:
        print(__doc__)
        return 1
    empno = auth.norm_empno(a[1])
    if empno not in users:
        print("没有这个工号:", empno)
        return 1

    if cmd == "passwd":
        pw = getpass.getpass("新密码（至少 6 位）: ")
        if len(pw) < 6:
            print("太短了，至少 6 位")
            return 1
        if getpass.getpass("再输一遍: ") != pw:
            print("两次不一致")
            return 1
        users[empno]["salt"] = auth.secrets.token_hex(16)
        users[empno]["hash"] = auth.hash_pw(pw, users[empno]["salt"])
        auth._write(auth.USERS_F, users)
        # 顺手踢掉他所有的登录态，旧 cookie 立即失效
        s = auth._read(auth.SESS_F, {})
        s = {k: v for k, v in s.items() if v.get("empno") != empno}
        auth._write(auth.SESS_F, s)
        print("已重置密码，该账号的登录状态已全部失效")
        return 0

    if cmd == "role":
        if len(a) < 3 or a[2] not in auth.ROLES:
            print("角色只能是：", " / ".join(auth.ROLES))
            return 1
        users[empno]["role"] = a[2]
        auth._write(auth.USERS_F, users)
        print("已改为", auth.ROLES[a[2]])
        return 0

    if cmd in ("del", "delete", "rm"):
        if input("确认删除 %s（%s）及其全部对话？输入 yes 确认: "
                 % (users[empno].get("name", ""), empno)).strip() != "yes":
            print("已取消")
            return 1
        users.pop(empno, None)
        auth._write(auth.USERS_F, users)
        d = os.path.join(auth.CHATS, empno)
        if os.path.isdir(d):
            shutil.rmtree(d)
        s = auth._read(auth.SESS_F, {})
        auth._write(auth.SESS_F, {k: v for k, v in s.items() if v.get("empno") != empno})
        print("已删除")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
