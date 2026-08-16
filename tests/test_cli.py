"""Tests for cli process management: PID-file-lock based liveness (#1/#6).

is_daemon_running uses a cross-platform exclusive lock on a dedicated lock file
(msvcrt on Windows, fcntl on POSIX) instead of OpenProcess/os.kill, so a
recycled PID can no longer be mistaken for the daemon. The lock is separate
from the PID file content so read_pid_file stays unobstructed.

flock/msvcrt locks are per-process, so liveness is verified with a real holder
subprocess (same-process acquire+probe would not contend).
"""

import os
import subprocess
import sys
import time

from ai_email.cli import acquire_worker_lock, is_daemon_running


def test_is_daemon_running_false_when_no_lockfile(tmp_path):
    lockfile = str(tmp_path / "p.lock")
    assert is_daemon_running(lockfile) is False


def test_acquire_worker_lock_returns_handle(tmp_path):
    lockfile = str(tmp_path / "p.lock")
    f = acquire_worker_lock(lockfile)
    assert f is not None
    f.close()


def test_is_daemon_running_true_when_holder_alive(tmp_path):
    """#1: 锁被持有时 is_daemon_running 返回 True（不受 PID 复用影响）。"""
    lockfile = str(tmp_path / "p.lock")
    code = (
        "import time\n"
        f"from ai_email.cli import acquire_worker_lock\n"
        f"f = acquire_worker_lock({lockfile!r})\n"
        "print('LOCKED', flush=True)\n"
        "time.sleep(60)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert holder.stdout.readline().strip() == b"LOCKED"
        # LOCKED 已打印即表明锁获取完成，无需 sleep 等待
        assert is_daemon_running(lockfile) is True
    finally:
        holder.terminate()
        holder.wait(timeout=10)


def test_is_daemon_running_false_after_holder_exits(tmp_path):
    """持锁进程退出后锁释放，is_daemon_running 返回 False。"""
    lockfile = str(tmp_path / "p.lock")
    code = (
        f"from ai_email.cli import acquire_worker_lock\n"
        f"f = acquire_worker_lock({lockfile!r})\n"
        "print('LOCKED', flush=True)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
    )
    holder.stdout.readline()  # LOCKED
    holder.wait(timeout=10)  # 脚本结束，进程退出，锁释放
    # 轮询等待锁释放可见（慢 CI 上释放传播可能有延迟），替代固定 sleep
    for _ in range(20):
        if not is_daemon_running(lockfile):
            break
        time.sleep(0.1)
    assert is_daemon_running(lockfile) is False


def test_daemon_closes_log_handle_when_popen_fails(monkeypatch, tmp_path):
    """R7 回归：Popen 抛异常（如 cwd/env 失效）时日志句柄必须被关闭。"""
    import pytest

    from ai_email import cli

    log = tmp_path / "daemon.log"
    monkeypatch.setattr(cli, "LOG_FILE", str(log))
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setattr(cli, "validate_env", lambda: None)
    monkeypatch.setattr(cli, "is_daemon_running", lambda *a, **kw: False)

    opened = {}
    real_open = open

    def _rec_open(file, mode="r", *args, **kwargs):
        f = real_open(file, mode, *args, **kwargs)
        opened[file] = f
        return f

    def _boom(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr("builtins.open", _rec_open)
    monkeypatch.setattr(cli.subprocess, "Popen", _boom)

    with pytest.raises(OSError):
        cli.cmd_daemon()
    assert opened[str(log)].closed


# --------------------------------------------------------------------------- #
# 非交互 stdin 防护：EOF 与管道输入不得挂死/崩溃 setup 向导
# --------------------------------------------------------------------------- #
def test_read_hidden_chars_eof_raises():
    """read_char 返回空串（pty 关闭/EOF）必须抛 EOFError，否则无限刷 · 挂死。"""
    import pytest

    from ai_email.cli import _read_hidden_chars

    with pytest.raises(EOFError):
        _read_hidden_chars(lambda: "", "\n")


def test_read_hidden_chars_reads_until_newline(capsys):
    from ai_email.cli import _read_hidden_chars

    feed = iter(["a", "b", "\r"])
    assert _read_hidden_chars(lambda: next(feed), "\n") == "ab"


def test_hidden_input_non_tty_reads_line(monkeypatch):
    """非 TTY stdin（管道/CI）走按行读取，不依赖 msvcrt/termios。"""
    import io

    from ai_email import cli

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("secret\n"))  # isatty()=False
    assert cli._hidden_input("密码: ") == "secret"


def test_hidden_input_non_tty_eof_raises(monkeypatch):
    """非 TTY stdin 已耗尽时抛 EOFError——返回空串会被上层当空输入无限重试。"""
    import io

    import pytest

    from ai_email import cli

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))
    with pytest.raises(EOFError):
        cli._hidden_input("密码: ")


# --------------------------------------------------------------------------- #
# 【高】cmd_setup 扫码失败必须保留已有 QQ Bot 配置（静默清空属数据丢失）；
# 只有用户明确选择"不配置"才清空
# --------------------------------------------------------------------------- #
def _run_setup(monkeypatch, tmp_path, env_text, inputs, scan_result=None):
    """驱动 cmd_setup：重定向数据目录与 .env，mock 交互与外部依赖。

    返回写入的 .env 内容（键值 dict）。inputs 按序回答所有 input() 提问。
    """
    from ai_email import cli

    env_file = tmp_path / ".env"
    if env_text is not None:
        env_file.write_text(env_text, encoding="utf-8")
    monkeypatch.setattr(cli, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_ENV_FILE", str(env_file))

    def fake_prompt(var_name, prompt, hidden, default="", optional=False):
        return default or "test-value"

    responses = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    monkeypatch.setattr(cli, "_prompt_value", fake_prompt)
    monkeypatch.setattr(cli, "_test_model_connection", lambda *a: (True, "ok"))
    monkeypatch.setattr("ai_email.qq_onboard.qr_register", lambda: scan_result)
    monkeypatch.setattr(cli, "_test_qq_bot", lambda *a: (True, ""))

    cli.cmd_setup()
    values = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            values[k] = v
    return values


_FULL_ENV = "\n".join(
    [
        "MODEL=old-model",
        "BASE_URL=http://old/v1",
        "API_KEY=old-key",
        "QQEMAIL=old@qq.com",
        "EMAIL_PASSWORD=old-pwd",
        "QQ_APP_ID=old-appid",
        "QQ_CLIENT_SECRET=old-secret",
        "QQ_NOTIFY_TARGET=c2c:old-openid",
        "",
    ]
)


def test_setup_scan_failure_preserves_existing_qq_config(monkeypatch, tmp_path):
    """扫码失败（qr_register 返回 None）时保留现有 QQ 凭据，不得清空写入 .env。"""
    values = _run_setup(
        monkeypatch,
        tmp_path,
        env_text=_FULL_ENV,
        inputs=["", "2"],  # 基于现有配置修改；扫码重新配置
        scan_result=None,
    )
    assert values["QQ_APP_ID"] == "old-appid"
    assert values["QQ_CLIENT_SECRET"] == "old-secret"
    assert values["QQ_NOTIFY_TARGET"] == "c2c:old-openid"


def test_setup_scan_success_without_openid_preserves_config(monkeypatch, tmp_path):
    """扫码成功但未返回 openid：同样回退保留现有配置。"""
    values = _run_setup(
        monkeypatch,
        tmp_path,
        env_text=_FULL_ENV,
        inputs=["", "2"],
        scan_result={"app_id": "new-appid", "client_secret": "new-secret"},  # 无 openid
    )
    assert values["QQ_APP_ID"] == "old-appid"


def test_setup_scan_success_writes_scanned_values(monkeypatch, tmp_path):
    """扫码成功路径不受影响：新凭据写入 .env。"""
    values = _run_setup(
        monkeypatch,
        tmp_path,
        env_text=_FULL_ENV,
        inputs=["", "2"],
        scan_result={
            "app_id": "new-appid",
            "client_secret": "new-secret",
            "user_openid": "new-openid",
        },
    )
    assert values["QQ_APP_ID"] == "new-appid"
    assert values["QQ_CLIENT_SECRET"] == "new-secret"
    assert values["QQ_NOTIFY_TARGET"] == "c2c:new-openid"


def test_setup_explicit_skip_still_clears(monkeypatch, tmp_path):
    """用户明确选择不配置（非扫码失败）仍清空 QQ 项——原有语义不回归。"""
    values = _run_setup(
        monkeypatch,
        tmp_path,
        env_text=_FULL_ENV.replace("QQ_APP_ID=old-appid", "QQ_APP_ID="),
        inputs=["", ""],  # 基于现有修改；扫码配置？[y/N] 回车=N
    )
    assert values["QQ_APP_ID"] == ""
    assert values["QQ_CLIENT_SECRET"] == ""
    assert values["QQ_NOTIFY_TARGET"] == ""


def test_setup_sanitizes_newline_in_values(monkeypatch, tmp_path):
    """值含换行会破坏 .env 的按行解析：必须截断为首行并提示。"""
    from ai_email import cli

    env_file = tmp_path / ".env"
    env_file.write_text("MODEL=m\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_ENV_FILE", str(env_file))
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    monkeypatch.setattr(
        cli,
        "_prompt_value",
        lambda var_name, prompt, hidden, default="", optional=False: (
            "bad\nQQ_APP_ID=injected" if var_name == "API_KEY" else (default or "v")
        ),
    )
    monkeypatch.setattr(cli, "_test_model_connection", lambda *a: (True, "ok"))
    monkeypatch.setattr("ai_email.qq_onboard.qr_register", lambda: None)

    cli.cmd_setup()
    content = env_file.read_text(encoding="utf-8")
    assert "injected" not in content  # 换行注入的第二行被清除
    assert "API_KEY=bad" in content


def test_tail_log_reads_only_tail_of_big_file(tmp_path):
    """大日志只从尾部读取：不整载入内存，且返回最后 n 行。"""
    from ai_email.cli import _tail_log

    log = tmp_path / "big.log"
    lines = [f"line-{i}" for i in range(10000)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _tail_log(str(log), 3) == ["line-9997", "line-9998", "line-9999"]
