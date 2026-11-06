# Haul Weather

A React and Django desk for choosing a truck route when the weather changes along the road. The driver enters an origin, a destination, a departure time, and a load weight. The app asks a routing service for three real driving routes, samples the forecast at the truck’s estimated arrival, and recommends the trip with the least weather exposure.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/python backend/manage.py test routing

cd frontend
npm install
npm run dev
```

In another terminal:

```bash
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:5173. The Vite dev server proxies `/api` to Django. **Chicago demo** plans Chicago to Indianapolis for tomorrow at 7:00 with a 44,000 lb load, sampled every 25 miles.

No API keys are required.

| Job | Service |
| --- | --- |
| Driving routes | [OSRM](https://project-osrm.org/) public API |
| Place search | [Nominatim](https://nominatim.org/) |
| Hourly forecast | [Open-Meteo](https://open-meteo.com/) |
| Map | Esri World Dark Gray Canvas |

## What the planner does

1. Geocode the two places, unless the UI already picked a suggestion.
2. Ask OSRM for up to three alternatives. When the public server returns one road, two more routes are requested through waypoints offset to either side of the direct path. Near-duplicate paths and huge detours are dropped.
3. Place a checkpoint every 10, 25, or 50 miles, including the start and the destination. Arrival time comes from OSRM’s per-segment durations, so a slow stretch is not treated as if the truck held one average speed.
4. Read wind gust, rain, and snow for the hour that contains that arrival. Gust is the wind value. Sustained wind is shown beside it when they differ.
5. Score each checkpoint, give it the miles halfway to its neighbors, and rank the routes.

A checkpoint’s risk is the worst of wind, rain, and snow.

| | Low | Moderate | High | Severe | No travel |
| --- | --- | --- | --- | --- | --- |
| Wind | <25 mph | 25–34 | 35–44 | 45–54 | ≥55 mph |
| Rain | <0.10 in/hr | 0.10–0.25 | 0.25–0.50 | 0.50–1.00 | >1.00 |
| Snow | <0.5 in/hr | 0.5–1.0 | 1.0–2.0 | 2.0–3.0 | >3.0 |

Load weight changes wind only:

- 55 mph or more is no-travel for every load.
- 45–54 mph is no-travel above 30,000 lb.
- 35–44 mph is severe above 40,000 lb.

Shared edges use the more cautious bin. Rain of 1.00 in/hr and snow of 3.0 in/hr stay severe; anything above those is no-travel.

Each sample owns the road halfway back to the previous sample and halfway forward to the next one. One severe reading therefore counts about one sample interval, not both neighboring gaps.

## Recommendation

Lower is better:

1. Fewest no-travel miles. These outrank severe miles so a blocked stretch cannot hide behind a zero severe-mile score.
2. Fewest severe miles.
3. Fewest high miles.
4. Lowest mile-weighted average risk. Low is 1, moderate 2, high 3, severe 4, no-travel 5.
5. Shortest travel time.

The card explains the first rule that actually separates the routes. If every route is tied on weather, the shortest trip wins and the sentence says so.

## Map

The selected route is outlined in cream and colored by the risk of each sample. Other routes stay on the map at lower opacity. Checkpoints open an ETA and the wind, rain, and snow that set the color.

The corridor heatmap is drawn on a canvas from the forecast already returned with the plan. The 0–48 hour slider does not call the network again. Play steps through the hours, and **Windiest hour** jumps to the hour with the highest total risk along the corridor. Points off screen are skipped while the map moves.

## API

`GET /api/health`

`GET /api/geocode?q=Chicago`

`POST /api/plan`

```json
{
  "origin": "Chicago, Illinois",
  "origin_lat": 41.8781,
  "origin_lon": -87.6298,
  "destination": "Indianapolis, Indiana",
  "destination_lat": 39.7684,
  "destination_lon": -86.1581,
  "departure": "2026-11-07T07:00",
  "timezone": "America/Chicago",
  "load_lb": 44000,
  "sample_miles": 25
}
```

`sample_miles` must be 10, 25, or 50. Departure has to fall inside the forecast window, from about 12 hours ago through 10 days ahead. Weather for a repeated plan is cached for 10 minutes, so changing the load and planning again does not refetch the same cells.

## Tests

```bash
.venv/bin/python backend/manage.py test routing
```

The tests lock the threshold edges, the load rules, mile ownership, the ranking order, arrival time on a route that slows down, and request validation.

## Production

The Docker image builds the frontend and serves it from Django on port 8000.

```bash
docker compose up --build
```

Set `PORT` and `DJANGO_SECRET_KEY` on the host. Gunicorn’s timeout is 120 seconds because a long haul at a 10-mile interval waits on the routing and forecast services.

## Walkthrough

A five to ten minute recording can follow this path:

1. Show the inputs and open **Risk rules**. Point out the 30,000 lb and 40,000 lb wind changes.
2. Run **Chicago demo** (tomorrow 7:00, 44,000 lb, every 25 miles).
3. Read the recommendation sentence and the comparison table. If the morning is calm, the sentence should say the weather is tied and the shortest trip wins.
4. Select another route. Open a checkpoint and show the ETA, gust, rain, and snow.
5. Drag the forecast slider and press **Play**, then **Windiest hour**. The corridor should change color without a new request.
6. Plan the same trip again at 20,000 lb and at 60,000 lb. Mention that a windier hour is what moves a route from high to severe, or from severe to no-travel.
