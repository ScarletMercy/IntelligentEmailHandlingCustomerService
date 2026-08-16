"""Tests for log_setup: JSONFormatter serialization and setup idempotency."""

import json
import logging

from ai_email.log_setup import JSONFormatter, setup_logging


def _make_record(msg="hello", level=logging.INFO):
    return logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_formatter_basic_fields():
    out = json.loads(JSONFormatter().format(_make_record()))
    assert out["level"] == "INFO"
    assert out["logger"] == "test.logger"
    assert out["msg"] == "hello"
    assert "ts" in out  # ISO8601 UTC


def test_formatter_serializes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _make_record()
        record.exc_info = sys.exc_info()
    out = json.loads(JSONFormatter().format(record))
    assert "ValueError: boom" in out["exception"]


def test_setup_logging_idempotent():
    """重复调用不得叠加 JSON handler（否则每条日志输出多行）。"""
    root = logging.getLogger()
    before = len(root.handlers)
    setup_logging()
    setup_logging()
    json_handlers = [h for h in root.handlers if isinstance(h.formatter, JSONFormatter)]
    assert len(json_handlers) == 1
    assert len(root.handlers) == before + 1  # 只新增了一个


def test_setup_logging_env_level(monkeypatch):
    """LOG_LEVEL 环境变量生效；非法值回退 INFO。"""
    root = logging.getLogger()
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    setup_logging()
    assert root.level == logging.DEBUG
    monkeypatch.setenv("LOG_LEVEL", "not-a-level")
    setup_logging()
    assert root.level == logging.INFO
    setup_logging(level=logging.WARNING)  # 显式参数优先
    assert root.level == logging.WARNING
