# IASW Deployment Guide

> Complete guide for running the Intelligent Account Servicing Workflow in Docker (local) and Kubernetes (production/cloud).

---

## Table of Contents

1. [Docker Compose — Local Full Stack](#docker-compose)
2. [Local Dev Without Docker](#local-dev)
3. [Environment Variables Reference](#environment-variables)
4. [Kubernetes — Production Deployment](#kubernetes)
5. [Database Operations](#database-operations)
6. [Troubleshooting](#troubleshooting)

---

## Docker Compose — Local Full Stack <a name="docker-compose"></a>

### What starts

```
docker compose up -d
```

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `db` | postgres:15 | 5432 | PostgreSQL — all 5 tables |
| `redis` | redis:7-alpine | 6379 | Checker queue cache (TTL 30s) |
| `backend` | Built from `Dockerfile` | 8000 | FastAPI + LangGraph pipeline |
| `frontend` | Built from `k8s/frontend.Dockerfile` | 5173 | React SPA served by nginx |

### First-time setup

```bash
# 1. Copy and configure environment
cp .env.example .env
#    Set GEMINI_API_KEY and JWT_SECRET at minimum

# 2. Build and start
docker compose up -d

# 3. Verify all services are healthy
docker compose ps
```

Expected output:
```
NAME                                          STATUS
intelligentaccountservicingworkflow-db-1      Up (healthy)
intelligentaccountservicingworkflow-redis-1   Up (healthy)
intelligentaccountservicingworkflow-backend-1 Up (healthy)
intelligentaccountservicingworkflow-frontend-1 Up
```

### What happens on first startup (automatic)

The backend startup hook runs three idempotent seeds:

1. **Database tables** — `init_db()` creates all 5 tables via SQLAlchemy
2. **Seed users** — `admin/admin123` (ADMIN) and `user/user123` (USER) created in `users` table
3. **Seed RPS records** — 3 demo customers (C001, C002, C003) inserted into `rps_records`

All seeds are **idempotent** — safe to run on every restart, won't duplicate rows.

### Common Docker commands

```bash
# Start all services
docker compose up -d

# Stop and remove containers (keep volumes/data)
docker compose down

# Stop and WIPE all data (fresh start)
docker compose down -v

# View live logs
docker compose logs backend -f
docker compose logs frontend -f

# Rebuild after code changes
docker compose build backend   # after Python changes
docker compose build frontend  # after React changes
docker compose up -d           # apply rebuilt images

# Rebuild everything from scratch
docker compose build --no-cache
docker compose up -d
```

---

## Local Dev Without Docker <a name="local-dev"></a>

### Backend

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment (SQLite used automatically in local dev)
cp .env.example .env
# Set GEMINI_API_KEY in .env

# Start backend
uvicorn app.main:app --reload --port 8000
```

The backend starts with:
- SQLite database (`iasw.db`) auto-created
- Redis optional — if not running, checker queue caching is disabled (system still works)
- Demo users and RPS records seeded automatically

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:5173`. API calls go to `http://localhost:8000`.

---

## Environment Variables Reference <a name="environment-variables"></a>

Copy `.env.example` → `.env`. Never commit `.env`.

### Required

| Variable | Example | Notes |
|----------|---------|-------|
| `GEMINI_API_KEY` | `AIza...` | Leave blank for mock mode (no Gemini calls) |
| `JWT_SECRET` | `change-me-in-prod` | **Must be changed** in production |

### Gemini

| Variable | Default | Notes |
|----------|---------|-------|
| `GEMINI_MODEL` | `gemini-2.5-flash-preview-04-17` | Best for document vision. Options: `gemini-2.0-flash`, `gemini-1.5-pro` |
| `GEMINI_STRICT` | `false` | `true` = fail on Gemini errors instead of fallback to mock |

### Database

| Variable | Default | Notes |
|----------|---------|-------|
| `DATABASE_URL` | `sqlite:///./iasw.db` | Local dev. Docker overrides with PostgreSQL URL. |

### Auth

| Variable | Default | Notes |
|----------|---------|-------|
| `JWT_EXPIRE_MINUTES` | `60` | Token TTL in minutes |
| `SEED_ADMIN_USERNAME` | `admin` | Default admin username |
| `SEED_ADMIN_PASSWORD` | `admin123` | **Change in production** |
| `SEED_USER_USERNAME` | `user` | Default user username |
| `SEED_USER_PASSWORD` | `user123` | **Change in production** |

### Thresholds

| Variable | Default | Notes |
|----------|---------|-------|
| `APPROVE_THRESHOLD` | `0.80` | Overall confidence ≥ this → APPROVE |
| `FLAG_THRESHOLD` | `0.60` | Confidence between FLAG and APPROVE → FLAG (human review) |
| `RATE_LIMIT` | `10/minute` | Intake submissions per IP per minute |

---

## Kubernetes — Production Deployment <a name="kubernetes"></a>

Kubernetes manifests are in the `k8s/` directory, targeting GKE (adaptable to EKS / AKS).

### Manifests

| File | Resource |
|------|---------|
| `k8s/namespace.yaml` | `iasw` namespace |
| `k8s/secrets.yaml` | Database passwords, JWT secret, Gemini key |
| `k8s/configmap.yaml` | Non-secret configuration |
| `k8s/postgres.yaml` | PostgreSQL StatefulSet + Service |
| `k8s/redis.yaml` | Redis Deployment + Service |
| `k8s/backend.yaml` | FastAPI Deployment + Service |
| `k8s/frontend.yaml` | React/nginx Deployment + Service |
| `k8s/ingress.yaml` | Ingress (HTTPS, path-based routing) |

### Deploy to Kubernetes

```bash
# 1. Set your registry and image tags in k8s/*.yaml
# 2. Build and push images
docker build -t your-registry/iasw-backend:latest .
docker push your-registry/iasw-backend:latest

docker build -f k8s/frontend.Dockerfile -t your-registry/iasw-frontend:latest ./frontend
docker push your-registry/iasw-frontend:latest

# 3. Update secrets (base64 encoded)
echo -n "your-jwt-secret" | base64
echo -n "your-gemini-key" | base64
# Paste into k8s/secrets.yaml

# 4. Apply manifests in order
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/redis.yaml
kubectl apply -f k8s/backend.yaml
kubectl apply -f k8s/frontend.yaml
kubectl apply -f k8s/ingress.yaml

# 5. Verify
kubectl get pods -n iasw
kubectl get services -n iasw
```

---

## Database Operations <a name="database-operations"></a>

### View tables (Docker)

```bash
# Connect to PostgreSQL
docker compose exec db psql -U postgres -d iasw_db

# List all tables
\dt

# View RPS records (mock core banking)
SELECT customer_id, name, dob, address FROM rps_records;

# View pending change requests
SELECT id, customer_id, overall_status, ai_recommendation, confidence_name
FROM pending_requests
ORDER BY created_at DESC;

# View audit trail for a request
SELECT actor, action, detail, created_at
FROM audit_log
WHERE request_id = '<paste-request-id>'
ORDER BY created_at;

# View all users
SELECT username, role, active, created_at FROM users;

# View pending registrations
SELECT username, requested_role, status, created_at FROM user_registrations;
```

### Reset database (fresh start)

```bash
docker compose down -v    # removes postgres_data and redis_data volumes
docker compose up -d      # recreates with fresh seed data
```

### Backup database

```bash
docker compose exec db pg_dump -U postgres iasw_db > backup_$(date +%Y%m%d).sql
```

### Restore database

```bash
cat backup_20240429.sql | docker compose exec -T db psql -U postgres -d iasw_db
```

---

## Troubleshooting <a name="troubleshooting"></a>

### Backend won't start

```bash
# Check logs
docker compose logs backend --tail=50

# Common causes:
# - Missing .env file  → cp .env.example .env
# - DB not ready      → wait 15s, or check docker compose ps
# - Port 8000 in use  → lsof -i :8000 and kill the process
```

### "Container name already in use" error

```bash
docker stop iasw-frontend && docker rm iasw-frontend
# Then re-run docker compose up -d
```

### Frontend shows blank page / cannot reach API

1. Check backend is running: `curl http://localhost:8000/health`
2. Check CORS: `ALLOWED_ORIGINS` in `.env` must include `http://localhost:5173`
3. Check browser console for 401 — you may need to log in again (JWT expired)

### Gemini returning mock results

1. Check `GEMINI_API_KEY` is set in `.env`
2. Verify health endpoint: `curl http://localhost:8000/health` → `"llm_mode": "gemini"`
3. If still mock: check Docker picked up the new `.env` → `docker compose down && docker compose up -d`

### High false-positive rejection rate on valid documents

- The system uses `gemini-2.5-flash-preview-04-17` for document analysis
- Make sure the document is clearly photographed (not blurry, not a screenshot of a screenshot)
- Supported document types: Marriage Certificate, Gazette Notification, Deed Poll

### Check which Gemini model is active

```bash
docker compose logs backend | grep GEMINI_MODEL_SELECTED
```

### Redis not connecting (graceful degradation)

Redis is optional. If unavailable, the checker queue won't be cached but everything else works. Check:
```bash
docker compose ps redis
docker compose exec redis redis-cli ping   # should return PONG
```
