"""Tests for _run_email_pipeline: bounded worker pool, exception isolation,
graceful shutdown and resource cleanup.

All collaborators are stubbed; no network or real IMAP/SMTP is touched.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import ai_email.workflow as mod


class _FakeListener:
    """Minimal listener stub: yields preset emails, supports stop/disconnect."""

    def __init__(self, emails):
        self._emails = list(emails)
        self._stopped = False

    async def listen_for_emails(self, check_interval=5):
        for email in self._emails:
            if self._stopped:
                return
            yield email

    def stop_listening(self):
        self._stopped = True

    async def disconnect(self):
        pass


def _make_email(i):
    return {"email_id": str(i), "sender_email": "u@x.com", "email_content": "c"}


# --------------------------------------------------------------------------- #
# All emails processed
# --------------------------------------------------------------------------- #
def test_worker_pool_processes_all_emails():
    """Every email dispatched by the listener is processed by the pool."""
    emails = [_make_email(i) for i in range(5)]
    listener = _FakeListener(emails)

    processed_ids = []

    class _Wf:
        context = MagicMock(qq_notifier=None)

        async def run(self, email_data):
            processed_ids.append(email_data["email_id"])
            return True

    result = asyncio.run(
        mod._run_email_pipeline(_Wf(), listener, concurrency=3, install_signal_handlers=False)
    )
    assert result == 5
    assert sorted(processed_ids) == ["0", "1", "2", "3", "4"]


# --------------------------------------------------------------------------- #
# Exception isolation
# --------------------------------------------------------------------------- #
def test_worker_pool_isolates_exceptions():
    """A single email raising does not prevent the others from processing."""
    emails = [_make_email(i) for i in range(5)]
    listener = _FakeListener(emails)

    class _Wf:
        context = MagicMock(qq_notifier=None)

        attempted = []  # 记录被尝试的 id，验证异常隔离（都被尝试了）

        async def run(self, email_data):
            eid = email_data["email_id"]
            self.attempted.append(eid)
            if eid == "2":
                raise RuntimeError("boom")
            return True

    wf = _Wf()
    result = asyncio.run(
        mod._run_email_pipeline(wf, listener, concurrency=3, install_signal_handlers=False)
    )
    # 所有 5 封都被尝试（异常被隔离，不影响其它）
    assert sorted(wf.attempted) == ["0", "1", "2", "3", "4"]
    # 但只有 4 封成功完成（"2" 抛异常，不计入已处理）
    assert result == 4


# --------------------------------------------------------------------------- #
# Concurrency bound
# --------------------------------------------------------------------------- #
def test_concurrency_is_bounded():
    """At most ``concurrency`` emails run simultaneously."""
    state = {"current": 0, "max": 0}

    class _Wf:
        context = MagicMock(qq_notifier=None)

        async def run(self, email_data):
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
            await asyncio.sleep(0.03)
            state["current"] -= 1
            return True

    emails = [_make_email(i) for i in range(8)]
    listener = _FakeListener(emails)
    asyncio.run(
        mod._run_email_pipeline(_Wf(), listener, concurrency=3, install_signal_handlers=False)
    )
    assert state["max"] <= 3
    assert state["max"] >= 2  # genuinely concurrent, not serial


# --------------------------------------------------------------------------- #
# Graceful shutdown closes resources
# --------------------------------------------------------------------------- #
def test_graceful_shutdown_closes_listener_and_notifier():
    """disconnect() and notifier.close() are called on exit."""
    notifier = MagicMock()
    notifier.close = AsyncMock()

    listener = _FakeListener([_make_email(1)])
    listener.disconnect = AsyncMock()

    class _Wf:
        def __init__(self):
            self.context = MagicMock(qq_notifier=notifier)

        async def run(self, email_data):
            return True

    asyncio.run(
        mod._run_email_pipeline(_Wf(), listener, concurrency=2, install_signal_handlers=False)
    )
    listener.disconnect.assert_awaited_once()
    notifier.close.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Graceful shutdown waits for in-flight tasks
# --------------------------------------------------------------------------- #
def test_graceful_shutdown_waits_for_inflight():
    """In-flight tasks are awaited (not abandoned) when the listener stops."""
    finished = {"done": False}

    class _QuickExitListener:
        """Yields one email then returns immediately, simulating listener stop.
        The worker task is still running when dispatch ends, so _graceful_shutdown
        must await it."""

        def __init__(self):
            self._stopped = False

        async def listen_for_emails(self, check_interval=5):
            yield _make_email(1)
            # generator ends -> dispatch_task completes -> finally triggers shutdown
            # while the worker is still sleeping

        def stop_listening(self):
            self._stopped = True

        async def disconnect(self):
            pass

    class _Wf:
        context = MagicMock(qq_notifier=None)

        async def run(self, email_data):
            await asyncio.sleep(0.1)  # still in-flight when shutdown triggers
            finished["done"] = True
            return True

    listener = _QuickExitListener()
    result = asyncio.run(
        mod._run_email_pipeline(_Wf(), listener, concurrency=2, install_signal_handlers=False)
    )
    assert result == 1
    assert finished["done"] is True
