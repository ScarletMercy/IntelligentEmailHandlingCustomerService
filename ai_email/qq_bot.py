"""QQ Bot 通知客户端（异步版）。

精简自 myclaw-py 的 QQ Bot 实现，仅支持发送通知（HTTP），不接收消息。
使用 QQ 官方 Bot API，全异步。
"""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

# README/.env.example 里的示例占位值。曾因原样写入配置，机器人拿字面量
# "openid" 调 /v2/users/openid/messages，服务端报 11255 用户不存在。
_PLACEHOLDER_TARGETS = {"c2c:openid", "group:groupid", "openid", "groupid"}


def is_placeholder_target(target: str) -> bool:
    """识别示例占位符目标；真实 openid/groupid 调用方正常放行。"""
    return target.strip() in _PLACEHOLDER_TARGETS


class QQBotNotifier:
    """QQ Bot 异步通知发送客户端。"""

    def __init__(self, app_id: str, client_secret: str):
        self._app_id = app_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._seq = 0
        # 并发 401/过期时多个通知会同时刷新 token：锁保证只发一次刷新请求，
        # 且不会出现"旧值覆盖新值"的竞态
        self._token_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def _fetch_token(self) -> str:
        resp = await self._client.post(
            TOKEN_URL,
            json={"appId": self._app_id, "clientSecret": self._client_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"QQ Bot token 获取失败: {data}")
        expires_in = int(data.get("expires_in", 7200))
        self._token = token
        self._expires_at = time.time() + expires_in
        logger.debug("QQ Bot token 已刷新，%d 秒后过期", expires_in)
        return token

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            return await self._fetch_token()

    async def _api_post(self, path: str, body: dict) -> dict | None:
        token = await self._get_token()
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }
        resp = await self._client.post(f"{API_BASE}{path}", headers=headers, json=body)
        if resp.status_code == 401:
            self._token = None
            token = await self._get_token()
            headers["Authorization"] = f"QQBot {token}"
            resp = await self._client.post(f"{API_BASE}{path}", headers=headers, json=body)
        if resp.status_code >= 400:
            logger.error("QQ Bot API 错误: status=%d body=%s", resp.status_code, resp.text[:200])
            return None
        # QQ 开放平台部分业务错误以 HTTP 2xx + body 内 code/retcode 返回
        # （如 11255 用户不存在；官方文档：201/202 异步受理也可能携带错误 body），
        # 只判状态码会误报成功，导致"转人工"通知静默丢失。
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        try:
            data = resp.json()
        except ValueError:
            logger.error(
                "QQ Bot API 响应非 JSON: status=%d body=%s", resp.status_code, resp.text[:200]
            )
            return None
        if not isinstance(data, dict):
            # 非 JSON 对象（如数组）属于异常响应：按失败处理，
            # 否则 send_text 的 `result is not None` 会误判为成功
            logger.error("QQ Bot API 响应非 JSON 对象: %s", resp.text[:200])
            return None
        code = data.get("code", data.get("retcode", 0))
        if code not in (0, None):
            logger.error("QQ Bot API 业务错误: code=%s body=%s", code, resp.text[:200])
            return None
        return data

    def _parse_target(self, target: str) -> tuple[str, str]:
        if ":" in target:
            prefix, id_part = target.split(":", 1)
            return prefix, id_part
        return "c2c", target

    async def send_text(self, target: str, text: str) -> bool:
        """发送文本通知。target 格式: 'c2c:openid' 或 'group:groupid'。"""
        kind, tid = self._parse_target(target)
        # msg_seq 直接递增（官方允许 uint32）：取模回卷可能产生重复 seq，
        # 服务端若按 seq 去重会吞掉通知
        self._seq += 1
        body = {
            "content": text,
            "msg_type": 0,
            "msg_seq": self._seq,
        }
        if kind == "c2c":
            result = await self._api_post(f"/v2/users/{tid}/messages", body)
        elif kind == "group":
            result = await self._api_post(f"/v2/groups/{tid}/messages", body)
        else:
            logger.error("未知的 QQ Bot 目标格式: %s", target)
            return False
        return result is not None

    async def close(self) -> None:
        await self._client.aclose()
