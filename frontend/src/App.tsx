import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { DemoState, ScenarioName } from "./types";
import { stockflowApi } from "./api";
import { useStockFlowUi } from "./store";
import { OperationsMap } from "./components/OperationsMap";
import {
  AgentPanel,
  ComparisonPanel,
  DecisionPanel,
  LiveSignalPanel,
  MetricBoard,
  TimelinePanel,
} from "./components/Panels";

const scenarios: Array<{ name: ScenarioName; label: string }> = [
  { name: "weekend-rush", label: "Weekend Rush" },
  { name: "game-day-spike", label: "Game Day" },
  { name: "delivery-delay", label: "Delivery Delay" },
  { name: "expiry-rescue", label: "Expiry Rescue" },
  { name: "store-to-store-transfer", label: "Transfer Rescue" },
];

const stateQueryKey = ["demo-state"];

export function App() {
  const queryClient = useQueryClient();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const autoplayTimer = useRef<number | null>(null);
  const lastLiveSequence = useRef<number | null>(null);
  const { activeScenario, isAutoplaying, liveStreamStatus, setActiveScenario, setAutoplaying, setLiveStreamStatus } =
    useStockFlowUi();

  const stateQuery = useQuery({
    queryKey: stateQueryKey,
    queryFn: stockflowApi.getState,
    refetchOnWindowFocus: false,
  });
  const state = stateQuery.data;

  useEffect(() => {
    if (state?.scenario?.name) {
      setActiveScenario(state.scenario.name);
    }
  }, [setActiveScenario, state?.scenario?.name]);

  const updateState = (nextState: DemoState) => {
    setErrorMessage(null);
    queryClient.setQueryData(stateQueryKey, nextState);
  };

  const runDayMutation = useMutation({
    mutationFn: stockflowApi.runDay,
    onSuccess: updateState,
    onError: (error: Error) => setErrorMessage(error.message),
  });
  const resetMutation = useMutation({
    mutationFn: stockflowApi.reset,
    onSuccess: (nextState) => {
      stopLocalAutoplay();
      updateState(nextState);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });
  const scenarioMutation = useMutation({
    mutationFn: stockflowApi.setScenario,
    onSuccess: updateState,
    onError: (error: Error) => setErrorMessage(error.message),
  });
  const decisionMutation = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "reject" }) => stockflowApi.decide(id, action),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: stateQueryKey }),
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const busy =
    stateQuery.isFetching ||
    runDayMutation.isPending ||
    resetMutation.isPending ||
    scenarioMutation.isPending ||
    decisionMutation.isPending;

  useEffect(() => {
    if (!window.EventSource) {
      setLiveStreamStatus("unsupported");
      return;
    }
    const source = new EventSource("/live/events");
    setLiveStreamStatus("connecting");
    source.addEventListener("open", () => setLiveStreamStatus("live"));
    source.addEventListener("error", () => setLiveStreamStatus("reconnecting"));
    source.addEventListener("stockflow-state", (event) => {
      setLiveStreamStatus("live");
      try {
        const payload = JSON.parse(event.data) as { sequence?: number };
        if (payload.sequence !== lastLiveSequence.current) {
          lastLiveSequence.current = payload.sequence ?? null;
          window.setTimeout(() => queryClient.invalidateQueries({ queryKey: stateQueryKey }), 250);
        }
      } catch (error) {
        console.error("Bad live event payload:", error);
      }
    });
    return () => source.close();
  }, [queryClient, setLiveStreamStatus]);

  useEffect(() => {
    if (!isAutoplaying) return;
    const speed = state?.simulation_speed_ms || 4500;
    autoplayTimer.current = window.setInterval(() => runDayMutation.mutate(), speed);
    return () => stopLocalAutoplay();
  }, [isAutoplaying, runDayMutation, state?.simulation_speed_ms]);

  const stopLocalAutoplay = () => {
    if (autoplayTimer.current) {
      window.clearInterval(autoplayTimer.current);
      autoplayTimer.current = null;
    }
    setAutoplaying(false);
  };

  const toggleAutoplay = async () => {
    if (isAutoplaying) {
      stopLocalAutoplay();
      await stockflowApi.stopAutoplay();
    } else {
      await stockflowApi.startAutoplay();
      setAutoplaying(true);
    }
    await queryClient.invalidateQueries({ queryKey: stateQueryKey });
  };

  if (!state) {
    return (
      <main className="loading-shell">
        <div className="loading-card">
          <span className="loading-mark">SF</span>
          <strong>{stateQuery.isError ? "StockFlow is unavailable" : "Loading StockFlow"}</strong>
          <p>{stateQuery.isError ? String(stateQuery.error.message) : "Connecting to the inventory network…"}</p>
        </div>
      </main>
    );
  }

  const criticalStores = state.restaurants.filter((store) => store.status === "critical").length;

  return (
    <div className="app-shell" aria-busy={busy}>
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark">SF</span>
          <div>
            <h1>StockFlow</h1>
            <p>Inventory decisions for every location</p>
          </div>
        </div>
        <div className="system-status">
          <span className={`status-dot ${liveStreamStatus}`} />
          <span>{liveStreamStatus === "live" ? "Live data" : liveStreamStatus}</span>
          <strong>Day {state.sim_day}</strong>
          <span>{state.sim_date}</span>
        </div>
      </header>

      <section className="control-bar" aria-label="Simulation controls">
        <div className="scenario-control">
          <label htmlFor="scenario">Scenario</label>
          <select
            id="scenario"
            value={activeScenario}
            onChange={(event) => scenarioMutation.mutate(event.target.value as ScenarioName)}
          >
            {scenarios.map((scenario) => (
              <option value={scenario.name} key={scenario.name}>
                {scenario.label}
              </option>
            ))}
          </select>
        </div>
        <div className="scenario-summary">
          <strong>{state.scenario.label}</strong>
          <span>{state.scenario.description}</span>
        </div>
        <div className="control-actions">
          <button className="button secondary" onClick={toggleAutoplay} disabled={busy}>
            {isAutoplaying ? "Pause" : "Auto play"}
          </button>
          <button className="button secondary" onClick={() => resetMutation.mutate()} disabled={busy}>
            Reset
          </button>
          <button className="button primary" onClick={() => runDayMutation.mutate()} disabled={busy}>
            {runDayMutation.isPending ? "Running…" : "Run next day"}
          </button>
        </div>
      </section>

      {errorMessage ? <div className="error-banner">{errorMessage}</div> : null}

      <MetricBoard metrics={state.metrics} />

      <main className="primary-grid">
        <section className="map-panel card">
          <div className="section-heading">
            <div>
              <span className="section-kicker">Network overview</span>
              <h2>Where attention is needed</h2>
            </div>
            <span className={`attention-badge ${criticalStores ? "warning" : "healthy"}`}>
              {criticalStores ? `${criticalStores} locations at risk` : "All locations stable"}
            </span>
          </div>
          <OperationsMap state={state} />
          <div className="legend-bar" aria-label="Map legend">
            <span><i className="legend healthy" />Healthy</span>
            <span><i className="legend low" />Low stock</span>
            <span><i className="legend critical" />Stockout risk</span>
            <span><i className="legend expiry" />Expiry risk</span>
            <span><i className="legend warehouse" />Warehouse</span>
          </div>
        </section>

        <aside className="insight-column">
          <DecisionPanel
            decisions={state.pending_decisions}
            pendingCount={state.metrics.pending_decisions}
            onDecision={(id, action) => decisionMutation.mutate({ id, action })}
          />
        </aside>
      </main>

      <section className="secondary-grid" aria-label="Decision context">
        <ComparisonPanel metrics={state.metrics} />
        <LiveSignalPanel signals={state.live_signals} />
      </section>

      <AgentPanel agents={state.agents} />
      <TimelinePanel events={state.events} traces={state.reasoning_traces} fillRate={state.metrics.fill_rate} />
    </div>
  );
}
