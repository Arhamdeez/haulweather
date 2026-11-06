import type { Place, Plan, PlanInput } from "./types";

export async function geocode(query: string, signal: AbortSignal): Promise<Place[]> {
  const response = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`, { signal });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || "Place search failed");
  }
  return body.results ?? [];
}

export async function planTrip(input: PlanInput, signal: AbortSignal): Promise<Plan> {
  const payload: Record<string, unknown> = {
    origin: input.origin,
    destination: input.destination,
    departure: input.departure,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    load_lb: input.load,
    sample_miles: input.sample,
  };
  if (input.originPoint) {
    payload.origin_lat = input.originPoint.lat;
    payload.origin_lon = input.originPoint.lon;
  }
  if (input.destinationPoint) {
    payload.destination_lat = input.destinationPoint.lat;
    payload.destination_lon = input.destinationPoint.lon;
  }
  const response = await fetch("/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `Planning failed (${response.status})`);
  }
  return body as Plan;
}
