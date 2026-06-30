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
      setAutoplaying(Boolean(state.autoplay));
    }
  }, [setActiveScenario, setAutoplaying, state?.autoplay, state?.scenario?.name]);

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
    document.body.classList.toggle("busy", busy);
  }, [busy]);

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
      await queryClient.invalidateQueries({ queryKey: stateQueryKey });
      return;
    }
    await stockflowApi.startAutoplay();
    setAutoplaying(true);
    await queryClient.invalidateQueries({ queryKey: stateQueryKey });
  };

  if (!state) {
    return (
      <main className="loading-shell">
        <div className="empty-state">{stateQuery.isError ? String(stateQuery.error.message) : "Loading StockFlow..."}</div>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">AI-agent operations room</p>
          <h1>StockFlow</h1>
          <p className="subtitle">Multi-Agent Franchise Supply Chain Simulator</p>
        </div>
        <div className="clock-panel">
          <span>{state.scenario.label}</span>
          <strong>Day {state.sim_day}</strong>
          <span>{state.sim_date}</span>
          <span id="live-stream-status" data-status={liveStreamStatus}>
            Stream: {liveStreamStatus}
          </span>
        </div>
      </header>

      <main className="demo-grid">
        <section className="map-panel">
          <div className="map-toolbar">
            <div className="scenario-tabs" aria-label="Simulation scenarios">
              {scenarios.map((scenario) => (
                <button
                  className={`scenario-btn ${activeScenario === scenario.name ? "active" : ""}`}
                  key={scenario.name}
                  onClick={() => scenarioMutation.mutate(scenario.name)}
                >
                  {scenario.label}
                </button>
              ))}
            </div>
            <div className="control-row">
              <button className="control primary" onClick={() => runDayMutation.mutate()}>
                Run Day
              </button>
              <button className="control" onClick={toggleAutoplay}>
                {isAutoplaying ? "Pause" : "Auto Play"}
              </button>
              <button className="control ghost" onClick={() => resetMutation.mutate()}>
                Reset
              </button>
            </div>
          </div>
          <OperationsMap state={state} />
          <div className="legend-bar">
            <span>
              <i className="legend healthy" />
              Healthy
            </span>
            <span>
              <i className="legend low" />
              Low stock
            </span>
            <span>
              <i className="legend critical" />
              Stockout risk
            </span>
            <span>
              <i className="legend expiry" />
              Expiry risk
            </span>
            <span>
              <i className="legend warehouse" />
              Warehouse
            </span>
          </div>
        </section>

        <aside className="side-panel">
          <MetricBoard metrics={state.metrics} />
          <ComparisonPanel metrics={state.metrics} />
          <LiveSignalPanel signals={state.live_signals} />
          <AgentPanel agents={state.agents} />
          <DecisionPanel
            decisions={state.pending_decisions}
            pendingCount={state.metrics.pending_decisions}
            onDecision={(id, action) => decisionMutation.mutate({ id, action })}
          />
        </aside>

        <TimelinePanel
          events={state.events}
          traces={state.reasoning_traces}
          fillRate={state.metrics.fill_rate}
          error={errorMessage}
        />
      </main>
    </div>
  );
}
