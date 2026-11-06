export type Level = "low" | "moderate" | "high" | "severe" | "no_travel";

export interface Place {
  label: string;
  lat: number;
  lon: number;
}

export interface Checkpoint {
  mile: number;
  lat: number;
  lon: number;
  eta: string;
  wind_mph: number;
  sustained_mph: number | null;
  gust_mph: number | null;
  rain_in_hr: number;
  snow_in_hr: number;
  wind_level: Level;
  rain_level: Level;
  snow_level: Level;
  risk: Level;
  drivers: string[];
  load_note: string | null;
  forecast_clamped: boolean;
}

export interface Segment {
  risk: Level;
  from_mile: number;
  to_mile: number;
  path: [number, number][];
}

export interface RouteSummary {
  miles: Record<Level, number>;
  total_mi: number;
  average_score: number;
  average_level: Level;
}

export interface RouteOption {
  id: string;
  name: string;
  rank: number;
  recommended: boolean;
  distance_mi: number;
  duration_s: number;
  arrival: string;
  checkpoints: Checkpoint[];
  segments: Segment[];
  summary: RouteSummary;
}

export interface CorridorPoint {
  lat: number;
  lon: number;
  risk: Level[];
  wind: number[];
  rain: number[];
  snow: number[];
}

export interface Plan {
  origin: Place;
  destination: Place;
  departure: string;
  timezone: string;
  load_lb: number;
  sample_miles: number;
  recommendation: {
    route_id: string;
    text: string;
    blocked: boolean;
  };
  forecast_note: string | null;
  routes: RouteOption[];
  corridor: {
    times: string[];
    points: CorridorPoint[];
  };
}

export interface PlanInput {
  origin: string;
  destination: string;
  originPoint: Place | null;
  destinationPoint: Place | null;
  departure: string;
  load: number;
  sample: 10 | 25 | 50;
}
