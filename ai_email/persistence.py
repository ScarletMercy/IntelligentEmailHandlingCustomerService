"""已处理邮件 UID 持久化（幂等性），基于 stdlib sqlite3，无新依赖。"""

import logging
import os
import sqlite3
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(os.path.expanduser("~"), ".ai-email", "seen.db")

# 已完成建表/迁移的 db 路径（进程内记忆）。DDL 本身幂等，但每次调用重跑
# （建表×3 + PRAGMA 迁移检查 + commit）在 5 秒轮询热点路径上纯属浪费；
# 连接仍每次新建、生命周期不变，只跳过重复 DDL。
# 多线程首次并发调用的竞态最多导致幂等 DDL 重复执行一次，无害。
_initialized_paths: set[str] = set()


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    path = os.path.abspath(db_path or DEFAULT_DB_PATH)
    if path in _initialized_paths:
        # 目录在首次初始化时已创建，直接连接
        return sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # timeout=5：多 worker（to_thread 线程池）并发写时的显式忙等，
    # 避免极端情况下 "database is locked" 直接逃逸
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    # WAL 是 db 文件的持久化属性（首次设置后重启仍生效）：写不阻塞读，
    # 轮询查询（is_seen/dequeue_retry）不再与写入互相卡顿
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        logger.debug("WAL 模式不可用（网络盘等），回退默认 journal", exc_info=True)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_emails "
        "(uid TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'done')"
    )
    # 迁移旧表（无 status 列）：旧行视为 done，避免被 reconcile_orphans 误回滚
    if "status" not in {r[1] for r in conn.execute("PRAGMA table_info(seen_emails)")}:
        conn.execute("ALTER TABLE seen_emails ADD COLUMN status TEXT NOT NULL DEFAULT 'done'")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS retry_queue ("
        "uid TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 1, "
        "enqueued_at REAL NOT NULL)"
    )
    conn.commit()
    _initialized_paths.add(path)
    return conn


@contextmanager
def _connection_ctx(db_path: str | None = None):
    """连接上下文：复用 _connect（建表/迁移），退出时确保关闭。"""
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """建表，幂等。在服务启动时调用一次。"""
    _connect(db_path).close()


def is_seen(uid: str, db_path: str | None = None) -> bool:
    """该 UID 是否已处理过。"""
    with _connection_ctx(db_path) as conn:
        return (
            conn.execute("SELECT 1 FROM seen_emails WHERE uid = ?", (str(uid),)).fetchone()
            is not None
        )


def claim(uid: str, db_path: str | None = None) -> bool:
    """原子抢占 uid（并发安全的去重锁）。

    使用 ``INSERT OR IGNORE`` 在单条 SQL 语句内完成“检查 + 插入”，
    消除 is_seen→mark_seen 的 TOCTOU 竞争窗口。

    返回 ``True`` 表示抢占成功（首次插入，该 uid 之前不存在），
    返回 ``False`` 表示已被抢占或已处理（uid 已存在）。
    """
    with _connection_ctx(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO seen_emails (uid, status) VALUES (?, 'processing')",
            (str(uid),),
        )
        conn.commit()
        return cur.rowcount > 0


def mark_done(uid: str, db_path: str | None = None) -> None:
    """标记 uid 为已成功处理（status='done'）。

    发送成功后调用，使崩溃对账不再回滚它（与仅 claim 的 'processing' 区分）。
    """
    with _connection_ctx(db_path) as conn:
        conn.execute("UPDATE seen_emails SET status='done' WHERE uid = ?", (str(uid),))
        conn.commit()


def release(uid: str, db_path: str | None = None) -> None:
    """释放抢占（发送失败时回滚），删除 seen 记录以允许后续重试。"""
    with _connection_ctx(db_path) as conn:
        conn.execute("DELETE FROM seen_emails WHERE uid = ?", (str(uid),))
        conn.commit()


_LAST_UID_KEY = "last_uid"


def _read_meta_int(conn: sqlite3.Connection, key: str) -> int | None:
    """读 meta 表的整数值；缺失、损坏或非数字时返回 None（视为未记录）。"""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return int(row[0])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning("meta[%s] 值损坏（%r），视为未记录", key, row[0])
        return None


def get_last_uid(db_path: str | None = None) -> int | None:
    """读取持久化的最大已见 UID。无记录返回 None（表示从未运行过）。"""
    with _connection_ctx(db_path) as conn:
        return _read_meta_int(conn, _LAST_UID_KEY)


def set_last_uid(uid: int, db_path: str | None = None) -> None:
    """持久化最大已见 UID（listener 每轮轮询后调用）。"""
    with _connection_ctx(db_path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_LAST_UID_KEY, str(int(uid))),
        )
        conn.commit()


# -- UIDVALIDITY（邮箱 UID 空间回卷检测，#8） --

_UIDVALIDITY_KEY = "uidvalidity"


def get_uidvalidity(db_path: str | None = None) -> int | None:
    """读取持久化的 UIDVALIDITY。无记录返回 None。"""
    with _connection_ctx(db_path) as conn:
        return _read_meta_int(conn, _UIDVALIDITY_KEY)


def set_uidvalidity(uidvalidity: int, db_path: str | None = None) -> None:
    """持久化 UIDVALIDITY。"""
    with _connection_ctx(db_path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_UIDVALIDITY_KEY, str(int(uidvalidity))),
        )
        conn.commit()


def reset_state(db_path: str | None = None) -> None:
    """UIDVALIDITY 变化时清空去重表与 last_uid（旧 UID 空间已失效）。

    retry_queue 一并清空：残留的旧 UID 空间重试项在新空间可能指向
    另一封邮件，重投语义已错误。保留 UIDVALIDITY 自身（由调用方紧接着写入新值）。
    """
    with _connection_ctx(db_path) as conn:
        conn.execute("DELETE FROM seen_emails")
        conn.execute("DELETE FROM retry_queue")
        conn.execute("DELETE FROM meta WHERE key = ?", (_LAST_UID_KEY,))
        conn.commit()


# -- 重试队列（发送失败/处理异常的邮件，独立于 last_uid 推进） --


def reconcile_orphans(db_path: str | None = None) -> list[str]:
    """启动对账：把残留的 status='processing' 记录（claim 后进程崩溃所致）移入
    retry_queue 并从 seen_emails 删除，使其通过重试路径重新处理，避免永久丢失。

    返回被回滚的 uid 列表。已 mark_done（status='done'）的记录保留去重，不受影响。
    移入 retry_queue 的项受 retry_backoff 退避约束，不会在启动瞬间风暴重投。
    """
    with _connection_ctx(db_path) as conn:
        orphans = [
            row[0]
            for row in conn.execute(
                "SELECT uid FROM seen_emails WHERE status='processing'"
            ).fetchall()
        ]
        if not orphans:
            return []
        now = time.time()
        for uid in orphans:
            conn.execute("DELETE FROM seen_emails WHERE uid = ?", (uid,))
            conn.execute(
                "INSERT INTO retry_queue (uid, attempts, enqueued_at) VALUES (?, 1, ?) "
                "ON CONFLICT(uid) DO UPDATE SET attempts = attempts + 1, "
                "enqueued_at = excluded.enqueued_at",
                (uid, now),
            )
        conn.commit()
        return orphans


def enqueue_retry(uid: str, db_path: str | None = None) -> int:
    """把 uid 加入重试队列。已存在则 attempts+1。返回当前 attempts。"""
    with _connection_ctx(db_path) as conn:
        conn.execute(
            "INSERT INTO retry_queue (uid, attempts, enqueued_at) "
            "VALUES (?, 1, ?) "
            "ON CONFLICT(uid) DO UPDATE SET attempts = attempts + 1, "
            "enqueued_at = excluded.enqueued_at",
            (str(uid), time.time()),
        )
        conn.commit()
        row = conn.execute("SELECT attempts FROM retry_queue WHERE uid = ?", (str(uid),)).fetchone()
        return int(row[0]) if row else 0


def dequeue_retry(db_path: str | None = None, min_age: float = 0) -> list[tuple[str, int]]:
    """返回待重试的 uid 列表（每项 (uid_str, attempts)）。

    min_age：只返回入队时间距今超过 min_age 秒的（退避过滤，避免风暴重试）。
    """
    with _connection_ctx(db_path) as conn:
        if min_age > 0:
            cutoff = time.time() - min_age
            rows = conn.execute(
                "SELECT uid, attempts FROM retry_queue WHERE enqueued_at <= ? ORDER BY enqueued_at",
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT uid, attempts FROM retry_queue ORDER BY enqueued_at"
            ).fetchall()
        return [(row[0], int(row[1])) for row in rows]


def clear_retry(uid: str, db_path: str | None = None) -> None:
    """处理成功后清除该 uid 的重试记录。"""
    with _connection_ctx(db_path) as conn:
        conn.execute("DELETE FROM retry_queue WHERE uid = ?", (str(uid),))
        conn.commit()
