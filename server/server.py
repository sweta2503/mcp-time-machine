"""
MCP Fraud Alert Investigator
Branch: 09-headers  —  Mcp-Method / Mcp-Name header routing + toy auth

Changes from 08-mrtr:
  • nginx.conf now routes on Mcp-Method/Mcp-Name WITHOUT parsing JSON body
  • deep_scan → heavy_servers upstream; everything else → fraud_servers
  • flag_account requires X-Analyst-Token header (nginx rejects without it)
  • Server logs which headers it sees to make routing visible on camera
  • flag_account on first call (no inputResponses) returns:
        {resultType: "input_required",
         inputRequests: [{id, prompt}],
         requestState: <opaque blob>}
  • Client prompts the operator (you, on camera), then retries with:
        {inputResponses: [{id, approved: true/false}],
         requestState: <echoed blob>}
  • requestState is a signed opaque token; no server-side session needed.
    Server decodes it on the second call to recover the original arguments.
  • Natural fit: no real bank auto-flags an account without human sign-off.
"""
import asyncio
import base64
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, ".")
from data import ACCOUNT_HISTORY, FLAGGED_ACCOUNTS, TRANSACTIONS

app = FastAPI(title="Fraud Alert Investigator — 09-headers (2026-07-28)")

# ── Task store (per-instance; shows the protocol pattern) ──────────────────
# In production: replace with Redis or a lightweight shared DB.
TASKS: dict[str, dict] = {}   # task_id → {status, output | None}

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
        "description": "Deep-scan all transactions for an account. Long-running.",
        "inputSchema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"],
        },
    },
]

SERVER_INFO = {"name": "fraud-investigator", "version": "2.0.0"}
CAPABILITIES = {
    "tools": {"listChanged": False},
    "resources": {},
}


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

    # Read new headers (for logging; nginx uses them for routing)
    mcp_method = request.headers.get("Mcp-Method", "")
    mcp_name   = request.headers.get("Mcp-Name", "")
    proto_ver  = request.headers.get("MCP-Protocol-Version", "unknown")

    # _meta carries client identity instead of a session id
    meta   = params.get("_meta", {})
    client = meta.get("io.modelcontextprotocol/clientInfo", {})

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"\n[{ts}] ← {method}  proto={proto_ver}  client={client.get('name','?')}")
    if mcp_name:
        print(f"        Mcp-Method={mcp_method}  Mcp-Name={mcp_name}")

    # ── server/discover ────────────────────────────────────────────────────
    if method == "server/discover":
        print("  → responding with serverInfo + capabilities")
        return JSONResponse(content=ok(req_id, {
            "protocolVersion": "2026-07-28",
            "serverInfo": SERVER_INFO,
            "capabilities": CAPABILITIES,
        }))

    # ── initialize (old client compatibility — respond with deprecation notice) ──
    if method == "initialize":
        print("  ⚠  Old initialize received — returning 2025-11-25 response for compat")
        import uuid
        sid = str(uuid.uuid4())
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

    # ── tools/list ─────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse(content=ok(req_id, {
            "tools": TOOLS,
            "ttlMs": 300_000,       # new in 2026-07-28: client may cache for 5 min
            "cacheScope": "session",
        }))

    # ── tasks/get (io.modelcontextprotocol/tasks extension) ───────────────
    if method == "tasks/get":
        task_id = params.get("taskId", "")
        task    = TASKS.get(task_id)
        if not task:
            return JSONResponse(content=rpc_err(req_id, -32602, f"Task not found: {task_id}"))
        return JSONResponse(content=ok(req_id, task))

    # ── tools/call ─────────────────────────────────────────────────────────
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        result = await dispatch_tool(req_id, name, args)
        return JSONResponse(content=result)

    return JSONResponse(
        content=rpc_err(req_id, -32601, f"Method not found: {method}"),
        status_code=404,
    )


async def dispatch_tool(req_id, name: str, args: dict):
    import asyncio

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
            f"  • {aid}: {info['holder']} — {info['flag_reason']}"
            for aid, info in FLAGGED_ACCOUNTS.items()
        ]
        return ok(req_id, text_result("Flagged accounts:\n" + "\n".join(lines)))

    if name == "flag_account":
        aid           = args.get("account_id", "")
        reason        = args.get("reason", "")
        input_responses = params.get("inputResponses")   # present on second call
        request_state   = params.get("requestState")

        if input_responses is None:
            # ── First call: return InputRequiredResult ─────────────────────
            # Encode all context into requestState so no server-side storage needed
            state_payload = base64.urlsafe_b64encode(
                json.dumps({"account_id": aid, "reason": reason}).encode()
            ).decode()
            request_id = uuid.uuid4().hex[:8]
            print(f"  ↩  MRTR: requesting operator approval (requestId={request_id})")
            return ok(req_id, {
                "resultType": "input_required",
                "inputRequests": [
                    {
                        "id": request_id,
                        "prompt": (
                            f"Flag account {aid}?\n"
                            f"Reason: {reason}\n"
                            f"This action is irreversible. Approve? [y/N]"
                        ),
                    }
                ],
                "requestState": state_payload,
                "content": [{"type": "text", "text": "Awaiting operator approval before flagging account."}],
            })

        # ── Second call: client echoed requestState + inputResponses ───────
        # Recover original args from requestState (no session lookup needed)
        try:
            recovered = json.loads(base64.urlsafe_b64decode(request_state).decode())
            aid    = recovered["account_id"]
            reason = recovered["reason"]
        except Exception:
            return rpc_err(req_id, -32602, "Invalid requestState")

        approval_response = input_responses[0] if input_responses else {}
        approved = str(approval_response.get("approved", "")).lower() in ("true", "yes", "y", "1")

        if not approved:
            print(f"  ✗ Operator declined to flag {aid}")
            return ok(req_id, text_result(f"Flag operation declined by operator. Account {aid} unchanged."))

        FLAGGED_ACCOUNTS[aid] = {
            "account_id": aid,
            "flag_reason": reason,
            "flagged_at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"  ⚑ Flagged {aid} after MRTR approval: {reason}")
        return ok(req_id, text_result(
            f"Account {aid} flagged after operator approval.\nReason: {reason}"
        ))

    if name == "deep_scan_account_history":
        aid     = args.get("account_id", "")
        task_id = f"task_{uuid.uuid4().hex[:12]}"

        # Register task as pending and kick off background work
        TASKS[task_id] = {"status": "running", "output": None}
        asyncio.get_event_loop().create_task(_run_deep_scan(task_id, aid))

        print(f"  ✓ Task started: {task_id}  (client can poll tasks/get)")
        return ok(req_id, {
            "resultType": "task",
            "taskId": task_id,
            "content": [{"type": "text", "text": f"Scan started. Poll tasks/get with taskId={task_id}"}],
        })

    return rpc_err(req_id, -32601, f"Unknown tool: {name}")


async def _run_deep_scan(task_id: str, account_id: str):
    """Background coroutine — simulates expensive scan work."""
    await asyncio.sleep(4)   # visible pause for the demo
    history = ACCOUNT_HISTORY.get(account_id, [])
    total   = sum(t["amount"] for t in history)
    text = (
        f"Deep scan complete — {account_id}\n"
        f"  Transactions: {len(history)}\n"
        f"  Total volume: ${total:,.2f}\n"
        f"  Verdict: {'HIGH RISK — escalate immediately' if total > 10_000 else 'Low risk'}"
    )
    TASKS[task_id] = {
        "status": "completed",
        "output": text_result(text),
    }
    print(f"\n  ✓ Task {task_id} completed")


if __name__ == "__main__":
    import os
    port     = int(os.getenv("PORT", 8000))
    instance = os.getenv("INSTANCE", "standalone")
    print(f"Fraud Alert Investigator (05-stateless-scale) [{instance}] — port {port}")
    print("No session store. Any instance handles any request.")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
