import type { DemoState, ScenarioName } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

async function fetchJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, options);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${body}`);
  }
  return response.json() as Promise<T>;
}

export const stockflowApi = {
  getState: () => fetchJson<DemoState>("/demo/state"),
  runDay: () => fetchJson<DemoState>("/demo/tick", { method: "POST" }),
  reset: () => fetchJson<DemoState>("/demo/reset", { method: "POST" }),
  startAutoplay: () => fetchJson<DemoState>("/demo/autoplay/start", { method: "POST" }),
  stopAutoplay: () => fetchJson<DemoState>("/demo/autoplay/stop", { method: "POST" }),
  setScenario: (scenarioName: ScenarioName) =>
    fetchJson<DemoState>(`/demo/scenario/${scenarioName}`, { method: "POST" }),
  decide: (decisionId: number, action: "approve" | "reject") =>
    fetchJson(`/agents/decisions/${decisionId}/${action}`, { method: "POST" }),
};
