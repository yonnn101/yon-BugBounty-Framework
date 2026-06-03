# yonnn

Unified bug bounty / attack-surface framework.

**Security:** defaults, env files, and what to do if secrets hit Git — see [SECURITY.md](SECURITY.md). Optional: `scripts/check-tracked-secrets.ps1` before commits.

## Run everything with Docker

From the repo root:

```bash
docker compose up --build -d
```

- **Web UI:** http://localhost:8080 — React app; JSON API is under **`/api/*`** (nginx proxies that to the API) so **`/programs`** in the browser stays the SPA (reload-safe).  
- **API (direct):** http://localhost:8000 — e.g. `GET http://localhost:8000/api/programs` (auth required); OpenAPI at http://localhost:8000/docs  
- **Postgres:** `localhost:15432` → container `5432` (high host port avoids common Windows reserved ranges near 5432)  
  - If Docker still reports **bind / access permissions** on `15432`, run **Admin PowerShell:** `netsh interface ipv4 show excludedportrange protocol=tcp` and change the host side in `docker-compose.yml` (e.g. `30432:5432`) to a port **outside** those ranges; update `.env` / `DATABASE_URL` to match.  
- **Redis:** `localhost:6379`  
- **MinIO API:** `localhost:9000` — console: http://localhost:9001  

On first start the API container runs `alembic upgrade head`, then starts Uvicorn. The **`worker`** service (Celery, queues `fast` + `slow`) starts with the same `docker compose up` so background jobs do not sit **PENDING** forever.

**Rebuild after code changes:**

```bash
docker compose build api --no-cache
docker compose up -d api
```

Use `.gitattributes` so `scripts/docker-entrypoint.sh` stays LF on Windows; otherwise the entrypoint may fail.

## Web UI (React)

The **`frontend/`** app covers **register, login, programs (CRUD), asset ingest, graph view**, and a superuser **admin ping** button.

**With Docker** (recommended full stack): `docker compose up --build -d` includes the **`frontend`** service. Open **http://localhost:8080** — nginx serves the built SPA and reverse-proxies API paths to **`api:8000`** (same-origin; no extra CORS setup).

**Local dev:**

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. Vite **proxies** `/api` (and `/health`, `/docs`, OpenAPI) to **http://127.0.0.1:8000**; the client defaults to **`/api`** for fetch URLs.

- **Custom API URL (static build / CDN):** set `VITE_API_BASE_URL` in `frontend/.env` and configure **`CORS_ORIGINS`** on the API.

```bash
cd frontend
npm run build   # or: docker compose build frontend
```

## Quick start (local Python)

1. Copy `.env.example` to `.env` and adjust values.
2. Start infra: `docker compose up -d` (Postgres, Redis, MinIO).
3. Install deps (Python 3.11+): `pip install -r requirements.txt` or `uv pip install -r requirements.txt`.
4. Run migrations (requires `DATABASE_URL`):

   ```bash
   set DATABASE_URL=postgresql+asyncpg://yonnn_user:yonnn_password@localhost:15432/yonnn_db
   alembic upgrade head
   ```

5. API:

   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

6. Celery worker (optional): consume both **fast** and **slow** queues (DNS/light work vs heavy scans).

   ```bash
   celery -A workers.celery_app worker -Q fast,slow --loglevel=info
   ```

   Smoke test (with Redis, Postgres, and worker running): `celery -A workers.celery_app call yonnn.debug.ping` — expect result `"pong"` after the task runs on **fast**.

## Authentication (JWT)

1. **Register:** `POST /api/auth/register` with JSON `{ "email", "password", "full_name?" }`.
2. **Token:** `POST /api/auth/token` (OAuth2 form) — `username` = email, `password` = password. Returns `access_token` in JSON (Postman, scripts, OpenAPI).
3. **Cookie (browser UI):** the same response also sets an **httpOnly** cookie (name from **`ACCESS_TOKEN_COOKIE_NAME`**, default `access_token`) with lifetime aligned to **`ACCESS_TOKEN_EXPIRE_MINUTES`**. The React app uses **`credentials: 'include'`** and does **not** store the JWT in `localStorage`.
4. **Use API:** send **`Authorization: Bearer <access_token>`** *or* send the auth cookie — protected routes accept either (Bearer wins if both are present).
5. **Logout (cookie clients):** `POST /api/auth/logout` clears the cookie.
6. **Profile:** `GET /api/auth/me` with Bearer or cookie.

Set **`JWT_SECRET_KEY`** in production (see `.env.example`). Use **`COOKIE_SECURE=true`** when the API is only served over HTTPS so browsers send the cookie on TLS only.

### Superuser bootstrap

Optional env vars (see `.env.example`):

- **`SUPERUSER_EMAIL`** — on API startup, if set together with a password, this account is **created** (if missing) or **promoted** to `is_superuser=true`.
- **`SUPERUSER_PASSWORD`** — must be at least **8 characters** (same minimum as registration).

Use `GET /api/auth/me` to confirm `is_superuser`. Superuser-only routes live under **`/api/admin/*`** (e.g. `GET /api/admin/ping` with Bearer token).

Public without auth: `/health`, `/api/auth/register`, `/api/auth/token`, `/docs`, `/openapi.json`. (`/api/auth/logout` is safe unauthenticated — it only clears the cookie.)

## API highlights

- **Programs and assets are per-user:** each program has an `owner_id`. Listing, read, update, delete, graph, and asset ingest require a JWT for that owner. Another account receives **404** for the same `program_id`.
- **422 validation errors** redact common secret fields (`password`, `access_token`, etc.) so failed requests don’t echo credentials. Do not put real secrets in `settings` / scope JSON — those fields are returned on program reads as you stored them.
- `POST /api/programs` — create program (scope container) **(auth)**; you become the owner.
- `GET /api/programs/{id}/graph` — nodes (assets) and edges (relations) **(auth, owner only)**.
- `POST /api/programs/{id}/assets` — get-or-create asset + optional parent relation **(auth, owner only)**.
- `GET /api/admin/ping` — sanity check for superuser JWT **(superuser only)**.

## Execution layer (`AsyncBaseTool`)

External tools (Subfinder, Nuclei, …) subclass **`core.base_tool.AsyncBaseTool`**: async subprocess via `asyncio.create_subprocess_exec`, separate stdout/stderr, default **10 minute** timeout (process killed on expiry), `save_raw_output()` under **`storage/raw_outputs/`** (MinIO later), and abstract **`parse_output()`** for tool-specific parsing. Alias: **`BaseTool`**.

### How to verify `AsyncBaseTool`

1. **Unit tests** (no DB required): from repo root run  
   `python -m unittest tests.test_base_tool -v`  
   You should see **7 tests OK** (success, non-zero exit, timeout kill, save file, invalid filename, `run_and_parse`, empty parse on failure).

2. **Manual smoke test** in a Python REPL from repo root:
   ```python
   import asyncio, sys
   from core.base_tool import AsyncBaseTool
   from typing import Any

   class Demo(AsyncBaseTool):
       tool_name = "demo"
       def parse_output(self, s: str) -> list[dict[str, Any]]:
           return [{"line": x} for x in s.splitlines() if x.strip()]

   async def main():
       t = Demo(binary_path=sys.executable, output_directory="storage/raw_outputs")
       r = await t.run_subprocess(["-c", "print('ok')"])
       assert r.exit_code == 0 and "ok" in r.stdout
       path = t.save_raw_output(r.stdout, "demo_stdout.txt")
       print(path, path.read_text())

   asyncio.run(main())
   ```
   Confirm **`storage/raw_outputs/demo_stdout.txt`** exists and contains `ok`.

3. **Logging:** Run the tests or REPL with loguru default sink; check logs for the exact **Executing:** line (full argv).

## Layout

| Path | Role |
|------|------|
| `api/` | FastAPI routes |
| `frontend/` | React (Vite) UI |
| `core/` | DB, config, BaseTool |
| `models/` | SQLAlchemy ORM |
| `schemas/` | Pydantic DTOs |
| `services/` | Business logic |
| `workers/` | Celery tasks |
