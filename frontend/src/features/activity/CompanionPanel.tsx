import { useState } from "react";
import type { Api, Item } from "../../api/client";
import type { View } from "../../app/navigation";
import { Icon } from "../../components/Icon";
import { useResource } from "../../hooks/useResource";
import { list, txt } from "../../lib/format";

type CompanionTab = "Activity" | "Approvals" | "Identity";

function timestamp(value: unknown): number {
  if (typeof value === "number") return value;
  return typeof value === "string" && value ? new Date(value).getTime() : NaN;
}

function shortTime(value: unknown, includeDay = false): string {
  const date = new Date(timestamp(value));
  if (Number.isNaN(date.getTime())) return "Time not recorded";
  return date.toLocaleString(undefined, {
    ...(includeDay ? { month: "short", day: "numeric" } as const : {}),
    hour: "numeric", minute: "2-digit",
  });
}

const eventTitles: Record<string, string> = {
  "runtime.recovery": "Workspace recovery checked",
  "scheduler.error": "Schedule encountered an error",
  "run.started": "Assistant run started",
  "run.completed": "Assistant run completed",
  "run.failed": "Assistant run failed",
  "run.usage": "Run usage recorded",
  "tool.requested": "Tool action requested",
  "source.observed": "Source checked",
  "action.started": "Approved action started",
  "action.completed": "Approved action completed",
  "action.uncertain": "Action needs a result check",
  "workflow.created": "Workflow created",
  "workflow.retry": "Workflow retry requested",
  "workflow.failed": "Workflow failed",
  "workflow.completed": "Workflow completed",
  "workflow.agent-completed": "Workflow stage completed",
  "subtask.completed": "Background task completed",
  "delivery.decision": "Result delivery reviewed",
  "memory.projection-failed": "Memory update failed",
  "mcp.result": "Connected tool returned a result",
  "artifact.written": "Workspace file written",
};

function eventTitle(item: Item): string {
  const type = txt(item.type ?? item.kind);
  const words = type.replace(/[._-]+/g, " ");
  return eventTitles[type] || (words ? words[0].toUpperCase() + words.slice(1) : "Activity recorded");
}

function eventState(item: Item): "completed" | "failed" | "recorded" {
  const type = txt(item.type ?? item.kind);
  if (/(?:^|[.\-])(failed|error|uncertain)$/.test(type)) return "failed";
  if (/(?:^|[.\-])completed$/.test(type)) return "completed";
  return "recorded";
}

export function CompanionPanel({ api, runtime, onNavigate, onClose }: {
  api: Api;
  runtime: Item;
  onNavigate: (view: View) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<CompanionTab>("Activity");
  const schedules = useResource(api, "/schedules", true);
  const events = useResource(api, "/events", true);
  const approvals = useResource(api, "/approvals", true);
  const activeSchedules = list(schedules.data, "schedules")
    .filter((item) => item.status === "active")
    .sort((a, b) => {
      const aTime = timestamp(a.nextRunAt ?? a.next_run_at ?? a.runAt);
      const bTime = timestamp(b.nextRunAt ?? b.next_run_at ?? b.runAt);
      return (Number.isNaN(aTime) ? Infinity : aTime) - (Number.isNaN(bTime) ? Infinity : bTime);
    });
  const today = new Date();
  const todayEvents = list(events.data, "events")
    .filter((item) => new Date(timestamp(item.createdAt ?? item.created_at)).toDateString() === today.toDateString())
    .sort((a, b) => timestamp(b.createdAt ?? b.created_at) - timestamp(a.createdAt ?? a.created_at));
  const pendingApprovals = list(approvals.data, "approvals")
    .filter((item) => ["pending", "pending_approval"].includes(txt(item.status)));

  return (
    <aside className="companion-panel" aria-label="Your assistant">
      <button type="button" className="companion-close" aria-label="Close assistant panel" onClick={onClose}>
        <Icon name="close" size={18} />
      </button>
      <div className="companion-profile">
        <div className="companion-avatar" aria-hidden="true"><Icon name="sparkles" size={48} /></div>
        <h2>Personal assistant</h2>
        <p className={`companion-connection ${runtime.local === true ? "connected" : ""}`}>
          <span className="companion-connection-dot" aria-hidden="true" />
          {runtime.local === true ? "Workspace connected" : runtime.unavailable ? "Workspace unavailable" : "Connecting to workspace…"}
        </p>
      </div>
      <div className="companion-tabs" role="group" aria-label="Assistant information">
        {(["Activity", "Approvals", "Identity"] as const).map((name) => (
          <button key={name} type="button" className={`companion-tab ${tab === name ? "active" : ""}`} aria-pressed={tab === name} onClick={() => setTab(name)}>{name}</button>
        ))}
      </div>
      <div className="companion-content">
        {tab === "Activity" && <>
          <section className="companion-section" aria-labelledby="companion-schedules-title">
            <div className="companion-section-heading"><h3 id="companion-schedules-title">Scheduled tasks</h3>{!schedules.loading && !schedules.error && <span className="companion-count">{activeSchedules.length} active</span>}</div>
            {schedules.loading ? <p className="companion-empty" role="status">Loading schedules…</p> : schedules.error ? <p className="companion-error" role="alert">{schedules.error}</p> : activeSchedules.length === 0 ? <p className="companion-empty">No active schedules yet.</p> : <div className="companion-list">
              {activeSchedules.slice(0, 3).map((item, index) => <div className="companion-row" key={txt(item.id) || index}>
                <span className="companion-row-icon"><Icon name="calendar" size={17} /></span>
                <div className="companion-row-copy"><p>{txt(item.prompt) || "Scheduled task"}</p><span className="companion-muted">{shortTime(item.nextRunAt ?? item.next_run_at ?? item.runAt, true)}</span></div>
              </div>)}
            </div>}
            <button className="companion-view-all" type="button" onClick={() => onNavigate("Schedules")}>View all schedules <Icon name="arrow-right" size={14} /></button>
          </section>
          <section className="companion-section" aria-labelledby="companion-activity-title">
            <div className="companion-section-heading"><h3 id="companion-activity-title">Today's activities</h3></div>
            {events.loading ? <p className="companion-empty" role="status">Loading activity…</p> : events.error ? <p className="companion-error" role="alert">{events.error}</p> : todayEvents.length === 0 ? <p className="companion-empty">No activity recorded today.</p> : <div className="companion-list">
              {todayEvents.slice(0, 8).map((item, index) => {
                const state = eventState(item);
                return <div className={`companion-row companion-event ${state}`} key={txt(item.id) || index}>
                  <span className={`companion-row-icon ${state}`} aria-label={state === "completed" ? "Completed" : state === "failed" ? "Needs attention" : "Recorded"}><Icon name={state === "completed" ? "check" : state === "failed" ? "alert" : "clock"} size={16} /></span>
                  <div className="companion-row-copy"><p>{eventTitle(item)}</p><span className="companion-muted">{shortTime(item.createdAt ?? item.created_at)}</span></div>
                </div>;
              })}
            </div>}
            <button className="companion-view-all" type="button" onClick={() => onNavigate("Activity")}>View all activity <Icon name="arrow-right" size={14} /></button>
          </section>
        </>}
        {tab === "Approvals" && <section className="companion-section" aria-labelledby="companion-approvals-title">
          <div className="companion-section-heading"><h3 id="companion-approvals-title">Waiting for your review</h3>{!approvals.loading && !approvals.error && <span className="companion-count">{pendingApprovals.length}</span>}</div>
          {approvals.loading ? <p className="companion-empty" role="status">Loading approvals…</p> : approvals.error ? <p className="companion-error" role="alert">{approvals.error}</p> : pendingApprovals.length === 0 ? <p className="companion-empty">Nothing waiting on you. Proposed actions will appear here.</p> : <div className="companion-list">
            {pendingApprovals.slice(0, 5).map((item, index) => <div className="companion-row" key={txt(item.id) || index}>
              <span className="companion-row-icon"><Icon name="shield" size={17} /></span>
              <div className="companion-row-copy"><p>{txt(item.summary) || txt(item.kind) || "Proposed action"}</p><button type="button" className="companion-review" onClick={() => onNavigate("Approvals")}>Review action</button></div>
            </div>)}
          </div>}
          <button className="companion-view-all" type="button" onClick={() => onNavigate("Approvals")}>View all approvals <Icon name="arrow-right" size={14} /></button>
        </section>}
        {tab === "Identity" && <section className="companion-section companion-identity" aria-labelledby="companion-identity-title">
          <div className="companion-section-heading"><h3 id="companion-identity-title">Your workspace</h3></div>
          <dl className="companion-details">
            <div><dt>Model</dt><dd>{txt(runtime.model) || "Not reported"}</dd></div>
            <div><dt>Backend</dt><dd>{txt(runtime.backend) || "Not reported"}</dd></div>
            <div><dt>Runtime</dt><dd>{runtime.local === true ? "Local workspace" : "Not connected"}</dd></div>
          </dl>
          <p className="companion-muted">Model and service access depend on your workspace configuration.</p>
          <button className="companion-view-all" type="button" onClick={() => onNavigate("Connections")}>Manage connections <Icon name="arrow-right" size={14} /></button>
        </section>}
      </div>
    </aside>
  );
}
