import asyncio
import email
import email.message
import logging
import os
import re
from collections.abc import AsyncIterator
from email.header import decode_header
from typing import cast

from aioimaplib import aioimaplib

from ai_email.persistence import (
    claim,
    clear_retry,
    dequeue_retry,
    enqueue_retry,
    get_last_uid,
    get_uidvalidity,
    is_seen,
    reset_state,
    set_last_uid,
    set_uidvalidity,
)

logger = logging.getLogger(__name__)
_UIDVALIDITY_RE = re.compile(r"UIDVALIDITY (\d+)")

DEFAULT_CHECK_INTERVAL = 5  # 与 workflow._run_email_pipeline 的默认轮询周期一致


def _env_float(name: str, default: float) -> float:
    """读环境变量浮点值，非法值回退默认并告警（与 LLM_TIMEOUT_SECONDS 容错一致）。"""
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("%s 非数字，回退默认 %s", name, default)
        return default


def _decode_bytes(payload: bytes, charset: str | None = None) -> str:
    """将 bytes 解码为 str：指定编码优先，失败或未指定则 utf-8（忽略无效字节）"""
    if charset:
        try:
            return payload.decode(charset)
        except (UnicodeDecodeError, LookupError):
            pass
    return payload.decode("utf-8", errors="ignore")


class QQEmailListener:
    def __init__(self, email_address: str, password: str, db_path: str | None = None):
        self.email_address = email_address
        self.password = password
        self.imap_server = "imap.qq.com"
        self.imap_port = 993
        self.client: aioimaplib.IMAP4_SSL | None = None
        self.should_stop = False
        self.last_uid: int | None = None
        self.db_path = db_path  # 持久化 last_uid，消除重启失忆
        # 重试退避秒数，避免 SMTP 故障时风暴重试（RETRY_BACKOFF_SECONDS 可覆盖）
        self.retry_backoff = _env_float("RETRY_BACKOFF_SECONDS", 30)

    async def connect(self) -> bool:
        try:
            self.client = aioimaplib.IMAP4_SSL(host=self.imap_server, port=self.imap_port)
            await self.client.wait_hello_from_server()
            await self.client.login(self.email_address, self.password)
            await self.client.select("inbox")
            logger.info("成功连接到QQ邮箱")
            return True
        except Exception as e:
            logger.error(f"连接失败: {e}", exc_info=True)
            self.client = None  # 清理半初始化对象，避免下次 connect 覆盖时泄漏（#3）
            return False

    async def disconnect(self) -> None:
        try:
            if self.client:
                try:
                    await self.client.logout()
                    logger.info("已断开邮箱连接")
                except Exception:
                    logger.debug("logout 异常，强制放弃连接", exc_info=True)
        finally:
            # 无论 logout 成功与否都丢弃 client 引用（#3：避免状态不一致/连接泄漏）
            self.client = None

    async def check_new_emails(self) -> list[dict]:
        """单轮轮询：增量搜索新邮件 + 重投重试队列。

        注意：本方法故意不捕获 select/search 的异常——连接断开时它们会抛异常，
        冒泡到 listen_for_emails 触发重连。若此处 catch-all 返回 []，会导致断线后静默不重连。
        """
        if self.client is None:
            raise RuntimeError("IMAP client 未初始化：check_new_emails 前需先 connect() 成功")
        status, response = await self.client.select("inbox")
        if status != "OK":
            return []

        await self._refresh_uidvalidity_guard(response)
        if self.last_uid is None and not await self._init_baseline():
            return []

        new_uids = await self._search_new_uids()
        retry_emails = await self._drain_retry_queue()
        if not new_uids and not retry_emails:
            return []

        new_emails = await self._fetch_new_emails(new_uids) if new_uids else []
        return new_emails + retry_emails

    async def _refresh_uidvalidity_guard(self, select_response: list) -> None:
        """UIDVALIDITY 守卫（#8）：邮箱 UID 空间回卷时重置基线与去重表，避免误判/丢失。

        注：本类内所有 sqlite 调用统一走 to_thread——磁盘慢（网络盘/杀毒扫描）
        时同步 IO 会卡住整个事件循环，与 workflow 侧的惯例保持一致。
        """
        uidvalidity = self._parse_uidvalidity(select_response)
        if uidvalidity is not None and self.db_path:
            stored = await asyncio.to_thread(get_uidvalidity, self.db_path)
            if stored is None:
                await asyncio.to_thread(set_uidvalidity, uidvalidity, self.db_path)  # 首次记录
            elif stored != uidvalidity:
                logger.warning(f"UIDVALIDITY 变化 {stored} -> {uidvalidity}，重置基线与去重表")
                await asyncio.to_thread(reset_state, self.db_path)
                self.last_uid = None
                await asyncio.to_thread(set_uidvalidity, uidvalidity, self.db_path)

    async def _init_baseline(self) -> bool:
        """初始化 last_uid 基线。返回 False 表示本轮应结束（基线已建立或本轮无法建立）。"""
        # 首启：优先从持久化读取 last_uid（消除重启失忆）
        if self.db_path:
            persisted = await asyncio.to_thread(get_last_uid, self.db_path)
            if persisted is not None:
                self.last_uid = persisted
                logger.info(f"从持久化恢复 last_uid={self.last_uid}，继续检测新邮件")
        if self.last_uid is not None:
            return True
        # 全新部署：全量 search 拿最大 UID 作基线（跳过海量历史）
        # 注意：aioimaplib 的通用 uid() 只分发 FETCH/STORE/COPY/MOVE/EXPUNGE，
        # 对 SEARCH 会在客户端直接抛 Abort，必须用专用的 uid_search()。
        assert self.client is not None
        status, response = await self.client.uid_search("ALL", charset=None)
        if status != "OK":
            # 本轮无法建立基线：返回 False 结束本轮（last_uid 仍为 None），
            # 下轮重试全量搜索——与旧实现的 return [] 行为一致
            return False
        uid_list = self._parse_uids(response)
        self.last_uid = max(int(u) for u in uid_list) if uid_list else 0
        if self.db_path:
            await asyncio.to_thread(set_last_uid, self.last_uid, self.db_path)
        logger.info(f"全新部署，基线 last_uid={self.last_uid}（跳过历史邮件）")
        return False

    async def _search_new_uids(self) -> list[bytes]:
        """增量搜索：只搜 last_uid 之后的邮件（避免全量 search ALL 的开销）。

        注意：IMAP UID n:* 可能返回 n 本身，客户端仍需过滤作为安全冗余。
        """
        assert self.client is not None and self.last_uid is not None
        status, response = await self.client.uid_search(f"UID {self.last_uid + 1}:*", charset=None)
        if status != "OK":
            return []
        uid_list = self._parse_uids(response)
        return [u for u in uid_list if int(u) > self.last_uid]

    async def _drain_retry_queue(self) -> list[dict]:
        """扫描重试队列：只重投已过退避期（≥retry_backoff 秒）的，避免风暴重试。

        fetch 失败（邮件被删/IMAP 异常）的视为死信，clear_retry 清除避免堆积。
        """
        retry_emails: list[dict] = []
        if not self.db_path:
            return retry_emails
        retry_items = await asyncio.to_thread(
            dequeue_retry, self.db_path, min_age=self.retry_backoff
        )
        for uid_str, _attempts in retry_items:
            # 已在 seen_emails（在途 processing / 已完成 done）的不重投：
            # 处理耗时超过一个轮询周期时，否则每轮都会重复派发（claim 虽能
            # 拦住重复处理，但会每 5 秒刷一条"已被其它 worker 处理"的日志）
            if await asyncio.to_thread(is_seen, uid_str, self.db_path):
                continue
            fetched = await self._fetch_email(uid_str)
            if fetched is not None:
                retry_emails.append(fetched)
            else:
                await asyncio.to_thread(clear_retry, uid_str, self.db_path)
                logger.warning(f"重试邮件 {uid_str} fetch 失败（可能已删除），清除重试记录")
        if retry_emails:
            logger.info(f"从重试队列重新拉取 {len(retry_emails)} 封邮件")
        return retry_emails

    async def _fetch_new_emails(self, new_uids: list[bytes]) -> list[dict]:
        """逐封 fetch 新邮件并推进基线（含崩溃安全占位与失败兜底）。"""
        logger.info(f"检测到 {len(new_uids)} 封新邮件")
        ordered_uids = sorted(new_uids, key=int)
        new_emails: list[dict] = []
        failed_uids: list[str] = []
        for uid in ordered_uids:
            fetched = await self._fetch_email(uid.decode())
            if fetched is not None:
                # 崩溃安全：fetch 成功立即 claim 占位（processing 行），然后才允许
                # 下方推进基线。若先推进后 claim，窗口内硬崩溃的邮件既不在
                # seen_emails 也不在 retry_queue，而增量搜索不再覆盖 ≤last_uid
                # 的 uid → 永久丢失（fetch 失败者已有对等保护，见下）。
                # 带 _pre_claimed 标记，workflow.run 不再重复 claim。
                if self.db_path:
                    claimed = await asyncio.to_thread(claim, uid.decode(), self.db_path)
                    if not claimed:
                        # 已处理过/在途（如重试路径已派发同号 uid），不重复派发
                        continue
                    fetched["_pre_claimed"] = True
                new_emails.append(fetched)
            else:
                failed_uids.append(uid.decode())
        await self._advance_baseline(new_uids, failed_uids, ordered_uids)
        return new_emails

    async def _advance_baseline(
        self, new_uids: list[bytes], failed_uids: list[str], ordered_uids: list[bytes]
    ) -> None:
        """推进 last_uid（含持久化）。失败者的保护策略：

        - 有持久化：失败者先入重试队列再推进基线（顺序不可颠倒：推进后、
          入队前崩溃的邮件将永久丢失——增量搜索不再覆盖 ≤last_uid 的 uid）。
        - 无持久化兜底：不能越过失败者推进（否则本会话内永久丢失），
          基线只推进到开头连续成功的前缀，失败者留待下轮增量重搜。
        """
        assert self.last_uid is not None
        if failed_uids:
            if self.db_path:
                for uid_str in failed_uids:
                    await asyncio.to_thread(enqueue_retry, uid_str, self.db_path)
                self.last_uid = max(self.last_uid, max(int(u) for u in new_uids))
            else:
                failed_set = set(failed_uids)
                for uid in ordered_uids:
                    if uid.decode() in failed_set:
                        break
                    self.last_uid = max(self.last_uid, int(uid))
            logger.warning(f"{len(failed_uids)} 封新邮件 fetch 失败，已入重试兜底: {failed_uids}")
        else:
            self.last_uid = max(self.last_uid, max(int(u) for u in new_uids))
        if self.db_path:
            await asyncio.to_thread(set_last_uid, self.last_uid, self.db_path)

    @staticmethod
    def _parse_uids(response) -> list[bytes]:
        """从 IMAP search 响应中解析 UID 列表。"""
        tokens = b""
        for line in response or []:
            if isinstance(line, (bytes, bytearray)):
                tokens += b" " + line
        return [t for t in tokens.split() if t.isdigit()]

    @staticmethod
    def _parse_uidvalidity(response) -> int | None:
        """从 IMAP SELECT 响应中解析 UIDVALIDITY，找不到返回 None。"""
        for line in response or []:
            is_bytes = isinstance(line, (bytes, bytearray))
            text = line.decode("ascii", "ignore") if is_bytes else str(line)
            m = _UIDVALIDITY_RE.search(text)
            if m:
                return int(m.group(1))
        return None

    def _decode_subject(self, subject: str | None) -> str:
        if not subject:
            return ""
        parts = []
        for fragment, encoding in decode_header(subject):
            if isinstance(fragment, bytes):
                parts.append(_decode_bytes(fragment, encoding))
            else:
                parts.append(fragment)
        return "".join(parts)

    def _get_body(self, msg: email.message.Message) -> str:
        """提取邮件正文"""
        # get_payload(decode=True) 的 stub 标注宽泛（Message|bytes|None），
        # decode=True 时实际只可能是 bytes|None
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    payload = cast("bytes | None", part.get_payload(decode=True))
                    if payload:
                        return _decode_bytes(payload, part.get_content_charset())
        else:
            payload = cast("bytes | None", msg.get_payload(decode=True))
            if payload:
                return _decode_bytes(payload, msg.get_content_charset())
        return ""

    async def _fetch_email(self, uid_str: str) -> dict | None:
        """fetch 单个邮件并解析为 dict，失败返回 None。"""
        assert self.client is not None
        status, msg_data = await self.client.uid("fetch", uid_str, "(RFC822)")
        if status != "OK" or not msg_data:
            return None
        raw = msg_data[1] if len(msg_data) > 1 else msg_data[0]
        if isinstance(raw, tuple):
            raw = raw[1]
        # aioimaplib 的 RFC822 载荷是 bytearray（不是 bytes 子类），str(bytearray)
        # 是 repr 串，解析不出任何头（From/Subject 全为 None → RCPT TO:<> → 501）
        if isinstance(raw, (bytes, bytearray)):
            msg = email.message_from_bytes(bytes(raw))
        else:
            msg = email.message_from_string(str(raw))
        return {
            "id": uid_str,
            "subject": self._decode_subject(msg["Subject"]),
            "from": msg["From"],
            "date": msg["Date"],
            "content": self._get_body(msg),
        }

    async def listen_for_emails(
        self, check_interval: int = DEFAULT_CHECK_INTERVAL
    ) -> AsyncIterator[dict]:
        """异步邮件监听生成器"""
        logger.info("开始监听新邮件...")
        # 重连退避：连续失败逐次翻倍（10→20→40→60 封顶），成功后重置，
        # 避免服务端长时间故障时固定 10 秒间隔持续冲击
        reconnect_delay = 10
        while not self.should_stop:
            if not await self.connect():
                logger.warning(f"连接失败，{reconnect_delay} 秒后重试...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                continue
            try:
                while not self.should_stop:
                    new_emails = await self.check_new_emails()
                    for info in new_emails:
                        email_data = {
                            "email_id": info["id"],
                            "sender_email": info["from"],
                            "email_content": {
                                "主题": info["subject"],
                                "内容预览": info["content"][:100],
                                "content": info["content"],
                                "日期": info["date"],
                            },
                        }
                        # listener 侧已预占位的邮件传导标记，workflow.run 据此跳过重复 claim
                        if info.get("_pre_claimed"):
                            email_data["_pre_claimed"] = True
                        yield email_data
                    await asyncio.sleep(check_interval)
                    reconnect_delay = 10  # 一轮完整轮询成功，连接健康，重置退避
            except Exception as e:
                logger.error(f"监听中断: {e}，{reconnect_delay} 秒后重连...", exc_info=True)
                await self.disconnect()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)

    def stop_listening(self) -> None:
        self.should_stop = True
