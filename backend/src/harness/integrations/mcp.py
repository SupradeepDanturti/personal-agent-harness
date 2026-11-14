"""Owner-registered Streamable HTTP MCP connections with explicit tool grants.

Endpoints and credentials are never supplied by a model. All reconnect/grant/
execution operations share a lock; approvals bind both the connection generation
and the exact discovered tool descriptor. Server read-only hints grant nothing.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import socket
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from harness.integrations.browser import validate_browser_url, is_public_browser_address
from harness.integrations.credentials import CredentialCipher

MAX_RESPONSE = 1024 * 1024
TIMEOUT = 20


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _descriptor(tool, secret=''):
    value = tool.model_dump(mode='json', by_alias=True, exclude_none=True) if hasattr(tool, 'model_dump') else tool
    if not isinstance(value, dict) or not isinstance(value.get('name'), str) or not 1 <= len(value['name']) <= 128:
        raise ValueError('Invalid MCP tool descriptor')
    if secret and secret in _json(value): raise ValueError('MCP tool descriptor contains protected connection data')
    schema = value.get('inputSchema')
    if not isinstance(schema, dict) or len(_json(schema).encode()) > 8000:
        raise ValueError('MCP tool schema exceeds the supported limit')
    # Remote references must never trigger outbound schema fetches.
    def check(item, depth=0):
        if depth > 30: raise ValueError('MCP tool schema is too deeply nested')
        if isinstance(item, dict):
            if 'pattern' in item or 'patternProperties' in item:
                raise ValueError('MCP regex schemas are unsupported in this bounded validator')
            for key in ('$ref', '$dynamicRef', '$recursiveRef'):
                if key in item and (not isinstance(item[key], str) or not item[key].startswith('#')):
                    raise ValueError('External MCP schema references are unsupported')
            for key, child in item.items():
                if key in ('properties', '$defs', 'definitions', 'dependentSchemas', 'dependencies') and isinstance(child, dict):
                    # Property names are data: a tool argument called "pattern"
                    # or "$ref" is not itself a regex/reference schema keyword.
                    for schema in child.values(): check(schema, depth + 1)
                elif key not in ('default', 'examples', 'const', 'enum'):
                    check(child, depth + 1)
        elif isinstance(item, list):
            for child in item: check(child, depth + 1)
    check(schema)
    Draft202012Validator.check_schema(schema)
    if len(_json(value).encode()) > 24000: raise ValueError('MCP tool descriptor exceeds the supported limit')
    return {'name': value['name'], 'description': str(value.get('description', ''))[:2000],
            'inputSchema': schema, 'fingerprint': hashlib.sha256(_json(value).encode()).hexdigest()}


class _LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream): self.stream, self.size = stream, 0
    async def __aiter__(self):
        async for chunk in self.stream:
            self.size += len(chunk)
            if self.size > MAX_RESPONSE: raise ValueError('MCP response exceeds 1 MiB')
            yield chunk
    async def aclose(self): await self.stream.aclose()


class _SecretFilter(logging.Filter):
    def __init__(self, secret): super().__init__(); self.secret = secret
    def filter(self, record):
        record.msg = record.getMessage().replace(self.secret, '[redacted]') if self.secret else record.getMessage()
        record.args = ()
        # SDK exception traces can contain untrusted response bodies.
        record.exc_info = record.exc_text = None
        return True


class _PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, endpoint, address):
        self.endpoint, self.address = httpx.URL(endpoint), address
        self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request):
        if request.url != self.endpoint:
            raise ValueError('MCP endpoint changes are blocked')
        # DNS was checked once and the socket connects to that exact address.
        # TLS still verifies the registered hostname via SNI.
        extensions = {**request.extensions, 'sni_hostname': self.endpoint.host}
        pinned = httpx.Request(request.method, request.url.copy_with(host=self.address),
                               headers=request.headers, stream=request.stream, extensions=extensions)
        response = await self.inner.handle_async_request(pinned)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise ValueError('MCP redirects are blocked')
        encoding = response.headers.get('content-encoding', 'identity').lower()
        if encoding not in ('identity', ''):
            await response.aclose()
            raise ValueError('Compressed MCP responses are unsupported')
        length = response.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > MAX_RESPONSE):
            await response.aclose()
            raise ValueError('MCP response exceeds 1 MiB')
        response.stream = _LimitedStream(response.stream)
        return response

    async def aclose(self): await self.inner.aclose()


class MCPRegistry:
    def __init__(self, store, secret, *, trusted_loopback_urls=(), session_factory=None):
        self.store, self.cipher = store, CredentialCipher(secret)
        self.trusted_loopback_urls = frozenset(trusted_loopback_urls)
        self.session_factory = session_factory
        self.lock = asyncio.Lock()
        store.db.execute('CREATE TABLE IF NOT EXISTS mcp_connections(id TEXT PRIMARY KEY,envelope TEXT NOT NULL)')

    def _load(self, identifier):
        row = self.store.db.execute('SELECT envelope FROM mcp_connections WHERE id=?', (identifier,)).fetchone()
        if not row: raise ValueError('MCP connection not found')
        return self.cipher.open(json.loads(row[0]), 'mcp:' + identifier)

    def _save(self, connection):
        envelope = self.cipher.seal(connection, 'mcp:' + connection['id'])
        self.store.db.execute('INSERT OR REPLACE INTO mcp_connections VALUES (?,?)', (connection['id'], _json(envelope)))

    @staticmethod
    def _view(connection):
        grants = connection['grants']
        return {k: connection[k] for k in ('id', 'name', 'url', 'connected', 'connectionId')} | {
            'tools': [{**tool, 'access': grants.get(tool['name'], 'disabled')} for tool in connection['tools']]}

    async def status(self):
        return [self._view(self._load(r[0])) for r in self.store.db.execute('SELECT id FROM mcp_connections ORDER BY rowid').fetchall()]

    def _validate_url(self, url):
        if not isinstance(url, str) or len(url) > 2048: raise ValueError('Provide an MCP HTTPS endpoint')
        parsed = urlsplit(url)
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('MCP endpoint credentials, query strings and fragments are blocked')
        if url in self.trusted_loopback_urls:
            try: local = ipaddress.ip_address(parsed.hostname or '').is_loopback
            except ValueError: local = False
            if parsed.scheme not in ('http', 'https') or not local:
                raise ValueError('Trusted fixture endpoints must use a literal loopback address')
        else:
            validate_browser_url(url)
            if parsed.scheme != 'https': raise ValueError('Public MCP endpoints require HTTPS')
        return url

    async def save(self, body):
        if not isinstance(body, dict) or set(body) - {'name', 'url', 'token'}: raise ValueError('Invalid MCP connection')
        name, token = body.get('name'), body.get('token', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError('MCP name must have 1-60 characters')
        if not isinstance(token, str) or len(token) > 8000 or (token and not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token)):
            raise ValueError('Invalid MCP bearer token')
        url = self._validate_url(body.get('url'))
        if token and (token in url or token in name): raise ValueError('Keep the MCP credential separate from its name and endpoint')
        async with self.lock:
            if self.store.db.execute('SELECT count(*) FROM mcp_connections').fetchone()[0] >= 10:
                raise ValueError('At most 10 MCP connections can be saved')
            connection = dict(id=secrets.token_hex(16), name=name.strip(), url=url, token=token,
                              connectionId=secrets.token_hex(16), connected=False, tools=[], grants={})
            self._save(connection)
            return self._view(connection)

    async def _address(self, url):
        self._validate_url(url)
        host = urlsplit(url).hostname
        if url in self.trusted_loopback_urls: return host
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM), 5)
            addresses = [r[4][0] for r in rows]
        except Exception: raise ValueError('Cannot verify MCP endpoint public address') from None
        if not addresses or any(not is_public_browser_address(a) for a in addresses):
            raise ValueError('MCP endpoint resolves to a private or reserved address')
        return addresses[0]

    @asynccontextmanager
    async def _session(self, connection):
        redactor = _SecretFilter(connection['token'])
        loggers = [logger for name, logger in logging.Logger.manager.loggerDict.items()
                   if isinstance(logger, logging.Logger) and name.startswith(('mcp.', 'httpx', 'httpcore'))]
        for logger in loggers: logger.addFilter(redactor)
        try:
            async with asyncio.timeout(TIMEOUT):
                if self.session_factory:
                    async with self.session_factory(connection) as session:
                        await session.initialize()
                        yield session
                else:
                    address = await self._address(connection['url'])
                    headers = {'Authorization': 'Bearer ' + connection['token']} if connection['token'] else {}
                    async with httpx.AsyncClient(headers={**headers, 'Accept-Encoding': 'identity'},
                            timeout=httpx.Timeout(TIMEOUT), trust_env=False, follow_redirects=False,
                            transport=_PinnedTransport(connection['url'], address)) as client:
                        async with streamable_http_client(connection['url'], http_client=client) as (reader, writer, _):
                            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=TIMEOUT)) as session:
                                await session.initialize()
                                yield session
        except Exception:
            # Remote error text may echo credentials. Only fixed local errors escape.
            raise ValueError('MCP operation failed; verify endpoint, authorization and tool availability') from None
        finally:
            for logger in loggers: logger.removeFilter(redactor)

    async def discover(self, identifier):
        async with self.lock:
            connection = self._load(identifier)
            connection.update(connectionId=secrets.token_hex(16), connected=False, grants={}, tools=[])
            self._save(connection)  # Failed reconnect also revokes every old grant/approval.
            async with self._session(connection) as session:
                listed = await session.list_tools()
                tools = listed.tools
                if len(tools) > 20 or listed.nextCursor:
                    raise ValueError('MCP discovery supports at most 20 tools without pagination')
                descriptors = [_descriptor(tool, connection['token']) for tool in tools]
                if len({tool['name'] for tool in descriptors}) != len(descriptors): raise ValueError('Duplicate MCP tool names')
                if len(_json(descriptors).encode()) > 100000: raise ValueError('MCP tool catalogue exceeds the supported limit')
            connection.update(connected=True, tools=descriptors)
            self._save(connection)
            return self._view(connection)

    async def grant(self, identifier, body):
        if not isinstance(body, dict) or set(body) != {'grants'} or not isinstance(body['grants'], list) or len(body['grants']) > 20:
            raise ValueError('Supply at most 20 explicit MCP tool grants')
        async with self.lock:
            connection = self._load(identifier)
            if not connection['connected']: raise ValueError('Test the MCP connection before granting tools')
            known = {t['name']: t for t in connection['tools']}; grants = {}
            for grant in body['grants']:
                if not isinstance(grant, dict) or set(grant) != {'name', 'fingerprint', 'access'}:
                    raise ValueError('Invalid MCP grant')
                name = grant['name']
                if not isinstance(name, str) or name not in known or name in grants or grant['fingerprint'] != known[name]['fingerprint'] or grant['access'] not in ('read', 'approval'):
                    raise ValueError('MCP tool changed; test and review its schema again')
                grants[name] = grant['access']
            connection.update(grants=grants, connectionId=secrets.token_hex(16))
            self._save(connection)
            return self._view(connection)

    async def remove(self, identifier):
        async with self.lock:
            self._load(identifier)
            self.store.db.execute('DELETE FROM mcp_connections WHERE id=?', (identifier,))
            return {'removed': True}

    def _bound(self, connection, tool, arguments, access):
        descriptor = next((t for t in connection['tools'] if t['name'] == tool), None)
        if not connection['connected'] or not descriptor or connection['grants'].get(tool) != access:
            raise ValueError('MCP tool has no matching owner grant')
        if not isinstance(arguments, dict) or len(_json(arguments).encode()) > 16000:
            raise ValueError('MCP arguments must be a JSON object within 16000 bytes')
        try: Draft202012Validator(descriptor['inputSchema']).validate(arguments)
        except Exception: raise ValueError('MCP arguments do not match the reviewed tool schema') from None
        return dict(id=connection['id'], connectionId=connection['connectionId'], tool=tool,
                    fingerprint=descriptor['fingerprint'], arguments=json.loads(_json(arguments)))

    async def bind_write(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'id', 'tool', 'arguments'}:
            raise ValueError('Supply the saved MCP server, tool and exact arguments')
        async with self.lock:
            return self._bound(self._load(payload['id']), payload['tool'], payload['arguments'], 'approval')

    async def _call(self, connection, bound):
        async with self._session(connection) as session:
            # Detect remote schema changes immediately before execution, not only on reconnect.
            listed = await session.list_tools()
            if len(listed.tools) > 20 or listed.nextCursor: raise ValueError('MCP tool catalogue changed; reconnect')
            current = next((_descriptor(t, connection['token']) for t in listed.tools if t.name == bound['tool']), None)
            if not current or current['fingerprint'] != bound['fingerprint']:
                raise ValueError('MCP tool schema changed; reconnect and review grants')
            result = await session.call_tool(bound['tool'], bound['arguments'])
            if result.isError: raise ValueError('MCP tool reported failure')
            text = '\n'.join(block.text for block in result.content if getattr(block, 'type', None) == 'text')
            if result.structuredContent is not None:
                text += '\nSTRUCTURED RESULT\n' + _json(result.structuredContent)
            if connection['token']: text = text.replace(connection['token'], '[redacted]')
            if len(_json(result.model_dump(mode='json', by_alias=True, exclude_none=True)).encode()) > MAX_RESPONSE:
                raise ValueError('MCP result exceeds 1 MiB')
        return {k: bound[k] for k in ('id', 'connectionId', 'tool', 'fingerprint')} | {
            'text': text[:12000], 'truncated': len(text) > 12000,
            'omittedContentBlocks': sum(getattr(b, 'type', None) != 'text' for b in result.content)}

    async def call_read(self, identifier, tool, arguments):
        async with self.lock:
            connection = self._load(identifier)
            bound = self._bound(connection, tool, arguments, 'read')
            return await self._call(connection, bound)

    async def execute_write(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'id', 'connectionId', 'tool', 'fingerprint', 'arguments'}:
            raise ValueError('Invalid bound MCP approval')
        async with self.lock:
            connection = self._load(payload['id'])
            current = self._bound(connection, payload['tool'], payload['arguments'], 'approval')
            if current != payload: raise ValueError('MCP connection or grant changed; request a fresh approval')
            return await self._call(connection, current)

