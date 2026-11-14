import asyncio
from contextlib import asynccontextmanager
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from harness.integrations.mcp import MCPRegistry, _LimitedStream, _PinnedTransport, MAX_RESPONSE
from harness.persistence.store import LocalStore
from tests.offline_loop import OfflineAsyncCase


class MCPTests(OfflineAsyncCase):
    async def asyncSetUp(self):
        self.store = LocalStore(':memory:')
        self.tool = Tool(name='lookup', description='Lookup a record', inputSchema={
            'type': 'object', 'properties': {'key': {'type': 'string'}}, 'required': ['key'], 'additionalProperties': False})
        self.session = SimpleNamespace(initialize=AsyncMock(),
            list_tools=AsyncMock(return_value=ListToolsResult(tools=[self.tool])),
            call_tool=AsyncMock(return_value=CallToolResult(content=[TextContent(type='text', text='Verified record')])) )
        @asynccontextmanager
        async def factory(connection): yield self.session
        self.registry = MCPRegistry(self.store, 'mcp-test-key-with-at-least-24-characters', session_factory=factory)
        self.connection = await self.registry.save({'name': 'Example', 'url': 'https://example.com/mcp', 'token': 'private-bearer-fixture'})
        self.id = self.connection['id']

    async def asyncTearDown(self): self.store.close()

    async def grant(self, access='read'):
        view = await self.registry.discover(self.id)
        return await self.registry.grant(self.id, {'grants': [{'name': 'lookup', 'fingerprint': view['tools'][0]['fingerprint'], 'access': access}]})

    async def test_encrypted_storage_no_implicit_grants_and_read_result(self):
        raw = self.store.db.execute('SELECT envelope FROM mcp_connections').fetchone()[0]
        self.assertNotIn('private-bearer-fixture', raw)
        self.assertNotIn('https://example.com', raw)
        view = await self.registry.discover(self.id)
        self.assertEqual(view['tools'][0]['access'], 'disabled')
        with self.assertRaisesRegex(ValueError, 'grant'): await self.registry.call_read(self.id, 'lookup', {'key': 'one'})
        await self.grant()
        result = await self.registry.call_read(self.id, 'lookup', {'key': 'one'})
        self.assertEqual(result['text'], 'Verified record')
        self.assertNotIn('token', json.dumps(await self.registry.status()))
        self.session.call_tool.assert_awaited_once_with('lookup', {'key': 'one'})

    async def test_arguments_and_remote_schema_changes_block_execution(self):
        await self.grant()
        with self.assertRaisesRegex(ValueError, 'schema'): await self.registry.call_read(self.id, 'lookup', {'unexpected': True})
        self.tool.description = 'Changed authorization meaning'
        with self.assertRaises(ValueError): await self.registry.call_read(self.id, 'lookup', {'key': 'one'})
        self.session.call_tool.assert_not_awaited()

    async def test_write_bindings_revoke_on_regrant_reconnect_and_remove(self):
        await self.grant('approval')
        payload = await self.registry.bind_write({'id': self.id, 'tool': 'lookup', 'arguments': {'key': 'one'}})
        self.assertNotIn('url', payload)
        result = await self.registry.execute_write(payload)
        self.assertEqual(result['text'], 'Verified record')
        await self.grant('approval')
        with self.assertRaisesRegex(ValueError, 'changed'): await self.registry.execute_write(payload)
        fresh = await self.registry.bind_write({'id': self.id, 'tool': 'lookup', 'arguments': {'key': 'two'}})
        self.session.initialize.side_effect = RuntimeError('private-bearer-fixture')
        with self.assertRaises(ValueError) as error: await self.registry.discover(self.id)
        self.assertNotIn('private-bearer-fixture', str(error.exception))
        with self.assertRaises(ValueError): await self.registry.execute_write(fresh)
        await self.registry.remove(self.id)
        self.assertEqual(await self.registry.status(), [])

    async def test_explicit_read_grants_ignore_server_hints_and_hide_secret_echo(self):
        from mcp.types import ToolAnnotations
        self.tool.annotations = ToolAnnotations(readOnlyHint=True)
        await self.registry.discover(self.id)
        with self.assertRaises(ValueError): await self.registry.call_read(self.id, 'lookup', {'key': 'one'})
        await self.grant()
        self.session.call_tool.return_value = CallToolResult(content=[TextContent(type='text', text='private-bearer-fixture ' + 'x' * 14000)])
        result = await self.registry.call_read(self.id, 'lookup', {'key': 'one'})
        self.assertNotIn('private-bearer-fixture', result['text'])
        self.assertTrue(result['truncated']); self.assertEqual(len(result['text']), 12000)
        self.tool.description = 'private-bearer-fixture'
        with self.assertRaises(ValueError): await self.registry.discover(self.id)

    async def test_remote_refs_oversized_catalogues_and_pagination_are_rejected(self):
        for ref in ('$ref', '$dynamicRef', '$recursiveRef'):
            self.tool.inputSchema = {ref: 'https://internal.invalid/schema'}
            with self.assertRaises(ValueError): await self.registry.discover(self.id)
        for schema in ({'type': 'string', 'pattern': '(a+)+$'}, {'type': 'object', 'patternProperties': {'a+': {'type': 'string'}}}):
            self.tool.inputSchema = schema
            with self.assertRaises(ValueError): await self.registry.discover(self.id)
            self.assertFalse((await self.registry.status())[0]['connected'])
        self.tool.inputSchema = {'type': 'object', 'properties': {'pattern': {'type': 'string'}}, 'examples': [{'pattern': 'example'}]}
        self.assertTrue((await self.registry.discover(self.id))['connected'])
        self.tool.inputSchema = {'type': 'object'}
        self.session.list_tools.return_value = ListToolsResult(tools=[self.tool], nextCursor='more')
        with self.assertRaises(ValueError): await self.registry.discover(self.id)
        self.session.list_tools.return_value = ListToolsResult(tools=[self.tool] * 21)
        with self.assertRaises(ValueError): await self.registry.discover(self.id)

    async def test_public_url_checks_dns_and_trusted_loopback_are_explicit(self):
        for url in ('http://example.com/mcp', 'https://127.0.0.1/mcp', 'https://example.com/mcp?token=abc', 'https://user:pass@example.com/mcp', 'file:///test'):
            with self.assertRaises(ValueError): await self.registry.save({'name': 'Bad', 'url': url})
        with patch('socket.getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 0))]):
            with self.assertRaisesRegex(ValueError, 'private'): await self.registry._address('https://example.com/mcp')
        local = MCPRegistry(self.store, 'mcp-test-key-with-at-least-24-characters', trusted_loopback_urls=('http://127.0.0.1:9786/mcp',))
        self.assertEqual(await local._address('http://127.0.0.1:9786/mcp'), '127.0.0.1')
        with self.assertRaises(ValueError): await local._address('http://127.0.0.1:9787/mcp')

    async def test_write_execution_serializes_remove(self):
        await self.grant('approval')
        payload = await self.registry.bind_write({'id': self.id, 'tool': 'lookup', 'arguments': {'key': 'one'}})
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args):
            started.set(); await release.wait()
            return CallToolResult(content=[TextContent(type='text', text='Done')])
        self.session.call_tool.side_effect = delayed
        execution = asyncio.create_task(self.registry.execute_write(payload))
        await started.wait()
        removal = asyncio.create_task(self.registry.remove(self.id))
        await asyncio.sleep(0)
        self.assertFalse(removal.done())
        release.set(); await execution; await removal
        self.assertEqual(await self.registry.status(), [])

    async def test_transport_pins_address_retains_host_and_blocks_redirects(self):
        transport = _PinnedTransport('https://example.com/mcp', '93.184.216.34')
        async def response(request):
            self.assertEqual(request.url.host, '93.184.216.34')
            self.assertEqual(request.headers['host'], 'example.com')
            self.assertEqual(request.extensions['sni_hostname'], 'example.com')
            return httpx.Response(302, headers={'location': 'https://example.com/elsewhere'})
        transport.inner = httpx.MockTransport(response)
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaisesRegex(ValueError, 'redirect'): await client.get('https://example.com/mcp')
        class Huge(httpx.AsyncByteStream):
            async def __aiter__(self): yield b'x' * (MAX_RESPONSE + 1)
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            async for _ in _LimitedStream(Huge()): pass

    async def test_structured_only_results_are_readable_and_redacted(self):
        await self.grant()
        self.session.call_tool.return_value = CallToolResult(content=[], structuredContent={'answer': 'private-bearer-fixture'})
        result = await self.registry.call_read(self.id, 'lookup', {'key': 'one'})
        self.assertIn('STRUCTURED RESULT', result['text'])
        self.assertIn('[redacted]', result['text'])
        self.assertNotIn('private-bearer-fixture', result['text'])

    async def test_operation_deadline_is_bounded_and_errors_hide_remote_content(self):
        await self.grant()
        async def hang(*args): await asyncio.Event().wait()
        self.session.call_tool.side_effect = hang
        with patch('harness.integrations.mcp.TIMEOUT', 0.02):
            with self.assertRaisesRegex(ValueError, 'MCP operation failed'):
                await self.registry.call_read(self.id, 'lookup', {'key': 'one'})

    async def test_sdk_logs_redact_literal_bearer_echo_and_exception_trace(self):
        await self.grant()
        async def logged(*args):
            logging.getLogger('mcp.client.streamable_http').error('Untrusted echo %s', 'private-bearer-fixture', exc_info=RuntimeError('private-bearer-fixture'))
            return CallToolResult(content=[TextContent(type='text', text='Okay')])
        self.session.call_tool.side_effect = logged
        with self.assertLogs('mcp.client.streamable_http', level='ERROR') as logs:
            await self.registry.call_read(self.id, 'lookup', {'key': 'one'})
        self.assertNotIn('private-bearer-fixture', '\n'.join(logs.output))
        self.assertIn('[redacted]', '\n'.join(logs.output))

    async def test_real_sdk_sse_bytecap_and_deadline_and_redirect_rejection(self):
        self.registry.session_factory = None
        self.registry._address = AsyncMock(return_value='93.184.216.34')
        mode = 'normal'
        called = []
        tool = self.tool.model_dump(mode='json', by_alias=True, exclude_none=True)
        class SSEBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                if mode == 'hang':
                    await asyncio.Event().wait()
                yield b'data: ' + b'x' * MAX_RESPONSE
        async def handler(request):
            payload = json.loads(await request.aread())
            method = payload['method']
            if method == 'notifications/initialized': return httpx.Response(202)
            if method == 'initialize':
                result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'bounded-fixture', 'version': '1'}}
            elif method == 'tools/list': result = {'tools': [tool]}
            elif method == 'tools/call':
                called.append(mode)
                if mode == 'redirect': return httpx.Response(307, headers={'location': 'https://example.com/redirected'})
                if mode == 'compressed': return httpx.Response(200, headers={'content-type': 'text/event-stream', 'content-encoding': 'gzip'}, content=b'bad')
                return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=SSEBody())
            else: raise AssertionError(method)
            return httpx.Response(200, json={'jsonrpc': '2.0', 'id': payload['id'], 'result': result})
        def transport(endpoint, address):
            value = _PinnedTransport(endpoint, address)
            value.inner = httpx.MockTransport(handler)
            return value
        with patch('harness.integrations.mcp._PinnedTransport', side_effect=transport):
            await self.grant()
            for failure in ('oversize', 'redirect', 'compressed', 'hang'):
                mode = failure
                with self.subTest(mode=mode), patch('harness.integrations.mcp.TIMEOUT', 2):
                    with self.assertRaisesRegex(ValueError, 'MCP operation failed'):
                        await self.registry.call_read(self.id, 'lookup', {'key': 'one'})
                    self.assertIn(mode, called)


class MCPProtocolTests(OfflineAsyncCase):
    async def test_real_sdk_streamable_http_initialize_discover_and_call(self):
        # Real MCP client and FastMCP server exchange HTTP/JSON-RPC through the
        # ASGI transport. No canned session methods or external account is used.
        from mcp.server.fastmcp import FastMCP
        from mcp.server.transport_security import TransportSecuritySettings
        server = FastMCP('Protocol fixture', stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        @server.tool()
        async def lookup(key: str) -> dict:
            return {'key': key, 'verified': True}
        app = server.streamable_http_app()
        store = LocalStore(':memory:')
        registry = MCPRegistry(store, 'mcp-protocol-test-key-at-least-24-characters')
        connection = await registry.save({'name': 'Protocol', 'url': 'https://example.com/mcp'})
        registry._address = AsyncMock(return_value='93.184.216.34')
        try:
            async with server.session_manager.run():
                with patch('harness.integrations.mcp._PinnedTransport', side_effect=lambda endpoint,address: httpx.ASGITransport(app=app)):
                    view = await registry.discover(connection['id'])
                    self.assertEqual(view['tools'][0]['name'], 'lookup')
                    await registry.grant(connection['id'], {'grants': [{'name': 'lookup', 'fingerprint': view['tools'][0]['fingerprint'], 'access': 'read'}]})
                    result = await registry.call_read(connection['id'], 'lookup', {'key': 'fixture'})
                    self.assertIn('fixture', result['text'])
                    self.assertIn('true', result['text'])
        finally:
            store.close()
