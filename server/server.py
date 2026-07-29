"""
MCP Fraud Alert Investigator
Branch: 11-redis-tasks — Redis-backed task store

Changes from 10-finale:
  • TASKS in-memory dict replaced with Redis (key task:{id}, 1h TTL)
  • Any instance can read or write any task — no per-instance state
  • New MCP method: tasks/cancel
    — sets status to "cancelled" in Redis
    — background coroutine checks the flag before writing output
  • Demonstrates task durability: restart all servers, task result survives
  • STRICT_SESSIONS env var (false by default): when true, validates
    Mcp-Session-Id against a per-instance in-memory store.
    Used by the benchmark branch (12) to show old-protocol failures
    under round-robin without changing any client code.
"""
import asyncio
import base64
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, ".")
from data import ACCOUNT_HISTORY, FLAGGED_ACCOUNTS, TRANSACTIONS

app = FastAPI(title="Fraud Alert Investigator — 11-redis-tasks")

# ── Redis ───────────────────────────────────────────────────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def task_set(task_id: str, data: dict, ttl: int = 3600):
    r = await get_redis()
    await r.set(f"task:{task_id}", json.dumps(data), ex=ttl)


async def task_get(task_id: str) -> dict | None:
    r = await get_redis()
    raw = await r.get(f"task:{task_id}")
    return json.loads(raw) if raw else None


# ── Strict-session mode (benchmark use) ────────────────────────────────────
# When STRICT_SESSIONS=true the server validates Mcp-Session-Id against a
# per-instance in-memory dict. Round-robin then breaks sessions exactly as
# it did in branch 01, letting the benchmark show the failure rate live.
STRICT_SESSIONS = os.environ.get("STRICT_SESSIONS", "false").lower() == "true"
LOCAL_SESSIONS: dict[str, str] = {}   # session_id → ISO timestamp, per-instance

TOOLS = [
    {
        "name": "lookup_transaction",
        "description": "Look up a single transaction by ID. Fast, read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "e.g. TXN001"}
            },
            "required": ["transaction_id"],
        },
    },
    {
        "name": "list_flagged_accounts",
        "description": "Return all currently flagged accounts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "flag_account",
        "description": "Flag an account for fraud review. Requires human approval (MRTR).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["account_id", "reason"],
        },
    },
    {
        "name": "deep_scan_account_history",
        "description": "Deep-scan all transactions for an account. Long-running — returns a task.",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"],
        },
    },
]

SERVER_INFO = {"name": "fraud-investigator", "version": "2.0.0"}
CAPABILITIES = {"tools": {"listChanged": False}, "resources": {}}


def ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def text_result(text: str, is_error: bool = False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


@app.post("/mcp")
async def mcp(request: Request):
    body   = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    proto_ver = request.headers.get("MCP-Protocol-Version", "unknown")
    meta      = params.get("_meta", {})
    client    = meta.get("io.modelcontextprotocol/clientInfo", {})

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    instance = os.environ.get("INSTANCE", "standalone")
    print(f"[{ts}] [{instance}] ← {method}  proto={proto_ver}  client={client.get('name', '?')}")

    # ── Strict session enforcement (old-protocol simulation) ────────────────
    if STRICT_SESSIONS and method in ("tools/list", "tools/call"):
        sid = request.headers.get("Mcp-Session-Id") or request.headers.get("mcp-session-id")
        if not sid or sid not in LOCAL_SESSIONS:
            print(f"  ✗ [{instance}] Session {sid!r} not found — returning error")
            return JSONResponse(content=rpc_err(req_id, -32600, f"Invalid session on {instance} — re-initialize"))

    # ── server/discover ──────────────────────────────────────────────────────
    if method == "server/discover":
        return JSONResponse(content=ok(req_id, {
            "resultType": "complete",
            "supportedVersions": ["2026-07-28", "2025-11-25"],
            "capabilities": CAPABILITIES,
            "_meta": {"serverInfo": SERVER_INFO},
        }))

    # ── initialize (old-client compat + strict-session mint) ────────────────
    if method == "initialize":
        sid = str(uuid.uuid4())
        if STRICT_SESSIONS:
            LOCAL_SESSIONS[sid] = datetime.now(timezone.utc).isoformat()
            print(f"  ⚑ [{instance}] Session minted: {sid[:8]}…")
        return JSONResponse(
            content=ok(req_id, {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "_meta": {"deprecation": "initialize is deprecated; use server/discover"},
            }),
            headers={"Mcp-Session-Id": sid},
        )

    if method == "notifications/initialized":
        from fastapi.responses import Response
        return Response(status_code=204)

    # ── tools/list ───────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse(content=ok(req_id, {
            "tools": TOOLS,
            "ttlMs": 300_000,
            "cacheScope": "private",
        }))

    # ── tasks/get ────────────────────────────────────────────────────────────
    if method == "tasks/get":
        task_id = params.get("taskId", "")
        task = await task_get(task_id)
        if not task:
            return JSONResponse(content=rpc_err(req_id, -32602, f"Task not found: {task_id}"))
        return JSONResponse(content=ok(req_id, task))

    # ── tasks/cancel ─────────────────────────────────────────────────────────
    if method == "tasks/cancel":
        task_id = params.get("taskId", "")
        task = await task_get(task_id)
        if not task:
            return JSONResponse(content=rpc_err(req_id, -32602, f"Task not found: {task_id}"))
        if task.get("status") != "working":
            return JSONResponse(content=rpc_err(
                req_id, -32602,
                f"Cannot cancel task with status={task['status']!r}"
            ))
        task["status"] = "cancelled"
        await task_set(task_id, task)
        print(f"  ✗ Task {task_id} cancelled")
        return JSONResponse(content=ok(req_id, {"cancelled": True, "taskId": task_id}))

    # ── tools/call ───────────────────────────────────────────────────────────
    if method == "tools/call":
        name   = params.get("name")
        args   = params.get("arguments", {})
        mcp_name_hdr = request.headers.get("Mcp-Name", "")
        if mcp_name_hdr and name and mcp_name_hdr != name:
            return JSONResponse(
                content=rpc_err(req_id, -32600, f"Mcp-Name header {mcp_name_hdr!r} doesn't match body name={name!r}"),
                status_code=400,
            )
        result = await dispatch_tool(req_id, name, args, params)
        return JSONResponse(content=result)

    return JSONResponse(
        content=rpc_err(req_id, -32601, f"Method not found: {method}"),
        status_code=404,
    )


async def dispatch_tool(req_id, name: str, args: dict, params: dict = {}):

    if name == "lookup_transaction":
        tid = args.get("transaction_id", "")
        txn = TRANSACTIONS.get(tid)
        if not txn:
            return ok(req_id, text_result(f"Transaction {tid!r} not found.", is_error=True))
        flag = "⚠ FLAGGED" if txn["flagged"] else "✓ clean"
        text = (
            f"Transaction {tid}\n"
            f"  Amount:   ${txn['amount']:,.2f}\n"
            f"  Merchant: {txn['merchant']}\n"
            f"  Account:  {txn['account_id']}\n"
            f"  Risk:     {txn['risk_score']:.0%}\n"
            f"  Status:   {flag}"
        )
        return ok(req_id, text_result(text))

    if name == "list_flagged_accounts":
        if not FLAGGED_ACCOUNTS:
            return ok(req_id, text_result("No accounts currently flagged."))
        lines = [
            f"  • {aid}: {info.get('holder', 'Unknown')} — {info['flag_reason']}"
            for aid, info in FLAGGED_ACCOUNTS.items()
        ]
        return ok(req_id, text_result("Flagged accounts:\n" + "\n".join(lines)))

    if name == "flag_account":
        aid    = args.get("account_id", "")
        reason = args.get("reason", "")
        input_responses = params.get("inputResponses")
        request_state   = params.get("requestState")

        if input_responses is None:
            # WARNING: requestState is not signed. In production, bind it to
            # the authenticated user with HMAC to prevent client tampering.
            state_payload = base64.urlsafe_b64encode(
                json.dumps({"account_id": aid, "reason": reason}).encode()
            ).decode()
            request_id = uuid.uuid4().hex[:8]
            print(f"  ↩  MRTR: requesting operator approval (requestId={request_id})")
            return ok(req_id, {
                "resultType": "input_required",
                "inputRequests": [{
                    "id": request_id,
                    "prompt": (
                        f"Flag account {aid}?\n"
                        f"Reason: {reason}\n"
                        f"This action is irreversible. Approve? [y/N]"
                    ),
                }],
                "requestState": state_payload,
                "content": [{"type": "text", "text": "Awaiting operator approval before flagging account."}],
            })

        try:
            recovered = json.loads(base64.urlsafe_b64decode(request_state).decode())
            aid    = recovered["account_id"]
            reason = recovered["reason"]
        except Exception:
            return rpc_err(req_id, -32602, "Invalid requestState")

        approval_response = input_responses[0] if input_responses else {}
        approved = str(approval_response.get("approved", "")).lower() in ("true", "yes", "y", "1")

        if not approved:
            return ok(req_id, text_result(f"Flag operation declined by operator. Account {aid} unchanged."))

        FLAGGED_ACCOUNTS[aid] = {
            "account_id": aid,
            "holder": "Unknown",
            "flag_reason": reason,
            "flagged_at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"  ⚑ Flagged {aid} after MRTR approval: {reason}")
        return ok(req_id, text_result(f"Account {aid} flagged after operator approval.\nReason: {reason}"))

    if name == "deep_scan_account_history":
        client_caps = params.get("_meta", {}).get("io.modelcontextprotocol/capabilities", {})
        supports_tasks = "io.modelcontextprotocol/tasks" in client_caps
        aid     = args.get("account_id", "")
        task_id = f"task_{uuid.uuid4().hex[:12]}"

        now = datetime.now(timezone.utc).isoformat()
        await task_set(task_id, {
            "status": "working",
            "startedAt": now,
            "ttlMs": 300_000,
            "pollIntervalMs": 1_000,
        })
        asyncio.create_task(_run_deep_scan(task_id, aid))

        print(f"  ✓ Task {task_id} started (Redis-backed)")
        return ok(req_id, {
            "resultType": "task",
            "taskId": task_id,
            "content": [{"type": "text", "text": f"Scan started. Poll tasks/get with taskId={task_id}"}],
        })

    return rpc_err(req_id, -32601, f"Unknown tool: {name}")


async def _run_deep_scan(task_id: str, account_id: str):
    """Background coroutine — writes result to Redis so any instance can serve it."""
    await asyncio.sleep(4)

    # Respect cancellation — check Redis before writing result
    task = await task_get(task_id)
    if task and task.get("status") == "cancelled":
        print(f"  ✗ Task {task_id} was cancelled — discarding result")
        return

    history = ACCOUNT_HISTORY.get(account_id, [])
    total   = sum(t["amount"] for t in history)
    text = (
        f"Deep scan complete — {account_id}\n"
        f"  Transactions: {len(history)}\n"
        f"  Total volume: ${total:,.2f}\n"
        f"  Verdict: {'HIGH RISK — escalate immediately' if total > 10_000 else 'Low risk'}"
    )
    await task_set(task_id, {
        "status": "completed",
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "result": text_result(text),
    })
    print(f"  ✓ Task {task_id} completed — result written to Redis")


if __name__ == "__main__":
    port     = int(os.getenv("PORT", 8000))
    instance = os.getenv("INSTANCE", "standalone")
    mode     = "STRICT-SESSIONS" if STRICT_SESSIONS else "stateless"
    print(f"Fraud Alert Investigator (11-redis-tasks) [{instance}] — port {port}  [{mode}]")
    print(f"Redis: {REDIS_URL}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
