# IASW Deployment Guide

This document covers everything needed to run, manage, and (optionally) deploy the Intelligent Account Servicing Workflow.

---

## Current Setup — What's Running

| Service | How it runs | URL | Port |
|---------|-------------|-----|------|
| FastAPI Backend | Docker Compose | http://localhost:8000 | 8000 |
| PostgreSQL | Docker Compose | internal | 5432 |
| Redis | Docker Compose | internal | 6379 |
| React Frontend (dev) | `npm run dev` | http://localhost:5173 | 5173 |
| React Frontend (prod) | Docker container | http://localhost:3000 | 3000 |

> You only need **one** frontend — either dev mode (5173) or Docker prod (3000). Not both.

---

## Local Deployment — Quick Reference

### Start Everything

```bash
cd "Intelligent Account Servicing Workflow"

# 1. Backend stack (DB + Redis + FastAPI)
docker-compose up -d

# 2a. Frontend — Dev mode (hot reload, for development)
cd frontend && npm run dev
# → http://localhost:5173

# 2b. Frontend — Production Docker (nginx, for demos)
docker run -d --name iasw-frontend -p 3000:80 iasw-frontend:latest
# → http://localhost:3000
```

### Stop Everything

```bash
# Stop backend stack
docker-compose down

# Stop frontend container (if running in Docker)
docker stop iasw-frontend && docker rm iasw-frontend

# Stop dev server — Ctrl+C in the npm run dev terminal
```

### Check Status

```bash
# All services
docker-compose ps
docker ps --filter "name=iasw-frontend"

# Health check
curl http://localhost:8000/health
```

### View Logs

```bash
# Backend logs (live)
docker-compose logs -f backend

# All services
docker-compose logs -f

# Structured JSON logs (human readable)
tail -f logs/iasw.log | python3 -m json.tool
```

---

## Rebuild After Code Changes

### Backend changed (Python files)

```bash
docker-compose build backend --no-cache
docker-compose up -d backend
```

### Frontend changed (React/JSX files)

```bash
# Dev mode: Vite hot-reloads automatically — nothing to do

# Production Docker: rebuild the image
cd "Intelligent Account Servicing Workflow"
cd frontend && npm run build && cd ..
docker stop iasw-frontend && docker rm iasw-frontend
docker build -f k8s/frontend.Dockerfile -t iasw-frontend:latest ./frontend
docker run -d --name iasw-frontend -p 3000:80 iasw-frontend:latest
```

> **Important:** Always run `docker build` from the **project root** (not from `frontend/`), using `./frontend` as the build context.

---

## Environment Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | _(blank)_ | Leave blank for mock mode; set for real Gemini OCR |
| `GEMINI_STRICT` | `false` | Set `true` to disable mock fallback in demos |
| `DATABASE_URL` | `sqlite:///./iasw.db` | Local dev SQLite; Docker uses PostgreSQL automatically |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://localhost:3000` | CORS allowlist — add your production domain here |
| `RATE_LIMIT` | `10/minute` | Max intake submissions per minute per IP |

> Docker Compose overrides `DATABASE_URL` and `REDIS_URL` automatically with internal service hostnames. You don't need to change these for Docker.

---

## Build the Frontend Docker Image

> **Prerequisite:** Node.js 20+ must be installed (`node --version`)  
> Vite v8 (used in this project) requires Node.js 20.19+ or 22.12+.

```bash
# From project root:
cd "Intelligent Account Servicing Workflow"

# Step 1: Build the React app
cd frontend && npm run build && cd ..

# Step 2: Build the Docker image (always from project root)
docker build -f k8s/frontend.Dockerfile -t iasw-frontend:latest ./frontend

# Step 3: Run it
docker run -d --name iasw-frontend -p 3000:80 iasw-frontend:latest

# Verify
curl -s -o /dev/null -w "HTTP %{http_code}" http://localhost:3000
# → HTTP 200
```

---

## Async Pipeline — How It Works

```
POST /api/intake
    │
    ├─ Duplicate guard (409 if customer already has open request of same type)
    ├─ File validation (max 20MB, non-empty)
    ├─ Return 202 Accepted { task_id, poll_url }   ← instant response
    │
    └─ asyncio thread pool → run_iasw_pipeline()   ← runs in background
           ├─ Agent 1: Validation (RPS cross-check)
           ├─ Agent 2: Document Processing (Gemini multimodal OCR)
           └─ Agent 3: Confidence Scoring (fuzzy match + 5-priority chain)
                  └─ DB write → AI_VERIFIED_PENDING_HUMAN

GET /api/tasks/{task_id}   ← responds in <55ms while pipeline runs
    └─ { status: QUEUED | RUNNING | COMPLETED | FAILED }
```

---

## API Reference

### `POST /api/intake` — Submit change request

Returns `202 Accepted` immediately with a `task_id` for polling.

```bash
curl -X POST http://localhost:8000/api/intake \
  -F customer_id=C001 \
  -F change_type=LEGAL_NAME_CHANGE \
  -F old_value="Priya Sharma" \
  -F new_value="Priya Mehta" \
  -F document_type=MARRIAGE_CERTIFICATE \
  -F document=@/path/to/document.pdf
```

**Responses:**
- `202` — Accepted, `{ task_id, poll_url }`
- `409` — Duplicate: customer already has open request of same change type
- `422` — Validation error (missing fields)
- `429` — Rate limited (10/min per IP)

---

### `GET /api/tasks/{task_id}` — Poll task status

```bash
curl http://localhost:8000/api/tasks/3468dd86-7454-4310-8444-f64459575308
```

```json
{
  "task_id": "3468dd86-...",
  "status": "COMPLETED",
  "result": {
    "request_id": "46491c8e-...",
    "final_status": "AI_VERIFIED_PENDING_HUMAN"
  }
}
```

| `status` | Meaning |
|----------|---------|
| `QUEUED` | Waiting in queue |
| `RUNNING` | Pipeline executing (Gemini processing) |
| `COMPLETED` | Done — check `result.final_status` |
| `FAILED` | Pipeline error — check `result.error` |

**`final_status` values:**

| Value | Meaning |
|-------|---------|
| `AI_VERIFIED_PENDING_HUMAN` | ✅ In checker queue — awaiting human decision |
| `VALIDATION_FAILED` | ❌ Customer ID not found or old value mismatch |

---

### `GET /api/checker/queue` — Pending requests for checker

```bash
curl http://localhost:8000/api/checker/queue
```

Returns all `AI_VERIFIED_PENDING_HUMAN` records. Cached in Redis with 30s TTL.

---

### `POST /api/checker/decide` — Approve or reject

```bash
curl -X POST http://localhost:8000/api/checker/decide \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "46491c8e-...",
    "checker_id": "checker_sup_01",
    "decision": "APPROVED",
    "notes": "Documents verified, signature matches."
  }'
```

---

### `GET /health` — System health

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "version": "1.0.0",
  "llm_mode": "gemini",
  "cache": "connected",
  "timestamp": 22213.843
}
```

---

## Security Notes

### CORS
Origins are **not** wildcarded. Set `ALLOWED_ORIGINS` in `.env`:
```bash
# Local dev (default)
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000

# Production — replace with your actual domain
ALLOWED_ORIGINS=https://iasw.yourbank.com
```

### Validation error data privacy
When a submitted old-value doesn't match the record, the error message does **not** reveal the actual value on record:
```
✅ "The current value submitted for customer 'C001' does not match what is on record."
❌ "Submitted 'Wrong' does not match RPS record 'Priya Sharma'."  ← old behaviour, removed
```

### Duplicate submission guard
Blocks on `customer_id + change_type` — one open request per customer per change type, regardless of what new value is requested.

### HITL boundary
The AI pipeline **cannot** write to RPS. Three independent enforcement layers:
1. No RPS write node in the LangGraph graph
2. `POST /api/rps/write` checks `checker_id IS NOT NULL` before executing
3. DB constraint enforces `checker_id` requirement

---

## Confidence Scoring

5-priority decision chain (highest priority first):

```
1. forgery_check == FAIL     → REJECT
2. forgery_check == WARN     → FLAG
3. document_type mismatch    → REJECT
4. missing required fields   → REJECT
5. score threshold:
      ≥ 0.80  → APPROVE
      0.60–0.79 → FLAG
      < 0.60  → REJECT
```

Score formula:
```
overall = (name_match × 0.6) + (authenticity × 0.4)
```

---

## Kubernetes (Cloud Deployment)

> This section is for deploying to a real cloud cluster (GKE / EKS / AKS).  
> **Not required for local demos or the technical assignment.**

### Prerequisites
- A Kubernetes cluster (Docker Desktop k8s, Minikube, or cloud)
- `kubectl` configured (`kubectl get nodes` works)
- A container registry (Docker Hub, GCR, ECR, etc.)

### Steps

```bash
# 1. Build and push backend image
docker build -t your-registry/iasw-backend:latest .
docker push your-registry/iasw-backend:latest

# 2. Build and push frontend image (from project root)
docker build -f k8s/frontend.Dockerfile -t your-registry/iasw-frontend:latest ./frontend
docker push your-registry/iasw-frontend:latest

# 3. Update image fields in k8s/frontend.yaml
#    Change: image: iasw-frontend:latest
#    To:     image: your-registry/iasw-frontend:latest

# 4. Create namespace
kubectl create namespace iasw

# 5. Deploy frontend
kubectl apply -f k8s/frontend.yaml

# 6. Verify
kubectl get pods -n iasw
kubectl get svc -n iasw
```

### What the k8s manifests include

| Feature | Detail |
|---------|--------|
| Replicas | 2 frontend pods |
| Rolling updates | Zero-downtime (`maxUnavailable: 0`) |
| Health probes | Liveness + readiness on port 80 |
| Anti-affinity | Pods spread across nodes |
| Resource limits | CPU 200m, Memory 128Mi |

---

## Troubleshooting

### Backend container keeps restarting

```bash
docker-compose logs backend --tail=30
```

Common causes:
- `DATABASE_URL` wrong — check `.env` and docker-compose environment overrides
- `GEMINI_API_KEY` invalid format — set to blank for mock mode

### Redis connection warning on startup

```
Redis not available — running without cache
```

This is non-fatal. The system works without Redis — checker queue just hits the DB on every request. To fix:
```bash
docker-compose up -d redis
```

### Frontend shows "Network Error" on submit

The frontend at `localhost:3000` is trying to reach `localhost:8000`. Verify:
```bash
curl http://localhost:8000/health   # Should return {"status":"ok"}
docker-compose ps                  # backend should be "Up (healthy)"
```

### Vite build fails with "Node.js version" error

```
Vite requires Node.js version 20.19+ or 22.12+
```

Upgrade Node.js:
```bash
# Using nvm:
nvm install 20 && nvm use 20
node --version   # Should show v20.x.x
```

### docker build fails: "lstat k8s: no such file or directory"

You ran the command from the wrong directory. **Always run from the project root:**
```bash
# ❌ Wrong — running from frontend/
docker build -f k8s/frontend.Dockerfile ...

# ✅ Correct — from project root
cd "Intelligent Account Servicing Workflow"
docker build -f k8s/frontend.Dockerfile -t iasw-frontend:latest ./frontend
```

---

## Mock RPS Customers (Test Data)

| Customer ID | Name | Change Type to test |
|-------------|------|---------------------|
| `C001` | Priya Sharma | `LEGAL_NAME_CHANGE` |
| `C002` | Rahul Verma | `LEGAL_NAME_CHANGE` |
| `C003` | Meena Iyer | `LEGAL_NAME_CHANGE` |

Use these exact values as "Old Name" in the intake form to pass validation.
