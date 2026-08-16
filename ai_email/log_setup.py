"""结构化 JSON 日志配置（仅用 stdlib logging + json，无新依赖）。

把每条日志格式化为单行 JSON，便于日志系统采集与解析：
    {"ts": "<ISO8601 UTC>", "level": "INFO", "logger": "name", "msg": "..."}

所有模块只要用 ``logging.getLogger(__name__)`` 并共享 root handler，
即会自动输出 JSON 行，无需改动各自的 logger 调用语句。
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """将 LogRecord 格式化为单行 JSON 字符串。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


def _env_level() -> int:
    """从 LOG_LEVEL 环境变量解析日志级别（线上排障可临时开 DEBUG 免改码重启）。"""
    name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = logging.getLevelName(name)
    return level if isinstance(level, int) else logging.INFO


def setup_logging(level: int | None = None) -> None:
    """配置 root logger，使其通过共享 handler 输出 JSON 行到 stdout。

    幂等：可安全多次调用，不会重复添加 JSON handler。
    level 为 None 时取 LOG_LEVEL 环境变量（默认 INFO）。
    """
    root = logging.getLogger()
    root.setLevel(level if level is not None else _env_level())
    for handler in root.handlers:
        if isinstance(handler.formatter, JSONFormatter):
            return  # 已经配置过 JSON formatter，避免重复
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
