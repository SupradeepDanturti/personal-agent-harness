"""MCP authorization integrated with durable approvals, recall and the API."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.test_runtime import RuntimeTests
from tests.offline_loop import OfflineAsyncCase


class MCPRuntimeTests(OfflineAsyncCase):
    asyncSetUp = RuntimeTests.asyncSetUp
    asyncTearDown = RuntimeTests.asyncTearDown
    runner = RuntimeTests.runner
    request = RuntimeTests.request
    async def test_mcp_receipts_are_searchable_and_agent_catalog_excludes_secrets(self):
        self.runtime.mcp = SimpleNamespace(
            status=AsyncMock(return_value=[{'id': 'conn', 'name': 'Documents', 'url': 'https://private.example/mcp',
                'connected': True, 'tools': [
                    {'name': 'read_notes', 'description': 'Read notes', 'inputSchema': {'type': 'object'}, 'fingerprint': 'f1', 'access': 'read'},
                    {'name': 'disabled_tool', 'description': '', 'inputSchema': {}, 'fingerprint': 'f2', 'access': 'disabled'}]}]),
            call_read=AsyncMock(return_value={'content': 'rememberedmcpsourceresult'}))
        catalog = await self.runtime.operate('mcp_list', {}, read_only=True)
        self.assertNotIn('url', catalog['connections'][0])
        self.assertEqual([t['name'] for t in catalog['connections'][0]['tools']], ['read_notes'])
        self.assertNotIn('inputSchema', catalog['connections'][0]['tools'][0])
        schema = await self.runtime.operate('mcp_list', {'id': 'conn', 'tool': 'read_notes'}, read_only=True)
        self.assertEqual(schema['tools'][0]['inputSchema'], {'type': 'object'})
        await self.runtime.operate('mcp_read', {'id': 'conn', 'tool': 'read_notes', 'arguments': {}}, read_only=True)
        match = self.runtime.recall.search('rememberedmcpsourceresult')['results'][0]
        self.assertEqual(match['kind'], 'tool')
        self.assertIn('read_notes', self.runtime.recall.read('tool', match['id'])['content'])
        memory = self.runtime.store.save_memory({'text': 'rememberedmcpsourceresult', 'source': 'user', 'provenance': 'explicit'})
        self.runtime.store.forget_memory(memory['id'])
        self.assertEqual(self.runtime.recall.search('rememberedmcpsourceresult')['results'], [])

    async def test_mcp_write_requires_exact_approval_and_does_not_repeat(self):
        payload = {'id': 'conn', 'tool': 'save_note', 'arguments': {'text': 'Reviewed note'}}
        bound = {**payload, 'connectionId': 'verified-version', 'fingerprint': 'schema-fingerprint'}
        self.runtime.mcp = SimpleNamespace(bind_write=AsyncMock(return_value=bound),
            execute_write=AsyncMock(return_value={'content': 'Written note receipt'}))
        approval = await self.runtime.propose_action('mcp.call', payload, 'Save reviewed note')
        self.runtime.mcp.execute_write.assert_not_awaited()
        self.assertEqual(approval['payload'], bound)
        with self.assertRaisesRegex(ValueError, 'read-only'):
            await self.runtime.operate('propose_action', {'kind': 'mcp.call', 'payload': payload}, read_only=True)
        self.runtime.store.decide_approval(approval['id'], 'approved', approval['digest'])
        await self.runtime.execute_action(self.runtime.store.get_approval(approval['id']))
        self.runtime.mcp.execute_write.assert_awaited_once_with(bound)
        with self.assertRaises(ValueError): await self.runtime.execute_action(self.runtime.store.get_approval(approval['id']))
        self.assertEqual(self.runtime.store.get_approval(approval['id'])['status'], 'completed')

    async def test_mcp_configuration_routes_do_not_grant_agent_configuration(self):
        self.runtime.mcp = SimpleNamespace(status=AsyncMock(return_value=[]),
            save=AsyncMock(return_value={'id': 'conn', 'tools': []}),
            discover=AsyncMock(return_value={'tools': []}), grant=AsyncMock(return_value={'tools': []}),
            remove=AsyncMock(return_value={'deleted': True}))
        status, _ = await self.request('/mcp', {'name': 'Owner MCP', 'url': 'https://example.com/mcp'})
        self.assertEqual(status, 201)
        status, result = await self.request('/mcp')
        self.assertEqual((status, result), (200, {'connections': []}))
        self.assertEqual((await self.request('/mcp/conn/test', {}))[0], 200)
        self.assertEqual((await self.request('/mcp/conn/grants', {'grants': []}))[0], 200)
        self.assertEqual((await self.request('/mcp/conn', method='DELETE'))[0], 200)
        with self.assertRaisesRegex(ValueError, 'Unknown capability'):
            await self.runtime.operate('mcp_configure', {})

# The fixture class is imported only to reuse setup, not as another collected suite.
del RuntimeTests
