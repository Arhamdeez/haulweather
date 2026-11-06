import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { geocode, planTrip } from "./api";
import { LEVELS, LEVEL_LABEL, RISK_COLOR } from "./colors";
import { formatDuration, formatLoad, formatMiles, formatWhen, tomorrowAt } from "./format";
import { MapView } from "./components/MapView";
import type { Place, Plan, PlanInput, RouteOption } from "./types";

const CHICAGO: Place = { label: "Chicago, Illinois", lat: 41.8781, lon: -87.6298 };
const INDIANAPOLIS: Place = { label: "Indianapolis, Indiana", lat: 39.7684, lon: -86.1581 };

export function App() {
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [originPoint, setOriginPoint] = useState<Place | null>(null);
  const [destinationPoint, setDestinationPoint] = useState<Place | null>(null);
  const [departure, setDeparture] = useState(() => tomorrowAt(7));
  const [load, setLoad] = useState(44000);
  const [sample, setSample] = useState<10 | 25 | 50>(25);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const submitPlan = useCallback(async (input: PlanInput) => {
    setError(null);
    setLoading(true);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const next = await planTrip(input, controller.signal);
      setPlan(next);
      setSelectedId(next.recommendation.route_id);
      setOrigin(next.origin.label);
      setDestination(next.destination.label);
      setOriginPoint(next.origin);
      setDestinationPoint(next.destination);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof Error ? caught.message : "Could not plan this trip.");
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, []);

  function currentInput(): PlanInput {
    return {
      origin: origin.trim(),
      destination: destination.trim(),
      originPoint,
      destinationPoint,
      departure,
      load,
      sample,
    };
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const input = currentInput();
    if (!input.origin || !input.destination) {
      setError("Enter both an origin and a destination.");
      return;
    }
    if (!input.departure) {
      setError("Enter a departure date and time.");
      return;
    }
    void submitPlan(input);
  }

  function runDemo() {
    const when = tomorrowAt(7);
    setOrigin(CHICAGO.label);
    setDestination(INDIANAPOLIS.label);
    setOriginPoint(CHICAGO);
    setDestinationPoint(INDIANAPOLIS);
    setDeparture(when);
    setLoad(44000);
    setSample(25);
    void submitPlan({
      origin: CHICAGO.label,
      destination: INDIANAPOLIS.label,
      originPoint: CHICAGO,
      destinationPoint: INDIANAPOLIS,
      departure: when,
      load: 44000,
      sample: 25,
    });
  }

  const selectRoute = useCallback((id: string) => setSelectedId(id), []);

  return (
    <div className="app">
      <aside className="sidebar">
        <header className="brand">
          <p className="eyebrow">Dispatch desk</p>
          <h1>Haul Weather</h1>
          <p className="lede">
            Three driving routes, scored for the wind, rain, and snow a loaded truck meets along the way.
          </p>
        </header>

        <form onSubmit={onSubmit} className="plan-form">
          <PlaceField
            label="Origin"
            value={origin}
            locked={originPoint !== null}
            onText={(value) => {
              setOrigin(value);
              setOriginPoint(null);
            }}
            onPick={(place) => {
              setOrigin(place.label);
              setOriginPoint(place);
            }}
          />
          <button
            className="swap"
            type="button"
            onClick={() => {
              setOrigin(destination);
              setDestination(origin);
              setOriginPoint(destinationPoint);
              setDestinationPoint(originPoint);
            }}
          >
            Swap
          </button>
          <PlaceField
            label="Destination"
            value={destination}
            locked={destinationPoint !== null}
            onText={(value) => {
              setDestination(value);
              setDestinationPoint(null);
            }}
            onPick={(place) => {
              setDestination(place.label);
              setDestinationPoint(place);
            }}
          />

          <label className="field">
            <span>Departure</span>
            <input type="datetime-local" value={departure} onChange={(event) => setDeparture(event.target.value)} required />
          </label>

          <div className="field">
            <div className="field-label">
              <span>Load weight</span>
              <strong>{formatLoad(load)} lb</strong>
            </div>
            <input
              type="range"
              min={5000}
              max={80000}
              step={500}
              value={Math.min(80000, Math.max(5000, load))}
              onChange={(event) => setLoad(Number(event.target.value))}
              aria-label="Load weight"
            />
            <div className="ticks" aria-hidden="true">
              <span style={{ left: tickLeft(30000) }}>30k</span>
              <span style={{ left: tickLeft(40000) }}>40k</span>
            </div>
            <label className="inline-load">
              Pounds
              <input
                type="number"
                min={1}
                max={120000}
                step={100}
                value={load}
                onChange={(event) => setLoad(Number(event.target.value))}
              />
            </label>
            {load > 40000 && (
              <p className="hint">Over 40,000 lb: 35–44 mph wind is severe, and 45–54 mph is no-travel.</p>
            )}
            {load > 30000 && load <= 40000 && (
              <p className="hint">Over 30,000 lb: 45–54 mph wind is no-travel.</p>
            )}
          </div>

          <fieldset className="intervals">
            <legend>Sample weather every</legend>
            <div role="radiogroup" aria-label="Sample interval">
              {([10, 25, 50] as const).map((miles) => (
                <button
                  key={miles}
                  type="button"
                  role="radio"
                  aria-checked={sample === miles}
                  className={sample === miles ? "on" : ""}
                  onClick={() => setSample(miles)}
                >
                  {miles} mi
                </button>
              ))}
            </div>
          </fieldset>

          <div className="actions">
            <button className="primary" type="submit" disabled={loading}>
              {loading ? "Planning…" : "Plan routes"}
            </button>
            <button className="ghost" type="button" onClick={runDemo} disabled={loading}>
              Chicago demo
            </button>
          </div>
          {loading && <p className="hint">Routing three roads, then reading the forecast at each checkpoint.</p>}
        </form>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        {plan && (
          <section className="results">
            <div className={plan.recommendation.blocked ? "recommend blocked" : "recommend"}>
              <p className="eyebrow">{plan.recommendation.blocked ? "No clear route" : "Recommended"}</p>
              <p>{plan.recommendation.text}</p>
            </div>
            {plan.forecast_note && <p className="hint">{plan.forecast_note}</p>}
            <p className="trip-line">
              {plan.origin.label} → {plan.destination.label}
              <span>
                {formatWhen(plan.departure, plan.timezone)} · {formatLoad(plan.load_lb)} lb · every {plan.sample_miles} mi
              </span>
            </p>
            <div className="cards">
              {plan.routes.map((route) => (
                <RouteCard
                  key={route.id}
                  route={route}
                  timeZone={plan.timezone}
                  selected={route.id === selectedId}
                  onSelect={() => selectRoute(route.id)}
                />
              ))}
            </div>
            <Compare routes={plan.routes} />
          </section>
        )}

        <details className="rules">
          <summary>Risk rules</summary>
          <p>Each checkpoint takes the worst of wind, rain, and snow. Wind uses the forecast gust.</p>
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Low</th>
                <th>Moderate</th>
                <th>High</th>
                <th>Severe</th>
                <th>No travel</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>Wind</th>
                <td>&lt;25 mph</td>
                <td>25–34</td>
                <td>35–44</td>
                <td>45–54</td>
                <td>≥55</td>
              </tr>
              <tr>
                <th>Rain</th>
                <td>&lt;0.10 in/hr</td>
                <td>0.10–0.25</td>
                <td>0.25–0.50</td>
                <td>0.50–1.00</td>
                <td>&gt;1.00</td>
              </tr>
              <tr>
                <th>Snow</th>
                <td>&lt;0.5 in/hr</td>
                <td>0.5–1.0</td>
                <td>1.0–2.0</td>
                <td>2.0–3.0</td>
                <td>&gt;3.0</td>
              </tr>
            </tbody>
          </table>
          <ul>
            <li>55 mph or more is no-travel for every load.</li>
            <li>45–54 mph is no-travel above 30,000 lb.</li>
            <li>35–44 mph is severe above 40,000 lb.</li>
          </ul>
          <p>
            Miles belong to the nearest sample. The recommendation prefers the fewest no-travel miles, then severe,
            then high, then the lowest average risk, then the shortest trip.
          </p>
        </details>

        <footer className="sources">
          Roads from OSRM. Places from Nominatim. Forecast from Open-Meteo. Map tiles © Esri.
        </footer>
      </aside>
      <MapView plan={plan} selectedId={selectedId} loading={loading} onSelect={selectRoute} />
    </div>
  );
}

function tickLeft(pounds: number): string {
  const min = 5000;
  const max = 80000;
  return `${((pounds - min) / (max - min)) * 100}%`;
}

function PlaceField({
  label,
  value,
  locked,
  onText,
  onPick,
}: {
  label: string;
  value: string;
  locked: boolean;
  onText: (value: string) => void;
  onPick: (place: Place) => void;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Place[]>([]);

  useEffect(() => {
    if (locked || value.trim().length < 3) {
      setItems([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      geocode(value, controller.signal)
        .then(setItems)
        .catch((caught: unknown) => {
          if (caught instanceof DOMException && caught.name === "AbortError") return;
          setItems([]);
        });
    }, 350);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [value, locked]);

  return (
    <label className="field">
      <span>{label}</span>
      <input
        value={value}
        placeholder="City, state"
        autoComplete="off"
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 160)}
        onChange={(event) => onText(event.target.value)}
      />
      {open && items.length > 0 && (
        <ul className="suggestions">
          {items.map((place) => (
            <li key={`${place.lat.toFixed(3)},${place.lon.toFixed(3)},${place.label}`}>
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  onPick(place);
                  setOpen(false);
                }}
              >
                {place.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </label>
  );
}

function RouteCard({
  route,
  timeZone,
  selected,
  onSelect,
}: {
  route: RouteOption;
  timeZone: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const miles = route.summary.miles;
  const total = LEVELS.reduce((sum, level) => sum + miles[level], 0) || 1;
  return (
    <button type="button" className={selected ? "route-card selected" : "route-card"} onClick={onSelect}>
      <div className="card-top">
        <div>
          <p className="eyebrow">
            Rank {route.rank}
            {route.recommended ? " · Recommended" : ""}
          </p>
          <h2>{route.name}</h2>
        </div>
        <strong className="avg" style={{ color: RISK_COLOR[route.summary.average_level] }}>
          {route.summary.average_score.toFixed(2)}
          <span>{LEVEL_LABEL[route.summary.average_level]}</span>
        </strong>
      </div>
      <p className="meta">
        {formatMiles(route.distance_mi)} mi · {formatDuration(route.duration_s)} · arrives {formatWhen(route.arrival, timeZone)}
      </p>
      <div className="bar" aria-hidden="true">
        {LEVELS.map((level) =>
          miles[level] > 0 ? (
            <span key={level} style={{ width: `${(miles[level] / total) * 100}%`, background: RISK_COLOR[level] }} />
          ) : null,
        )}
      </div>
      <p className="mile-line">
        {LEVELS.filter((level) => miles[level] > 0)
          .map((level) => `${formatMiles(miles[level])} mi ${LEVEL_LABEL[level].toLowerCase()}`)
          .join(" · ")}
      </p>
    </button>
  );
}

function Compare({ routes }: { routes: RouteOption[] }) {
  const rows: { label: string; read: (route: RouteOption) => number; render: (value: number) => string }[] = [
    { label: "No-travel mi", read: (route) => route.summary.miles.no_travel, render: formatMiles },
    { label: "Severe mi", read: (route) => route.summary.miles.severe, render: formatMiles },
    { label: "High mi", read: (route) => route.summary.miles.high, render: formatMiles },
    { label: "Avg risk", read: (route) => route.summary.average_score, render: (value) => value.toFixed(2) },
    { label: "Minutes", read: (route) => Math.round(route.duration_s / 60), render: (value) => String(value) },
  ];
  return (
    <table className="compare">
      <thead>
        <tr>
          <th></th>
          {routes.map((route) => (
            <th key={route.id}>{route.name}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const best = Math.min(...routes.map(row.read));
          return (
            <tr key={row.label}>
              <th>{row.label}</th>
              {routes.map((route) => {
                const value = row.read(route);
                return (
                  <td key={route.id} className={value === best ? "best" : ""}>
                    {row.render(value)}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
