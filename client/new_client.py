"""
new_client.py — speaks 2026-07-28 stateless MCP protocol.

Key differences from old_client.py:
  • No initialize handshake, no Mcp-Session-Id
  • Every request carries MCP-Protocol-Version + _meta (clientInfo)
  • Mcp-Method + Mcp-Name headers on every request (for gateway routing)
  • server/discover for capability sniffing (optional but shown)
  • MRTR loop: flag_account may return {resultType:"input_required"};
    client prompts user, then resumes with inputResponses
  • Tasks: deep_scan returns task_id immediately; client polls tasks/get

Run:
  python new_client.py [--host http://localhost:8000]
"""
import argparse
import json
import time
import urllib.request

BASE = "http://localhost:8000"
PROTOCOL = "2026-07-28"
CLIENT_INFO = {"name": "new_client", "version": "2.0.0"}


def post(url: str, body: dict, extra_headers: dict | None = None) -> dict:
    method_name = body.get("method", "")
    tool_name   = body.get("params", {}).get("name", "")
    headers = {
        "Content-Type": "application/json",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method_name,
        **({"Mcp-Name": tool_name} if tool_name else {}),
        **(extra_headers or {}),
    }
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw.strip() else {}


def _meta() -> dict:
    """Every request carries _meta instead of a session id."""
    return {"io.modelcontextprotocol/clientInfo": CLIENT_INFO}


def banner(title: str):
    print(f"\n{'═'*55}")
    print(f"  {title}")
    print(f"{'═'*55}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=BASE)
    args = parser.parse_args()
    url = f"{args.host}/mcp"

    banner("new_client.py — 2026-07-28 stateless protocol")

    # ── server/discover (optional capability sniff) ────────────────────────
    print("\n[discover] server/discover …")
    resp = post(url, {
        "jsonrpc": "2.0", "id": 1, "method": "server/discover",
        "params": {"_meta": _meta()},
    })
    info = resp.get("result", {}).get("serverInfo", {})
    caps = resp.get("result", {}).get("capabilities", {})
    print(f"  ✓ Server: {info.get('name')} {info.get('version')}")
    print(f"  ✓ Capabilities: {list(caps.keys())}")

    # ── tools/list  (with caching metadata) ───────────────────────────────
    print("\n[tools/list] …")
    resp = post(url, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        "params": {"_meta": _meta()},
    })
    result = resp.get("result", {})
    tools  = result.get("tools", [])
    ttl    = result.get("ttlMs")
    cache  = result.get("cacheScope")
    print(f"  ✓ {len(tools)} tools: {[t['name'] for t in tools]}")
    if ttl:
        print(f"  ✓ Cache: ttlMs={ttl}, scope={cache}")

    # ── lookup_transaction ─────────────────────────────────────────────────
    for tid in ("TXN001", "TXN003"):
        print(f"\n[tools/call] lookup_transaction({tid}) …")
        resp = post(url, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "lookup_transaction", "arguments": {"transaction_id": tid}, "_meta": _meta()},
        })
        text = resp["result"]["content"][0]["text"]
        for line in text.splitlines():
            print(f"  {line}")

    # ── list_flagged_accounts ──────────────────────────────────────────────
    print("\n[tools/call] list_flagged_accounts() …")
    resp = post(url, {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "list_flagged_accounts", "arguments": {}, "_meta": _meta()},
    })
    text = resp["result"]["content"][0]["text"]
    for line in text.splitlines():
        print(f"  {line}")

    # ── flag_account  (MRTR loop) ──────────────────────────────────────────
    print("\n[tools/call] flag_account — MRTR demo …")
    account_to_flag = "ACC456"
    flag_reason     = "Velocity spike: 12 transactions in 4 minutes"

    resp = post(url, {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {
            "name": "flag_account",
            "arguments": {"account_id": account_to_flag, "reason": flag_reason},
            "_meta": _meta(),
        },
    })
    result = resp.get("result", {})

    if result.get("resultType") == "input_required":
        # Server needs human confirmation before committing
        print(f"\n  ⚠  Server requires input before flagging {account_to_flag}:")
        for req in result.get("inputRequests", []):
            print(f"     {req['prompt']}")

        # Prompt the operator (you, on camera)
        answer = input("\n  Approve? [y/N] ").strip().lower()
        approved = answer == "y"

        print(f"\n  → Resuming with approval={approved} …")
        request_state = result.get("requestState")
        resp = post(url, {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {
                "name": "flag_account",
                "arguments": {"account_id": account_to_flag, "reason": flag_reason},
                "_meta": _meta(),
                "inputResponses": [{"approved": approved}],
                "requestState": request_state,
            },
        })
        result = resp.get("result", {})

    text = result.get("content", [{}])[0].get("text", "")
    for line in text.splitlines():
        print(f"  {line}")

    # ── deep_scan_account_history  (Task) ──────────────────────────────────
    print("\n[tools/call] deep_scan_account_history — Task demo …")
    resp = post(url, {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {
            "name": "deep_scan_account_history",
            "arguments": {"account_id": "ACC123"},
            "_meta": _meta(),
        },
    })
    result = resp.get("result", {})

    if result.get("resultType") == "task":
        task_id = result["taskId"]
        print(f"  ✓ Task started: {task_id}")
        print("  … client is free to do other work …")
        time.sleep(1)

        # Poll until complete
        for attempt in range(10):
            print(f"  → Polling tasks/get (attempt {attempt + 1}) …")
            poll = post(url, {
                "jsonrpc": "2.0", "id": 8 + attempt, "method": "tasks/get",
                "params": {"taskId": task_id, "_meta": _meta()},
            })
            task_result = poll.get("result", {})
            status = task_result.get("status")
            print(f"     status: {status}")
            if status == "completed":
                text = task_result.get("output", {}).get("content", [{}])[0].get("text", "")
                for line in text.splitlines():
                    print(f"  {line}")
                break
            time.sleep(1)
    else:
        # Fallback for branches where deep_scan is still synchronous
        text = result.get("content", [{}])[0].get("text", "")
        for line in text.splitlines():
            print(f"  {line}")

    banner("Done ✓")


if __name__ == "__main__":
    main()
