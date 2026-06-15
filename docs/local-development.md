# Run And Stop

These commands assume you are running from the repository root.

Repo root:

```bash
cd <repo-root>
```

## Start the backend

```bash
cd backend/llm-layer
cp .env.example .env
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Backend health check:

```bash
curl http://127.0.0.1:8010/health
```

## Start the UI

```bash
cd ui/mcp-test-console
cp .env.example .env
npm run dev
```

UI URL:

```text
http://localhost:4173
```

## Run both in separate terminals

Terminal 1:

```bash
cd backend/llm-layer
cp .env.example .env
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Terminal 2:

```bash
cd ui/mcp-test-console
cp .env.example .env
npm run dev
```

## Stop the backend

If it is running in the foreground, press:

```text
Ctrl+C
```

If you need to stop it by process name:

```bash
pkill -f 'uvicorn app.main:app --host 0.0.0.0 --port 8010'
```

## Stop the UI

If it is running in the foreground, press:

```text
Ctrl+C
```

If you need to stop it by process name:

```bash
pkill -f 'vite --host 0.0.0.0 --port 4173'
pkill -f 'node_modules/.bin/vite --host 0.0.0.0 --port 4173'
```

## Check whether they are running

```bash
ss -ltnp '( sport = :8010 or sport = :4173 )'
```

## Background start example

Backend:

```bash
cd backend/llm-layer
cp .env.example .env
nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 >/tmp/llm-layer.log 2>&1 </dev/null &
```

UI:

```bash
cd ui/mcp-test-console
cp .env.example .env
nohup npm run dev >/tmp/llm-layer-mcp-test-console.log 2>&1 </dev/null &
```

Logs:

```bash
tail -f /tmp/llm-layer.log
tail -f /tmp/llm-layer-mcp-test-console.log
```
