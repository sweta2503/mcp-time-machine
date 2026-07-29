"""
MCP Fraud Alert Investigator
Branch: 04-discover  —  2026-07-28 stateless protocol, phase 1

Changes from 01-legacy:
  • initialize/initialized REMOVED
  • server/discover ADDED — returns serverInfo + capabilities
  • _meta in every request params (carries clientInfo instead of session)
  • MCP-Protocol-Version header accepted and echoed
  • Mcp-Method / Mcp-Name headers accepted (routing support)
  • Session store DELETED — no per-instance state

Old client still works: it will hit 'method not found' for initialize,
but that's shown in README as expected during the transition window.
"""
import sys
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, ".")
from data import ACCOUNT_HISTORY, FLAGGED_ACCOUNTS, TRANSACTIONS

app = FastAPI(title="Fraud Alert Investigator — 04-discover (2026-07-28)")

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
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


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
            "resultType": "complete",
            "supportedVersions": ["2026-07-28", "2025-11-25"],
            "capabilities": CAPABILITIES,
            "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
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
            "resultType": "complete",
            "tools": TOOLS,
            "ttlMs": 300_000,
            "cacheScope": "private",
        }))

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
            f"  • {aid}: {info.get('holder', 'Unknown')} — {info['flag_reason']}"
            for aid, info in FLAGGED_ACCOUNTS.items()
        ]
        return ok(req_id, text_result("Flagged accounts:\n" + "\n".join(lines)))

    if name == "flag_account":
        aid    = args.get("account_id", "")
        reason = args.get("reason", "")
        # Stateless: flag immediately (MRTR added in branch 08)
        FLAGGED_ACCOUNTS[aid] = {
            "account_id": aid,
            "holder": "Unknown",
            "flag_reason": reason,
            "flagged_at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"  ⚑ Flagged {aid}: {reason}")
        return ok(req_id, text_result(f"Account {aid} flagged. Reason: {reason}"))

    if name == "deep_scan_account_history":
        aid = args.get("account_id", "")
        print(f"  ⏳ Scanning {aid} (blocking) …")
        await asyncio.sleep(3)
        history = ACCOUNT_HISTORY.get(aid, [])
        total = sum(t["amount"] for t in history)
        text = (
            f"Deep scan complete — {aid}\n"
            f"  Transactions: {len(history)}\n"
            f"  Total volume: ${total:,.2f}\n"
            f"  Verdict: {'HIGH RISK — escalate' if total > 10_000 else 'Low risk'}"
        )
        return ok(req_id, text_result(text))

    return rpc_err(req_id, -32601, f"Unknown tool: {name}")


if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 8000))
    print(f"Fraud Alert Investigator (04-discover) — port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
