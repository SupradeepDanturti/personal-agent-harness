import { useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { type Api, type Item } from "../../api/client";
import { text, rows } from "../../lib/knowledge";
import { useData } from "../../hooks/useData";
import { ErrorNotice } from "../../components/ErrorNotice";

const kindLabel = (kind: unknown) => {
  const name = text(kind);
  return ({ message: "Conversation", task: "Task / specialist", workflow: "Workflow", memory: "Saved memory", action: "Completed action", tool: "MCP tool result" } as Record<string, string>)[name] || name;
};

export function HistoryExplorer({ api }: { api: Api }) {
  const state = useData(api, "/history/index");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<Item | null>(null);
  const [selected, setSelected] = useState<Item | null>(null);
  const [filter, setFilter] = useState("all");
  const [searchedQuery, setSearchedQuery] = useState("");
  const search = (semantic = false) => void state.run(async () => {
    setSelected(null);
    setResult(null);
    setFilter("all");
    const response = await api(semantic ? "/history/search" : "/recall/search", { query: query.trim(), ...(semantic ? { semantic: true } : {}) });
    setSearchedQuery(query.trim());
    setResult(semantic ? {
      ...response,
      results: rows(response.messages).map((message) => ({ ...message, kind: "message", title: `${text(message.role)} message` })),
    } : response);
  });
  const results = rows(result?.results);
  const filtered = filter === "all" ? results : results.filter((item) => item.kind === filter);
  return (
    <>
      <ErrorNotice value={state.error} />
      <section className="section-card knowledge-card">
        <div className="eyebrow">YOUR WORK, REMEMBERED</div>
        <h2>Pick up where you left off</h2>
        <p>Search retained conversations, tasks, specialist results, workflows, and saved activity. Open a source to see its original content before reusing it.</p>
        <form className="stack-form" onSubmit={(event) => { event.preventDefault(); if (query.trim() && !state.busy) search(); }}>
          <label className="native-field">
            What are you looking for?
            <input value={query} maxLength={1000} onChange={(e) => setQuery(e.target.value)} placeholder="A decision, project name, or result…" />
          </label>
          <div className="button-row">
            <Button label={state.busy ? "Searching…" : "Keyword search"} variant="primary" isDisabled={state.busy || !query.trim()} onClick={() => search()} />
            <Button label="Search by meaning" variant="secondary" isDisabled={state.busy || !query.trim()} onClick={() => search(true)} />
          </div>
          <small className="section-description">Keyword search stays local and uses no model calls. Search by meaning searches conversations only and may use up to two extra model calls.</small>
        </form>
      </section>
      {selected && (
        <section className="section-card recall-source" aria-label="Original source">
          <div className="section-heading">
            <div><div className="eyebrow">{kindLabel(selected.kind)}</div><h2>{text(selected.title) || "Original source"}</h2></div>
            <Button label="Close source" variant="secondary" onClick={() => setSelected(null)} />
          </div>
          <p className="section-description">Saved {new Date(Number(selected.createdAt)).toLocaleString()}</p>
          <pre className="break-text">{text(selected.content)}</pre>
          {selected.truncated === true && <p className="section-description">Showing the first 12,000 characters of this source.</p>}
          <small>Source reference: {text(selected.kind)}:{text(selected.id)}</small>
        </section>
      )}
      {result && (
        <section className="record-list" aria-label="Search results" aria-live="polite">
          <div className="recall-results-heading">
            <p>{results.length} {results.length === 1 ? "result" : "results"} for <strong>{searchedQuery}</strong></p>
            <label className="native-field recall-filter">Show
              <select aria-label="Filter sources" value={filter} onChange={(event) => setFilter(event.target.value)}>
                <option value="all">All sources</option>
                {[...new Set(results.map((item) => text(item.kind)))].map((kind) => <option value={kind} key={kind}>{kindLabel(kind)}</option>)}
              </select>
            </label>
          </div>
          {text(result.limitation) && <p className="section-description">{text(result.limitation)}</p>}
          {filtered.map((item) => (
            <article className="record" key={`${text(item.kind)}:${text(item.id)}`}>
              <div className="record-body">
                <div className="recall-meta"><span className="recall-kind">{kindLabel(item.kind)}</span><span>{new Date(Number(item.createdAt)).toLocaleString()}</span></div>
                <h3>{text(item.title)}</h3>
                <p className="break-text">{text(item.excerpt)}</p>
              </div>
              <Button label="Open source" variant="secondary" isDisabled={state.busy} onClick={() => void state.run(async () => {
                setSelected(null);
                setSelected(await api(`/recall/item?kind=${encodeURIComponent(text(item.kind))}&id=${encodeURIComponent(text(item.id))}`));
              })} />
            </article>
          ))}
          {!filtered.length && <div className="section-card"><h3>No matches yet</h3><p>Try a project name or a shorter phrase. Only content retained in this workspace can be found.</p></div>}
        </section>
      )}
    </>
  );
}
