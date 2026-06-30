import { create } from "zustand";
import type { ScenarioName } from "./types";

type LiveStreamStatus = "connecting" | "live" | "reconnecting" | "unsupported";

type UiState = {
  activeScenario: ScenarioName;
  isAutoplaying: boolean;
  liveStreamStatus: LiveStreamStatus;
  setActiveScenario: (scenario: ScenarioName) => void;
  setAutoplaying: (value: boolean) => void;
  setLiveStreamStatus: (status: LiveStreamStatus) => void;
};

export const useStockFlowUi = create<UiState>((set) => ({
  activeScenario: "weekend-rush",
  isAutoplaying: false,
  liveStreamStatus: "connecting",
  setActiveScenario: (activeScenario) => set({ activeScenario }),
  setAutoplaying: (isAutoplaying) => set({ isAutoplaying }),
  setLiveStreamStatus: (liveStreamStatus) => set({ liveStreamStatus }),
}));
