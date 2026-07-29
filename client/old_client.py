"""
old_client.py — speaks 2025-11-25 stateful MCP protocol.
Branch 02-break-it: adds a deliberate pause between calls so the
round-robin load balancer routes initialize and the next call to
different server instances — causing the visible session failure.

Flow:
  1. POST /mcp  {initialize}          → receive Mcp-Session-Id (from instance A)
  2. POST /mcp  {notifications/initialized}
  3. POST /mcp  {tools/list}          ← may land on instance B → ✗ Session not found
  4. (continues to show what would have worked on instance A)

Run:
  python old_client.py [--host http://localhost:8000]
"""
import argparse
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"


def post(url: str, body: dict, headers: dict | None = None) -> tuple[dict, dict]:
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), dict(resp.headers)
    except urllib.request.HTTPError as e:
        body = json.loads(e.read())
        return body, {}


def banner(title: str):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=BASE)
    args = parser.parse_args()
    url = f"{args.host}/mcp"

    banner("old_client.py — BREAK-IT demo (02-break-it)")
    print("\n  Two server instances behind round-robin nginx.")
    print("  Watch which instance handles initialize vs. the next call.\n")

    # ── initialize (likely hits instance A) ───────────────────────────────
    print("[1] Sending initialize …")
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
        print("  ✗ No session ID returned. Server may already be broken.")
        sys.exit(1)
    print(f"  ✓ Session ID: {session_id}")

    # ── notifications/initialized ──────────────────────────────────────────
    post(url, {"jsonrpc": "2.0", "id": None,
               "method": "notifications/initialized", "params": {}},
         headers={"Mcp-Session-Id": session_id})

    # ── Give nginx time to cycle to the other instance ────────────────────
    print("\n  [pause 0.5s — letting round-robin advance to the next instance]")
    time.sleep(0.5)

    # ── tools/list (likely hits instance B — BOOM) ────────────────────────
    print("\n[2] Sending tools/list with same session ID …")
    resp, _ = post(url, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }, headers={"Mcp-Session-Id": session_id})

    if "error" in resp:
        print(f"\n  ✗✗✗  SESSION FAILURE ✗✗✗")
        print(f"  Error: {resp['error']['message']}")
        print("\n  This is why stateful MCP breaks behind a plain load balancer.")
        print("  → Proceed to branch 03-new-sdk-old-behavior to start the fix.\n")
    else:
        # Sometimes both requests land on the same instance — re-run to see the failure
        tools = resp.get("result", {}).get("tools", [])
        print(f"  ✓ Got {len(tools)} tools (both requests landed on the same instance).")
        print("  Re-run to get the failure — round-robin may not cycle on 2 requests.")

    banner("End of break-it demo")


if __name__ == "__main__":
    main()
