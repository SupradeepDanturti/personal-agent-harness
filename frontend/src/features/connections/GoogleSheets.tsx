import { useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { type Api, type Item } from "../../api/client";
import { list, obj, pretty, txt } from "../../lib/format";
import { useResource } from "../../hooks/useResource";
import { useAction } from "../../hooks/useAction";
import { Card, Feedback, Refresh } from "../../components/ui";

function spreadsheetId(value: string): string {
  return value.match(/spreadsheets\/d\/([A-Za-z0-9_-]+)/)?.[1] || value.trim();
}

function updateValues(value: string): unknown[][] {
  let parsed: unknown;
  try { parsed = JSON.parse(value); }
  catch { throw new Error('Enter valid JSON rows, for example [["value", 123]].'); }
  if (
    !Array.isArray(parsed) || !parsed.length || parsed.length > 100 ||
    parsed.some((row) => !Array.isArray(row) || !row.length || row.length > 20 ||
      row.some((cell: unknown) => cell !== null && typeof cell !== "string" &&
        typeof cell !== "boolean" && (typeof cell !== "number" || !Number.isFinite(cell)))) ||
    parsed.reduce((count, row) => count + row.length, 0) > 1000
  ) throw new Error("Enter a JSON array of rows: up to 100 rows, 20 columns, and 1,000 cells, using text, numbers, booleans or null.");
  return parsed as unknown[][];
}

export function GoogleSheets({ api, google, openApprovals }: {
  api: Api;
  google: Item;
  openApprovals: () => void;
}) {
  const resource = useResource(api, "/google/sheets");
  const action = useAction();
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [access, setAccess] = useState("range");
  const [allowedRange, setAllowedRange] = useState("");
  const [query, setQuery] = useState("");
  const [discovered, setDiscovered] = useState<Item[]>([]);
  const [nextPage, setNextPage] = useState<string | null>(null);
  const [partial, setPartial] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [range, setRange] = useState("");
  const [tabs, setTabs] = useState<Item[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [result, setResult] = useState<Item | null>(null);
  const [draftValues, setDraftValues] = useState("");
  const [approval, setApproval] = useState<Item | null>(null);
  const registered = list(resource.data, "sheets");
  const selected = registered.find((sheet) => txt(sheet.id) === selectedId) ||
    discovered.find((sheet) => txt(sheet.id) === selectedId);
  const rangeOnly = selected?.access === "range";
  const capabilities = obj(google.capabilities);
  const discoveryEnabled = resource.data.discoveryEnabled === true;
  const busy = action.busy || resource.loading;
  const choose = (sheet: Item) => {
    setSelectedId(txt(sheet.id));
    setRange(txt(sheet.range));
    setTabs([]);
    setNextOffset(null);
    setResult(null);
    setDraftValues("");
    setApproval(null);
  };
  const discover = (pageToken: string | null = null) => void action.run(async () => {
    const data = await api("/google/sheets/search", { query: query.trim(), pageToken });
    const items = list(data, "sheets");
    setDiscovered((previous) => pageToken ? [...previous, ...items] : items);
    setNextPage(typeof data.nextPageToken === "string" ? data.nextPageToken : null);
    setPartial(data.partial === true);
  });
  const loadTabs = (offset = 0) => void action.run(async () => {
    const data = await api(`/google/sheets/${encodeURIComponent(selectedId)}/tabs`, { offset });
    const items = list(data, "tabs");
    setTabs((previous) => offset ? [...previous, ...items] : items);
    setNextOffset(typeof data.nextOffset === "number" ? data.nextOffset : null);
  });
  return (
    <>
      <Feedback loading={resource.loading} error={resource.error || action.error} notice={action.notice} />
      <Card title="Registered spreadsheets" actions={<Refresh reload={resource.reload} disabled={busy} />}>
        <p className="section-description">
          Register a spreadsheet you choose, with either one allowed range or the
          whole spreadsheet. Registrations belong to the connected Google account.
        </p>
        <div className="stack-form">
          <label className="native-field">Spreadsheet name
            <input value={name} maxLength={60} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="native-field">Spreadsheet URL or ID
            <input value={id} maxLength={250} onChange={(event) => setId(event.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…/edit" />
          </label>
          <label className="native-field">Allowed access
            <select aria-label="Allowed access" value={access} onChange={(event) => setAccess(event.target.value)}>
              <option value="range">One cell range</option>
              <option value="spreadsheet">Whole spreadsheet</option>
            </select>
          </label>
          {access === "range" && <label className="native-field">Allowed registration range
            <input value={allowedRange} maxLength={220} onChange={(event) => setAllowedRange(event.target.value)} placeholder="'Budget'!A1:D25" />
          </label>}
          <Button label="Register spreadsheet" variant="secondary"
            isDisabled={busy || !name.trim() || !id.trim() || (access === "range" && !allowedRange.trim())}
            onClick={() => void action.run(async () => {
              const sheet = await api("/google/sheets", { name: name.trim(), spreadsheetId: spreadsheetId(id), access,
                range: access === "range" ? allowedRange.trim() : null });
              await resource.reload();
              choose(sheet);
            }, "Spreadsheet registered. No cells were changed.")} />
        </div>
        <div className="record-list">
          {registered.map((sheet) => <article className="record" key={txt(sheet.id)}>
            <div className="record-body"><h3>{txt(sheet.name)}</h3>
              <p>{sheet.access === "range" ? txt(sheet.range) : "Whole spreadsheet"}</p>
              <div className="button-row">
                <Button label={`Open ${txt(sheet.name)}`} variant="secondary" isDisabled={busy} onClick={() => choose(sheet)} />
                <Button label={`Unregister ${txt(sheet.name)}`} variant="secondary" isDisabled={busy}
                  onClick={() => void action.run(async () => {
                    await api(`/google/sheets/${encodeURIComponent(txt(sheet.id))}`, undefined, "DELETE");
                    if (selectedId === txt(sheet.id)) { setSelectedId(""); setResult(null); setApproval(null); }
                    await resource.reload();
                  }, "Registration removed. The Google spreadsheet was not deleted.")} />
              </div>
            </div>
          </article>)}
          {!registered.length && !resource.loading && <p className="section-description">No spreadsheets registered yet.</p>}
        </div>
      </Card>

      <Card title="Find spreadsheets in Drive">
        <p className="section-description">
          Optional spreadsheet-name discovery. Enable it explicitly to search
          Drive metadata; your registered spreadsheets work without discovery.
        </p>
        {!capabilities.driveDiscovery && <p className="info-banner">Reconnect Google with Drive metadata access to enable discovery.</p>}
        <Button label={discoveryEnabled ? "Disable spreadsheet discovery" : "Enable spreadsheet discovery"}
          variant="secondary" isDisabled={busy || !capabilities.driveDiscovery}
          onClick={() => void action.run(async () => {
            await api("/google/sheets/discovery", { enabled: !discoveryEnabled }, "PATCH");
            if (discoveryEnabled) {
              if (discovered.some((sheet) => txt(sheet.id) === selectedId)) setSelectedId("");
              setDiscovered([]); setNextPage(null);
            }
            await resource.reload();
          })} />
        {discoveryEnabled && <div className="stack-form">
          <label className="native-field">Spreadsheet name search
            <input value={query} maxLength={100} disabled={busy} onChange={(event) => {
              setQuery(event.target.value); setNextPage(null); setDiscovered([]);
            }} />
          </label>
          <div className="button-row">
            <Button label="Search spreadsheets" variant="secondary" isDisabled={busy || !query.trim()} onClick={() => discover()} />
            {nextPage && <Button label="More spreadsheet results" variant="secondary" isDisabled={busy} onClick={() => discover(nextPage)} />}
          </div>
          {partial && <p className="section-description">This is a bounded result list. More matching spreadsheets may exist.</p>}
          {discovered.map((sheet) => <article className="record" key={txt(sheet.id)}><div className="record-body">
            <h3>{txt(sheet.name)}</h3><p>Temporary access expires after five minutes. Register it for later use.</p>
            <div className="button-row">
              <Button label={`Inspect ${txt(sheet.name)}`} variant="secondary" isDisabled={busy} onClick={() => choose(sheet)} />
              <Button label={`Fill registration for ${txt(sheet.name)}`} variant="secondary" isDisabled={busy}
                onClick={() => { setName(txt(sheet.name).slice(0, 60)); setId(txt(sheet.spreadsheetId)); }} />
            </div>
          </div></article>)}
        </div>}
      </Card>

      {selected && <Card title={`Spreadsheet: ${txt(selected.name)}`}>
        <p className="section-description">{rangeOnly ? `Restricted to ${txt(selected.range)}.` : "Choose a tab-qualified range before reading or proposing an update."}
          {selected.canEdit === false ? " This spreadsheet is read-only for the connected account." : " Updates always require a separate approval."}</p>
        {!rangeOnly && <>
          <div className="button-row"><Button label="List spreadsheet tabs" variant="secondary" isDisabled={busy} onClick={() => loadTabs()} />
            {nextOffset !== null && <Button label="More spreadsheet tabs" variant="secondary" isDisabled={busy} onClick={() => loadTabs(nextOffset)} />}</div>
          <div className="record-list">{tabs.map((tab) => <article className="record" key={txt(tab.title)}><div className="record-body">
            <h3>{txt(tab.title)}</h3><p>{txt(tab.rows)} rows · {txt(tab.columns)} columns{tab.supported === false ? " · Unsupported tab type" : ""}</p>
            {tab.supported !== false && <Button label={`Use tab ${txt(tab.title)}`} variant="secondary" isDisabled={busy}
              onClick={() => {
                const rows = Math.max(1, Math.min(25, Number(tab.rows) || 25));
                const columns = Math.max(1, Math.min(4, Number(tab.columns) || 4));
                setRange(`'${txt(tab.title).replaceAll("'", "''")}'!A1:${String.fromCharCode(64 + columns)}${rows}`);
                setResult(null); setApproval(null);
              }} />}
          </div></article>)}</div>
        </>}
        <div className="stack-form">
          <label className="native-field">A1 range to read or update
            <input value={range} maxLength={220} disabled={rangeOnly || busy} onChange={(event) => {
              setRange(event.target.value); setResult(null); setApproval(null);
            }} placeholder="'Budget'!A1:D25" />
          </label>
          <p className="section-description">Up to 100 rows, 20 columns and 1,000 cells per request.</p>
          <Button label="Read spreadsheet range" variant="secondary" isDisabled={busy || !range.trim()}
            onClick={() => void action.run(async () => {
              const data = await api(`/google/sheets/${encodeURIComponent(selectedId)}/read`, rangeOnly ? {} : { range: range.trim() });
              setResult(data);
            })} />
          {result && <details open><summary>Returned cell values (untrusted reference data)</summary><pre>{pretty(result.values)}</pre></details>}
          <label className="native-field">Proposed cell values (JSON rows)
            <textarea value={draftValues} maxLength={10000} rows={5} disabled={busy || selected.canEdit === false}
              onChange={(event) => { setDraftValues(event.target.value); setApproval(null); }} placeholder={'[["Example", 123], ["Another row", true]]'} />
          </label>
          <p className="section-description">Values are written as literal values using RAW mode. Creating a proposal does not change the spreadsheet.</p>
          <Button label="Propose exact cell update" variant="secondary" isDisabled={busy || selected.canEdit === false || !range.trim() || !draftValues.trim()}
            onClick={() => void action.run(async () => {
              const values = updateValues(draftValues);
              const data = await api(`/google/sheets/${encodeURIComponent(selectedId)}/propose`, { ...(rangeOnly ? {} : { range: range.trim() }), values });
              setApproval(data);
            }, "Update proposed. Open Approvals to review the exact account, range and values before anything changes.")} />
          {approval && <div className="info-banner" role="status"><p>Pending approval: {txt(approval.summary) || txt(approval.id)}</p>
            <Button label="Review spreadsheet update in Approvals" variant="secondary" onClick={openApprovals} />
          </div>}
        </div>
      </Card>}
    </>
  );
}
