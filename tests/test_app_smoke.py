# -*- coding: utf-8 -*-
"""用 Streamlit AppTest 渲染整个应用，验证登录后的工作流页面不报错。"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from streamlit.testing.v1 import AppTest

at = AppTest.from_file("../app.py", default_timeout=120)
at.run()
assert not at.exception, [e.value for e in at.exception]

# 模拟项目组成员登录（绕过登录页安全码，直接注入会话状态）
at.session_state["logged_in"] = True
at.session_state["user_role"] = "项目组成员"
at.run()
assert not at.exception, [e.value for e in at.exception]
print("AppTest PASS: 登录后全流程页面渲染无异常")
print("tabs:", [t.label for t in at.tabs][:4] if at.tabs else "(no tabs)")

