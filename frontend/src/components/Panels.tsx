import type { Agent, AgentEvent, Decision, LiveSignals, Metrics, ReasoningTrace } from "../types";
import { currency, number } from "../utils";

type MetricBoardProps = { metrics: Metrics };

export function MetricBoard({ metrics }: MetricBoardProps) {
  const cards = [
    {
      label: "Fill rate",
      value: `${Math.round(metrics.fill_rate * 100)}%`,
      detail: "Customer demand fulfilled",
      tone: metrics.fill_rate >= 0.9 ? "positive" : metrics.fill_rate >= 0.75 ? "neutral" : "warning",
    },
    {
      label: "Needs approval",
      value: number(metrics.pending_decisions),
      detail: "Current agent recommendations",
      tone: metrics.pending_decisions ? "neutral" : "positive",
    },
    {
      label: "Stockouts avoided",
      value: number(metrics.stockouts_avoided),
      detail: "Units protected by approved actions",
      tone: "positive",
    },
    {
      label: "Waste prevented",
      value: `${number(metrics.waste_reduced)} units`,
      detail: `${currency(metrics.estimated_profit_saved)} estimated value saved`,
      tone: "positive",
    },
  ];

  return (
    <section className="metric-board" aria-label="Network health summary">
      {cards.map((card) => (
        <article className={`metric-card ${card.tone}`} key={card.label}>
          <span>{card.label}</span>
          <strong>{card.value}</strong>
          <p>{card.detail}</p>
        </article>
      ))}
    </section>
  );
}

export function ComparisonPanel({ metrics }: MetricBoardProps) {
  const stockoutImprovement = Math.max(
    0,
    metrics.without_agents.projected_stockouts - metrics.with_agents.stockouts,
  );
  const wasteImprovement = Math.max(0, metrics.without_agents.projected_waste - metrics.with_agents.waste);

  return (
    <section className="card comparison-panel">
      <div className="section-heading compact">
        <div>
          <span className="section-kicker">Measured impact</span>
          <h2>With agents vs. baseline</h2>
        </div>
      </div>
      <div className="comparison-row">
        <div><span>Stockouts</span><strong>{number(metrics.with_agents.stockouts)}</strong></div>
        <span className="comparison-baseline">Baseline {number(metrics.without_agents.projected_stockouts)}</span>
        <span className="comparison-delta">{number(stockoutImprovement)} avoided</span>
      </div>
      <div className="comparison-row">
        <div><span>Waste</span><strong>{number(metrics.with_agents.waste)} units</strong></div>
        <span className="comparison-baseline">Baseline {number(metrics.without_agents.projected_waste)}</span>
        <span className="comparison-delta">{number(wasteImprovement)} saved</span>
      </div>
    </section>
  );
}

export function LiveSignalPanel({ signals }: { signals: LiveSignals }) {
  const reason = signals.reasons?.[0] || "No outside demand signal is affecting the forecast.";
  return (
    <section className="card signal-panel">
      <div className="signal-icon" aria-hidden="true">↗</div>
      <div>
        <span className="section-kicker">Live demand signal</span>
        <strong>{Number(signals.demand_multiplier || 1).toFixed(2)}× pressure</strong>
        <p>{reason}</p>
      </div>
    </section>
  );
}

export function AgentPanel({ agents }: { agents: Agent[] }) {
  return (
    <section className="card agent-panel">
      <div className="section-heading compact">
        <div>
          <span className="section-kicker">Decision workflow</span>
          <h2>How StockFlow reaches a recommendation</h2>
        </div>
        <span className="plain-status"><i /> {agents.length} focused agents</span>
      </div>
      <div className="agent-flow">
        {agents.map((agent, index) => (
          <article className="agent-step" key={agent.name}>
            <span className="step-number">{index + 1}</span>
            <div>
              <h3>{agent.name.replace(" Agent", "")}</h3>
              <p>{agent.role}</p>
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
  const visibleDecisions = decisions.slice(0, 3);
  return (
    <section className="card decision-panel">
      <div className="section-heading compact">
        <div>
          <span className="section-kicker">Human approval</span>
          <h2>Recommended actions</h2>
        </div>
        <span className="count-badge">{pendingCount}</span>
      </div>
      <p className="section-intro">Agents prepare the action. A manager makes the final decision.</p>
      <div className="decision-list">
        {!visibleDecisions.length ? (
          <div className="empty-state">
            <strong>No decisions waiting</strong>
            <span>Run the next day to evaluate the network.</span>
          </div>
        ) : (
          visibleDecisions.map((decision) => (
            <article className="decision-card" key={decision.id}>
              <div className="decision-summary">
                <span className={`decision-type ${decision.decision_type}`}>{labelForDecision(decision.decision_type)}</span>
                <strong>{number(decision.quantity)} units</strong>
              </div>
              <h3>{decision.item_name || `Item ${decision.item_id}`}</h3>
              <p className="decision-location">
                {decision.store_name || `Store ${decision.store_id}`}
                {decision.target_store_name ? ` → ${decision.target_store_name}` : ""}
              </p>
              <p className="decision-reason">{decision.reason}</p>
              <div className="decision-actions">
                <button className="approve" onClick={() => onDecision(decision.id, "approve")}>Approve</button>
                <button className="reject" onClick={() => onDecision(decision.id, "reject")}>Reject</button>
              </div>
            </article>
          ))
        )}
      </div>
      {pendingCount > visibleDecisions.length ? (
        <p className="queue-note">Showing the 3 newest of {pendingCount} recommendations.</p>
      ) : null}
    </section>
  );
}

export function TimelinePanel({
  events,
  traces,
  fillRate,
}: {
  events: AgentEvent[];
  traces: ReasoningTrace[];
  fillRate: number;
}) {
  const activity = [
    ...events.slice(0, 5).map((event) => ({
      key: `event-${event.id}`,
      agent: event.agent_name,
      label: event.event_type.replaceAll("_", " "),
      message: event.message,
      severity: event.severity,
    })),
    ...traces.slice(0, 3).map((trace) => ({
      key: `trace-${trace.id}`,
      agent: trace.agent_name,
      label: trace.tool_name.replaceAll("_", " "),
      message: trace.observation,
      severity: "trace",
    })),
  ];

  return (
    <details className="card activity-panel">
      <summary>
        <div>
          <span className="section-kicker">Audit trail</span>
          <strong>Agent activity and reasoning</strong>
        </div>
        <span>{activity.length} recent events · {Math.round(fillRate * 100)}% fill rate</span>
      </summary>
      <div className="activity-list">
        {activity.map((item) => (
          <article className="activity-item" key={item.key}>
            <i className={item.severity} />
            <div>
              <div><strong>{item.agent}</strong><span>{item.label}</span></div>
              <p>{item.message}</p>
            </div>
          </article>
        ))}
      </div>
    </details>
  );
}

function labelForDecision(type: Decision["decision_type"]): string {
  return {
    order: "Order",
    transfer: "Transfer",
    markdown: "Markdown",
    donation: "Donate",
  }[type];
}
