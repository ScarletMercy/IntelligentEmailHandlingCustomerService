"""AI Email Handler CLI"""

import argparse
import contextlib
import os
import platform
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from typing import IO

_DATA_DIR = os.path.join(os.path.expanduser("~"), ".ai-email")
_ENV_FILE = os.path.join(_DATA_DIR, ".env")
PID_FILE = os.path.join(_DATA_DIR, "ai-email.pid")
LOG_FILE = os.path.join(_DATA_DIR, "ai-email.log")
LOCK_FILE = os.path.join(_DATA_DIR, "ai-email.lock")  # 存活锁（与 PID 内容分离，#1）

CONFIG_GROUPS = [
    (
        "=== AI 模型配置 ===",
        [
            ("MODEL", "模型名称（如 gpt-4o, deepseek-chat）", False, False),
            ("BASE_URL", "API 地址", False, False),
            ("API_KEY", "API Key", True, False),
        ],
    ),
    (
        "=== QQ 邮箱配置 ===",
        [
            ("QQEMAIL", "QQ 邮箱地址", False, False),
            ("EMAIL_PASSWORD", "邮箱授权码（非登录密码）", True, False),
        ],
    ),
    (
        "=== QQ Bot 通知配置（可选，扫码一键配置） ===",
        [
            ("QQ_APP_ID", "QQ Bot App ID", False, True),
            ("QQ_CLIENT_SECRET", "QQ Bot Client Secret", True, True),
            ("QQ_NOTIFY_TARGET", "通知目标（扫码自动填入）", False, True),
        ],
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ai-email", description="AI 驱动的 QQ 邮件自动客服系统")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("setup", help="交互式配置向导")
    sub.add_parser("daemon", help="以守护进程运行")
    sub.add_parser("stop", help="停止守护进程")
    sub.add_parser("status", help="查看守护进程状态")
    sub.add_parser("_worker", help=argparse.SUPPRESS)  # 内部：守护进程 worker
    return parser.parse_args()


def _read_hidden_chars(read_char: "Callable[[], str]", newline_echo: str) -> str:
    """通用逐字符读取循环：回显 ·，退格回删，Ctrl+C 中断。"""
    chars: list[str] = []
    while True:
        ch = read_char()
        if ch == "":
            # pty 关闭等场景下 read(1) 返回空串：不拦截会无限刷 ·
            raise EOFError("标准输入已关闭（stdin EOF）")
        if ch in ("\r", "\n"):
            sys.stdout.write(newline_echo)
            sys.stdout.flush()
            break
        if ch == "\x03":  # Ctrl+C
            sys.stdout.write(newline_echo)
            sys.stdout.flush()
            raise KeyboardInterrupt
        if ch in ("\x08", "\x7f"):  # Backspace / DEL
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        chars.append(ch)
        sys.stdout.write("·")
        sys.stdout.flush()
    return "".join(chars)


def _hidden_input(prompt_text: str) -> str:
    """逐字符读取输入，每输入一个字符显示一个 ·，退格时删除一个 ·。"""
    sys.stdout.write(prompt_text)
    sys.stdout.flush()
    if not sys.stdin.isatty():
        # 非交互 stdin（管道/CI/重定向）：msvcrt/termios 均不可用，退化为按行读取。
        # EOF 必须抛异常——返回空串会被上层当"空输入"无限重试提示
        line = sys.stdin.readline()
        if not line:
            raise EOFError("标准输入已关闭（stdin EOF）")
        return line.rstrip("\r\n")
    if platform.system() == "Windows":
        import msvcrt

        # 文本模式 stdout 自动把 \n 转为 CRLF
        return _read_hidden_chars(msvcrt.getwch, "\n")  # type: ignore[attr-defined]
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        tty.setraw(fd)  # type: ignore[attr-defined]  # raw 模式需显式 \r\n 回显
        return _read_hidden_chars(lambda: sys.stdin.read(1), "\r\n")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


def _prompt_value(
    var_name: str, prompt: str, hidden: bool, default: str = "", optional: bool = False
) -> str:
    """交互式获取单个配置值，密码字段用 · 遮挡显示"""
    display = "·" * len(default) if hidden and default else (default or "")
    hint = f" [{display}]" if display else ""
    label = f"  {prompt}{hint}: "
    while True:
        raw = _hidden_input(label) if hidden else input(label)
        value = raw.strip()  # 可见/隐藏输入统一 strip：尾随空白大概率是输入手滑
        if not value and default:
            return default
        if not value:
            if optional:
                return ""
            print(f"    [!] {var_name} 为必填项")
            continue
        if var_name == "QQEMAIL" and "@" not in value:
            print("    [!] 请输入有效的邮箱地址")
            continue
        return value


def _test_model_connection(model: str, base_url: str, api_key: str) -> tuple[bool, str]:
    """发送测试请求验证模型配置是否可用。返回 (成功?, 消息)。"""
    try:
        from openai import OpenAI

        # 显式超时：SDK 默认 600 秒会让 setup 向导在模型端挂起时长时间卡死
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=30.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "你好"}],
            max_tokens=16,
        )
        reply = resp.choices[0].message.content or ""
        return True, reply
    except Exception as e:
        return False, str(e)


def _test_qq_bot(app_id: str, client_secret: str) -> tuple[bool, str]:
    """测试 QQ Bot 凭据是否可用（获取 access_token）。"""
    try:
        import httpx

        resp = httpx.post(
            "https://bots.qq.com/app/getAppAccessToken",
            json={"appId": app_id, "clientSecret": client_secret},
            timeout=15.0,
        )
        data = resp.json()
        if data.get("access_token"):
            return True, ""
        return False, data.get("message", str(data))
    except Exception as e:
        return False, str(e)


def cmd_setup() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    existing = {}
    if os.path.exists(_ENV_FILE):
        print(f"发现已有配置文件: {_ENV_FILE}")
        try:
            with open(_ENV_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        existing[k.strip()] = v.strip()
        except Exception:
            pass
        if input("是否基于现有配置修改？[Y/n]: ").strip().lower() == "n":
            existing.clear()
    print("\n[AI Email Handler] 配置向导\n")
    results = {}

    # --- AI 模型配置（带自动验证，失败时重新输入） ---
    ai_group_title, ai_vars = CONFIG_GROUPS[0]
    while True:
        print(f"\n{ai_group_title}")
        for var_name, prompt, hidden, optional in ai_vars:
            results[var_name] = _prompt_value(
                var_name, prompt, hidden, existing.get(var_name, ""), optional=optional
            )
        print("\n  正在验证模型连接...")
        ok, msg = _test_model_connection(results["MODEL"], results["BASE_URL"], results["API_KEY"])
        if ok:
            print(f"  [OK] 模型连接成功，模型回复: {msg.strip()}")
            break
        print(f"\n  [!] 模型连接失败: {msg}")
        print("  请重新输入 AI 模型配置。")
        existing.update({vn: results.get(vn, "") for vn, _, _, _ in ai_vars})

    # --- QQ 邮箱配置 ---
    qq_title, qq_vars = CONFIG_GROUPS[1]
    print(f"\n{qq_title}")
    for var_name, prompt, hidden, optional in qq_vars:
        results[var_name] = _prompt_value(
            var_name, prompt, hidden, existing.get(var_name, ""), optional=optional
        )

    # --- QQ Bot 通知配置（可选：扫码一键配置，openid 无法手填自动跳过） ---
    bot_title, bot_vars = CONFIG_GROUPS[2]
    from ai_email.qq_bot import is_placeholder_target

    print(f"\n{bot_title}")
    has_full = all(existing.get(vn) for vn, _, _, _ in bot_vars)
    if has_full:
        raw = input("  配置方式: 1]保留当前配置  2]扫码重新配置 [回车=1]: ").strip()
        action = "scan" if raw == "2" else "keep"
    else:
        raw = input("  扫码配置 QQ Bot 通知？[y/N]: ").strip().lower()
        action = "scan" if raw == "y" else "skip"

    if action == "keep":
        for var_name, _, _, _ in bot_vars:
            results[var_name] = existing.get(var_name, "")
        if is_placeholder_target(results.get("QQ_NOTIFY_TARGET", "")):
            print("  [!] 当前通知目标是示例占位符（c2c:openid），请扫码重新配置")
            action = "scan"

    if action == "scan":
        print("\n  正在生成配置链接...")
        scan = None
        try:
            from ai_email.qq_onboard import qr_register

            scan = qr_register()
        except Exception as exc:
            print(f"  [!] 扫码配置失败: {exc}")
        if not scan:
            print("  [!] 扫码未完成，QQ Bot 通知未启用（可重跑 setup 再试）")
        elif not scan.get("user_openid"):
            print("  [!] 扫码成功但未返回 openid，QQ Bot 通知未启用")
        if not scan or not scan.get("user_openid"):
            # 扫码失败回退保留现有凭据——静默清空可用配置是数据丢失 bug；
            # 仅当用户明确选择"不配置"（action 保持 skip）时才走下方清空分支
            for var_name, _, _, _ in bot_vars:
                results[var_name] = existing.get(var_name, "")
        else:
            results["QQ_APP_ID"] = scan["app_id"]
            results["QQ_CLIENT_SECRET"] = scan["client_secret"]
            results["QQ_NOTIFY_TARGET"] = f"c2c:{scan['user_openid']}"
            print(f"\n  [OK] 扫码完成，已自动填入 App ID: {scan['app_id']}")
            print(f"  [OK] 通知目标: {results['QQ_NOTIFY_TARGET']}")
            print("\n  正在验证 QQ Bot 连接...")
            ok, msg = _test_qq_bot(results["QQ_APP_ID"], results["QQ_CLIENT_SECRET"])
            if ok:
                print("  [OK] QQ Bot 连接成功")
            else:
                print(f"  [!] 连接验证失败: {msg}（配置仍将保存，可稍后重跑 setup）")

    if action == "skip":
        results["QQ_APP_ID"] = ""
        results["QQ_CLIENT_SECRET"] = ""
        results["QQ_NOTIFY_TARGET"] = ""

    # .env 按行解析 key=value：值含换行会破坏文件结构、静默丢失后续所有行
    for k, v in results.items():
        if "\n" in v or "\r" in v:
            print(f"  [!] {k} 含换行符，已截断为首行保存")
            results[k] = v.splitlines()[0]

    with open(_ENV_FILE, "w", encoding="utf-8") as f:
        f.write("# AI Email Handler 配置文件\n# 由 'ai-email setup' 自动生成\n\n")
        for k, v in results.items():
            f.write(f"{k}={v}\n")
    # 收紧权限：文件含 API_KEY/邮箱授权码/QQ Secret，POSIX 下默认 umask 可能 0644；
    # Windows 上 chmod 仅控制只读位，失败也不阻断配置流程
    with contextlib.suppress(OSError):
        os.chmod(_ENV_FILE, 0o600)
    print(f"\n[OK] 配置已保存到 {_ENV_FILE}")
    print("运行 'ai-email' 启动服务，或 'ai-email daemon' 后台运行。")


def _load_env() -> None:
    """加载 ~/.ai-email/.env 文件，不存在时提示并退出。"""
    from dotenv import load_dotenv

    if not os.path.exists(_ENV_FILE):
        print(f"[ERROR] 未找到配置文件: {_ENV_FILE}")
        print("请先运行 'ai-email setup' 配置。")
        sys.exit(1)
    load_dotenv(_ENV_FILE, override=False)


def validate_env() -> None:
    missing = [
        vn for _, vl in CONFIG_GROUPS for vn, _, _, opt in vl if not opt and not os.environ.get(vn)
    ]
    if missing:
        print(f"[ERROR] 缺少必要配置: {', '.join(missing)}")
        print("请先运行 'ai-email setup' 配置。")
        sys.exit(1)


def cmd_run() -> None:
    _load_env()
    validate_env()
    import asyncio

    from ai_email.workflow import main as run_workflow

    try:
        asyncio.run(run_workflow())
    except KeyboardInterrupt:
        print("\n正在关闭...")


def _rotate_log_if_huge(max_bytes: int = 10 * 1024 * 1024) -> None:
    """守护进程日志以追加方式作为子进程 stdout，无法在线轮转；

    启动时超限则滚动一次（.log → .log.1），避免长期运行无限增长。
    """
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > max_bytes:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except OSError:
        pass  # 轮转失败不阻断 daemon 启动


def cmd_daemon() -> None:
    _load_env()
    validate_env()
    if is_daemon_running():
        print("[ERROR] 守护进程已在运行")
        print("运行 'ai-email stop' 停止后再试。")
        sys.exit(1)
    _rotate_log_if_huge()
    log_path = os.path.abspath(LOG_FILE)
    env = os.environ.copy()
    # 用正规子命令而非 python -c 字符串拼接（顺带消除引号/转义风险）
    # 句柄交给子进程继承、父进程随即关闭：不能用 with（Popen 失败路径需手动回收）
    out = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    kwargs: dict = {
        "args": [sys.executable, "-m", "ai_email", "_worker"],
        "env": env,
        "stdout": out,
        "stderr": subprocess.STDOUT,
        "cwd": os.getcwd(),
    }
    if platform.system() == "Windows":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(**kwargs)
    except Exception:
        out.close()  # Popen 失败（如 cwd 失效）时父进程必须回收句柄
        raise
    out.close()  # 父进程关闭日志句柄（子进程已继承 fd），避免泄漏
    # 轮询确认 worker 成功持锁（#6）：崩溃则清理 PID 文件并报错，不误报启动成功
    deadline = time.time() + 5.0
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            print(f"[ERROR] 守护进程启动失败（退出码 {rc}），详见日志: {LOG_FILE}")
            remove_pid_file()
            sys.exit(1)
        if is_daemon_running():
            print(f"[OK] 守护进程已启动 (PID {proc.pid})")
            print(f"   日志文件: {LOG_FILE}")
            return
        time.sleep(0.2)
    print(f"[WARN] 守护进程已派发 (PID {proc.pid})，5 秒内未确认就绪，请用 'ai-email status' 检查")
    print(f"   日志文件: {LOG_FILE}")


def cmd_worker() -> None:
    """内部命令：守护进程 worker。加载 env、锁存活锁、写 PID、运行 pipeline。"""
    _load_env()
    validate_env()
    lock_f = acquire_worker_lock(LOCK_FILE)
    write_pid_file(os.getpid())
    import asyncio

    from ai_email.workflow import main as run_workflow

    try:
        asyncio.run(run_workflow())
    finally:
        with contextlib.suppress(Exception):
            lock_f.close()
        remove_pid_file()


def _kill_process(pid: int, force: bool = False) -> None:
    """终止进程：Windows 用 taskkill（/F 强杀），POSIX 用 SIGTERM/SIGKILL。"""
    if platform.system() == "Windows":
        cmd = ["taskkill", "/PID", str(pid)]
        if force:
            cmd.append("/F")
        # 列表参数 + capture：不做 shell 拼接，也不依赖 cmd 的重定向语法
        subprocess.run(cmd, capture_output=True, check=False)
    else:
        sig = signal.SIGKILL if force else signal.SIGTERM  # type: ignore[attr-defined]
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def _wait_until_stopped(pid: int, timeout: float = 5.0) -> bool:
    """轮询等待锁释放（worker 退出即释放）。停止返回 True。"""
    for _ in range(int(timeout / 0.5)):
        time.sleep(0.5)
        if not is_daemon_running():
            print(f"[OK] 守护进程已停止 (原 PID {pid})")
            remove_pid_file()
            return True
    return False


def cmd_stop():
    pid = read_pid_file()
    if not is_daemon_running():
        print("守护进程未在运行。")
        remove_pid_file()
        return
    if pid is None:
        # 锁被持有但 PID 文件缺失/损坏：无处下发终止信号，只能提示手动处理
        print("[ERROR] 守护进程运行中但 PID 文件缺失/损坏，无法自动停止。")
        print("        请通过任务管理器/ps 找到进程后手动终止。")
        return
    print(f"正在停止进程 {pid}...")
    # 先温和终止（WM_CLOSE/SIGTERM），等待锁释放；超时则强杀兜底。
    # 用锁释放（worker 退出即释放）判定停止，而非依赖 PID 存活检测（#1）
    _kill_process(pid)
    if _wait_until_stopped(pid):
        return
    _kill_process(pid, force=True)
    if _wait_until_stopped(pid):
        return
    print(f"[WARN] 进程 {pid} 未在超时内停止，可能需要手动终止。")


def _format_size(num_bytes: int) -> str:
    """把字节数格式化为人类可读的大小。"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _query_processed_count(db_path: str) -> str:
    """查询已成功处理的邮件数（只读、短超时，避免与运行中服务抢锁）。

    只统计 status='done'：seen_emails 还含在途（processing）与死信残留行，
    与"已处理"的展示语义不符。失败返回 'N/A'。
    """
    if not os.path.exists(db_path):
        return "N/A"
    try:
        conn = sqlite3.connect(db_path, timeout=2.0)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM seen_emails WHERE status='done'")
            return str(cur.fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return "N/A"


def _tail_log(path: str, n: int = 5) -> list[str]:
    """读取日志文件最后 n 行（utf-8, errors='replace'）。失败返回空列表。

    只从文件尾部读取约 8KB：daemon 长期运行的日志可能很大，
    全量 readlines 会拖慢 status 并占用内存。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > 8192:
                f.seek(size - 8192)
                f.readline()  # 丢弃可能被截断的半行
            chunk = f.read()
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        return lines[-n:] if len(lines) >= n else lines
    except OSError:
        return []


def cmd_status() -> None:
    pid = read_pid_file()
    if not is_daemon_running():
        if pid:
            print(f"[WARN] 残留 PID 文件 (PID {pid} 未运行)，运行 'ai-email stop' 清理。")
        else:
            print("未检测到运行中的守护进程。")
        return

    print(f"[OK] 守护进程运行中 (PID {pid})")

    # -- 日志文件状态 --
    if os.path.exists(LOG_FILE):
        log_stat = os.stat(LOG_FILE)
        mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log_stat.st_mtime))
        print(f"   日志文件: {LOG_FILE}")
        print(f"   日志最后修改: {mtime_str}  大小: {_format_size(log_stat.st_size)}")
    else:
        print(f"   日志文件: {LOG_FILE} (不存在)")

    # -- 已处理邮件数 (sqlite) --
    # 路径复用 persistence 的权威定义，避免与 _DATA_DIR 拼接产生第二份知识
    from ai_email.persistence import DEFAULT_DB_PATH

    print(f"   已处理邮件数: {_query_processed_count(DEFAULT_DB_PATH)}")

    # -- 日志最后 5 行 --
    tail_lines = _tail_log(LOG_FILE, 5)
    if tail_lines:
        print(f"   --- 日志最后 {len(tail_lines)} 行 ---")
        for line in tail_lines:
            print(f"   {line.rstrip()}")


# -- PID helpers --


def read_pid_file() -> int | None:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                return int(f.read().strip())
        except (ValueError, OSError):
            pass
    return None


def write_pid_file(pid: int) -> None:
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


# -- PID 文件锁（可靠存活检测，#1：不受 PID 复用影响） --


def _lock_file_obj(f: "IO[str]", blocking: bool) -> bool:
    """对已打开文件对象加独占锁。返回是否成功。"""
    if platform.system() == "Windows":
        import msvcrt

        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK  # type: ignore[attr-defined]
        try:
            msvcrt.locking(f.fileno(), mode, 1)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    else:
        import fcntl

        flag = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        try:
            fcntl.flock(f.fileno(), flag)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False


def acquire_worker_lock(lockfile: str = LOCK_FILE) -> "IO[str]":
    """获取存活锁（阻塞），持有期间表示 daemon 运行。返回文件对象，调用方须保持引用。

    锁与 PID 内容分离（锁单独存于 lockfile）：read_pid_file 不受 Windows mandatory
    lock 阻塞，且存活判据基于锁而非 PID，避免 PID 复用误判（#1）。
    """
    f = open(lockfile, "w")  # noqa: SIM115  # 句柄即存活锁，须由调用方长期持有
    f.write("L")  # 占位字节，确保文件非空以便 locking 探测
    f.flush()
    os.fsync(f.fileno())
    f.seek(0)
    _lock_file_obj(f, blocking=True)
    return f


def is_daemon_running(lockfile: str = LOCK_FILE) -> bool:
    """基于存活锁判断 daemon 是否运行（#1：替代 OpenProcess/os.kill）。

    能获取独占锁 = 无持有者 = 未运行；获取失败 = 运行中。
    """
    if not os.path.exists(lockfile) or os.path.getsize(lockfile) == 0:
        return False
    try:
        f = open(lockfile, "r+")  # noqa: SIM115  # 探测句柄，用毕即关，见下
    except OSError:
        # exists 检查与 open 之间锁文件可能被并发 stop/daemon 删除/重建
        return False
    f.seek(0)
    try:
        acquired = _lock_file_obj(f, blocking=False)
    except OSError:
        acquired = False
    f.close()  # 关闭探测句柄（不影响持有者的锁；成功获取时也一并释放）
    return not acquired


# -- 入口 --


def main() -> None:
    args = parse_args()
    cmd = args.command
    if cmd == "setup":
        cmd_setup()
    elif cmd == "daemon":
        cmd_daemon()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "_worker":
        cmd_worker()
    else:
        cmd_run()


if __name__ == "__main__":
    main()
