# Remuxarr — Frontend

React + Vite SPA.  All UI code lives in `src/App.jsx`.

## Development (hot-reload against live backend)

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The Vite dev server proxies `/api` and `/ws` to `http://localhost:8000`
automatically, so the backend and frontend run on separate ports with no CORS issues.

## Production build (served by FastAPI)

```bash
cd frontend
npm install
npm run build        # outputs to frontend/dist/
```

FastAPI detects `frontend/dist/` at startup and serves it at `/`.
The API docs remain available at `/docs`.

## Docker

The `Dockerfile` runs `npm run build` during image build so the final
container serves everything from a single port (8000).
