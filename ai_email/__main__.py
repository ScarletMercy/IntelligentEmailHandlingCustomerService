"""支持 `python -m ai_email` 入口（守护进程 worker 经此派发）。"""

from ai_email.cli import main

if __name__ == "__main__":
    main()
