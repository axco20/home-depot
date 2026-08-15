# StockPath

StockPath builds a receipt-aware Home Depot clearance route from a starting ZIP and a list of SKUs. It profiles the 100 stores returned by Hidden Clearances, groups mixed-SKU receipts, enforces one price adjustment per destination, and orders the selected stores by road miles.

Only prices at least 80% below retail qualify as direct-clearance or price-adjustment destinations.
Plans default to a 50-mile radius from the starting ZIP; the user can set any radius from 5 to 150 miles.

## Setup

```bash
npm install
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Add your openrouteservice API key to `.env`. The Hidden Clearances bearer token can be pasted into the planner when generating a route; `HIDDEN_CLEARANCES_TOKEN` remains available as an optional server-side fallback.

## Run

Start the API and frontend together:

```bash
npm run dev
```

For separate terminals, use `npm run api` and `npm run dev:web`.

Open the Vite URL, normally `http://localhost:5173`.

## Checks

```bash
npm run build
npm run lint
npm run test:backend
```

Stock results are fetched fresh for each plan. Geocoded addresses and store-to-store road distances are cached in `backend/stockpath.sqlite3`.

Hidden Clearances receives exactly one request per unique SKU. Requests are sequential with a 5-second interval (25 request starts span two minutes), are never retried automatically, and the batch stops immediately on authentication or rate-limit responses.

The automatic planner ranks stores by savings and the marginal road miles required to add them to the current loop, so dense store groups are preferred over scattered stops. After the stock snapshot loads, every in-radius store appears on the map. Map selections can rebuild an exact custom store loop—or an all-store master loop—without making more Hidden Clearances requests.

Before a stock run, `GET /api/stores` geocodes the starting ZIP and uses OpenStreetMap/Overpass store-location data to display nearby Home Depot locations. The user can allow cluster optimization, create an exact map whitelist, or force the full mapped loop. This lookup does not call Hidden Clearances. Hidden still receives one request per SKU because its endpoint cannot accept a store filter; selected store IDs are applied immediately after each stock snapshot and before road-matrix generation.
# home-depot
