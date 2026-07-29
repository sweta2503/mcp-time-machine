# MCP Time Machine — Fraud Alert Investigator

Live migration demo from MCP **2025-11-25** (stateful) → **2026-07-28** (stateless).
Each git branch/tag = one YouTube segment. Same server, same app, one step at a time.

## Quick start (any branch)

```bash
cd server
pip install -r requirements.txt
python server.py          # runs on :8000

# separate terminal
python client/old_client.py   # or new_client.py
```

---

## Segment-by-segment run instructions

### `01-legacy` — Stateful baseline

```bash
git checkout 01-legacy
cd server && python server.py
# new terminal:
python client/old_client.py
```

**What to show:** Full `initialize` → `notifications/initialized` → `tools/list` → `tools/call` flow.
Session ID echoed on every request. All 4 tools work. Single instance, no drama.

---

### `02-break-it` — The cold-open hook

```bash
git checkout 02-break-it
cd server && docker-compose up --build
# new terminal:
python client/old_client.py
```

**What to show:** `initialize` lands on instance A, next request lands on instance B.
Watch the terminal print: `✗✗✗ SESSION FAILURE`. This is the hook — run it first.

> If both requests accidentally land on the same instance, re-run. Round-robin is not
> guaranteed to interleave on exactly 2 requests — add a `--count 5` loop if needed.

---

### `03-new-sdk-old-behavior` — SDK bump, zero code change

```bash
git checkout 03-new-sdk-old-behavior
git diff 02-break-it -- server/requirements.txt   # show: only version numbers changed
cd server && python server.py
python client/old_client.py   # runs identically to branch 01
```

**What to show:** `git diff` reveals only `requirements.txt` changed. Old client,
old protocol, new SDK — works unmodified. Deprecation window is real.

---

### `04-discover` — `initialize` out, `server/discover` in

```bash
git checkout 04-discover
git diff 03-new-sdk-old-behavior -- server/server.py   # show the diff on camera
cd server && python server.py
# show new client:
python client/new_client.py
# then show old client still works (compat shim):
python client/old_client.py
```

**What to show:** `server/discover` response includes `ttlMs` + `cacheScope` on `tools/list`.
Every request carries `_meta` with `clientInfo` instead of `Mcp-Session-Id`.
Old client hits the compat shim — still gets a session id, but sees a `deprecation` field.

---

### `05-stateless-scale` — Kill a server mid-demo

```bash
git checkout 05-stateless-scale
cd server && docker-compose up --build
# terminal 2:
python client/new_client.py   # start calling tools
# terminal 3 (mid-demo):
docker-compose stop server_2
# terminal 2 continues — no errors
```

**What to show:** Requests keep succeeding after `server_2` dies. No session state to lose.
Show `docker-compose ps` before and after the kill.

---

### `06-workflow-handle` — Explicit handle, no hidden state

```bash
git checkout 06-workflow-handle
cd server && python server.py
python client/new_client.py
```

**What to show:** `flag_account` first call returns a `workflowHandle` in the response.
Copy-paste the handle into the second call (or let `new_client.py` do it automatically).
Server decodes the handle to recover the original arguments — no session lookup.

---

### `07-tasks` — Fire and forget the deep scan

```bash
git checkout 07-tasks
cd server && python server.py
python client/new_client.py
```

**What to show:** `deep_scan_account_history` returns `{resultType:"task", taskId:"…"}` in
~50ms. Client prints "… free to do other work …", then polls `tasks/get` every second.
After 4 seconds, status flips to `completed` and the result arrives.

---

### `08-mrtr` — Human approval on camera

```bash
git checkout 08-mrtr
cd server && python server.py
python client/new_client.py
```

**What to show:** `flag_account` returns `resultType:"input_required"` with a prompt.
`new_client.py` prints the prompt and waits for `y/N`. Type `y` live on camera.
Client resumes with `inputResponses + requestState`. Server decodes state, commits flag.
Type `N` on a second run to show the decline path.

---

### `09-headers` — nginx routes without touching the JSON body

```bash
git checkout 09-headers
cd server && docker-compose up --build
```

Open `infra/nginx-roundrobin.conf` on camera — point out `$http_mcp_name` and `$http_mcp_method`.

```bash
# deep_scan → heavy_servers (show in nginx logs)
python client/new_client.py

# flag_account without token → 403 from nginx
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Method: tools/call" \
  -H "Mcp-Name: flag_account" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"flag_account","arguments":{"account_id":"ACC123","reason":"test"},"_meta":{}}}'

# flag_account with token → passes nginx, reaches server
curl -s -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Method: tools/call" \
  -H "Mcp-Name: flag_account" \
  -H "X-Analyst-Token: demo-token-123" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"flag_account","arguments":{"account_id":"ACC123","reason":"test"},"_meta":{}}}'
```

---

### `10-finale` — Side by side in split panes

```bash
git checkout 10-finale
cd server && docker-compose up --build
```

Split your terminal (iTerm2 / tmux). Left pane = old protocol, right pane = new protocol.

```bash
# Left pane (2025-11-25):
python client/old_client.py

# Right pane (2026-07-28):
python client/new_client.py
```

**What to show:** Both clients complete successfully against the same live deployment.
Server logs show `initialize` on the left, `server/discover` on the right.
No forced cutover. The deprecation window works exactly as designed.

---

## Repo layout

```
mcp-time-machine/
  server/
    server.py          # evolves across branches
    data.py            # shared fixture data (PaySim-flavoured, hardcoded)
    Dockerfile
    docker-compose.yml # evolves: 1 → 2 → 3 instances
    requirements.txt   # bumped in branch 03
  client/
    old_client.py      # speaks 2025-11-25 (initialize, Mcp-Session-Id)
    new_client.py      # speaks 2026-07-28 (server/discover, _meta, MRTR, Tasks)
  infra/
    nginx-roundrobin.conf   # plain round-robin (02) → header routing (09)
  README.md
```

## MCP protocol reference

| Feature | 2025-11-25 | 2026-07-28 |
|---|---|---|
| Session init | `initialize` / `notifications/initialized` | `server/discover` (optional) |
| Identity | `Mcp-Session-Id` header | `_meta.io.modelcontextprotocol/clientInfo` |
| State | Per-session server-side store | Stateless; explicit handles if needed |
| List caching | None | `ttlMs` + `cacheScope` on list responses |
| Long-running ops | Blocking response | Tasks: `{resultType:"task", taskId}` + `tasks/get` |
| Human approval | Not specified | MRTR: `{resultType:"input_required"}` + `inputResponses` |
| Gateway routing | Parse JSON body | `Mcp-Method` + `Mcp-Name` headers |
| Transport | HTTP+SSE (deprecated) | Streamable HTTP |
