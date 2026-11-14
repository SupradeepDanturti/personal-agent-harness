import { useEffect, useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { type Api, type Item } from "../../api/client";
import { text, rows, pretty } from "../../lib/knowledge";
import { useData } from "../../hooks/useData";
import { ErrorNotice } from "../../components/ErrorNotice";

type Access = "disabled" | "read" | "approval";

export function MCPConnections({ api }: { api: Api }) {
  const state = useData(api, "/mcp");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [notice, setNotice] = useState("");
  return (
    <section className="section-card knowledge-card" aria-label="MCP connections">
      <div className="eyebrow">BRING YOUR OWN TOOLS</div>
      <h2>MCP servers</h2>
      <p>Connect a server you trust, discover its tools, then choose what your assistant can use. Adding a server grants no tool access.</p>
      <ErrorNotice value={state.error} />
      {notice && <p role="status">{notice}</p>}
      <div className="stack-form">
        <label className="native-field">MCP server name
          <input value={name} maxLength={60} onChange={(event) => setName(event.target.value)} placeholder="My research tools" />
        </label>
        <label className="native-field">MCP server URL
          <input type="url" value={url} maxLength={2000} onChange={(event) => setUrl(event.target.value)} placeholder="https://your-server.example/mcp" />
        </label>
        <label className="native-field">MCP bearer token (optional)
          <input type="password" autoComplete="off" value={token} maxLength={4096} onChange={(event) => setToken(event.target.value)} />
        </label>
        <p className="section-description">Credentials are saved encrypted on your assistant server. Enter tokens here rather than in chat. A server connection uses its own credentials; it does not inherit this app's connected accounts.</p>
        <Button label="Add MCP server" variant="primary" isDisabled={state.busy || !name.trim() || !url.trim()} onClick={() => void state.run(async () => {
          setNotice("");
          await api("/mcp", { name: name.trim(), url: url.trim(), ...(token.trim() ? { token: token.trim() } : {}) });
          setName(""); setUrl(""); setToken("");
          setNotice("Server saved. Discover its tools before granting access.");
        })} />
      </div>
      <div className="mcp-connections">
        {rows(state.data.connections).map((connection) => <MCPServer key={text(connection.id)} api={api} connection={connection} reload={state.reload} />)}
        {!rows(state.data.connections).length && <p className="section-description">No MCP servers added yet.</p>}
      </div>
    </section>
  );
}

function MCPServer({ api, connection, reload }: { api: Api; connection: Item; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<Record<string, Access>>({});
  useEffect(() => { setDraft({}); }, [connection.connectionId]);
  const tools = rows(connection.tools);
  const id = encodeURIComponent(text(connection.id));
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await fn(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { await reload(); setBusy(false); }
  };
  return (
    <article className="mcp-server" aria-label={`MCP server ${text(connection.name)}`}>
      <div className="section-heading">
        <div><h3>{text(connection.name)}</h3><p className="break-text section-description">{text(connection.url)}</p></div>
        <span className="recall-kind">{connection.connected ? "Tools discovered" : "Not verified"}</span>
      </div>
      <ErrorNotice value={error} />
      {notice && <p role="status">{notice}</p>}
      <div className="button-row">
        <Button label="Discover MCP tools" variant="secondary" isDisabled={busy} onClick={() => void run(async () => {
          await api(`/mcp/${id}/test`, {});
          setDraft({});
          setNotice("Discovery complete. Review tool permissions before using them.");
        })} />
        <Button label="Remove MCP server" variant="secondary" isDisabled={busy} onClick={() => void run(async () => { await api(`/mcp/${id}`, undefined, "DELETE"); })} />
      </div>
      {tools.length > 0 && <p className="section-description">Discovering again removes existing tool access, including when discovery fails. Review and save permissions again afterward.</p>}
      {tools.length > 0 && <>
        <p className="section-description">Disabled tools cannot run. “Always review” asks you to approve each exact call. “Allow without review” trusts this server to respect read access; the harness cannot prove a remote tool is read-only. Tool changes require reviewing its access again.</p>
        {tools.map((tool) => <div className="mcp-tool" key={`${text(tool.name)}:${text(tool.fingerprint)}`}>
          <h4>{text(tool.name)}</h4>
          <p className="break-text">{text(tool.description) || "No description supplied by this server."}</p>
          <label className="native-field">Access for {text(tool.name)}
            <select aria-label={`Access for ${text(tool.name)}`} disabled={busy} value={draft[text(tool.name)] ?? (text(tool.access) || "disabled")} onChange={(event) => setDraft({ ...draft, [text(tool.name)]: event.target.value as Access })}>
              <option value="disabled">Disabled</option>
              <option value="read">Allow without review (trusted reads)</option>
              <option value="approval">Always review</option>
            </select>
          </label>
          <details><summary>Tool definition</summary><pre className="break-text">{pretty(tool.inputSchema)}</pre><small className="break-text">Definition reference: {text(tool.fingerprint)}</small></details>
        </div>)}
        <Button label="Save MCP tool access" variant="primary" isDisabled={busy} onClick={() => void run(async () => {
          const grants = tools.map((tool) => ({ name: text(tool.name), fingerprint: text(tool.fingerprint), access: draft[text(tool.name)] ?? (text(tool.access) || "disabled") })).filter((grant) => grant.access !== "disabled");
          await api(`/mcp/${id}/grants`, { grants });
          setDraft({}); setNotice("Tool access saved.");
        })} />
      </>}
      {!tools.length && <p className="section-description">Discover tools to inspect what this server offers. No tools currently have access.</p>}
    </article>
  );
}
