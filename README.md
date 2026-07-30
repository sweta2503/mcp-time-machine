# MCP Time Machine — Fraud Alert Investigator

Live migration demo from MCP **2025-11-25** (stateful) → **2026-07-28** (stateless).  
Each git branch = one important segment. Same server, same app — one protocol change at a time.

## What this repo covers

| Feature | Where shown |
|---|---|
| Stateful baseline (initialize / Mcp-Session-Id) | `01-legacy` |
| Session affinity failure under round-robin | `02-break-it` |
| SDK bump without protocol change | `03-new-sdk-old-behavior` |
| `server/discover`, `_meta`, `clientCapabilities` | `04-discover` |
| Stateless horizontal scale (kill a node mid-demo) | `05-stateless-scale` |
| Explicit workflow handles (no hidden state) | `06-workflow-handle` |
| MCP Tasks — `resultType:"task"`, `tasks/get` | `07-tasks` |
| MRTR — `resultType:"input_required"`, human approval | `08-mrtr` |
| nginx header routing (`Mcp-Method` / `Mcp-Name`) | `09-headers` |
| Old + new client, same server, side-by-side | `10-finale` |
| Redis-backed task store — restart durability, cancel | `11-redis-tasks` |
| Locust benchmark — old vs. new protocol, live error rate | `12-benchmark` |

---

## Quick start (any branch)

```bash
git checkout <branch>
cd server
pip install -r requirements.txt
python server.py          # runs on :8000

# separate terminal
python client/old_client.py   # or new_client.py
```

Branches that use Docker Compose (02, 05, 09, 10, 11, 12):

```bash
cd server
docker-compose up --build
```

---

## Segment-by-segment run instructions

### `01-legacy` — Stateful baseline

```bash
git checkout 01-legacy
cd server && python server.py
python client/old_client.py
```

**What to show:** Full `initialize` → `notifications/initialized` → `tools/list` → `tools/call` flow.
Session ID echoed on every request. All 4 tools work. Single instance, no drama.

---

### `02-break-it` — The cold-open hook

```bash
git checkout 02-break-it
cd server && docker-compose up --build
python client/old_client.py
```

**What to show:** `initialize` lands on instance A, next request lands on instance B.
Watch `✗✗✗ SESSION FAILURE` in the terminal. This is the hook — run it first.

> Round-robin is not guaranteed to interleave on exactly 2 requests — rerun if both land on the same instance.

---

### `03-new-sdk-old-behavior` — SDK bump, zero code change

```bash
git checkout 03-new-sdk-old-behavior
git diff 02-break-it -- server/requirements.txt   # only version numbers changed
cd server && python server.py
python client/old_client.py
```

**What to show:** `git diff` reveals only `requirements.txt` changed. Old client, old protocol,
new SDK — works unmodified. The deprecation window is real.

---

### `04-discover` — `initialize` out, `server/discover` in

```bash
git checkout 04-discover
git diff 03-new-sdk-old-behavior -- server/server.py
cd server && python server.py
python client/new_client.py
python client/old_client.py   # still works via compat shim
```

**What to show:** `server/discover` returns `resultType:"complete"`, `supportedVersions`,
`capabilities` (including `extensions`), and `_meta.io.modelcontextprotocol/serverInfo`.  
Every request carries `_meta` with `io.modelcontextprotocol/clientCapabilities`.  
Old client hits the compat shim — still gets a session id, sees a `deprecation` field.

---

### `05-stateless-scale` — Kill a server mid-demo

```bash
git checkout 05-stateless-scale
cd server && docker-compose up --build
python client/new_client.py   # start calling tools in terminal 2
docker-compose stop server_2  # terminal 3 — mid-demo kill
# terminal 2 continues — zero errors
```

**What to show:** Requests keep succeeding after `server_2` dies. No session state to lose.

---

### `06-workflow-handle` — Explicit handle, no hidden state

```bash
git checkout 06-workflow-handle
cd server && python server.py
python client/new_client.py
```

**What to show:** `flag_account` first call returns a `workflowHandle` (self-contained base64 blob).
Second call decodes the handle server-side to recover `account_id`/`reason` — no session lookup.

---

### `07-tasks` — Fire and forget the deep scan

```bash
git checkout 07-tasks
cd server && python server.py
python client/new_client.py
```

**What to show:** `deep_scan_account_history` returns `{resultType:"task", taskId, status:"working",
createdAt, lastUpdatedAt, ttlMs, pollIntervalMs}` in ~50ms. Client polls `tasks/get` every second.
After 4 s status flips to `completed` and the result arrives.

---

### `08-mrtr` — Human approval on camera

```bash
git checkout 08-mrtr
cd server && python server.py
python client/new_client.py
```

**What to show:** `flag_account` returns `resultType:"input_required"` with `inputRequests` map
(each entry is an `elicitation/create` call). `new_client.py` prints the message and waits for
`y/N`. Type `y` live. Client resumes with `inputResponses` map + `requestState`. Server decodes
state, commits the flag. Run again with `N` to show the decline path.

---

### `09-headers` — nginx routes without touching the JSON body

```bash
git checkout 09-headers
cd server && docker-compose up --build
python client/new_client.py
```

Show `infra/nginx-roundrobin.conf` — highlight `$http_mcp_name` routing and token auth.

```bash
# flag_account without X-Analyst-Token → 403 from nginx
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Method: tools/call" \
  -H "Mcp-Name: flag_account" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"flag_account","arguments":{"account_id":"ACC123","reason":"test"},"_meta":{}}}'

# flag_account with token → passes nginx
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Method: tools/call" \
  -H "Mcp-Name: flag_account" \
  -H "X-Analyst-Token: demo-token" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"flag_account","arguments":{"account_id":"ACC123","reason":"test"},"_meta":{}}}'
```

---

### `10-finale` — Old + new client, same server

```bash
git checkout 10-finale
cd server && docker-compose up --build
```

Split terminal. Left = old protocol, right = new protocol.

```bash
python client/old_client.py    # left pane (2025-11-25)
python client/new_client.py    # right pane (2026-07-28)
```

**What to show:** Both clients complete against the same live deployment. Server logs show
`initialize` on the left, `server/discover` on the right. No forced cutover.

---

### `11-redis-tasks` — Task durability + cancel

```bash
git checkout 11-redis-tasks
cd server && docker-compose up --build   # starts Redis + 3 server instances
python client/new_client.py
```

**What to show:**
1. `deep_scan_account_history` returns a task; result stored in Redis.
2. Client auto-restarts all 3 server instances mid-demo — task result survives.
3. Cancel demo: starts a second task and calls `tasks/cancel` immediately.
   `tasks/get` confirms `status: "cancelled"`.

> **Note:** The restart demo proves *result durability* — completed results survive restarts.
> True in-flight recovery of a task that was still running requires a separate worker process
> and a durable queue (Celery/RQ/etc.), which is outside this demo's scope.

---

### `12-benchmark` — Locust benchmark, live error rate

```bash
git checkout 12-benchmark
cd server && docker-compose up --build
# separate terminal:
cd benchmark
pip install locust
locust --headless -u 20 -r 5 -t 30s --host http://localhost:8001  # old protocol
locust --headless -u 20 -r 5 -t 30s --host http://localhost:8000  # new protocol
```

Or run both user classes together:

```bash
locust --headless -u 40 -r 10 -t 30s
```

**What to show:** `OldProtocolUser` hits port 8001 (3 strict-session instances behind nginx).
`NewProtocolUser` hits port 8000 (3 stateless Redis instances). Old protocol error rate: ~33%.
New protocol error rate: 0%.

---

## Repo layout

```
mcp-time-machine/
  server/
    server.py           # evolves across branches
    data.py             # shared fixture data (PaySim-flavoured, hardcoded)
    Dockerfile
    docker-compose.yml  # evolves: 1 → 2 → 3 instances → Redis → 6 instances (benchmark)
    requirements.txt    # bumped in branch 03; redis added in branch 11
  client/
    old_client.py       # speaks 2025-11-25 (initialize, Mcp-Session-Id)
    new_client.py       # speaks 2026-07-28 (server/discover, _meta, Tasks, MRTR)
  infra/
    nginx-roundrobin.conf   # plain round-robin (02) → header routing (09)
  benchmark/
    locustfile.py           # OldProtocolUser vs NewProtocolUser (branch 12)
    docker-compose.yml      # 6 instances + 2 nginx ports for side-by-side test
  README.md
```

---

## MCP 2026-07-28 protocol quick reference

| Feature | 2025-11-25 | 2026-07-28 |
|---|---|---|
| Session init | `initialize` / `notifications/initialized` | `server/discover` (optional) |
| Identity | `Mcp-Session-Id` header | `_meta.io.modelcontextprotocol/clientInfo` |
| Client capabilities | — | `_meta.io.modelcontextprotocol/clientCapabilities.extensions` |
| State | Per-session server-side store | Stateless; explicit handles if needed |
| List caching | None | `ttlMs` + `cacheScope: "private"` on list responses |
| Long-running ops | Blocking response | Tasks: `{resultType:"task", taskId, createdAt, …}` → `tasks/get` / `tasks/cancel` |
| Human approval | Not specified | MRTR: `{resultType:"input_required", inputRequests:{…}}` + `inputResponses:{…}` |
| Gateway routing | Parse JSON body | `Mcp-Method` + `Mcp-Name` headers |
| Transport | HTTP+SSE (deprecated) | Streamable HTTP |
