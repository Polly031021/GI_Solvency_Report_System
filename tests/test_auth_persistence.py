# -*- coding: utf-8 -*-
"""登录刷新持久化与主动登出回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_login_survives_new_browser_session_and_logout_clears_it() -> None:
    first = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    first.run()
    first.text_input(key="login_security_code").set_value("KPMG666")
    first.radio(key="login_user_role").set_value("项目组成员")
    first.button[0].click().run()
    assert first.session_state["logged_in"] is True
    token = first.query_params.get("solvency_session")
    assert token

    refreshed = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    refreshed.query_params["solvency_session"] = token
    refreshed.run()
    assert not refreshed.exception, [item.value for item in refreshed.exception]
    assert refreshed.session_state["logged_in"] is True
    assert refreshed.session_state["user_role"] == "项目组成员"

    refreshed.button(key="logout").click().run()
    assert refreshed.session_state["logged_in"] is False
    assert not refreshed.query_params.get("solvency_session")
    print("PASS 刷新后保留登录状态，退出登录清除持久会话")


if __name__ == "__main__":
    test_login_survives_new_browser_session_and_logout_clears_it()
    print("全部通过")
