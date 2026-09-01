"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

type View = "overview" | "intake" | "queue" | "flow";
type FlowMode = "current" | "future";
type TimeRange = "7d" | "30d" | "90d" | "all";
type QueueSort = "updated_desc" | "updated_asc";

type UIState = {
  schema_version: 1;
  active_view: View;
  time_range: TimeRange;
  filters: { queue: "all" | "attention"; flow: FlowMode };
  sort: { queue: QueueSort };
  scroll_position: number;
  current_project_id: string | null;
  revision: number;
};

type ProjectIntake = {
  id: string;
  title: string;
  sponsor: string;
  decision_owner: string;
  affected_user: string;
  desired_outcome: string;
  documents: Record<DocumentKey, boolean>;
  status: "draft" | "ready";
  revision: number;
};

/** One parked step waiting on a person. Mirrors GET /v1/decisions exactly. */
type Decision = {
  run_id: string;
  step: string;
  status: string;
  owner: string;
  action: string;
  risk: "green" | "yellow" | "red";
  goal: string;
  workflow_id: string;
  reason: string;
  impact: string;
  actionable: boolean;
  unblocks: number;
  depth: number;
  waiting_since: string;
  held_reason: string;
  context: Array<{ step: string; excerpt: string }>;
  brief: string;
};

type DocumentKey = "problem_statement" | "charter" | "sop" | "control_plan" | "uat" | "release_checklist";
const documentKeys: DocumentKey[] = ["problem_statement", "charter", "sop", "control_plan", "uat", "release_checklist"];

class RuntimeError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function runtimeRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/runtime${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  const payload = await response.json() as T & { error?: { message?: string } };
  if (!response.ok) throw new RuntimeError(response.status, payload.error?.message ?? "Runtime request failed");
  return payload;
}

const navItems: Array<{ id: View; label: string; index: string }> = [
  { id: "overview", label: "Overview", index: "01" },
  { id: "intake", label: "Project intake", index: "02" },
  { id: "queue", label: "Work & approvals", index: "03" },
  { id: "flow", label: "Value stream", index: "04" },
];

const intakeStages = [
  ["00", "Triage", "Request, sponsor, outcome"],
  ["01", "Clarify", "Evidence, scope, assumptions"],
  ["02", "Map", "SIPOC, value stream, journey"],
  ["03", "Specify", "Requirements, data contract"],
  ["04", "Control", "Risk, policy, rollback"],
  ["05", "Validate", "Tests, UAT, release gate"],
];

const blockers = [
  "Production identity binding and account-lifecycle evidence",
  "Live connector OAuth authorization and reconciliation evidence",
  "Human UAT, external review, go-live, and rollback approval",
];

const currentFlow = [
  ["1", "Free-form ask", "Outcome and authority can be implicit", "waste"],
  ["2", "Repeated clarification", "Questions split across documents", "waste"],
  ["3", "Maker prepares", "Ready gaps surface during work", "value"],
  ["4", "Checker reviews", "Logical identity only", "control"],
  ["5", "Human approves", "Queue visible only in files/CLI", "control"],
];

const futureFlow = [
  ["1", "Capture once", "Minimum intake validates immediately", "value"],
  ["2", "Route by reference", "One evidence log, typed questions", "value"],
  ["3", "Make from Ready", "Requirements and authority are explicit", "value"],
  ["4", "Independent check", "Immutable submission and bounded return", "control"],
  ["5", "Approve and measure", "Separate human gate, receipt, outcome", "control"],
];

function StatusPill({ children, tone }: { children: React.ReactNode; tone: string }) {
  return <span className={`status-pill status-${tone}`}>{children}</span>;
}

export default function ControlCenter({
  userName,
  isSignedIn,
}: {
  userName: string;
  isSignedIn: boolean;
}) {
  const [view, setView] = useState<View>("overview");
  const [flowMode, setFlowMode] = useState<FlowMode>("future");
  const [queueFilter, setQueueFilter] = useState<"all" | "attention">("all");
  const [timeRange, setTimeRange] = useState<TimeRange>("30d");
  const [queueSort, setQueueSort] = useState<QueueSort>("updated_desc");
  const [scrollPosition, setScrollPosition] = useState(0);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(null);
  const [projectRevision, setProjectRevision] = useState<number | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<"loading" | "saved" | "saving" | "error">("loading");
  const [notice, setNotice] = useState("");
  const [decisions, setDecisions] = useState<Decision[] | null>(null);
  const [decisionsError, setDecisionsError] = useState("");
  const [selectedDecision, setSelectedDecision] = useState<string | null>(null);
  const [decisionReason, setDecisionReason] = useState("");
  const [deciding, setDeciding] = useState(false);
  const uiRevision = useRef(0);
  const mainRef = useRef<HTMLElement>(null);
  const initialView = useRef(true);
  const [brief, setBrief] = useState({
    title: "",
    sponsor: "",
    owner: "",
    user: "",
    outcome: "",
  });
  const [documents, setDocuments] = useState<Record<DocumentKey, boolean>>({
    problem_statement: false,
    charter: false,
    sop: false,
    control_plan: false,
    uat: false,
    release_checklist: false,
  });

  const completion = useMemo(() => {
    const filled = Object.values(brief).filter((value) => value.trim()).length;
    return Math.round((filled / Object.keys(brief).length) * 100);
  }, [brief]);

  useEffect(() => {
    if (initialView.current) {
      initialView.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [view]);

  useEffect(() => {
    if (!isSignedIn) return;
    let active = true;
    (async () => {
      try {
        const state = await runtimeRequest<UIState>("/v1/ui-state");
        if (!active) return;
        uiRevision.current = state.revision;
        setView(state.active_view);
        setTimeRange(state.time_range);
        setQueueFilter(state.filters.queue);
        setFlowMode(state.filters.flow);
        setQueueSort(state.sort.queue);
        setScrollPosition(state.scroll_position);
        setCurrentProjectId(state.current_project_id);
        if (state.current_project_id) {
          const project = await runtimeRequest<ProjectIntake>(`/v1/projects/${state.current_project_id}`);
          if (!active) return;
          setBrief({ title: project.title, sponsor: project.sponsor, owner: project.decision_owner,
            user: project.affected_user, outcome: project.desired_outcome });
          setDocuments(project.documents);
          setProjectRevision(project.revision);
        }
        setRuntimeStatus("saved");
        setHydrated(true);
        requestAnimationFrame(() => window.scrollTo({ top: state.scroll_position }));
      } catch (error) {
        if (!active) return;
        setRuntimeStatus("error");
        setHydrated(true);
        setNotice(error instanceof Error ? error.message : "The governed runtime is unavailable");
      }
    })();
    return () => { active = false; };
  }, [isSignedIn]);

  useEffect(() => {
    let frame = 0;
    const observeScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setScrollPosition(Math.round(window.scrollY)));
    };
    window.addEventListener("scroll", observeScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", observeScroll);
    };
  }, []);

  useEffect(() => {
    if (!hydrated || !isSignedIn) return;
    const timer = window.setTimeout(async () => {
      setRuntimeStatus("saving");
      try {
        const saved = await runtimeRequest<UIState>("/v1/ui-state", {
          method: "PUT",
          body: JSON.stringify({ schema_version: 1, active_view: view, time_range: timeRange,
            filters: { queue: queueFilter, flow: flowMode }, sort: { queue: queueSort },
            scroll_position: scrollPosition, current_project_id: currentProjectId,
            revision: uiRevision.current }),
        });
        uiRevision.current = saved.revision;
        setRuntimeStatus("saved");
      } catch (error) {
        setRuntimeStatus("error");
        setNotice(error instanceof RuntimeError && error.status === 409
          ? "Your view changed in another session. Reload before continuing."
          : error instanceof Error ? error.message : "View preferences could not be saved");
      }
    }, 600);
    return () => window.clearTimeout(timer);
  }, [hydrated, isSignedIn, view, timeRange, queueFilter, flowMode, queueSort, scrollPosition, currentProjectId]);

  function changeView(next: View) {
    setView(next);
    setNotice("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function saveProject(event: FormEvent) {
    event.preventDefault();
    setRuntimeStatus("saving");
    const body = { title: brief.title, sponsor: brief.sponsor, decision_owner: brief.owner,
      affected_user: brief.user, desired_outcome: brief.outcome, documents,
      status: documentKeys.every((key) => documents[key]) ? "ready" as const : "draft" as const };
    try {
      const project = currentProjectId && projectRevision
        ? await runtimeRequest<ProjectIntake>(`/v1/projects/${currentProjectId}`, {
          method: "PUT", body: JSON.stringify({ ...body, revision: projectRevision }),
        })
        : await runtimeRequest<ProjectIntake>("/v1/projects", { method: "POST", body: JSON.stringify(body) });
      setCurrentProjectId(project.id);
      setProjectRevision(project.revision);
      setRuntimeStatus("saved");
      setNotice(`Project intake ${project.status === "ready" ? "is Ready" : "was saved as a draft"}. Revision ${project.revision}.`);
    } catch (error) {
      setRuntimeStatus("error");
      setNotice(error instanceof RuntimeError && error.status === 409
        ? "This intake changed in another session. Reload before overwriting it."
        : error instanceof Error ? error.message : "Project intake could not be saved");
    }
  }

  async function resetWorkspace() {
    try {
      const state = await runtimeRequest<UIState>("/v1/ui-state", { method: "DELETE" });
      uiRevision.current = state.revision;
      setView(state.active_view);
      setTimeRange(state.time_range);
      setQueueFilter(state.filters.queue);
      setFlowMode(state.filters.flow);
      setQueueSort(state.sort.queue);
      setCurrentProjectId(null);
      setScrollPosition(0);
      window.scrollTo({ top: 0 });
      setRuntimeStatus("saved");
      setNotice("Workspace preferences were reset to governed defaults. The project record was retained.");
    } catch (error) {
      setRuntimeStatus("error");
      setNotice(error instanceof Error ? error.message : "Workspace preferences could not be reset");
    }
  }

  const visibleDecisions = useMemo(() => {
    const queue = (decisions ?? []).filter((item) => queueFilter === "all" || item.actionable);
    // The runtime already returns them worst-first; oldest/newest is the operator's choice.
    return queueSort === "updated_asc"
      ? [...queue].sort((a, b) => a.waiting_since.localeCompare(b.waiting_since))
      : [...queue].sort((a, b) => b.waiting_since.localeCompare(a.waiting_since));
  }, [decisions, queueFilter, queueSort]);

  const chosenDecision = useMemo(
    () => visibleDecisions.find((item) => `${item.run_id}/${item.step}` === selectedDecision) ?? null,
    [visibleDecisions, selectedDecision],
  );

  const loadDecisions = useCallback(async () => {
    // Nothing is set before the first await on purpose: an effect must not change state
    // synchronously, or React re-renders before the fetch has said anything.
    try {
      const queue = await runtimeRequest<Decision[]>("/v1/decisions");
      setDecisions(queue);
      setDecisionsError("");
      setSelectedDecision((current) => {
        const stillThere = queue.some((d) => `${d.run_id}/${d.step}` === current);
        return stillThere ? current : (queue[0] ? `${queue[0].run_id}/${queue[0].step}` : null);
      });
    } catch (error) {
      setDecisions([]);
      setDecisionsError(error instanceof Error ? error.message : "The decision queue could not be read");
    }
  }, []);

  // The queue is only fetched when it is on screen, and again after every decision, so
  // what is shown is what the runtime currently says -- never a cached guess.
  useEffect(() => {
    if (!hydrated || !isSignedIn || (view !== "queue" && view !== "overview")) return;
    let active = true;
    (async () => {
      if (!active) return;
      await loadDecisions();
    })();
    return () => { active = false; };
  }, [hydrated, isSignedIn, view, loadDecisions]);

  async function decide(decision: Decision, verdict: "approve" | "reject") {
    const reason = decisionReason.trim();
    if (!reason) {
      setNotice("Say why. Every decision is recorded against your name and needs a reason.");
      return;
    }
    setDeciding(true);
    try {
      const result = await runtimeRequest<{ status: string }>(
        `/v1/decisions/${decision.run_id}/${decision.step}`,
        { method: "POST", body: JSON.stringify({ decision: verdict, reason }) },
      );
      setNotice(`${decision.step} is now ${result.status.replace(/_/g, " ")}. Recorded against your name in the audit log.`);
      setDecisionReason("");
      await loadDecisions();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The decision could not be recorded");
    } finally {
      setDeciding(false);
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <aside className="side-nav" aria-label="Primary navigation">
        <button type="button" className="brand" onClick={() => changeView("overview")} aria-label="MyOrg home">
          <span className="brand-mark">M</span>
          <span>
            <strong>MYORG</strong>
            <small>CONTROL CENTER</small>
          </span>
        </button>

        <nav className="nav-list">
          {navItems.map((item) => (
            <button
              type="button"
              key={item.id}
              className={view === item.id ? "nav-item active" : "nav-item"}
              onClick={() => changeView(item.id)}
              aria-current={view === item.id ? "page" : undefined}
            >
              <span>{item.index}</span>
              {item.label}
            </button>
          ))}
        </nav>

        <div className="side-status">
          <span className={`signal ${runtimeStatus === "error" ? "signal-red" : "signal-green"}`} />
          <div>
            <small>OPERATING MODE</small>
            <strong>{runtimeStatus === "error" ? "Runtime unavailable" : "Governed · durable"}</strong>
          </div>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="mobile-brand">
            <span className="brand-mark">M</span>
            <strong>MYORG</strong>
          </div>
          <div className="breadcrumb">ORG / {navItems.find((item) => item.id === view)?.label}</div>
          <div className="operator">
            <label className="compact-control">
              <span>Range</span>
              <select aria-label="Reporting time range" value={timeRange} onChange={(event) => setTimeRange(event.target.value as TimeRange)}>
                <option value="7d">7 days</option><option value="30d">30 days</option>
                <option value="90d">90 days</option><option value="all">All</option>
              </select>
            </label>
            <button type="button" className="reset-action" onClick={resetWorkspace}>Reset view</button>
            <span className="avatar">{userName.slice(0, 1).toUpperCase()}</span>
            <span>
              <strong>{userName}</strong>
              <small>{runtimeStatus === "saving" ? "Saving…" : runtimeStatus === "saved" ? "Identity bound · saved" : "Runtime check required"}</small>
            </span>
          </div>
        </header>

        <nav className="mobile-nav" aria-label="Mobile navigation">
          {navItems.map((item) => (
            <button
              type="button"
              key={item.id}
              onClick={() => changeView(item.id)}
              aria-current={view === item.id ? "page" : undefined}
              className={view === item.id ? "active" : ""}
            >
              {item.label}
            </button>
          ))}
        </nav>

        {notice && (
          <div className="notice" role="status">
            <strong>Governed runtime</strong>
            <span>{notice}</span>
            <button type="button" onClick={() => setNotice("")} aria-label="Dismiss notification">×</button>
          </div>
        )}

        <main className="workspace" id="main-content" ref={mainRef} tabIndex={-1}>
          {view === "overview" && (
            <>
              <section className="hero-row">
                <div>
                  <p className="eyebrow">OPERATIONS / RELEASE CONTROL</p>
                  <h1>Frame the work.<br />Protect the decision.</h1>
                  <p className="hero-copy">
                    One governed path from a customer need to checked evidence and a deliberate
                    human release decision.
                  </p>
                </div>
                <button type="button" className="primary-action" onClick={() => changeView("intake")}>
                  Begin project intake <span>→</span>
                </button>
              </section>

              <section className="metric-grid" aria-label="Operating summary">
                <article className="metric-card metric-dark">
                  <div className="metric-top"><span>RELEASE GATE</span><StatusPill tone="blocked">Blocked</StatusPill></div>
                  <strong>3</strong>
                  <p>critical foundations still require evidence</p>
                </article>
                <article className="metric-card">
                  <div className="metric-top"><span>GOVERNED CAPABILITIES</span><span className="metric-arrow">↗</span></div>
                  <strong>3</strong>
                  <p>intake · controlled work · visibility</p>
                </article>
                <article className="metric-card">
                  <div className="metric-top"><span>APPROVAL QUEUE</span><span className="metric-arrow">→</span></div>
                  <strong>1</strong>
                  <p>release decision waiting in validation run</p>
                </article>
                <article className="metric-card">
                  <div className="metric-top"><span>LIVE CONNECTORS</span><span className="metric-arrow">—</span></div>
                  <strong>0</strong>
                  <p>advisory mode until human authorization</p>
                </article>
              </section>

              <section className="content-grid">
                <article className="panel work-panel">
                  <div className="panel-heading">
                    <div>
                      <p className="eyebrow">WAITING ON YOU</p>
                      <h2>{decisions === null ? "Reading…" : decisions.length === 0 ? "Nothing needs you" : `${decisions.length} decision${decisions.length === 1 ? "" : "s"}`}</h2>
                    </div>
                    <button type="button" className="text-action" onClick={() => changeView("queue")}>Open queue →</button>
                  </div>
                  <div className="run-meta">
                    <span>{(decisions ?? []).filter((item) => item.actionable).length} you can decide</span>
                    <span>{(decisions ?? []).filter((item) => !item.actionable).length} handed back</span>
                    <span>{(decisions ?? []).reduce((total, item) => total + item.unblocks, 0)} step(s) blocked</span>
                  </div>
                  <div className="step-track">
                    {(decisions ?? []).slice(0, 3).map((item, index) => (
                      <div className="step-item" key={`${item.run_id}/${item.step}`}>
                        <div className={`step-dot ${item.risk === "red" ? "stop" : "wait"}`}>{index + 1}</div>
                        <div><strong>{item.action} — {item.step}</strong><small>{item.owner} · {item.run_id}</small></div>
                        <StatusPill tone={item.risk === "red" ? "stop" : "wait"}>{item.risk === "red" ? "Handed back" : "Awaiting you"}</StatusPill>
                      </div>
                    ))}
                    {decisions !== null && decisions.length === 0 && (
                      <p className="safety-copy">Work that reaches a yellow or red gate will show up here.</p>
                    )}
                  </div>
                </article>

                <aside className="panel gate-panel">
                  <div className="panel-heading compact">
                    <div><p className="eyebrow">WHY RELEASE IS BLOCKED</p><h2>Evidence before confidence</h2></div>
                  </div>
                  <ul className="blocker-list">
                    {blockers.map((blocker, index) => (
                      <li key={blocker}><span>{String(index + 1).padStart(2, "0")}</span>{blocker}</li>
                    ))}
                  </ul>
                  <button type="button" className="secondary-action" onClick={() => changeView("flow")}>Inspect future-state flow</button>
                </aside>
              </section>
            </>
          )}

          {view === "intake" && (
            <>
              <section className="page-heading">
                <div><p className="eyebrow">CAPABILITY 01 / GOVERNED INTAKE</p><h1>Make the work ready<br />before making the work.</h1></div>
                <div className="readiness-ring" style={{ "--progress": `${completion * 3.6}deg` } as React.CSSProperties}>
                  <span><strong>{completion}%</strong><small>minimum brief</small></span>
                </div>
              </section>

              <section className="intake-layout">
                <div className="stage-rail" aria-label="Intake stages">
                  {intakeStages.map(([number, label, detail], index) => (
                    <div className={index === 0 ? "stage active" : "stage"} key={number}>
                      <span>{number}</span><div><strong>{label}</strong><small>{detail}</small></div>
                    </div>
                  ))}
                </div>

                <form className="panel intake-form" onSubmit={saveProject}>
                  <div className="panel-heading">
                    <div><p className="eyebrow">00 / INTAKE BRIEF</p><h2>Minimum decision context</h2></div>
                    <StatusPill tone={documentKeys.every((key) => documents[key]) ? "done" : "wait"}>
                      {documentKeys.every((key) => documents[key]) ? "Ready" : "Draft"}
                    </StatusPill>
                  </div>
                  <p className="form-intro">All five fields are required before clarification. Unknown means stop and assign an owner—it never means guess.</p>
                  <div className="field-grid">
                    {[
                      ["title", "Working title", "e.g. Customer renewal risk review"],
                      ["sponsor", "Sponsor", "Accountable business sponsor"],
                      ["owner", "Decision owner", "Human who can approve scope"],
                      ["user", "Affected user/customer", "Whose behavior or outcome changes"],
                    ].map(([key, label, placeholder]) => (
                      <label key={key}>
                        <span>{label}</span>
                        <input
                          value={brief[key as keyof typeof brief]}
                          required
                          maxLength={160}
                          onChange={(event) => setBrief({ ...brief, [key]: event.target.value })}
                          placeholder={placeholder}
                        />
                      </label>
                    ))}
                    <label className="field-wide">
                      <span>Desired outcome / behavior</span>
                      <textarea
                        value={brief.outcome}
                        required
                        maxLength={1000}
                        onChange={(event) => setBrief({ ...brief, outcome: event.target.value })}
                        placeholder="Describe the observable result—not the solution or activity."
                        rows={4}
                      />
                    </label>
                  </div>
                  <div className="scope-note"><strong>Scope control</strong><span>Maximum three MVP capabilities. External writes require a separate human gate.</span></div>
                  <div className="form-actions">
                    <span>{Object.values(brief).filter(Boolean).length} / 5 minimum fields complete</span>
                    <button className="primary-action" type="submit">
                      {currentProjectId ? "Update intake" : "Create intake"} <span>→</span>
                    </button>
                  </div>
                </form>

                <aside className="panel document-panel">
                  <div className="panel-heading compact"><div><p className="eyebrow">REQUIRED EVIDENCE</p><h2>Six-document pack</h2></div></div>
                  <div className="document-list">
                    {intakeStages.map(([number, label], index) => {
                      const key = documentKeys[index];
                      return (
                        <label key={number} className="document-row">
                          <input
                            type="checkbox"
                            checked={documents[key]}
                            onChange={() => setDocuments({ ...documents, [key]: !documents[key] })}
                          />
                          <span className="fake-check">✓</span>
                          <span><strong>{number} · {label}</strong><small>{index === 5 ? "Release evidence" : "Required before Ready"}</small></span>
                        </label>
                      );
                    })}
                  </div>
                  <p className="panel-footnote">The brief and checklist are stored in the organization-scoped runtime. Ready requires all six evidence controls.</p>
                </aside>
              </section>
            </>
          )}

          {view === "queue" && (
            <>
              <section className="page-heading queue-heading">
                <div><p className="eyebrow">CAPABILITY 02 / CONTROLLED WORK</p><h1>One queue.<br />Two distinct decisions.</h1></div>
                <div className="segmented" aria-label="Queue filter">
                  <button type="button" className={queueFilter === "all" ? "active" : ""} onClick={() => setQueueFilter("all")} aria-pressed={queueFilter === "all"}>All work</button>
                  <button type="button" className={queueFilter === "attention" ? "active" : ""} onClick={() => setQueueFilter("attention")} aria-pressed={queueFilter === "attention"}>Needs attention</button>
                </div>
                <label className="sort-control">
                  <span>Sort</span>
                  <select value={queueSort} onChange={(event) => setQueueSort(event.target.value as QueueSort)}>
                    <option value="updated_desc">Newest first</option><option value="updated_asc">Oldest first</option>
                  </select>
                </label>
              </section>

              <section className="queue-layout">
                {decisions === null && <article className="panel run-detail"><p>Reading the queue…</p></article>}

                {decisions !== null && decisionsError && (
                  <article className="panel run-detail">
                    <h2>The queue could not be read</h2>
                    <p>{decisionsError}</p>
                    <div className="decision-actions">
                      <button type="button" className="secondary-action" onClick={() => void loadDecisions()}>Try again</button>
                    </div>
                  </article>
                )}

                {decisions !== null && !decisionsError && visibleDecisions.length === 0 && (
                  <article className="panel run-detail">
                    <div className="run-title-row"><div><span className="mono-label">NOTHING WAITING</span><h2>Nothing needs you</h2></div></div>
                    <p>No run is parked at a gate right now. Work that reaches a yellow or red step will appear here.</p>
                    <div className="decision-actions">
                      <button type="button" className="secondary-action" onClick={() => void loadDecisions()}>Refresh</button>
                    </div>
                  </article>
                )}

                {decisions !== null && !decisionsError && visibleDecisions.length > 0 && (
                  <article className="panel run-detail">
                    <div className="run-title-row">
                      <div><span className="mono-label">{visibleDecisions.length} WAITING · WORST FIRST</span><h2>What needs a person</h2></div>
                      <button type="button" className="text-action" onClick={() => void loadDecisions()}>Refresh</button>
                    </div>
                    <div className="queue-steps">
                      {visibleDecisions.map((item) => {
                        const key = `${item.run_id}/${item.step}`;
                        return (
                          <button
                            type="button"
                            key={key}
                            className={`queue-step${selectedDecision === key ? " selected" : ""}`}
                            aria-pressed={selectedDecision === key}
                            onClick={() => { setSelectedDecision(key); setDecisionReason(""); }}
                          >
                            <div className={`step-dot ${item.risk === "red" ? "stop" : "wait"}`}>{item.risk === "red" ? "!" : "?"}</div>
                            <div className="queue-step-copy"><strong>{item.action} — {item.step}</strong><small>{item.owner} · {item.run_id}</small></div>
                            <div className="queue-step-evidence">
                              <span>{item.unblocks > 0 ? `${item.unblocks} step(s) blocked` : "nothing blocked"}</span>
                              <StatusPill tone={item.risk === "red" ? "stop" : "wait"}>{item.risk === "red" ? "Handed back" : "Awaiting you"}</StatusPill>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </article>
                )}

                {chosenDecision && (
                  <aside className="panel approval-card">
                    <div className="approval-stripe" />
                    <p className="eyebrow">HUMAN DECISION / {chosenDecision.risk.toUpperCase()}</p>
                    <h2>{chosenDecision.action} — {chosenDecision.step}</h2>
                    <p>{chosenDecision.reason}</p>
                    {chosenDecision.held_reason && <p className="safety-copy">{chosenDecision.held_reason}</p>}
                    <dl>
                      <div><dt>Goal</dt><dd>{chosenDecision.goal}</dd></div>
                      <div><dt>Run</dt><dd>{chosenDecision.run_id}</dd></div>
                      <div><dt>Owner</dt><dd>{chosenDecision.owner}</dd></div>
                      <div><dt>If you wait</dt><dd>{chosenDecision.impact}</dd></div>
                    </dl>
                    {chosenDecision.brief && <pre className="decision-brief">{chosenDecision.brief}</pre>}
                    {chosenDecision.context.length > 0 && (
                      <div className="exchange-trail">
                        <div className="section-label"><span>WHAT CAME BEFORE</span><span>from the run log</span></div>
                        {chosenDecision.context.map((item) => (
                          <div className="message-row" key={item.step}>
                            <span className="message-kind">{item.step}</span><p>{item.excerpt}</p>
                          </div>
                        ))}
                      </div>
                    )}
                    {chosenDecision.actionable ? (
                      <>
                        <label className="sort-control decision-reason">
                          <span>Why (recorded against your name)</span>
                          <input
                            type="text"
                            value={decisionReason}
                            maxLength={200}
                            placeholder="e.g. checked the copy, safe to publish"
                            onChange={(event) => setDecisionReason(event.target.value)}
                          />
                        </label>
                        <div className="decision-actions">
                          <button type="button" className="primary-action" disabled={deciding || !decisionReason.trim()} onClick={() => void decide(chosenDecision, "approve")}>
                            {deciding ? "Recording…" : "Approve"}
                          </button>
                          <button type="button" className="secondary-action" disabled={deciding || !decisionReason.trim()} onClick={() => void decide(chosenDecision, "reject")}>
                            Reject
                          </button>
                        </div>
                        <small className="safety-copy">Approving lets the agent carry on. Rejecting stops the run. Either way the runtime writes who decided, when, and why.</small>
                      </>
                    ) : (
                      <small className="safety-copy">This is a red action. It has been handed back to a person to perform outside the system — no code path here can approve it.</small>
                    )}
                  </aside>
                )}
              </section>
            </>
          )}

          {view === "flow" && (
            <>
              <section className="page-heading flow-heading">
                <div><p className="eyebrow">CAPABILITY 03 / FLOW VISIBILITY</p><h1>Remove the waiting.<br />Keep the controls.</h1></div>
                <div className="segmented" aria-label="Value stream mode">
                  <button type="button" className={flowMode === "current" ? "active" : ""} onClick={() => setFlowMode("current")} aria-pressed={flowMode === "current"}>Current state</button>
                  <button type="button" className={flowMode === "future" ? "active" : ""} onClick={() => setFlowMode("future")} aria-pressed={flowMode === "future"}>Future state</button>
                </div>
              </section>

              <section className="panel flow-panel">
                <div className="panel-heading"><div><p className="eyebrow">SIX SIGMA / VALUE STREAM</p><h2>{flowMode === "future" ? "Designed future state" : "Observed structural current state"}</h2></div><span className="baseline-note">TIMING BASELINE · UNKNOWN</span></div>
                <p className="flow-intro">Five representative manual intakes must establish touch time, wait time, first-pass yield, rework, handoffs, and approval wait before an improvement is claimed.</p>
                <div className="flow-track">
                  {(flowMode === "future" ? futureFlow : currentFlow).map(([number, title, detail, tone]) => (
                    <div className={`flow-step flow-${tone}`} key={number}>
                      <span className="flow-number">{number}</span><strong>{title}</strong><p>{detail}</p><small>{tone === "waste" ? "NVA · REMOVE" : tone === "control" ? "BVA · RETAIN" : "VA · PROTECT"}</small>
                    </div>
                  ))}
                </div>
                <div className="legend"><span><i className="legend-value" /> Value-added</span><span><i className="legend-control" /> Business control</span><span><i className="legend-waste" /> Non-value-added</span></div>
              </section>

              <section className="flow-lower-grid">
                <article className="panel exchange-map">
                  <div className="panel-heading compact"><div><p className="eyebrow">BIDIRECTIONAL CONTRACT</p><h2>Every write returns evidence</h2></div></div>
                  <div className="exchange-line"><span>Sponsor</span><b>request ⇄ question</b><span>Intake</span></div>
                  <div className="exchange-line"><span>Runtime</span><b>task ⇄ evidence</b><span>Maker</span></div>
                  <div className="exchange-line"><span>Maker</span><b>submission ⇄ feedback</b><span>Checker</span></div>
                  <div className="exchange-line"><span>Gateway</span><b>action ⇄ receipt</b><span>System</span></div>
                  <p className="panel-footnote">The connector line is a target contract, not an implemented capability. Restricted raw payloads stay outside the event stream.</p>
                </article>

                <article className="panel journey-panel">
                  <div className="panel-heading compact"><div><p className="eyebrow">CUSTOMER + OPERATOR JOURNEY</p><h2>Trust at every moment</h2></div></div>
                  {[
                    ["Ask", "Say it once", "Guided minimum intake"],
                    ["Align", "See facts vs assumptions", "Approved charter"],
                    ["Follow", "No status chasing", "One correlated work queue"],
                    ["Review", "Trust independence", "Maker check before human gate"],
                    ["Learn", "Prove the outcome", "Baseline-to-result evidence"],
                  ].map(([moment, need, future]) => (
                    <div className="journey-row" key={moment}><span>{moment}</span><strong>{need}</strong><p>{future}</p></div>
                  ))}
                </article>
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
