"""
old_client.py — speaks 2025-11-25 stateful MCP protocol.

Flow:
  1. POST /mcp  {initialize}          → receive Mcp-Session-Id
  2. POST /mcp  {notifications/initialized}  (echo session id)
  3. POST /mcp  {tools/list}
  4. POST /mcp  {tools/call, …}       (all with Mcp-Session-Id header)

Run:
  python old_client.py [--host http://localhost:8000]
"""
import argparse
import json
import sys
import urllib.request

BASE = "http://localhost:8000"


def post(url: str, body: dict, headers: dict | None = None) -> tuple[dict, dict]:
    """Returns (response_json, response_headers)."""
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        resp_headers = dict(resp.headers)
        body = json.loads(resp.read())
    return body, resp_headers


def banner(title: str):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


def show(label: str, data):
    print(f"\n  {label}:")
    if isinstance(data, dict):
        for k, v in data.items():
            print(f"    {k}: {v}")
    else:
        for line in str(data).splitlines():
            print(f"    {line}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=BASE)
    args = parser.parse_args()
    url = f"{args.host}/mcp"

    banner("old_client.py — 2025-11-25 stateful protocol")

    # ── Step 1: initialize ─────────────────────────────────────────────────
    print("\n[1/4] Sending initialize …")
    resp, hdrs = post(url, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "clientInfo": {"name": "old_client", "version": "1.0.0"},
            "capabilities": {},
        },
    })
    session_id = hdrs.get("Mcp-Session-Id") or hdrs.get("mcp-session-id")
    if not session_id:
        print("  ✗ No Mcp-Session-Id in response — aborting.")
        sys.exit(1)
    show("Server response", resp.get("result", resp))
    print(f"\n  ✓ Session ID: {session_id}")

    # ── Step 2: notifications/initialized ─────────────────────────────────
    print("\n[2/4] Sending notifications/initialized …")
    post(url, {
        "jsonrpc": "2.0", "id": None,
        "method": "notifications/initialized", "params": {},
    }, headers={"Mcp-Session-Id": session_id})
    print("  ✓ Acknowledged.")

    # ── Step 3: tools/list ─────────────────────────────────────────────────
    print("\n[3/4] tools/list …")
    resp, _ = post(url, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }, headers={"Mcp-Session-Id": session_id})
    tools = resp.get("result", {}).get("tools", [])
    print(f"  ✓ {len(tools)} tools available: {[t['name'] for t in tools]}")

    # ── Step 4: call each tool ─────────────────────────────────────────────
    print("\n[4/4] Calling tools …")

    def call(tool: str, arguments: dict, req_id: int):
        print(f"\n  → {tool}({arguments})")
        r, _ = post(url, {
            "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }, headers={"Mcp-Session-Id": session_id})
        content = r.get("result", {}).get("content", [{}])
        text = content[0].get("text", json.dumps(r)) if content else json.dumps(r)
        for line in text.splitlines():
            print(f"     {line}")

    call("lookup_transaction",        {"transaction_id": "TXN001"}, 3)
    call("lookup_transaction",        {"transaction_id": "TXN002"}, 4)
    call("list_flagged_accounts",     {},                           5)
    call("flag_account",              {"account_id": "ACC456", "reason": "Rapid sequential withdrawals"}, 6)
    call("deep_scan_account_history", {"account_id": "ACC123"},    7)

    banner("Done — session completed successfully ✓")


if __name__ == "__main__":
    main()
