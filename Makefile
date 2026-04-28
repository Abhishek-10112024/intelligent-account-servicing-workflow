# ── IASW Development Shortcuts ────────────────────────────────────────────────
# Usage: make <target>
# Requires: Docker Desktop running, .env configured

.PHONY: up down restart reset backup restore logs ps shell-db shell-backend

# ── Start / Stop ──────────────────────────────────────────────────────────────

up:                          ## Start all services (data preserved)
	docker compose up -d

down:                        ## Stop all services — DATA IS PRESERVED
	docker compose down

restart:                     ## Restart backend only (after code changes)
	docker compose build --no-cache backend
	docker compose stop backend
	docker compose rm -f backend
	docker compose up -d backend

reset:                       ## ⚠️  WIPES ALL DATA and restarts fresh
	@echo "⚠️  This will delete ALL submitted requests, audit logs, and registrations."
	@echo "    RPS records and users will be re-seeded from config."
	@read -p "Are you sure? (yes/no): " confirm && [ "$$confirm" = "yes" ]
	docker compose down -v
	docker compose up -d

# ── Backup / Restore ──────────────────────────────────────────────────────────

backup:                      ## Dump the database to a timestamped SQL file
	@mkdir -p backups
	docker compose exec db pg_dump -U postgres iasw_db > backups/iasw_$(shell date +%Y%m%d_%H%M%S).sql
	@echo "✅ Backup saved to backups/iasw_$(shell date +%Y%m%d_%H%M%S).sql"

restore:                     ## Restore from latest backup (usage: make restore FILE=backups/iasw_xxx.sql)
	@[ -n "$(FILE)" ] || (echo "❌ Usage: make restore FILE=backups/iasw_xxx.sql" && exit 1)
	cat $(FILE) | docker compose exec -T db psql -U postgres -d iasw_db
	@echo "✅ Restored from $(FILE)"

# ── Observability ─────────────────────────────────────────────────────────────

logs:                        ## Follow backend logs in real time
	docker compose logs backend -f

ps:                          ## Show container status
	docker compose ps

# ── Database ──────────────────────────────────────────────────────────────────

shell-db:                    ## Open a psql shell
	docker compose exec db psql -U postgres -d iasw_db

shell-backend:               ## Open a bash shell inside the backend container
	docker compose exec backend bash

# ── Quick DB queries ──────────────────────────────────────────────────────────

db-requests:                 ## Show all pending change requests
	docker compose exec db psql -U postgres -d iasw_db -c \
	  "SELECT id, customer_id, overall_status, ai_recommendation, created_at FROM pending_requests ORDER BY created_at DESC;"

db-audit:                    ## Show full audit trail
	docker compose exec db psql -U postgres -d iasw_db -c \
	  "SELECT request_id, actor, action, created_at FROM audit_log ORDER BY created_at DESC LIMIT 50;"

db-users:                    ## Show all users
	docker compose exec db psql -U postgres -d iasw_db -c \
	  "SELECT username, role, active, created_at FROM users;"

db-rps:                      ## Show RPS records
	docker compose exec db psql -U postgres -d iasw_db -c \
	  "SELECT customer_id, name, dob FROM rps_records ORDER BY customer_id;"

db-registrations:            ## Show pending registration requests
	docker compose exec db psql -U postgres -d iasw_db -c \
	  "SELECT username, requested_role, status, created_at FROM user_registrations ORDER BY created_at DESC;"

help:                        ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
