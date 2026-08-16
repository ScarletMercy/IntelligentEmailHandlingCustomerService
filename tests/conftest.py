"""Shared pytest configuration and fixtures.

Environment variables are set *before* any test module imports
``ai_email.workflow`` so that the lazy ``_get_default_context()``
can create a (never-networked) AsyncOpenAI client during the session fixture.
"""

import os

os.environ.setdefault("MODEL", "test-model")
os.environ.setdefault("BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("QQEMAIL", "test@qq.com")
os.environ.setdefault("EMAIL_PASSWORD", "test-pwd")

import pytest


@pytest.fixture(scope="session", autouse=True)
def _ensure_workflow_initialized():
    """Create the lazy AsyncOpenAI client once per session (no network calls)."""
    import ai_email.workflow as mod

    mod._get_default_context()


@pytest.fixture(scope="session", autouse=True)
def _isolate_default_db(tmp_path_factory):
    """安全网：把 persistence 的默认 db 路径指到会话级临时目录。

    任何未显式传 db_path 的持久化调用（如今后新增测试的疏漏）都绝无
    可能写到真实的 ~/.ai-email/seen.db。
    """
    from ai_email import persistence

    original = persistence.DEFAULT_DB_PATH
    persistence.DEFAULT_DB_PATH = str(tmp_path_factory.mktemp("default-db") / "seen.db")
    yield
    persistence.DEFAULT_DB_PATH = original
