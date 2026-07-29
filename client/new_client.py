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
    """Every request carries _meta: identity, protocol version, and capabilities."""
    return {
        "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
        "io.modelcontextprotocol/protocolVersion": PROTOCOL,
        "io.modelcontextprotocol/clientCapabilities": {
            "extensions": {
                "io.modelcontextprotocol/tasks": {}
            }
        },
    }


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
    disc   = resp.get("result", {})
    info   = disc.get("_meta", {}).get("io.modelcontextprotocol/serverInfo", {}) or disc.get("_meta", {}).get("serverInfo", {}) or disc.get("serverInfo", {})
    caps   = disc.get("capabilities", {})
    vers   = disc.get("supportedVersions", [])
    print(f"  ✓ Server: {info.get('name')} {info.get('version')}")
    if vers:
        print(f"  ✓ Supported versions: {vers}")
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
        for _key, req in result.get("inputRequests", {}).items():
            msg = req.get("params", {}).get("message", "")
            print(f"     {msg}")

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
                "inputResponses": {"fraud_approval": {"approved": approved}},
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

    task_id = None
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
                result_data = task_result.get("result") or task_result.get("output", {})
                text = result_data.get("content", [{}])[0].get("text", "")
                for line in text.splitlines():
                    print(f"  {line}")
                break
            time.sleep(1)
    else:
        # Fallback for branches where deep_scan is still synchronous
        text = result.get("content", [{}])[0].get("text", "")
        for line in text.splitlines():
            print(f"  {line}")

    # ── Restart resilience demo (branch 11+: Redis-backed tasks) ──────────
    if task_id:
        import subprocess, shutil
        if shutil.which("docker"):
            print(f"\n[REDIS DEMO] Task {task_id} is stored in Redis.")
            print("[REDIS DEMO] Restarting ALL server instances to prove durability …")
            subprocess.run(
                ["docker", "compose", "-f", "server/docker-compose.yml",
                 "restart", "server_1", "server_2", "server_3"],
                capture_output=True,
            )
            print("[REDIS DEMO] Waiting for servers to come back online …")
            time.sleep(5)
            poll = post(url, {
                "jsonrpc": "2.0", "id": 99, "method": "tasks/get",
                "params": {"taskId": task_id, "_meta": _meta()},
            })
            status_after = poll.get("result", {}).get("status", poll.get("error", {}).get("message", "?"))
            print(f"[REDIS DEMO] ✓ Status after full restart: {status_after}")

        # ── Cancel demo ───────────────────────────────────────────────────
        print("\n[CANCEL DEMO] Starting a task and cancelling it immediately …")
        resp2 = post(url, {
            "jsonrpc": "2.0", "id": 100, "method": "tools/call",
            "params": {
                "name": "deep_scan_account_history",
                "arguments": {"account_id": "ACC789"},
                "_meta": _meta(),
            },
        })
        cancel_id = resp2.get("result", {}).get("taskId")
        if cancel_id:
            print(f"  Task {cancel_id} started")
            cancel_resp = post(url, {
                "jsonrpc": "2.0", "id": 101, "method": "tasks/cancel",
                "params": {"taskId": cancel_id, "_meta": _meta()},
            })
            cancelled = cancel_resp.get("result", {}).get("cancelled", False)
            print(f"  ✓ tasks/cancel returned: cancelled={cancelled}")
            time.sleep(2)
            verify = post(url, {
                "jsonrpc": "2.0", "id": 102, "method": "tasks/get",
                "params": {"taskId": cancel_id, "_meta": _meta()},
            })
            final_status = verify.get("result", {}).get("status", "?")
            print(f"  ✓ Final status in Redis: {final_status}")

    banner("Done ✓")


if __name__ == "__main__":
    main()
