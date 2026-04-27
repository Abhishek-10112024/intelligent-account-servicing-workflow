# Intelligent Account Servicing Workflow (IASW)

> **AI Product Engineer — Technical Assignment**
> End-to-end agentic AI system for automated bank account change verification with mandatory Human-in-the-Loop (HITL) Checker approval.

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────┐
│          React Frontend  (dev: 5173 · prod Docker: 3000) │
│  Staff Intake Form ──────────── Checker Review Dashboard │
└───────────────────┬─────────────────────┬───────────────┘
                    │ HTTP/REST            │ HTTP/REST
┌───────────────────▼─────────────────────▼───────────────┐
│           FastAPI Backend  (Uvicorn · port 8000)          │
│                                                           │
│  POST /api/intake  →  202 Accepted  →  task_id           │
│  GET  /api/tasks/{id}  (poll for status)                  │
│  GET  /api/checker/queue  (Redis-cached, TTL 30s)         │
│  POST /api/checker/decide                                 │
│  POST /api/rps/write  (HITL gated)                        │
│  GET  /health                                             │
├───────────────────────────────────────────────────────────┤
│           LangGraph Pipeline  (asyncio thread pool)        │
│                                                           │
│  [Agent 1]              [Agent 2]            [Agent 3]    │
│  Validation Agent  →  Doc Processor  →  Confidence Scorer │
│  RPS cross-check      Gemini OCR/NLP    Fuzzy match + AI  │
│                             │                             │
│                      ═══ HITL BOUNDARY ═══               │
│                             │                             │
│                    [Checker Review UI]                    │
│                     Human Approve/Reject                  │
│                             │                             │
│                     [Mock RPS Write]                      │
└──────────┬────────────────────────────────────┬──────────┘
           │                                    │
┌──────────▼──────────┐              ┌──────────▼──────────┐
│   PostgreSQL         │              │   Redis              │
│   PendingRequest     │              │   Checker queue cache│
│   AuditLog           │              │   TTL = 30s          │
└─────────────────────┘              └─────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Backend |
| Node.js | **20+** | React frontend (Vite 8 requires Node 20.19+) |
| Docker Desktop | Latest | Full stack (recommended) |
| Gemini API Key | Optional | Real OCR; mock mode works without it |

---

### Option A — Docker Compose (recommended)

Runs the full stack (Backend + PostgreSQL + Redis) in one command.

```bash
cd "Intelligent Account Servicing Workflow"

# 1. Configure environment
cp .env.example .env
# Edit .env — add GEMINI_API_KEY if you have one

# 2. Start backend stack (DB + Redis + FastAPI)
docker-compose up -d

# 3a. Frontend — Dev mode (hot reload, recommended for development)
cd frontend && npm install && npm run dev
# → http://localhost:5173

# 3b. Frontend — Production Docker (nginx, recommended for demos)
# Run from project root:
docker build -f k8s/frontend.Dockerfile -t iasw-frontend:latest ./frontend
docker run -d --name iasw-frontend -p 3000:80 iasw-frontend:latest
# → http://localhost:3000
```

| Service | Dev URL | Prod Docker URL |
|---------|---------|----------------|
| **React Frontend** | http://localhost:5173 | http://localhost:3000 |
| **FastAPI Backend** | http://localhost:8000 | http://localhost:8000 |
| **API Docs** | http://localhost:8000/docs | http://localhost:8000/docs |
| **Health Check** | http://localhost:8000/health | http://localhost:8000/health |

**To stop:**
```bash
docker-compose down                                     # stop backend stack
docker stop iasw-frontend && docker rm iasw-frontend    # stop frontend (if Docker)
# Ctrl+C in terminal                                    # stop npm run dev
```

---

### Option B — Local Development

```bash
cd "Intelligent Account Servicing Workflow"

# 1. Python virtual environment
python3 -m venv venv
source venv/bin/activate          # macOS/Linux
# OR: venv\Scripts\activate       # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — add GEMINI_API_KEY (optional)

# 4. Start backend
uvicorn app.main:app --reload --port 8000

# 5. Start frontend (separate terminal)
cd frontend
npm install
npm run dev
```

> **Note:** Local dev uses SQLite by default. Redis is optional — if unavailable, the system runs without caching (logs a warning but continues normally).

---

## 🎬 Demo Walkthrough

### Step 1 — Staff submits a change request

1. Open `http://localhost:5173` (dev) or `http://localhost:3000` (Docker prod)
2. Use one of the valid test customers:

| Customer ID | Current Name | Use as "Old Name" |
|-------------|-------------|-------------------|
| `C001` | Priya Sharma | Legal Name Change |
| `C002` | Rahul Verma | Legal Name Change |
| `C003` | Meena Iyer | Legal Name Change |

3. Upload any image (JPG/PNG) or PDF as the supporting document
4. Click **Submit to AI Document Processor**
5. Watch the live progress bar: `QUEUED → RUNNING → COMPLETED`

### Step 2 — Checker reviews and decides

1. Open `http://localhost:5173/checker` or `http://localhost:3000/checker` (or click Checker tab)
2. The pending request appears in the queue with AI summary, confidence scores, and forgery check result
3. Enter Checker ID (e.g. `checker_sup_01`) and click **Approve** or **Reject**
4. The request moves to `APPROVED` / `REJECTED` and the mock RPS is updated

### Step 3 — Verify

```bash
# View current mock RPS state
curl http://localhost:8000/api/rps/state

# View audit trail for a specific request
curl http://localhost:8000/api/checker/audit/{request_id}
```

---

## 📁 Project Structure

```
.
├── app/
│   ├── main.py                    # FastAPI app + lifespan + rate limiter setup
│   ├── config.py                  # Settings (env vars, mock RPS seed data)
│   ├── database.py                # SQLAlchemy schema: PendingRequest + AuditLog
│   ├── models.py                  # Pydantic request/response schemas
│   │
│   ├── agents/
│   │   ├── graph.py               # LangGraph 4-node state machine
│   │   ├── validation_agent.py    # Agent 1: RPS customer + value cross-check
│   │   ├── document_processor.py  # Agent 2: Gemini multimodal OCR + forgery
│   │   └── confidence_scorer.py   # Agent 3: Fuzzy match + 5-priority scoring
│   │
│   ├── routers/
│   │   ├── intake.py              # POST /api/intake (202 async + duplicate guard)
│   │   ├── checker.py             # GET/POST /api/checker/* (Redis-cached queue)
│   │   └── rps.py                 # Mock RPS write + state viewer (HITL gated)
│   │
│   └── services/
│       ├── async_tasks.py         # Background task manager (thread pool executor)
│       ├── cache.py               # Redis cache manager (get/set/invalidate)
│       ├── rate_limiter.py        # SlowAPI limiter singleton
│       ├── retry_utils.py         # Exponential backoff + circuit breaker (Gemini)
│       ├── filenet_mock.py        # Document archival (local filesystem mock)
│       └── observability.py       # structlog setup + DB audit trail
│
├── frontend/                      # React 18 + Vite
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Intake.jsx         # Staff submission form + async polling
│   │   │   ├── Checker.jsx        # Checker queue + decision modal
│   │   │   └── Dashboard.jsx      # System overview stats
│   │   ├── App.jsx
│   │   └── index.css
│   ├── package.json
│   └── vite.config.js
│
├── k8s/
│   ├── frontend.yaml              # Kubernetes Deployment + Service for frontend
│   ├── frontend.Dockerfile        # Multi-stage build: Node 20 → nginx:alpine
│   └── nginx.conf                 # SPA routing + security headers + gzip
│
├── tests/
│   ├── test_intake.py
│   └── test_checker.py
│
├── logs/                          # Structured JSON logs (auto-created)
├── uploads/                       # Mock FileNet store (auto-created)
├── docker-compose.yml             # Backend + PostgreSQL + Redis
├── Dockerfile                     # Multi-stage Python backend image
├── requirements.txt
├── .env.example
├── DEPLOYMENT.md                  # Docker + k8s deployment guide
└── README.md
```

---

## ⚙️ Tech Stack

| Layer | Tool | Version | Why |
|-------|------|---------|-----|
| **Orchestration** | LangGraph | 1.1.9 | Graph-based state machine with typed `WorkflowState`, conditional routing, natural HITL pause point |
| **LLM / OCR** | Gemini 1.5 Flash | via `langchain-google-genai` | Multimodal (vision + text) in one API call. Falls back to mock mode if key absent |
| **Backend** | FastAPI + Uvicorn | 0.136.1 / 0.46.0 | Async Python, auto OpenAPI docs, dependency injection |
| **Database** | SQLite (dev) / PostgreSQL (prod) | SQLAlchemy 2.0 | Zero-setup locally; swap `DATABASE_URL` to migrate |
| **Frontend** | React 18 + Vite | — | Live polling UI, async progress bar, hot-reload dev |
| **Caching** | Redis | 5.0.1 | Checker queue cached with 30s TTL; auto-invalidated on decision |
| **Rate Limiting** | SlowAPI | 0.1.9 | 10 req/min per IP on `POST /api/intake` |
| **Resilience** | Tenacity | 8.2.3 | Exponential backoff + circuit breaker on all Gemini API calls |
| **Fuzzy Matching** | fuzzywuzzy + python-Levenshtein | 0.18.0 / 0.27.1 | Handles OCR artefacts (casing, spacing) in name comparison |
| **Observability** | structlog | 25.5.0 | Structured JSON logs; every agent step written to `AuditLog` table |
| **Containers** | Docker Compose + k8s | — | Full backend stack containerised; k8s manifest for frontend |

---

## 🔒 Security & HITL Enforcement

### Human-in-the-Loop — three independent enforcement layers

1. **Graph layer** — The LangGraph pipeline terminates at `stage_to_pending`. No RPS write node exists in the graph — the AI pipeline physically cannot trigger a write.
2. **API layer** — `POST /api/rps/write` checks `checker_decision` status in the DB before executing. No `checker_id` → no write.
3. **DB layer** — `checker_id IS NOT NULL` enforced before status can advance from `AI_VERIFIED_PENDING_HUMAN`.

### Duplicate submission guard

If a customer already has an open request of the same `change_type` awaiting Checker review, any new submission returns:

```json
HTTP 409 Conflict
{
  "detail": "Customer 'C001' already has an open LEGAL_NAME_CHANGE request
             (request_id: ...) awaiting Checker review.
             No new requests can be submitted until that request is approved or rejected."
}
```

The guard blocks on **`customer_id + change_type`** — requesting a different new name does not bypass it.

### Data privacy in validation errors

When an old-value mismatch is detected, the error message does **not** reveal the actual value on record:

```
✅ Safe:  "The current value submitted for customer 'C001' does not match what is on record."
❌ Unsafe: "Submitted 'Wrong Name' does not match RPS record 'Priya Sharma'."  ← old behaviour
```

The real value is logged internally in the audit trail for bank staff investigations only.

---

## 🌐 LLM Modes

| Mode | Trigger | Behaviour |
|------|---------|-----------|
| **Gemini (real)** | `GEMINI_API_KEY` set in `.env` | Calls Gemini 1.5 Flash multimodal API for real document OCR + forgery detection |
| **Mock mode** | `GEMINI_API_KEY` blank | Returns realistic hardcoded extraction results — full flow works without internet |
| **Fallback** | Key set but API call fails | Logs `LLM_FALLBACK_TO_MOCK` warning, degrades gracefully to mock |
| **Strict mode** | `GEMINI_STRICT=true` | Disables fallback — pipeline errors instead of silently degrading (useful for demos) |

### Verifying Gemini is actually being called

```bash
# 1. Health check — shows current mode
curl http://localhost:8000/health
# → {"llm_mode": "gemini", ...}

# 2. Gemini self-test
curl http://localhost:8000/api/llm/self-test
# → {"ok": true, "mode": "gemini", "model": "gemini-1.5-flash"}

# 3. Confirm no fallback in logs
grep "LLM_FALLBACK_TO_MOCK" logs/iasw.log   # should return nothing
grep "GEMINI_EXTRACTION_SUCCESS" logs/iasw.log  # should have entries
```

---

## 📊 Confidence Scoring Logic

The confidence scorer uses a **5-priority decision chain**:

```
1. forgery_check == FAIL     → REJECT  (highest priority)
2. forgery_check == WARN     → FLAG    (force human review)
3. document_type mismatch    → REJECT
4. missing required fields   → REJECT
5. threshold-based scoring   → APPROVE (≥0.80) / FLAG (0.60–0.79) / REJECT (<0.60)
```

**Score formula:**
```
overall = (name_match × 0.6) + (authenticity × 0.4)

name_match    = fuzzywuzzy token_sort_ratio(extracted_name, new_value) / 100
authenticity  = Gemini self-reported extraction_confidence mapped to 0.0–1.0
                + penalty for WARN forgery (−0.16)
                + penalty for FAIL forgery (−0.55)
```

| Score | Recommendation |
|-------|---------------|
| ≥ 0.80 | **APPROVE** |
| 0.60 – 0.79 | **FLAG** (Checker must review carefully) |
| < 0.60 | **REJECT** |

The Checker always has final authority regardless of AI recommendation.

---

## 🔄 Async Pipeline Flow

```
POST /api/intake
    │
    ├── Duplicate guard (DB check: customer + change_type + AI_VERIFIED_PENDING_HUMAN)
    │   └── 409 Conflict if already pending
    │
    ├── File validation (size ≤ 20MB, non-empty)
    │
    ├── Write temp file
    │
    ├── Return 202 Accepted { task_id, poll_url }
    │
    └── asyncio.create_task → ThreadPoolExecutor
            │  (event loop stays free for poll requests)
            ▼
        run_iasw_pipeline()     ← synchronous, runs in worker thread
            │
            ├── Agent 1: Validation
            ├── Agent 2: Document Processing (Gemini)
            ├── Agent 3: Confidence Scoring
            └── Stage to DB → AI_VERIFIED_PENDING_HUMAN

GET /api/tasks/{task_id}       ← responds in <55ms while pipeline runs
    └── { status: QUEUED | RUNNING | COMPLETED | FAILED }
```

---

## 📋 Database Schema

### `pending_requests`

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID v4 |
| `customer_id` | TEXT | Bank customer identifier |
| `change_type` | TEXT | `LEGAL_NAME_CHANGE` \| `ADDRESS_CHANGE` \| `DOB_CORRECTION` \| `CONTACT_UPDATE` |
| `old_value` | TEXT | Current value as stored in RPS |
| `new_value` | TEXT | Requested new value |
| `extracted_value` | TEXT | AI-extracted value from document |
| `document_type` | TEXT | Declared document type |
| `filenet_ref_id` | TEXT | Mock FileNet archive reference |
| `confidence_name` | REAL | Name match score (0.0–1.0) |
| `confidence_authenticity` | REAL | Document authenticity score (0.0–1.0) |
| `forgery_check` | TEXT | `PASS` \| `WARN` \| `FAIL` |
| `ai_summary` | TEXT | Human-readable summary for Checker |
| `ai_recommendation` | TEXT | `APPROVE` \| `FLAG` \| `REJECT` |
| `overall_status` | TEXT | `AI_VERIFIED_PENDING_HUMAN` \| `APPROVED` \| `REJECTED` \| `VALIDATION_FAILED` |
| `checker_id` | TEXT | Mandatory before any status change |
| `checker_decision` | TEXT | `APPROVED` \| `REJECTED` |
| `checker_notes` | TEXT | Optional notes |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |
| `decided_at` | TIMESTAMP | |

### `audit_log`

Every agent step and checker decision is written here with `request_id`, `stage`, `actor`, `action`, and `metadata`.

---

## 🔍 Observability

| Signal | Location | How to access |
|--------|----------|---------------|
| Structured logs | `logs/iasw.log` | JSON, one line per agent step |
| Audit trail | `audit_log` DB table | `GET /api/checker/audit/{request_id}` |
| Health + cache | `/health` | `{"status":"ok","cache":"connected"}` |
| RPS state | `/api/rps/state` | Current mock RPS values for all customers |
| API docs | `/docs` | Swagger UI with all endpoints |

---

## 🧪 Running Tests

```bash
source venv/bin/activate
pytest tests/ -v
```

---

## 🌍 Deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for:
- Docker Compose production configuration
- Kubernetes manifests (`k8s/frontend.yaml`)
- Environment variable reference
- PostgreSQL migration from SQLite

---

## 🏦 Mock RPS Customers

| Customer ID | Name | Phone | Address |
|-------------|------|-------|---------|
| `C001` | Priya Sharma | 9876543210 | 12 MG Road, Mumbai |
| `C002` | Rahul Verma | 9123456789 | 45 Park Street, Delhi |
| `C003` | Meena Iyer | 9988776655 | 8 Anna Salai, Chennai |

---

*Stack: Python · FastAPI · LangGraph · Gemini 1.5 Flash · React 18 · PostgreSQL · Redis · Docker*
