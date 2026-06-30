import type { Agent, AgentEvent, Decision, LiveSignals, Metrics, ReasoningTrace } from "../types";
import { currency, number } from "../utils";

type MetricBoardProps = {
  metrics: Metrics;
};

export function MetricBoard({ metrics }: MetricBoardProps) {
  return (
    <section className="metric-board">
      <div>
        <span className="metric-label">Stockouts avoided</span>
        <strong>{number(metrics.stockouts_avoided)}</strong>
      </div>
      <div>
        <span className="metric-label">Waste reduced</span>
        <strong>{number(metrics.waste_reduced)} units</strong>
      </div>
      <div>
        <span className="metric-label">Transferred</span>
        <strong>{number(metrics.units_transferred)} units</strong>
      </div>
      <div>
        <span className="metric-label">Profit saved</span>
        <strong>{currency(metrics.estimated_profit_saved)}</strong>
      </div>
    </section>
  );
}

export function ComparisonPanel({ metrics }: MetricBoardProps) {
  return (
    <section className="comparison-panel">
      <h2>Without Agents vs With Agents</h2>
      <div className="compare-grid">
        <div>
          <span>Projected stockouts</span>
          <strong>{number(metrics.without_agents.projected_stockouts)}</strong>
        </div>
        <div>
          <span>Current stockouts</span>
          <strong>{number(metrics.with_agents.stockouts)}</strong>
        </div>
        <div>
          <span>Projected waste</span>
          <strong>{number(metrics.without_agents.projected_waste)} units</strong>
        </div>
        <div>
          <span>Current waste</span>
          <strong>{number(metrics.with_agents.waste)} units</strong>
        </div>
      </div>
    </section>
  );
}

export function LiveSignalPanel({ signals }: { signals: LiveSignals }) {
  const reasons = signals.reasons?.length ? signals.reasons : ["No live signals loaded yet."];

  return (
    <section className="live-signal-panel">
      <div className="panel-title-row">
        <h2>Free Live Signals</h2>
        <span>{signals.status || "unknown"}</span>
      </div>
      <strong>{Number(signals.demand_multiplier || 1).toFixed(2)}x demand pressure</strong>
      <div className="live-signal-reasons">
        {reasons.slice(0, 3).map((reason) => (
          <span key={reason}>{reason}</span>
        ))}
      </div>
    </section>
  );
}

export function AgentPanel({ agents }: { agents: Agent[] }) {
  return (
    <section className="agent-panel">
      <h2>Agent Team</h2>
      <div className="agent-roster">
        {agents.map((agent) => (
          <article className="agent-card" key={agent.name}>
            <div className="agent-pulse" />
            <div>
              <h3>{agent.name}</h3>
              <p>{agent.role}</p>
              <span>{agent.status}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

type DecisionPanelProps = {
  decisions: Decision[];
  pendingCount: number;
  onDecision: (id: number, action: "approve" | "reject") => void;
};

export function DecisionPanel({ decisions, pendingCount, onDecision }: DecisionPanelProps) {
  return (
    <section className="decision-panel">
      <div className="panel-title-row">
        <h2>Manager Decisions</h2>
        <span>{pendingCount} pending</span>
      </div>
      <div className="decision-list">
        {!decisions.length ? (
          <div className="empty-state">No pending decisions. Run a day or load a scenario.</div>
        ) : (
          decisions.map((decision) => {
            const target = decision.target_store_name ? ` -> ${decision.target_store_name}` : "";
            return (
              <article className={`decision-card ${decision.decision_type}`} key={decision.id}>
                <div className="decision-head">
                  <span>{decision.decision_type}</span>
                  <strong>{number(decision.quantity)} units</strong>
                </div>
                <h3>{decision.item_name || `Item ${decision.item_id}`}</h3>
                <p className="decision-store">
                  {decision.store_name || `Store ${decision.store_id}`}
                  {target}
                </p>
                <p>{decision.reason}</p>
                <p className="impact">{decision.expected_impact}</p>
                <div className="decision-actions">
                  <button className="approve" onClick={() => onDecision(decision.id, "approve")}>
                    Approve
                  </button>
                  <button className="reject" onClick={() => onDecision(decision.id, "reject")}>
                    Reject
                  </button>
                </div>
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}

export function TimelinePanel({
  events,
  traces,
  fillRate,
  error,
}: {
  events: AgentEvent[];
  traces: ReasoningTrace[];
  fillRate: number;
  error?: string | null;
}) {
  const traceCards = traces.slice(0, 10).map((trace) => ({
    key: `trace-${trace.id}`,
    kind: "trace",
    severity: "trace",
    agentName: trace.agent_name,
    label: `tool: ${trace.tool_name}`,
    message: `${trace.observation} Decision: ${trace.decision}`,
    input: trace.input_summary,
  }));
  const eventCards = events.slice(0, 14).map((event) => ({
    key: `event-${event.id}`,
    kind: "event",
    severity: event.severity,
    agentName: event.agent_name,
    label: event.event_type.replaceAll("_", " "),
    message: event.message,
    input: "",
  }));
  const cards = [...traceCards, ...eventCards].slice(0, 20);

  return (
    <section className="timeline-panel">
      <div className="panel-title-row">
        <h2>LangGraph Agent Timeline + Tool Traces</h2>
        <span>Fill rate {Math.round(fillRate * 100)}%</span>
      </div>
      <div className="event-feed">
        {error ? <EventCard severity="critical" agentName="Demo Error" label="request failed" message={error} /> : null}
        {!cards.length && !error ? (
          <div className="empty-state">LangGraph tool traces and agent events appear here as the simulation runs.</div>
        ) : (
          cards.map((card) => (
            <EventCard
              key={card.key}
              severity={card.severity}
              agentName={card.agentName}
              label={card.label}
              message={card.message}
              input={card.input}
            />
          ))
        )}
      </div>
    </section>
  );
}

function EventCard({
  severity,
  agentName,
  label,
  message,
  input,
}: {
  severity: string;
  agentName: string;
  label: string;
  message: string;
  input?: string;
}) {
  return (
    <article className={`event-card ${severity}`}>
      <div>
        <strong>{agentName}</strong>
        <span>{label}</span>
      </div>
      {input ? <p className="trace-input">{input}</p> : null}
      <p>{message}</p>
    </article>
  );
}
