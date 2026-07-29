"""
MCP Fraud Alert Investigator
Branch: 01-legacy  —  2025-11-25 stateful protocol

Protocol:
  POST /mcp
  Mcp-Session-Id: <uuid>          (server mints on initialize, client echoes back)
  initialize → initialized → tools/list → tools/call
"""
import asyncio
import sys
import uuid
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

sys.path.insert(0, ".")
from data import ACCOUNT_HISTORY, FLAGGED_ACCOUNTS, TRANSACTIONS

app = FastAPI(title="Fraud Alert Investigator — legacy (2025-11-25)")

# ── In-memory session store ────────────────────────────────────────────────
# This is exactly what will explode in segment 02.
sessions: dict[str, dict] = {}

# ── Tool definitions ───────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "lookup_transaction",
        "description": "Look up a single transaction by ID. Fast, read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "transaction_id": {
                    "type": "string",
                    "description": "Transaction ID, e.g. TXN001",
                }
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
        "description": "Flag an account for fraud review. Destructive — requires human approval.",
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


# ── Helpers ────────────────────────────────────────────────────────────────
def ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def text_result(text: str, is_error: bool = False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# ── Main endpoint ──────────────────────────────────────────────────────────
@app.post("/mcp")
async def mcp(request: Request):
    body = await request.json()
    method  = body.get("method", "")
    params  = body.get("params", {})
    req_id  = body.get("id")
    session_id = request.headers.get("Mcp-Session-Id")

    print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] ← {method}  session={session_id or '—'}")

    # ── initialize ─────────────────────────────────────────────────────────
    if method == "initialize":
        sid = str(uuid.uuid4())
        sessions[sid] = {
            "client": params.get("clientInfo", {}),
            "created": datetime.now(timezone.utc).isoformat(),
        }
        print(f"  ✓ New session: {sid}")
        return JSONResponse(
            content=ok(req_id, {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fraud-investigator", "version": "1.0.0"},
            }),
            headers={"Mcp-Session-Id": sid},
        )

    # ── notifications/initialized (no response body needed) ────────────────
    if method == "notifications/initialized":
        return Response(status_code=204)

    # ── all other methods require a valid session ──────────────────────────
    if not session_id or session_id not in sessions:
        print(f"  ✗ Session not found: {session_id!r}")
        return JSONResponse(
            content=rpc_err(req_id, -32600, f"Session not found: {session_id}"),
            status_code=400,
        )

    # ── tools/list ─────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse(content=ok(req_id, {"tools": TOOLS}))

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


# ── Tool dispatch ──────────────────────────────────────────────────────────
async def dispatch_tool(req_id, name: str, args: dict):
    # lookup_transaction
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

    # list_flagged_accounts
    if name == "list_flagged_accounts":
        if not FLAGGED_ACCOUNTS:
            return ok(req_id, text_result("No accounts currently flagged."))
        lines = [
            f"  • {aid}: {info['holder']} — {info['flag_reason']}"
            for aid, info in FLAGGED_ACCOUNTS.items()
        ]
        return ok(req_id, text_result("Flagged accounts:\n" + "\n".join(lines)))

    # flag_account  (legacy: immediate, no approval)
    if name == "flag_account":
        aid    = args.get("account_id", "")
        reason = args.get("reason", "")
        FLAGGED_ACCOUNTS[aid] = {
            "account_id": aid,
            "flag_reason": reason,
            "flagged_at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"  ⚑ Flagged {aid}: {reason}")
        return ok(req_id, text_result(f"Account {aid} flagged. Reason: {reason}"))

    # deep_scan_account_history  (legacy: blocks the response)
    if name == "deep_scan_account_history":
        aid = args.get("account_id", "")
        print(f"  ⏳ Scanning {aid} (blocking 3s) …")
        await asyncio.sleep(3)          # simulates long work; blocks this request
        history = ACCOUNT_HISTORY.get(aid, [])
        total = sum(t["amount"] for t in history)
        text = (
            f"Deep scan complete — {aid}\n"
            f"  Transactions: {len(history)}\n"
            f"  Total volume: ${total:,.2f}\n"
            f"  Verdict: {'HIGH RISK — escalate immediately' if total > 10_000 else 'Low risk'}"
        )
        return ok(req_id, text_result(text))

    return rpc_err(req_id, -32601, f"Unknown tool: {name}")


# ── Run ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 8000))
    print(f"Fraud Alert Investigator (legacy) — port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
