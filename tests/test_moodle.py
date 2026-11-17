import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from lmcore_sync.moodle import LoginExpired, Moodle


def response(status=200, body=b"pdf", url="https://school/file.pdf", headers=None):
    return SimpleNamespace(status=status, url=url, headers=headers or {}, body=AsyncMock(return_value=body),
                           text=AsyncMock(return_value=body.decode()), dispose=AsyncMock())


class MoodleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.m = Moodle("https://school", Path("unused"), delay=0, retries=2, log=lambda _: None)
        self.request = AsyncMock()
        self.m.ctx = SimpleNamespace(request=self.request)

    async def test_http_429_retries_and_disposes_responses(self):
        first = response(429, headers={"retry-after": "3"})
        last = response()
        self.request.get.side_effect = [first, last]
        with patch("lmcore_sync.moodle.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await self.m.fetch("https://school/file.pdf")
        self.assertEqual(result[0], b"pdf")
        sleep.assert_awaited_once_with(3.0)
        first.dispose.assert_awaited_once()
        last.dispose.assert_awaited_once()

    async def test_server_errors_retry_but_404_does_not(self):
        self.request.get.side_effect = [response(503), response()]
        with patch("lmcore_sync.moodle.asyncio.sleep", new_callable=AsyncMock):
            await self.m.fetch("https://school/file.pdf")
        self.assertEqual(self.request.get.await_count, 2)
        self.request.get.reset_mock(side_effect=True)
        self.request.get.return_value = response(404)
        with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            await self.m.fetch("https://school/file.pdf")
        self.assertEqual(self.request.get.await_count, 1)

    async def test_login_redirect_is_never_retried(self):
        r = response(url="https://school/login/index.php")
        self.request.get.return_value = r
        with self.assertRaises(LoginExpired):
            await self.m.fetch("https://school/file.pdf")
        self.assertEqual(self.request.get.await_count, 1)
        r.dispose.assert_awaited_once()

    async def test_sso_html_is_not_saved_as_document(self):
        self.request.get.return_value = response(body=b'<html><form><input type="password"></form></html>')
        with self.assertRaises(LoginExpired):
            await self.m.fetch("https://school/file.pdf")

    async def test_ajax_uses_shared_cookies_context_and_propagates_expiry(self):
        self.m.sesskey = "session-value"
        self.request.post.return_value = response(body=json.dumps([{"error": False, "data": {"courses": []}}]).encode())
        self.assertEqual(await self.m.ajax("courses", {}), {"courses": []})
        call = self.request.post.call_args
        self.assertTrue(call.args[0].startswith("https://school/lib/ajax/service.php?"))
        self.assertEqual(json.loads(call.kwargs["data"])[0]["methodname"], "courses")
        self.request.post.return_value = response(body=json.dumps([{"error": True, "exception": {"errorcode": "invalidsesskey"}}]).encode())
        with self.assertRaises(LoginExpired):
            await self.m.ajax("courses", {})

    async def test_head_disposes_its_response(self):
        r = response()
        self.request.fetch.return_value = r
        self.assertEqual((await self.m.head("https://school/file.pdf"))[0], r.url)
        r.dispose.assert_awaited_once()
