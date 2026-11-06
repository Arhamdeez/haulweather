import type { Level } from "./types";

export const LEVELS: Level[] = ["low", "moderate", "high", "severe", "no_travel"];

export const RISK_COLOR: Record<Level, string> = {
  low: "#3dbe86",
  moderate: "#f0b429",
  high: "#ff8a2a",
  severe: "#ff5d52",
  no_travel: "#d46bff",
};

export const LEVEL_LABEL: Record<Level, string> = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  severe: "Severe",
  no_travel: "No travel",
};

export const LEVEL_SCORE: Record<Level, number> = {
  low: 1,
  moderate: 2,
  high: 3,
  severe: 4,
  no_travel: 5,
};
