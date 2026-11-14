"""Removed connector routes must not dispatch provider operations."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness.api.routes import dispatch
from tests.offline_loop import OfflineAsyncCase


class RemovedProviderTests(OfflineAsyncCase):
    async def test_spotify_endpoints_are_not_supported(self):
        providers = SimpleNamespace(read=AsyncMock(), connect_token=AsyncMock(), disconnect=AsyncMock())
        store = SimpleNamespace(**{name: lambda: [] for name in (
            "list_goals", "list_tasks", "list_schedules", "list_approvals", "list_notifications", "list_events",
        )})
        runtime = SimpleNamespace(providers=providers, store=store)
        requests = (
            ("POST", "/api/connections/spotify/start"),
            ("POST", "/api/connections/spotify/authorize"),
            ("POST", "/api/connections/spotify/read"),
            ("DELETE", "/api/connections/spotify"),
            ("GET", "/oauth/spotify/callback"),
        )
        for method, path in requests:
            with self.subTest(method=method, path=path):
                response = await dispatch(runtime, method, path, {"token": "synthetic-token"})
                self.assertEqual(response.status_code, 404)
        providers.read.assert_not_awaited()
        providers.connect_token.assert_not_awaited()
        providers.disconnect.assert_not_awaited()
