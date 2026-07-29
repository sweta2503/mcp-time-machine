"""
benchmark/locustfile.py — MCP old-protocol vs new-protocol load test.

Two UserClasses, two upstreams, same nginx round-robin, 3 instances each:

  OldProtocolUser  → port 8001  (STRICT_SESSIONS=true, in-memory sessions)
                     Sessions minted on instance A; ~67% of requests hit
                     instance B or C → session not found → RPC error.
                     Error rate climbs toward ~67% as users scale.

  NewProtocolUser  → port 8000  (stateless, Redis-backed)
                     No session at all. Every request hits any instance.
                     Error rate stays at 0%.

Run:
  # 1. Start the benchmark stack
  docker compose -f benchmark/docker-compose.yml up --build -d

  # 2. Install locust (once)
  pip install locust

  # 3. Launch Locust web UI
  locust -f benchmark/locustfile.py
  # Open http://localhost:8089, spawn 100 users at 10/s, watch the graphs.

  # 4. Or headless (CI-friendly):
  locust -f benchmark/locustfile.py --headless -u 100 -r 10 --run-time 60s
"""
import random

from locust import HttpUser, between, task


class OldProtocolUser(HttpUser):
    """2025-11-25 stateful — session minted on one instance, breaks on others."""

    host = "http://localhost:8001"
    wait_time = between(0.05, 0.2)

    def on_start(self):
        resp = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "clientInfo": {"name": "locust-old", "version": "1.0"},
                    "capabilities": {},
                },
            },
            name="initialize",
        )
        self.session_id = resp.headers.get("Mcp-Session-Id", "")
        self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": None, "method": "notifications/initialized", "params": {}},
            headers={"Mcp-Session-Id": self.session_id},
            name="notifications/initialized",
        )

    @task(4)
    def lookup_transaction(self):
        tid = random.choice(["TXN001", "TXN002", "TXN003", "TXN004", "TXN005"])
        with self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "lookup_transaction", "arguments": {"transaction_id": tid}},
            },
            headers={"Mcp-Session-Id": self.session_id},
            name="tools/call[lookup_transaction]",
            catch_response=True,
        ) as resp:
            data = resp.json()
            if "error" in data:
                resp.failure(data["error"]["message"])

    @task(1)
    def list_flagged(self):
        with self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_flagged_accounts", "arguments": {}},
            },
            headers={"Mcp-Session-Id": self.session_id},
            name="tools/call[list_flagged_accounts]",
            catch_response=True,
        ) as resp:
            data = resp.json()
            if "error" in data:
                resp.failure(data["error"]["message"])


class NewProtocolUser(HttpUser):
    """2026-07-28 stateless — no session, any instance handles any request."""

    host = "http://localhost:8000"
    wait_time = between(0.05, 0.2)

    _meta = {
        "io.modelcontextprotocol/clientInfo": {"name": "locust-new", "version": "2.0"}
    }
    _headers = {"MCP-Protocol-Version": "2026-07-28"}

    def on_start(self):
        self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": self._meta},
            },
            headers=self._headers,
            name="server/discover",
        )

    @task(4)
    def lookup_transaction(self):
        tid = random.choice(["TXN001", "TXN002", "TXN003", "TXN004", "TXN005"])
        with self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "lookup_transaction",
                    "arguments": {"transaction_id": tid},
                    "_meta": self._meta,
                },
            },
            headers={
                **self._headers,
                "Mcp-Method": "tools/call",
                "Mcp-Name": "lookup_transaction",
            },
            name="tools/call[lookup_transaction]",
            catch_response=True,
        ) as resp:
            data = resp.json()
            if "error" in data:
                resp.failure(data["error"]["message"])

    @task(1)
    def list_flagged(self):
        with self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "list_flagged_accounts",
                    "arguments": {},
                    "_meta": self._meta,
                },
            },
            headers={
                **self._headers,
                "Mcp-Method": "tools/call",
                "Mcp-Name": "list_flagged_accounts",
            },
            name="tools/call[list_flagged_accounts]",
            catch_response=True,
        ) as resp:
            data = resp.json()
            if "error" in data:
                resp.failure(data["error"]["message"])
