"""Tests for persistence.py: init_db / is_seen / claim-release lifecycle.

Uses the ``tmp_path`` fixture so the real ``~/.ai-email/seen.db`` is never
touched.
"""

from ai_email import persistence


def test_init_db_creates_file(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert db.exists()


def test_is_seen_fresh_false(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.is_seen("100", str(db)) is False


def test_claim_then_is_seen_true(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    assert persistence.is_seen("100", str(db)) is True


def test_is_seen_other_uid_false(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    assert persistence.is_seen("200", str(db)) is False


def test_init_db_idempotent_preserves_data(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    persistence.init_db(str(db))  # re-init must not destroy existing rows
    assert persistence.is_seen("100", str(db)) is True


def test_int_uid_coerced_to_str(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim(300, str(db))
    assert persistence.is_seen("300", str(db)) is True
    assert persistence.is_seen(300, str(db)) is True


def test_db_in_nested_directory(tmp_path):
    db = tmp_path / "deep" / "nested" / "dir" / "seen.db"
    persistence.init_db(str(db))
    assert db.exists()
    assert persistence.is_seen("1", str(db)) is False


# --------------------------------------------------------------------------- #
# claim / release (concurrency-safe deduplication lock)
# --------------------------------------------------------------------------- #
def test_claim_returns_true_for_new_uid(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.claim("100", str(db)) is True


def test_claim_returns_false_for_existing_uid(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.claim("100", str(db)) is True
    # Second claim on the same uid must return False (already taken)
    assert persistence.claim("100", str(db)) is False


def test_claim_makes_is_seen_true(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    assert persistence.is_seen("100", str(db)) is True


def test_release_allows_retry(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    assert persistence.is_seen("100", str(db)) is True
    # Release -> uid is gone
    persistence.release("100", str(db))
    assert persistence.is_seen("100", str(db)) is False
    # Can reclaim after release
    assert persistence.claim("100", str(db)) is True


def test_release_nonexistent_uid_no_error(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    # Deleting a uid that was never inserted must not raise
    persistence.release("999", str(db))


def test_claim_release_claim_cycle(tmp_path):
    """Full lifecycle: claim -> fail -> release -> re-claim succeeds."""
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.claim("555", str(db)) is True
    persistence.release("555", str(db))
    assert persistence.claim("555", str(db)) is True
    assert persistence.is_seen("555", str(db)) is True


def test_claim_int_uid_coerced(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.claim(700, str(db)) is True
    assert persistence.is_seen("700", str(db)) is True
    assert persistence.claim("700", str(db)) is False


# --------------------------------------------------------------------------- #
# last_uid persistence (eliminates restart amnesia)
# --------------------------------------------------------------------------- #
def test_get_last_uid_returns_none_when_absent(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.get_last_uid(str(db)) is None


def test_set_then_get_last_uid(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.set_last_uid(1024, str(db))
    assert persistence.get_last_uid(str(db)) == 1024


def test_set_last_uid_overwrites(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.set_last_uid(100, str(db))
    persistence.set_last_uid(200, str(db))
    assert persistence.get_last_uid(str(db)) == 200


def test_set_last_uid_int_coerced(tmp_path):
    """set 接受 int，get 返回 int，往返保持类型。"""
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.set_last_uid("999", str(db))  # str 输入也应接受
    assert persistence.get_last_uid(str(db)) == 999
    assert isinstance(persistence.get_last_uid(str(db)), int)


# --------------------------------------------------------------------------- #
# retry_queue（发送失败/异常邮件的独立重试队列）
# --------------------------------------------------------------------------- #
def test_enqueue_retry_increments_attempts(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.enqueue_retry("101", str(db)) == 1
    assert persistence.enqueue_retry("101", str(db)) == 2
    assert persistence.enqueue_retry("101", str(db)) == 3


def test_dequeue_retry_returns_all(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.enqueue_retry("101", str(db))
    persistence.enqueue_retry("102", str(db))
    items = persistence.dequeue_retry(str(db))
    uids = sorted(uid for uid, _ in items)
    assert uids == ["101", "102"]


def test_dequeue_retry_empty(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.dequeue_retry(str(db)) == []


def test_clear_retry_removes_entry(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.enqueue_retry("101", str(db))
    persistence.enqueue_retry("102", str(db))
    persistence.clear_retry("101", str(db))
    items = persistence.dequeue_retry(str(db))
    assert [uid for uid, _ in items] == ["102"]


def test_dequeue_retry_min_age_filter(tmp_path):
    """退避过滤：min_age>0 时只返回入队时间距今超过 min_age 的。"""
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.enqueue_retry("recent", str(db))  # 刚入队
    # 退避 60 秒，recent 不应被返回
    assert persistence.dequeue_retry(str(db), min_age=60) == []
    # 不退避，recent 应被返回
    assert persistence.dequeue_retry(str(db), min_age=0) == [("recent", 1)]


# --------------------------------------------------------------------------- #
# 崩溃恢复对账 (#2)：claim 后进程被 SIGKILL，seen_emails 残留 processing，
# 重启时 reconcile_orphans 把这些"孤儿"移入 retry_queue 重新处理，避免永久丢失。
# --------------------------------------------------------------------------- #
def _status(db, uid):
    conn = persistence._connect(str(db))
    try:
        row = conn.execute("SELECT status FROM seen_emails WHERE uid=?", (str(uid),)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def test_claim_marks_processing(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    assert _status(db, "100") == "processing"


def test_mark_done_sets_done(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    persistence.mark_done("100", str(db))
    assert _status(db, "100") == "done"


def test_reconcile_orphans_moves_processing_to_retry(tmp_path):
    """claim 后进程崩溃(无 mark_done/release/enqueue)→ 对账把残留移入重试队列。"""
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))  # 模拟崩溃：不再做任何后续
    orphans = persistence.reconcile_orphans(str(db))
    assert "100" in orphans
    assert persistence.is_seen("100", str(db)) is False  # 从 seen 删除
    assert [u for u, _ in persistence.dequeue_retry(str(db))] == ["100"]  # 入重试队列


def test_reconcile_orphans_skips_done(tmp_path):
    """已成功处理(done)的不被回滚，仍保留去重。"""
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    persistence.mark_done("100", str(db))
    assert persistence.reconcile_orphans(str(db)) == []
    assert persistence.is_seen("100", str(db)) is True


def test_reconcile_orphans_empty(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.reconcile_orphans(str(db)) == []


def test_reclaim_after_reconcile(tmp_path):
    """对账后崩溃的 uid 可重新被 claim（完整恢复链路）。"""
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    persistence.reconcile_orphans(str(db))
    assert persistence.claim("100", str(db)) is True


def test_legacy_table_without_status_migrates(tmp_path):
    """旧版 seen_emails（无 status 列）启动时自动迁移，旧行视为 done 不被误回滚。"""
    import sqlite3

    db = tmp_path / "seen.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE seen_emails (uid TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO seen_emails (uid) VALUES ('legacy1')")
    conn.commit()
    conn.close()
    persistence.init_db(str(db))  # 触发迁移
    assert persistence.reconcile_orphans(str(db)) == []  # 旧行视为 done
    assert persistence.is_seen("legacy1", str(db)) is True
    assert persistence.claim("new1", str(db)) is True  # 新 claim 正常工作


# --------------------------------------------------------------------------- #
# UIDVALIDITY 持久化与状态重置 (#8)
# --------------------------------------------------------------------------- #
def test_get_set_uidvalidity(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    assert persistence.get_uidvalidity(str(db)) is None
    persistence.set_uidvalidity(1432, str(db))
    assert persistence.get_uidvalidity(str(db)) == 1432
    persistence.set_uidvalidity(2000, str(db))
    assert persistence.get_uidvalidity(str(db)) == 2000


def test_reset_state_clears_seen_and_last_uid(tmp_path):
    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    persistence.claim("100", str(db))
    persistence.mark_done("100", str(db))
    persistence.set_last_uid(500, str(db))
    persistence.set_uidvalidity(1, str(db))
    persistence.reset_state(str(db))
    assert persistence.is_seen("100", str(db)) is False
    assert persistence.get_last_uid(str(db)) is None
    assert persistence.get_uidvalidity(str(db)) == 1  # uidvalidity 保留


# --------------------------------------------------------------------------- #
# reset_state 必须一并清空 retry_queue：UIDVALIDITY 回卷后旧 UID 空间的
# 重试项在新空间可能指向另一封邮件，重投语义已错误
# --------------------------------------------------------------------------- #
def test_reset_state_clears_retry_queue(tmp_path):
    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.set_last_uid(100, str(db))
    persistence.enqueue_retry("101", str(db))
    persistence.reset_state(str(db))
    assert persistence.dequeue_retry(str(db), min_age=0) == []
    assert persistence.get_last_uid(str(db)) is None


def test_corrupt_meta_value_treated_as_unrecorded(tmp_path):
    """meta 值损坏（手改 DB/写坏）不得让轮询崩溃，视为未记录。"""
    import sqlite3

    from ai_email import persistence

    db = tmp_path / "seen.db"
    persistence.init_db(str(db))
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES ('last_uid', 'not-a-number')")
        conn.commit()
    assert persistence.get_last_uid(str(db)) is None
