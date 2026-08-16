"""Tests for QQEmailListener: interface contract + ``_decode_bytes``."""

import asyncio
import email
import inspect

import pytest

from ai_email.qq_email_listener import QQEmailListener, _decode_bytes


# --------------------------------------------------------------------------- #
# _decode_bytes
# --------------------------------------------------------------------------- #
def test_decode_utf8_bytes():
    assert _decode_bytes("你好".encode(), "utf-8") == "你好"


def test_decode_gbk_bytes():
    assert _decode_bytes("你好".encode("gbk"), "gbk") == "你好"


def test_decode_wrong_charset_fallback():
    assert _decode_bytes(b"hello", "nonexistent-charset") == "hello"


def test_decode_no_charset():
    assert _decode_bytes(b"hello") == "hello"


def test_decode_latin1_fallback():
    # b"\xff\xfe" is invalid utf-8 -> should still return *something*
    assert _decode_bytes(b"\xff\xfe", "utf-8") is not None


def test_decode_empty_payload():
    assert _decode_bytes(b"") == ""


# --------------------------------------------------------------------------- #
# QQEmailListener interface
# --------------------------------------------------------------------------- #
def _make_listener():
    return QQEmailListener("test@qq.com", "pwd")


def test_listener_has_required_attributes():
    listener = _make_listener()
    assert hasattr(listener, "connect")
    assert hasattr(listener, "disconnect")
    assert hasattr(listener, "listen_for_emails")
    assert hasattr(listener, "stop_listening")


def test_connect_is_coroutine():
    listener = _make_listener()
    assert asyncio.iscoroutinefunction(listener.connect)


def test_disconnect_is_coroutine():
    listener = _make_listener()
    assert asyncio.iscoroutinefunction(listener.disconnect)


def test_listen_for_emails_is_async():
    listener = _make_listener()
    assert asyncio.iscoroutinefunction(listener.listen_for_emails) or inspect.isasyncgenfunction(
        listener.listen_for_emails
    )


def test_stop_listening_sets_flag():
    listener = _make_listener()
    assert listener.should_stop is False
    listener.stop_listening()
    assert listener.should_stop is True


def test_last_uid_defaults_none():
    listener = _make_listener()
    assert listener.last_uid is None


# --------------------------------------------------------------------------- #
# _decode_subject
# --------------------------------------------------------------------------- #
def test_decode_subject_none():
    assert _make_listener()._decode_subject(None) == ""


def test_decode_subject_ascii():
    assert _make_listener()._decode_subject("Hello") == "Hello"


def test_decode_subject_encoded_utf8():
    assert _make_listener()._decode_subject("=?utf-8?B?5L2g5aW9?=") == "你好"


# --------------------------------------------------------------------------- #
# _get_body
# --------------------------------------------------------------------------- #
def test_get_body_single_part():
    msg = email.message_from_string("From: a@b.com\nSubject: test\n\nHello body")
    assert _make_listener()._get_body(msg) == "Hello body"


# --------------------------------------------------------------------------- #
# First-start behavior: last_uid persistence (eliminates restart amnesia)
# --------------------------------------------------------------------------- #
def _mock_uid_search(uids):
    """Build a fake client.uid_search response returning the given UIDs."""
    return ("OK", [b" ".join(str(u).encode() for u in uids)])


def _mock_uid_fetch(uid, body="hello"):
    """Build a fake client.uid response for 'fetch'."""
    msg_bytes = f"From: s@x.com\nSubject: t\n\ntest body {uid}".encode()
    return ("OK", [f"1 (UID {uid} RFC822 {{{len(msg_bytes)}}}".encode(), msg_bytes])


class _FakeClient:
    """Minimal IMAP client stub for check_new_emails."""

    def __init__(self, uids):
        self._uids = uids

    async def select(self, *a, **kw):
        return ("OK", [b"1"])

    async def uid_search(self, *criteria, charset=None):
        return _mock_uid_search(self._uids)

    async def uid(self, command, *args):
        if command == "fetch":
            # args[0] is the uid string
            return _mock_uid_fetch(args[0])
        return ("BAD", [])


def test_first_start_fresh_deployment_uses_max_as_baseline(tmp_path):
    """全新部署（db 无 last_uid）：用当前最大 UID 作基线，返回空，不处理历史邮件。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FakeClient([100, 101, 102])
    result = asyncio.run(listener.check_new_emails())
    assert result == []  # 跳过历史
    assert listener.last_uid == 102
    # 持久化了基线
    assert persistence.get_last_uid(str(db)) == 102


def test_first_start_restores_from_persistence(tmp_path):
    """重启恢复（db 有 last_uid=100）：处理大于 100 的新邮件，不丢 101/102。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FakeClient([100, 101, 102])
    result = asyncio.run(listener.check_new_emails())
    assert len(result) == 2  # 101, 102 被处理，不丢
    assert [r["id"] for r in result] == ["101", "102"]
    assert listener.last_uid == 102
    assert persistence.get_last_uid(str(db)) == 102


def test_no_db_path_uses_max_baseline_returning_empty():
    """无 db_path（旧行为兼容）：用当前最大作基线返回空。"""
    listener = QQEmailListener("t@q.com", "p")  # db_path=None
    listener.client = _FakeClient([50, 51])
    result = asyncio.run(listener.check_new_emails())
    assert result == []
    assert listener.last_uid == 51


def test_retry_queue_emails_refetched_without_new(tmp_path):
    """retry_queue 有项时，即使无新邮件（uid ≤ last_uid）也重新 fetch 重试。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))  # 基线100
    persistence.enqueue_retry("100", str(db))  # 100 在重试队列（≤last_uid）
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FakeClient([100])  # 邮箱只有100，无新邮件
    listener.retry_backoff = 0  # 测试环境绕过退避
    result = asyncio.run(listener.check_new_emails())
    # 无新邮件，但 retry 的100被重新 fetch 并产出
    assert len(result) == 1
    assert result[0]["id"] == "100"


def test_retry_backoff_skips_recent(tmp_path):
    """退避期内（刚入队）的邮件不被重投，避免风暴重试。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    persistence.enqueue_retry("100", str(db))  # 刚入队
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FakeClient([100])
    listener.retry_backoff = 60  # 60秒退避，刚入队的不会被取
    result = asyncio.run(listener.check_new_emails())
    assert result == []  # 退避期内不重投


def test_retry_dead_letter_cleared_on_fetch_fail(tmp_path):
    """毛病2：fetch 失败（邮件被删）的 retry 记录被清除，避免死信堆积。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    persistence.enqueue_retry("999", str(db))  # 999 不在邮箱里（被删）

    class _FetchFailClient(_FakeClient):
        async def uid(self, command, *args):
            if command == "fetch":
                return ("BAD", [])  # fetch 失败
            return await super().uid(command, *args)

    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FetchFailClient([100])
    listener.retry_backoff = 0
    asyncio.run(listener.check_new_emails())
    # 死信记录应被清除
    assert persistence.dequeue_retry(str(db)) == []


def test_connection_error_propagates_for_reconnect(tmp_path):
    """毛病7：select/search 抛异常（连接断开）应冒泡，不返回 [] 静默吞掉。"""
    db = tmp_path / "seen.db"
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))

    class _ConnFailClient(_FakeClient):
        async def select(self, *a, **kw):
            raise ConnectionError("IMAP connection lost")

    listener.client = _ConnFailClient([100])
    with pytest.raises(ConnectionError):
        asyncio.run(listener.check_new_emails())


def test_incremental_search_after_first_start(tmp_path):
    """毛病5：非首启时用增量搜索 UID n:* 而非全量 ALL。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.set_last_uid(100, str(db))

    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.retry_backoff = 0

    search_commands = []

    class _RecordingClient(_FakeClient):
        async def uid_search(self, *criteria, charset=None):
            search_commands.append(criteria)
            return await super().uid_search(*criteria, charset=charset)

    listener.client = _RecordingClient([100, 101, 102])
    result = asyncio.run(listener.check_new_emails())
    # 增量搜索而非全量
    assert len(result) == 2  # 101, 102
    assert any("UID 101:*" in str(a) for a in search_commands)
    assert not any("ALL" in str(a) for a in search_commands)


# --------------------------------------------------------------------------- #
# #10: _fetch_email 空响应防护（status OK 但 lines 为空时不抛 IndexError）
# --------------------------------------------------------------------------- #
def test_fetch_email_empty_response_returns_none(tmp_path):
    """fetch 返回 OK 但内容为空时返回 None，不抛 IndexError 导致整条监听重连。"""
    db = tmp_path / "seen.db"
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))

    class _EmptyFetchClient(_FakeClient):
        async def uid(self, command, *args):
            if command == "fetch":
                return ("OK", [])
            return await super().uid(command, *args)

    listener.client = _EmptyFetchClient([100])
    assert asyncio.run(listener._fetch_email("100")) is None


# --------------------------------------------------------------------------- #
# #3: connect 失败时清理半初始化的 client（避免下次 connect 覆盖时泄漏）
# --------------------------------------------------------------------------- #
def test_connect_clears_client_on_failure():
    from unittest.mock import patch

    listener = QQEmailListener("t@q.com", "p")

    class _BoomClient:
        def __init__(self, *a, **kw):
            pass

        async def wait_hello_from_server(self):
            raise RuntimeError("connect boom")

    with patch("ai_email.qq_email_listener.aioimaplib.IMAP4_SSL", _BoomClient):
        result = asyncio.run(listener.connect())
    assert result is False
    assert listener.client is None


# --------------------------------------------------------------------------- #
# #3: disconnect 即使 logout 抛异常也必须清空 self.client
# --------------------------------------------------------------------------- #
def test_disconnect_clears_client_even_if_logout_raises():
    listener = QQEmailListener("t@q.com", "p")

    class _BadLogoutClient(_FakeClient):
        async def logout(self):
            raise RuntimeError("logout boom")

    listener.client = _BadLogoutClient([100])
    asyncio.run(listener.disconnect())
    assert listener.client is None


# --------------------------------------------------------------------------- #
# #8: UIDVALIDITY 变化时重置基线与去重表，避免旧 UID 空间误判/丢失
# --------------------------------------------------------------------------- #
def test_parse_uidvalidity():
    assert QQEmailListener._parse_uidvalidity([b"OK [UIDVALIDITY 1432]"]) == 1432
    assert QQEmailListener._parse_uidvalidity([b"* OK [UIDVALIDITY 7] [UIDNEXT 9]"]) == 7
    assert QQEmailListener._parse_uidvalidity([b"1"]) is None
    assert QQEmailListener._parse_uidvalidity([]) is None


def test_uidvalidity_change_resets_state(tmp_path):
    """UIDVALIDITY 变化 → last_uid 清空 → 重新以当前最大 UID 为基线。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    persistence.set_uidvalidity(1, str(db))

    class _UidvalClient(_FakeClient):
        async def select(self, *a, **kw):
            return ("OK", [b"OK [UIDVALIDITY 2]"])

    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _UidvalClient([50])  # 新 UID 空间最大 50（< 旧 last_uid 100）
    asyncio.run(listener.check_new_emails())
    assert listener.last_uid == 50  # 重置后重新基线
    assert persistence.get_uidvalidity(str(db)) == 2


def test_uidvalidity_unchanged_keeps_state(tmp_path):
    """UIDVALIDITY 不变 → 保留 last_uid，正常增量推进。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    persistence.set_uidvalidity(2, str(db))

    class _UidvalClient(_FakeClient):
        async def select(self, *a, **kw):
            return ("OK", [b"OK [UIDVALIDITY 2]"])

    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _UidvalClient([100, 101])
    result = asyncio.run(listener.check_new_emails())
    assert listener.last_uid == 101  # 未重置，正常推进
    assert [r["id"] for r in result] == ["101"]


# --------------------------------------------------------------------------- #
# 重试队列不重复派发：在途（claimed）/已完成（done）的 uid 跳过重投
# --------------------------------------------------------------------------- #
def test_retry_not_refetched_while_in_flight_or_done(tmp_path):
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    persistence.enqueue_retry("100", str(db))

    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FakeClient([100])
    listener.retry_backoff = 0

    # 在途：claim 占位 processing → 不重投（否则处理期间每轮派发刷屏）
    persistence.claim("100", str(db))
    assert asyncio.run(listener.check_new_emails()) == []

    # 未占位（上次失败已 release）→ 正常重投
    persistence.release("100", str(db))
    result = asyncio.run(listener.check_new_emails())
    assert [r["id"] for r in result] == ["100"]

    # 已完成：mark_done → 不重投
    persistence.claim("100", str(db))
    persistence.mark_done("100", str(db))
    assert asyncio.run(listener.check_new_emails()) == []


# --------------------------------------------------------------------------- #
# R1 回归：新邮件 fetch 失败不得永久丢失（旧代码推进 last_uid 后无补救路径）
# --------------------------------------------------------------------------- #
class _PartialFailClient(_FakeClient):
    """指定 uid 的 fetch 返回 BAD，其余正常。"""

    def __init__(self, uids, fail_uids):
        super().__init__(uids)
        self._fail_uids = {str(u) for u in fail_uids}

    async def uid(self, command, *args):
        if command == "fetch" and args[0] in self._fail_uids:
            return ("BAD", [])
        return await super().uid(command, *args)


def test_new_email_fetch_fail_enqueued_not_lost(tmp_path):
    """部分新邮件 fetch 失败：失败者入重试队列，成功者正常产出，基线推进不丢信。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _PartialFailClient([100, 101, 102], fail_uids=[101])
    result = asyncio.run(listener.check_new_emails())
    assert [r["id"] for r in result] == ["102"]  # 101 失败被跳过，102 正常产出
    assert listener.last_uid == 102
    # 101 不随基线推进丢失：已持久化进重试队列
    retried = {uid for uid, _ in persistence.dequeue_retry(str(db), min_age=0)}
    assert "101" in retried
    assert persistence.get_last_uid(str(db)) == 102


def test_new_email_fetch_fail_all_keeps_baseline(tmp_path):
    """全部新邮件 fetch 失败：全部入重试队列，基线仍推进（有持久化兜底）。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _PartialFailClient([100, 101, 102], fail_uids=[101, 102])
    result = asyncio.run(listener.check_new_emails())
    assert result == []
    retried = {uid for uid, _ in persistence.dequeue_retry(str(db), min_age=0)}
    assert retried == {"101", "102"}


def test_new_email_fetch_fail_no_db_does_not_advance_past_failure():
    """无 db_path 兜底：基线只推进到失败者之前的连续成功前缀，失败者下轮重搜。"""
    listener = QQEmailListener("t@q.com", "p")  # db_path=None
    listener.last_uid = 50
    listener.client = _PartialFailClient([50, 51, 52], fail_uids=[51])
    result = asyncio.run(listener.check_new_emails())
    assert [r["id"] for r in result] == ["52"]
    assert listener.last_uid == 50  # 不越过失败的 51，下轮增量搜索仍覆盖 51/52


def test_new_email_fetch_fail_recovered_via_retry(tmp_path):
    """上轮 fetch 失败入队的邮件，网络恢复后经重试路径重新产出。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(102, str(db))
    persistence.enqueue_retry("101", str(db))  # 上一轮 fetch 失败的兜底记录
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FakeClient([101, 102])  # fetch 已恢复
    listener.retry_backoff = 0
    result = asyncio.run(listener.check_new_emails())
    assert [r["id"] for r in result] == ["101"]


# --------------------------------------------------------------------------- #
# R8 回归：fetch 成功的新邮件必须先 claim（processing 行）再推进基线——
# 旧代码先推进 last_uid 后返回、claim 发生在 workflow 侧，窗口内硬崩溃的邮件
# 既不在 seen_emails 也不在 retry_queue，增量搜索不再覆盖 → 永久丢失
# --------------------------------------------------------------------------- #
def test_new_emails_claimed_before_baseline_advance(tmp_path):
    """崩溃安全不变量：基线推进覆盖的每个 uid 都有 seen_emails 占位行。"""
    import sqlite3

    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FakeClient([100, 101, 102])
    result = asyncio.run(listener.check_new_emails())
    assert [r["id"] for r in result] == ["101", "102"]
    # 返回的邮件带预占位标记，workflow.run 据此跳过重复 claim
    assert all(r.get("_pre_claimed") is True for r in result)
    # 不变量：每个被基线越过的 uid 都有 processing 占位行
    with sqlite3.connect(str(db)) as conn:
        statuses = dict(conn.execute("SELECT uid, status FROM seen_emails").fetchall())
    assert statuses == {"101": "processing", "102": "processing"}
    assert persistence.get_last_uid(str(db)) == 102


def test_new_email_already_claimed_not_dispatched(tmp_path):
    """增量搜到的 uid 若已有 seen 行（如重试路径刚派发同号邮件），不重复派发。"""
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    persistence.claim("101", str(db))  # 重试路径已占位
    listener = QQEmailListener("t@q.com", "p", db_path=str(db))
    listener.client = _FakeClient([100, 101, 102])
    result = asyncio.run(listener.check_new_emails())
    assert [r["id"] for r in result] == ["102"]  # 101 被跳过
    assert persistence.get_last_uid(str(db)) == 102  # 基线照常推进（101 已有保护）


# --------------------------------------------------------------------------- #
# 重连退避自愈循环：check_new_emails 连续抛异常后恢复，监听不终止、退避翻倍
# --------------------------------------------------------------------------- #
def test_listen_for_emails_reconnects_with_backoff(monkeypatch):
    listener = QQEmailListener("t@q.com", "p")
    stats = {"connect": 0, "check": 0}
    emails_out = []
    sleeps = []

    async def fake_connect():
        stats["connect"] += 1
        return True

    async def fake_check():
        stats["check"] += 1
        if stats["check"] <= 2:
            raise ConnectionError("imap lost")
        if stats["check"] == 3:
            return [{"id": "1", "subject": "s", "from": "a@b.c", "date": "d", "content": "x"}]
        listener.stop_listening()  # 第 4 轮：验证存活后主动收尾
        return []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(listener, "connect", fake_connect)
    monkeypatch.setattr(listener, "check_new_emails", fake_check)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def consume():
        async for item in listener.listen_for_emails(check_interval=5):
            emails_out.append(item)

    asyncio.run(consume())
    assert stats["connect"] == 3  # 断线 2 次重连后恢复
    assert [e["email_id"] for e in emails_out] == ["1"]
    # 断线退避 10→20 翻倍；恢复后正常轮询 sleep(5)，退避重置
    assert sleeps == [10, 20, 5, 5]


def test_listen_for_emails_yields_pre_claimed_marker(monkeypatch):
    """listener 侧已预占位的邮件，产出时必须带 _pre_claimed 标记
    （workflow.run 依赖该标记跳过重复 claim，丢失标记会误判"已处理"跳过）。"""
    listener = QQEmailListener("t@q.com", "p")

    async def fake_connect():
        return True

    async def fake_check():
        listener.stop_listening()
        return [
            {
                "id": "7",
                "subject": "s",
                "from": "a@b.c",
                "date": "d",
                "content": "x",
                "_pre_claimed": True,
            }
        ]

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(listener, "connect", fake_connect)
    monkeypatch.setattr(listener, "check_new_emails", fake_check)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    out = []

    async def consume():
        async for item in listener.listen_for_emails(check_interval=5):
            out.append(item)

    asyncio.run(consume())
    assert out and out[0]["_pre_claimed"] is True
    assert out[0]["email_id"] == "7"


# --------------------------------------------------------------------------- #
# 回归：aioimaplib 的 RFC822 载荷是 bytearray（不是 bytes 子类），
# 旧代码 isinstance(raw, bytes) 不命中 → str(bytearray) 解析不出任何头
# （From/Subject 全为 None、正文为空 → 空收件人 → RCPT TO:<> → 501）
# --------------------------------------------------------------------------- #
def test_fetch_email_parses_bytearray_payload():
    import base64

    subject_b64 = base64.b64encode("标题".encode()).decode("ascii")
    msg_bytes = (f"From: s@x.com\nSubject: =?utf-8?B?{subject_b64}?=\n\n正文 hello").encode()

    class _BytearrayFetchClient(_FakeClient):
        async def uid(self, command, *args):
            if command == "fetch":
                return (
                    "OK",
                    [
                        f"1 (UID 100 RFC822 {{{len(msg_bytes)}}}".encode(),
                        bytearray(msg_bytes),  # 还原 aioimaplib 的真实返回类型
                    ],
                )
            return await super().uid(command, *args)

    listener = QQEmailListener("t@q.com", "p")
    listener.client = _BytearrayFetchClient([100])
    info = asyncio.run(listener._fetch_email("100"))
    assert info["from"] == "s@x.com"
    assert info["subject"] == "标题"
    assert "正文 hello" in info["content"]
