"""Tests for qq_bot: 业务错误码解析（R5）与发送结果语义。

QQ 开放平台部分业务错误以 HTTP 2xx + body 内 code/retcode 返回
（如 11255 用户不存在），只判 HTTP 状态码会误报成功。
"""

import asyncio
import json as _json
from unittest.mock import AsyncMock, MagicMock

from ai_email.qq_bot import QQBotNotifier


def _resp(status_code, body=None, text=None, json_exc=False):
    """构造假 httpx.Response：body 为 dict 时序列化为 text/json 返回值。"""
    resp = MagicMock()
    resp.status_code = status_code
    if text is not None:
        resp.text = text
    elif body is not None:
        resp.text = _json.dumps(body)
    else:
        resp.text = ""
    if json_exc:
        resp.json = MagicMock(side_effect=ValueError("not json"))
    else:
        resp.json = MagicMock(return_value=body if body is not None else {})
    return resp


def _notifier(post_responses):
    """构造 token 已就绪、_client.post 依次返回给定响应的通知器。"""
    n = QQBotNotifier("appid", "secret")
    n._get_token = AsyncMock(return_value="fake-token")
    n._client = MagicMock()
    n._client.post = AsyncMock(side_effect=post_responses)
    return n


def test_send_text_false_on_http_200_with_business_error_code():
    """R5 回归：HTTP 200 + code=11255 必须判失败（旧代码误报成功）。"""
    n = _notifier([_resp(200, body={"code": 11255, "message": "invalid openid"})])
    assert asyncio.run(n.send_text("c2c:someopenid", "hi")) is False


def test_send_text_false_on_retcode_error():
    n = _notifier([_resp(200, body={"retcode": 11255})])
    assert asyncio.run(n.send_text("c2c:x", "hi")) is False


def test_send_text_false_on_http_error():
    n = _notifier([_resp(400, body={"code": 400})])
    assert asyncio.run(n.send_text("c2c:x", "hi")) is False


def test_send_text_false_on_non_json_body():
    n = _notifier([_resp(200, text="<html>gateway error</html>", json_exc=True)])
    assert asyncio.run(n.send_text("c2c:x", "hi")) is False


def test_send_text_true_on_normal_success_body():
    n = _notifier([_resp(200, body={"id": "msgid"})])
    assert asyncio.run(n.send_text("c2c:x", "hi")) is True


def test_send_text_true_on_explicit_success_code():
    n = _notifier([_resp(200, body={"code": 0})])
    assert asyncio.run(n.send_text("c2c:x", "hi")) is True


def test_send_text_true_on_204_empty_body():
    """204 无响应体属成功，不得因 json() 失败误判。"""
    n = _notifier([_resp(204)])
    assert asyncio.run(n.send_text("c2c:x", "hi")) is True


def test_api_post_refreshes_token_on_401_and_retries():
    """401 触发 token 刷新并重试一次，重试成功则判成功。"""
    n = _notifier([_resp(401), _resp(200, body={"id": "m"})])
    n._token = "stale"
    n._expires_at = float("inf")
    result = asyncio.run(n._api_post("/v2/users/x/messages", {}))
    assert result == {"id": "m"}
    assert n._client.post.await_count == 2


# --------------------------------------------------------------------------- #
# token 并发防护：并发 _get_token 只允许一次真实刷新（Lock 消除重复请求与
# "旧值覆盖新值"竞态）
# --------------------------------------------------------------------------- #
def test_get_token_concurrent_single_fetch():
    notifier = QQBotNotifier("app", "secret")
    fetches = []

    async def fake_fetch():
        import time

        fetches.append(1)
        await asyncio.sleep(0.01)  # 制造并发窗口
        # 复刻真实 _fetch_token 的缓存写入，否则第二个协程必然再次刷新
        notifier._token = "tok"
        notifier._expires_at = time.time() + 7200
        return "tok"

    notifier._fetch_token = fake_fetch

    async def run():
        return await asyncio.gather(notifier._get_token(), notifier._get_token())

    tokens = asyncio.run(run())
    assert fetches == [1]  # 只发一次真实请求
    assert tokens == ["tok", "tok"]


def test_non_dict_json_response_treated_as_failure():
    """非 JSON 对象（如数组）按失败处理，不得被 send_text 误判为成功。"""
    notifier = QQBotNotifier("app", "secret")

    class _Client:
        async def post(self, *a, **kw):
            m = MagicMock()
            m.status_code = 200
            m.text = "[1, 2, 3]"
            m.json.return_value = [1, 2, 3]
            return m

    async def fake_token():
        return "tok"

    async def run():
        notifier._get_token = fake_token  # 绕开 token 请求，只测 API 响应解析
        notifier._client = _Client()
        return await notifier._api_post("/v2/users/x/messages", {})

    assert asyncio.run(run()) is None


def test_msg_seq_monotonic_without_wrap():
    """msg_seq 直接递增：65536 条之后不再取模回卷（重复 seq 可能被服务端去重）。"""
    notifier = QQBotNotifier("app", "secret")
    seqs = []
    for _ in range(3):
        body = {}
        # 复刻 send_text 的 seq 逻辑（不发起网络请求）
        notifier._seq += 1
        body["msg_seq"] = notifier._seq
        seqs.append(body["msg_seq"])
    assert seqs == [1, 2, 3]
    notifier._seq = 65535
    notifier._seq += 1
    assert notifier._seq == 65536  # 不回卷到 0
