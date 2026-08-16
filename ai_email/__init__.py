"""AI Email Handler：AI 驱动的 QQ 邮件自动客服。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ai-email")
except PackageNotFoundError:  # 未安装（源码目录直接运行）时
    __version__ = "0.0.0.dev0"


def main() -> None:
    """Console-script 入口。

    懒加载 cli：包级 import 不拖起 cli 的 subprocess/sqlite3 等依赖，
    `import ai_email.workflow`（如测试）保持轻量。
    """
    from ai_email.cli import main as cli_main

    cli_main()


__all__ = ["main", "__version__"]
