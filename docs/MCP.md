# Connect MCP tools

Use Connections -> MCP servers. Add a descriptive name, the exact HTTPS MCP
endpoint and an optional bearer token. Tokens are encrypted with Python dcrypt
inside SQLite using the configured credential key. They are not returned by the
API or put in model context. This connection uses separate credentials; it does
not inherit integrations configured in Codex or the browser.

Discover tools, inspect each tool definition, then save its access:

- Disabled: the agent cannot call it.
- Allow without review: explicitly trust this server/tool for direct reads.
  A server read-only annotation does not establish this permission or prove that
  the remote implementation cannot modify data.
- Always review: the agent proposes exact tool arguments in Approvals. It cannot
  execute the call until you approve. A completed receipt is separate from the
  approval itself. Interrupted writes become uncertain and need reconciliation.

Discovering again immediately clears every old grant, even if discovery fails.
Saving access also changes the connection generation. Pending approvals bind
that generation and the full reviewed descriptor fingerprint. A schema change,
revocation, rediscovery or removal invalidates old approvals. A call already
executing completes before reconnect/removal returns. There is no automatic
retry for a tool call whose outcome may be unknown.

## Local development

For a local server, set MCP_TRUSTED_LOCAL_URLS to a comma-separated list of exact
literal-loopback endpoints, for example http://127.0.0.1:9000/mcp, before starting
the harness. This is an operator setting, not an agent argument. In Docker,
127.0.0.1 means inside the container; it does not reach a host-side server.
Public servers require HTTPS and public resolved addresses. Redirects, implicit
proxy settings, URL credentials, query strings and fragments are blocked.
Host-side Docker MCP networking needs an explicitly designed grant before it
can be supported; do not broaden this setting to arbitrary private addresses.

The current client uses MCP 1.30 Streamable HTTP. It does not launch stdio
programs or host commands, import external credentials, automatically follow
resource links, run MCP prompts or grant server sampling/elicitation access.
Discovery supports at most 20 tools on a server, at most 10 saved servers, and
reviewed schemas up to 8,000 bytes. Regular-expression schema keywords and
external schema references are unsupported to keep validation local and bounded. Calls have a 20-second deadline and a 1 MiB
wire-response limit. Returned text and structured content are bounded to 12,000
characters. Embedded images/resources are omitted rather than fetched.

## Find previous results

The agent uses mcp_list to browse compact granted tool names, then inspects one
specific definition before using mcp_read or proposing mcp.call. This avoids
injecting every server's full schema into each model request. Direct-read results
are recorded as MCP tool results; approved calls retain completed action receipts.
History keyword search and recall_search/recall_read find these retained records
without another model call. Past results are reference data and may be stale.

Existing memory-forget behavior removes matching retained result receipts; raw
messages are removed only when the corresponding source-removal option is used.
Removing a connection revokes credentials/access but preserves past receipts so
completed work remains inspectable. Credential-key loss prevents reopening
existing encrypted connections; keep the key stable and back it up privately.

## Verification

Tests use synthetic tools and credentials. Coverage includes actual SDK/FastMCP
Streamable HTTP JSON-RPC, explicit grants, schema changes, revocation, encrypted
restart, timeouts, blocked redirects/private endpoints, response bounds,
credential redaction, exact approvals and searchable/forgettable receipts.
Chromium acceptance covers connection setup, discovery, permissions, restart,
revocation/removal and mobile layout. These checks do not authorize or verify a
real third-party server/account.
