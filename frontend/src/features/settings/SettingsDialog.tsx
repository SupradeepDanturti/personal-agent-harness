import { useEffect, useRef, useState } from "react";
import type { Api } from "../../api/client";
import type { View } from "../../app/navigation";
import { useResource } from "../../hooks/useResource";
import { obj, txt } from "../../lib/format";
import { Connections } from "../connections/Connections";

const sections = ["General", "Connectors", "Permissions", "Data controls", "Help & support"] as const;
type Section = typeof sections[number];
type Props = {
  api: Api;
  open: boolean;
  onClose: () => void;
  mode: "light" | "dark";
  onModeChange: (mode: "light" | "dark") => void;
  onNavigate: (view: View) => void;
  onDisconnect: () => void;
};

function SectionIcon({ section }: { section: Section }) {
  const paths: Record<Section, string> = {
    General: "M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
    Connectors: "m9 15 6-6m-5-2 2-2a5 5 0 0 1 7 7l-2 2m-3 3-2 2a5 5 0 0 1-7-7l2-2",
    Permissions: "m12 3 8 3v6c0 4-8 9-8 9s-8-5-8-9V6l8-3Zm-4 9 3 3 5-6",
    "Data controls": "M4 4h16v16H4V4Zm0 5h16M9 9v11",
    "Help & support": "M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3m0 3h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  };
  return <svg className="settings-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[section]} /></svg>;
}

function SettingsContent({ api, mode, onModeChange, onNavigate, onClose, onDisconnect }: Omit<Props, "open">) {
  const [section, setSection] = useState<Section>("General");
  const status = useResource(api, "/status");
  const budget = obj(status.data.budget);
  const summary = obj(budget.summary);
  const navigate = (view: View) => { onClose(); onNavigate(view); };
  const count = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "Unavailable";
  const charged = summary.chargedTokens;
  const limit = budget.dailyTokenLimit;
  const percentage = typeof charged === "number" && typeof limit === "number" && limit > 0 ? Math.min(100, Math.max(0, charged / limit * 100)) : undefined;
  const repo = "https://github.com/Tenacious-3/personal-agent-harness";
  return <div className="settings-shell">
    <aside className="settings-sidebar">
      <h2 id="settings-title" className="settings-title">Settings</h2>
      <div className="settings-tabs" role="tablist" aria-label="Settings sections" aria-orientation="vertical">
        {sections.map((name, index) => <button key={name} type="button" role="tab"
          id={`settings-tab-${index}`} aria-controls="settings-panel" aria-selected={section === name}
          tabIndex={section === name ? 0 : -1} className={`settings-tab ${section === name ? "active" : ""}`}
          onClick={() => setSection(name)} onKeyDown={(event) => {
            let next: number | undefined;
            if (event.key === "ArrowDown") next = (index + 1) % sections.length;
            if (event.key === "ArrowUp") next = (index + sections.length - 1) % sections.length;
            if (event.key === "Home") next = 0;
            if (event.key === "End") next = sections.length - 1;
            if (next === undefined) return;
            event.preventDefault();
            setSection(sections[next]);
            document.getElementById(`settings-tab-${next}`)?.focus();
          }}><SectionIcon section={name} /><span>{name}</span></button>)}
      </div>
      <div className="settings-sidebar-footer">
        <span>Personal workspace</span>
        <button type="button" className="settings-logout" onClick={() => { onClose(); onDisconnect(); }}>Log out <span aria-hidden="true">↗</span></button>
      </div>
    </aside>
    <div className="settings-content">
      <button type="button" className="settings-close" aria-label="Close settings" onClick={onClose}>×</button>
      <div id="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${sections.indexOf(section)}`} className="settings-panel" tabIndex={0}>
        <h3 className="settings-panel-title">{section}</h3>
        {status.error && <div role="alert" className="settings-error">{status.error}</div>}
        {section === "General" && <>
          <section className="settings-profile" aria-label="Workspace profile">
            <div className="settings-avatar" aria-hidden="true">P</div>
            <div><strong>Personal workspace</strong><p>Local workspace</p></div>
            <span className="settings-badge">{status.loading ? "Checking…" : status.data.local ? "Connected" : "Unavailable"}</span>
          </section>
          <section className="settings-section">
            <div className="settings-section-heading"><h4>Daily budget</h4><span>UTC{summary.day ? ` · ${txt(summary.day)}` : ""}</span></div>
            <p className="settings-muted">Model token usage in your local runtime.</p>
            {status.loading ? <p role="status">Loading usage…</p> : <>
              <div className="settings-budget-total"><strong>{count(charged)}</strong><span> / {count(limit)} tokens charged</span></div>
              {percentage !== undefined && <progress className="settings-budget-progress" value={percentage} max={100} aria-label="Daily token budget charged" />}
              <dl className="settings-usage-grid">
                <div><dt>Input tokens</dt><dd>{count(summary.inputTokens)}</dd></div>
                <div><dt>Output tokens</dt><dd>{count(summary.outputTokens)}</dd></div>
                <div><dt>Reserved tokens</dt><dd>{count(summary.reservedTokens)}</dd></div>
                <div><dt>Failed run charges</dt><dd>{count(summary.failedTokens)}</dd></div>
              </dl>
              <p className="settings-muted">Charged usage includes active reservations. Failed runs may retain their reserved charge when actual usage is unavailable.</p>
            </>}
          </section>
          <section className="settings-section">
            <div className="settings-section-heading"><div><h4>Appearance</h4><p className="settings-muted">Choose how your workspace looks.</p></div>
              <div className="settings-appearance" role="group" aria-label="Appearance">
                {(["light", "dark"] as const).map((choice) => <button key={choice} type="button" aria-pressed={mode === choice} onClick={() => onModeChange(choice)}>{choice === "light" ? "Light" : "Dark"}</button>)}
              </div>
            </div>
          </section>
        </>}
        {section === "Connectors" && <div className="settings-connectors"><p className="settings-muted">Connect accounts and review the access each service receives.</p><Connections api={api} runtime={status.data} navigate={navigate} /></div>}
        {section === "Permissions" && <section className="settings-section">
          <h4>Review actions before they happen</h4>
          <p>Sending mail, creating calendar events, updating spreadsheet cells, and supported provider writes require approval of the exact action. Browser writes require approval bound to the observed page URL.</p>
          <p>MCP tool access is reviewed per connection and tool. Saving a workflow template does not grant permissions or run agents.</p>
          <button type="button" className="settings-link-button" onClick={() => navigate("Approvals")}>Review approvals <span aria-hidden="true">↗</span></button>
        </section>}
        {section === "Data controls" && <section className="settings-section">
          <p className="settings-muted">Review the context and files kept in your workspace.</p>
          {([ ["Memory", "Review saved facts, preferences and their sources."], ["History", "Search conversations and previous work."], ["Files", "Browse workspace files and generated artifacts."] ] as const).map(([view, description]) => <button type="button" className="settings-data-link" key={view} onClick={() => navigate(view)}><span><strong>{view}</strong><span>{description}</span></span><span aria-hidden="true">↗</span></button>)}
        </section>}
        {section === "Help & support" && <section className="settings-section">
          <p className="settings-muted">Documentation and support for your personal harness.</p>
          <a className="settings-data-link" href={`${repo}#readme`} target="_blank" rel="noopener noreferrer">Read the setup guide <span aria-hidden="true">↗</span></a>
          <a className="settings-data-link" href={`${repo}/blob/main/docs/PLAN.md`} target="_blank" rel="noopener noreferrer">Explore the project plan <span aria-hidden="true">↗</span></a>
          <a className="settings-data-link" href={`${repo}/issues`} target="_blank" rel="noopener noreferrer">Report an issue <span aria-hidden="true">↗</span></a>
        </section>}
      </div>
    </div>
  </div>;
}

export function SettingsDialog(props: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (props.open && element && !element.open) element.showModal();
    if (!props.open && element?.open) element.close();
  }, [props.open]);
  return <dialog ref={dialog} className="settings-dialog" aria-labelledby="settings-title"
    onCancel={(event) => { event.preventDefault(); props.onClose(); }}
    onClick={(event) => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) props.onClose();
    }}>
    {props.open && <SettingsContent {...props} />}
  </dialog>;
}
