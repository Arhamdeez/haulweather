import { Fragment, memo, useEffect, useMemo, useState } from "react";
import L from "leaflet";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap } from "react-leaflet";
import { LEVELS, LEVEL_LABEL, LEVEL_SCORE, RISK_COLOR } from "../colors";
import { formatMiles, formatWhen } from "../format";
import type { Checkpoint, CorridorPoint, Level, Plan, RouteOption } from "../types";

interface MapViewProps {
  plan: Plan | null;
  selectedId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
}

export function MapView({ plan, selectedId, loading, onSelect }: MapViewProps) {
  const [hour, setHour] = useState(0);
  const [showHeat, setShowHeat] = useState(true);
  const [playing, setPlaying] = useState(false);
  const signature = plan
    ? `${plan.origin.lat},${plan.origin.lon},${plan.destination.lat},${plan.departure},${plan.sample_miles},${plan.load_lb}`
    : "";

  useEffect(() => {
    setHour(0);
    setPlaying(false);
  }, [signature]);

  useEffect(() => {
    if (!playing || !plan) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const step = reduced ? 1 : 0.2;
    const timer = window.setInterval(() => {
      setHour((current) => {
        const max = plan.corridor.times.length - 1;
        if (current >= max) return 0;
        return Math.min(max, current + step);
      });
    }, reduced ? 700 : 120);
    return () => window.clearInterval(timer);
  }, [playing, plan]);

  return (
    <div className="map-pane">
      <MapContainer center={[39.5, -96]} zoom={4} zoomControl={false} style={{ height: "100%", width: "100%" }}>
        <TileLayer
          attribution="Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ"
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
        />
        <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}" />
        <ZoomControl />
        {plan && <FitTo signature={signature} plan={plan} />}
        {plan && <CorridorHeat points={plan.corridor.points} hour={hour} visible={showHeat} />}
        {plan && <RouteLayer plan={plan} selectedId={selectedId} onSelect={onSelect} />}
        {plan && <CheckpointLayer plan={plan} selectedId={selectedId} onSelect={onSelect} />}
      </MapContainer>
      {plan && (
        <>
          <Legend sampleMiles={plan.sample_miles} />
          <ForecastDock
            times={plan.corridor.times}
            points={plan.corridor.points}
            timeZone={plan.timezone}
            hour={hour}
            playing={playing}
            showHeat={showHeat}
            onHour={(value) => {
              setPlaying(false);
              setHour(value);
            }}
            onPlay={() => setPlaying((value) => !value)}
            onHeat={() => setShowHeat((value) => !value)}
          />
        </>
      )}
      {!plan && !loading && <div className="map-empty">Plan a trip to draw routes, checkpoints, and the corridor forecast.</div>}
      {loading && <div className="map-status">Sampling the forecast along each road…</div>}
    </div>
  );
}

function ZoomControl() {
  const map = useMap();
  useEffect(() => {
    const control = L.control.zoom({ position: "topright" });
    control.addTo(map);
    return () => {
      control.remove();
    };
  }, [map]);
  return null;
}

function FitTo({ signature, plan }: { signature: string; plan: Plan }) {
  const map = useMap();
  const points = useMemo(() => plan.routes.flatMap((route) => fullPath(route)), [plan]);
  useEffect(() => {
    if (points.length === 0) return;
    map.fitBounds(points, { padding: [48, 48], maxZoom: 9 });
  }, [map, signature, points]);
  return null;
}

const RouteLayer = memo(function RouteLayer({
  plan,
  selectedId,
  onSelect,
}: {
  plan: Plan;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const ordered = [...plan.routes].sort((a, b) => Number(a.id === selectedId) - Number(b.id === selectedId));
  return (
    <>
      {ordered.map((route) => (
        <RouteLines key={route.id} route={route} selected={route.id === selectedId} onSelect={onSelect} />
      ))}
    </>
  );
});

function RouteLines({
  route,
  selected,
  onSelect,
}: {
  route: RouteOption;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const outline = fullPath(route);
  return (
    <Fragment>
      {selected && outline.length > 1 && (
        <Polyline
          positions={outline}
          pathOptions={{ color: "#f6f1e7", weight: 11, opacity: 0.9, lineCap: "round", lineJoin: "round" }}
          eventHandlers={{ click: () => onSelect(route.id) }}
        />
      )}
      {route.segments.map((segment, index) =>
        segment.path.length > 1 ? (
          <Polyline
            key={`${route.id}-${index}`}
            positions={segment.path}
            pathOptions={{
              color: RISK_COLOR[segment.risk],
              weight: selected ? 6 : 3.5,
              opacity: selected ? 0.95 : 0.5,
              lineCap: "round",
              lineJoin: "round",
            }}
            eventHandlers={{ click: () => onSelect(route.id) }}
          />
        ) : null,
      )}
    </Fragment>
  );
}

const CheckpointLayer = memo(function CheckpointLayer({
  plan,
  selectedId,
  onSelect,
}: {
  plan: Plan;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <>
      {plan.routes.map((route) =>
        route.checkpoints.map((point, index) => {
          const selected = route.id === selectedId;
          return (
            <CircleMarker
              key={`${route.id}-${index}`}
              center={[point.lat, point.lon]}
              radius={selected ? 7 : 4}
              pathOptions={{
                color: "#1a120c",
                weight: 1,
                fillColor: RISK_COLOR[point.risk],
                fillOpacity: selected ? 1 : 0.8,
              }}
              eventHandlers={{ click: () => onSelect(route.id) }}
            >
              <Popup>
                <CheckpointCard point={point} route={route} timeZone={plan.timezone} />
              </Popup>
            </CircleMarker>
          );
        }),
      )}
    </>
  );
});

function CheckpointCard({ point, route, timeZone }: { point: Checkpoint; route: RouteOption; timeZone: string }) {
  const gustDiffers =
    point.gust_mph !== null && point.sustained_mph !== null && Math.abs(point.gust_mph - point.sustained_mph) >= 3;
  return (
    <div className="popup">
      <p className="popup-route">{route.name}</p>
      <strong>
        Mile {formatMiles(point.mile)} · {LEVEL_LABEL[point.risk]}
      </strong>
      <p>{formatWhen(point.eta, timeZone)}</p>
      <ul>
        <li>
          Wind {point.wind_mph.toFixed(0)} mph · {LEVEL_LABEL[point.wind_level]}
        </li>
        {gustDiffers && (
          <li>
            Sustained {point.sustained_mph?.toFixed(0)} mph, gust {point.gust_mph?.toFixed(0)}
          </li>
        )}
        <li>
          Rain {point.rain_in_hr.toFixed(2)} in/hr · {LEVEL_LABEL[point.rain_level]}
        </li>
        <li>
          Snow {point.snow_in_hr.toFixed(2)} in/hr · {LEVEL_LABEL[point.snow_level]}
        </li>
      </ul>
      {point.drivers.length > 0 && <p>Set by {point.drivers.join(" and ")}.</p>}
      {point.load_note && <p className="note">{point.load_note}</p>}
      {point.forecast_clamped && <p className="note">Nearest forecast hour. This arrival sits outside the grid.</p>}
    </div>
  );
}

function Legend({ sampleMiles }: { sampleMiles: number }) {
  return (
    <div className="legend">
      {LEVELS.map((level) => (
        <div key={level}>
          <i style={{ background: RISK_COLOR[level] }} />
          {LEVEL_LABEL[level]}
        </div>
      ))}
      <p>Lines use the risk at the truck’s arrival, every {sampleMiles} mi. The glow follows the slider.</p>
    </div>
  );
}

function ForecastDock({
  times,
  points,
  timeZone,
  hour,
  playing,
  showHeat,
  onHour,
  onPlay,
  onHeat,
}: {
  times: string[];
  points: CorridorPoint[];
  timeZone: string;
  hour: number;
  playing: boolean;
  showHeat: boolean;
  onHour: (hour: number) => void;
  onPlay: () => void;
  onHeat: () => void;
}) {
  const max = Math.max(0, times.length - 1);
  const when = timeAt(times, hour);
  const snapshot = snapshotAt(points, hour);
  const windy = windiestHour(points);
  return (
    <section className="forecast">
      <div className="forecast-head">
        <div>
          <p className="eyebrow">Corridor forecast</p>
          <strong>{formatWhen(when, timeZone)}</strong>
        </div>
        <div className="forecast-actions">
          <button type="button" onClick={() => onHour(windy)}>
            Windiest hour
          </button>
          <button type="button" onClick={onPlay} aria-pressed={playing}>
            {playing ? "Pause" : "Play"}
          </button>
          <label>
            <input type="checkbox" checked={showHeat} onChange={onHeat} />
            Heatmap
          </label>
        </div>
      </div>
      <input
        type="range"
        min={0}
        max={max}
        step={0.05}
        value={hour}
        aria-label="Forecast hour after departure"
        onChange={(event) => onHour(Number(event.target.value))}
      />
      <div className="forecast-scale">
        <span>Departure</span>
        <span>{hour > 0.5 && hour < max - 0.5 ? `+${Math.round(hour)} h` : ""}</span>
        <span>+{max} h</span>
      </div>
      <p>
        Worst in view: {LEVEL_LABEL[snapshot.worst]}. {snapshot.counts.severe + snapshot.counts.no_travel} severe or
        no-travel samples, {snapshot.counts.high} high, {snapshot.counts.moderate} moderate.
      </p>
    </section>
  );
}

function CorridorHeat({ points, hour, visible }: { points: CorridorPoint[]; hour: number; visible: boolean }) {
  const map = useMap();
  const [layer] = useState(() => new CorridorCanvas());

  useEffect(() => {
    layer.addTo(map);
    return () => {
      layer.remove();
    };
  }, [layer, map]);

  useEffect(() => {
    layer.setState({ points, hour, visible });
  }, [layer, points, hour, visible]);

  return null;
}

interface HeatState {
  points: CorridorPoint[];
  hour: number;
  visible: boolean;
}

class CorridorCanvas extends L.Layer {
  private canvas: HTMLCanvasElement | null = null;
  private state: HeatState = { points: [], hour: 0, visible: false };
  private frame = 0;

  setState(state: HeatState) {
    this.state = state;
    this.schedule();
  }

  onAdd(map: L.Map) {
    this._map = map;
    this.canvas = L.DomUtil.create("canvas", "corridor-canvas");
    this.canvas.style.pointerEvents = "none";
    map.getPane("overlayPane")?.appendChild(this.canvas);
    map.on("move zoom resize", this.schedule, this);
    this.schedule();
    return this;
  }

  onRemove(map: L.Map) {
    map.off("move zoom resize", this.schedule, this);
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.canvas?.remove();
    this.canvas = null;
    return this;
  }

  private schedule = () => {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  };

  private draw() {
    const map = this._map;
    const canvas = this.canvas;
    if (!map || !canvas) return;
    const size = map.getSize();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(size.x * dpr));
    canvas.height = Math.max(1, Math.round(size.y * dpr));
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, size.x, size.y);
    const { points, hour, visible } = this.state;
    if (!visible || points.length === 0 || points[0].risk.length === 0) return;
    const low = Math.max(0, Math.min(points[0].risk.length - 1, Math.floor(hour)));
    const high = Math.min(points[0].risk.length - 1, low + 1);
    const mix = hour - Math.floor(hour);
    const ordered = [...points].sort(
      (a, b) => blendedScore(a.risk[low], a.risk[high], mix) - blendedScore(b.risk[low], b.risk[high], mix),
    );
    for (const point of ordered) {
      const center = map.latLngToContainerPoint([point.lat, point.lon]);
      const edge = map.latLngToContainerPoint([point.lat + 0.2, point.lon]);
      const radius = Math.max(26, Math.hypot(edge.x - center.x, edge.y - center.y));
      if (center.x < -radius || center.y < -radius || center.x > size.x + radius || center.y > size.y + radius) {
        continue;
      }
      const [red, green, blue] = mixRgb(RISK_COLOR[point.risk[low]], RISK_COLOR[point.risk[high]], mix);
      const alpha = 0.14 + (blendedScore(point.risk[low], point.risk[high], mix) / 5) * 0.36;
      const gradient = context.createRadialGradient(center.x, center.y, 0, center.x, center.y, radius);
      gradient.addColorStop(0, `rgba(${red}, ${green}, ${blue}, ${alpha})`);
      gradient.addColorStop(1, `rgba(${red}, ${green}, ${blue}, 0)`);
      context.fillStyle = gradient;
      context.beginPath();
      context.arc(center.x, center.y, radius, 0, Math.PI * 2);
      context.fill();
    }
  }
}

function blendedScore(low: Level, high: Level, mix: number): number {
  return LEVEL_SCORE[low] * (1 - mix) + LEVEL_SCORE[high] * mix;
}

function mixRgb(from: string, to: string, mix: number): [number, number, number] {
  const start = hex(from);
  const end = hex(to);
  return [
    Math.round(start[0] + (end[0] - start[0]) * mix),
    Math.round(start[1] + (end[1] - start[1]) * mix),
    Math.round(start[2] + (end[2] - start[2]) * mix),
  ];
}

function hex(value: string): [number, number, number] {
  return [parseInt(value.slice(1, 3), 16), parseInt(value.slice(3, 5), 16), parseInt(value.slice(5, 7), 16)];
}

function fullPath(route: RouteOption): [number, number][] {
  const path: [number, number][] = [];
  for (const segment of route.segments) {
    if (path.length === 0) path.push(...segment.path);
    else path.push(...segment.path.slice(1));
  }
  return path;
}

function timeAt(times: string[], hour: number): Date {
  if (times.length === 0) return new Date();
  const low = Math.max(0, Math.min(times.length - 1, Math.floor(hour)));
  const high = Math.min(times.length - 1, low + 1);
  const mix = Math.min(1, Math.max(0, hour - low));
  const start = new Date(times[low]).getTime();
  const end = new Date(times[high]).getTime();
  return new Date(start + (end - start) * mix);
}

function snapshotAt(points: CorridorPoint[], hour: number) {
  const counts: Record<Level, number> = { low: 0, moderate: 0, high: 0, severe: 0, no_travel: 0 };
  let worst: Level = "low";
  if (points.length === 0) return { counts, worst };
  const index = Math.max(0, Math.min(points[0].risk.length - 1, Math.round(hour)));
  for (const point of points) {
    const level = point.risk[index];
    counts[level] += 1;
    if (LEVEL_SCORE[level] > LEVEL_SCORE[worst]) worst = level;
  }
  return { counts, worst };
}

function windiestHour(points: CorridorPoint[]): number {
  if (points.length === 0 || points[0].risk.length === 0) return 0;
  let bestHour = 0;
  let best = -1;
  for (let hour = 0; hour < points[0].risk.length; hour += 1) {
    let score = 0;
    for (const point of points) score += LEVEL_SCORE[point.risk[hour]];
    if (score > best) {
      best = score;
      bestHour = hour;
    }
  }
  return bestHour;
}
