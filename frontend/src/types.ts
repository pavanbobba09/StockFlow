export type ScenarioName =
  | "weekend-rush"
  | "game-day-spike"
  | "delivery-delay"
  | "expiry-rescue"
  | "store-to-store-transfer";

export type Scenario = {
  name: ScenarioName;
  label: string;
  demand_multiplier: number;
  description: string;
};

export type Agent = {
  name: string;
  role: string;
  status: string;
};

export type Metrics = {
  stockouts_avoided: number;
  waste_reduced: number;
  units_transferred: number;
  estimated_profit_saved: number;
  fill_rate: number;
  pending_decisions: number;
  without_agents: {
    projected_stockouts: number;
    projected_waste: number;
  };
  with_agents: {
    stockouts: number;
    waste: number;
  };
};

export type LiveSignals = {
  status: string;
  demand_multiplier: number;
  reasons: string[];
};

export type Restaurant = {
  id: number;
  name: string;
  lat: number;
  lng: number;
  status: "healthy" | "low" | "critical" | "expiry";
  inventory_units: number;
  stockout_risk: number;
  expiry_risk: number;
  top_items: Array<{
    name: string;
    quantity: number;
    risk: number;
  }>;
};

export type Warehouse = {
  id: number;
  name: string;
  lat: number;
  lng: number;
  inventory_units: number;
};

export type Route = {
  type: "transfer" | "order";
  quantity: number;
  item_name: string;
  from: {
    name: string;
    lat: number;
    lng: number;
  };
  to: {
    name: string;
    lat: number;
    lng: number;
  };
};

export type Decision = {
  id: number;
  decision_type: "order" | "transfer" | "markdown" | "donation";
  store_id: number;
  store_name?: string;
  target_store_name?: string;
  item_id: number;
  item_name?: string;
  quantity: number;
  reason: string;
  expected_impact: string;
};

export type AgentEvent = {
  id: number;
  agent_name: string;
  event_type: string;
  severity: "info" | "warning" | "critical";
  message: string;
};

export type ReasoningTrace = {
  id: number;
  agent_name: string;
  tool_name: string;
  input_summary: string;
  observation: string;
  decision: string;
};

export type DemoState = {
  chain_name: string;
  sim_day: number;
  sim_date: string;
  autoplay: boolean;
  simulation_speed_ms: number;
  scenario: Scenario;
  metrics: Metrics;
  live_signals: LiveSignals;
  agents: Agent[];
  restaurants: Restaurant[];
  warehouses: Warehouse[];
  routes: Route[];
  pending_decisions: Decision[];
  events: AgentEvent[];
  reasoning_traces: ReasoningTrace[];
};
