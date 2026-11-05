# logseerr

`logseerr` is a local log-search assistant. It answers natural-language questions about your homelab logs by querying Loki live (Loki is the only log store; nothing is copied or embedded) and summarising the matching lines with an Ollama LLM, through a FastAPI backend and Vue frontend.

Searches are limited to the last 7 days because that is Loki's retention.

## Stack

- **Backend:** FastAPI + httpx, querying Loki via LogQL (`backend/`)
- **Frontend:** Vue 3 + Vite + Tailwind (`frontend/`)
- **Infra:** Docker Compose (`docker-compose.yml`)

## Prerequisites

- Docker + Docker Compose
- Node.js 24 (`.nvmrc`)
- Python 3.12 (for local backend development)

## Quick start (Docker)

1. Copy env file:

```bash
cp .env.example .env
```

2. Edit `.env` with reachable service URLs:

- `LOKI_URL`
- `OLLAMA_URL` — Ollama instance (serves `/api/generate` for query understanding and answers)
- `OLLAMA_MODEL`
- `VITE_API_BASE_URL`

> **Ollama:** one Ollama instance (the Mac mini, `192.168.50.121`) serves both
> LLM calls: turning the question into a structured Loki query, and writing the
> final answer. It must listen on the LAN (`OLLAMA_HOST=0.0.0.0`) and should
> have a stable IP (DHCP reservation). No embedding service is needed.

3. Build and run:

```bash
docker compose build
docker compose up -d
```

4. Verify:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"did plex have any issues in the last 24 hours?"}'
```

Notes:
- Frontend is served at `http://localhost`
- Backend is served at `http://localhost:8000`
- Log data stays in Loki; logseerr keeps no database of its own

## Local development

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run tests:

```bash
cd backend
source .venv/bin/activate
pytest -q
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Build:

```bash
cd frontend
npm run build
```

## API

- `GET /health` → checks connectivity to Loki (`/loki/api/v1/labels`) and the Ollama LLM:

```json
{ "status": "ok", "loki": "ok", "llm": "ok" }
```

Returns `503` with `"status": "degraded"` if either service is unreachable.
- `POST /search` with JSON body:

```json
{ "query": "did plex have any issues in the last 24 hours?" }
```

Response:

```json
{
  "answer": "string | null",
  "answer_status": "ok | no_results | llm_unavailable",
  "sources": []
}
```

For count/"which container" questions the counts are always computed directly from Loki and rendered as deterministic markdown without the LLM (no sources).

If Loki is unreachable, `/search` returns `503` with `Log search unavailable`.
