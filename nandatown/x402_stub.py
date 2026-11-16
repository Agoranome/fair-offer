# Copyright 2026 Kamkar Foundation, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A local stub seller for the x402-offer check: one paid A2A skill, honest, or with one planted defect.

It is an A2A 0.3 agent in the shape of NandaTown's reference seller (card at /.well-known/agent-card.json, JSON-RPC
message/send and tasks/get at /), except that its one skill, quote, is sold for $0.001 in USDC on Base with the
a2a-x402 extension: every unpaid task stops in input-required with an x402 offer. It never settles a payment.

The five defects are the seller-side twins of Agoranome's buyer drill (src/revenue/buyer/drill.py), built the same way:

  price_above_listing      the offer asks twice the listed price
  pay_to_swapped           the pay-to is an address the listing never named (the drill's pay_to_swap)
  network_switched         the offer names Ethereum (eip155:1) where the listing says Base (network_switch)
  lookalike_asset          the asset is USDC's address with its last four characters changed (lookalike_asset)
  terms_changed_on_retry   asked again in the same context, the offer asks ten times the price (price_switch)

Each changes one term only, so a check that reads the offer can name the defect by the stage that fails.

The stub has two shapes. "town" (the default) is NandaTown's reference shape: JSON-RPC at the root, and any message
is a request for the one skill. "routed" is the shape of an agent with many skills, such as Agoranome: JSON-RPC only at
/a2a (the card's url), a POST to the root answers 405, and a message must name its skill in a data part, or it gets a
message back, not a task. The routed shape is for testing the adapter NandaTown's agent test needs (town_adapter.py).

  python x402_stub.py --defect lookalike_asset --port 8950     # serve it
  StubSeller("http://stub.test", "honest").transport()         # or answer in process, as an httpx transport
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

X402_V01 = "https://github.com/google-a2a/a2a-x402/v0.1"
NETWORK = "eip155:8453"
OTHER_NETWORK = "eip155:1"
ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"     # USDC on Base
PRICE_ATOMIC = 1_000                                     # $0.001, the drill's listed price
SWITCHED_PRICE_ATOMIC = 10_000                           # the drill's price_switch, from the second ask on
PAY_TO = "0x" + hashlib.sha256(b"x402-offer stub: the listed pay-to").hexdigest()[:40]
SWAPPED_PAY_TO = "0x" + hashlib.sha256(b"pay_to_swap:x402-offer-stub").hexdigest()[:40]
LOOKALIKE_ASSET = ASSET[:-4] + "0bad"
SKILL = "quote"

SHAPES = ("town", "routed")
DEFECTS = ("price_above_listing", "pay_to_swapped", "network_switched", "lookalike_asset", "terms_changed_on_retry")
VARIANTS = ("honest",) + DEFECTS
# The stage of a2a-x402-offer@0.1 that should catch each defect, and nothing else.
EXPECTED_STAGE = {
    "price_above_listing": "price_within_listing",
    "pay_to_swapped": "pay_to_matches_listing",
    "network_switched": "network_matches_listing",
    "lookalike_asset": "asset_matches_listing",
    "terms_changed_on_retry": "repeat_same_terms",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StubSeller:
    """One stub agent. Thread-safe: the served version answers requests from several threads."""

    def __init__(self, base_url: str, defect: str = "honest", shape: str = "town"):
        if defect not in VARIANTS:
            raise ValueError(f"defect must be one of {VARIANTS}, not {defect!r}")
        if shape not in SHAPES:
            raise ValueError(f"shape must be one of {SHAPES}, not {shape!r}")
        self.base_url = base_url.rstrip("/")
        self.defect = defect
        self.shape = shape
        self.rpc_path = "/" if shape == "town" else "/a2a"
        self.asks: dict[str, int] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.requests = 0
        self._lock = threading.Lock()

    # -- what it publishes ------------------------------------------------------------------------------------------

    def card(self) -> dict[str, Any]:
        return {
            "name": "x402-offer stub seller",
            "description": "A local A2A seller for the x402-offer check: one paid skill. It never settles a"
                           " payment.",
            "url": self.base_url + self.rpc_path,
            "version": "0.1.0",
            "protocolVersion": "0.3.0",
            "preferredTransport": "JSONRPC",
            "capabilities": {"streaming": False, "pushNotifications": False, "extensions": [{
                "uri": X402_V01, "required": True,
                "description": "The quote skill takes x402 v2 payments in USDC on Base: the offer comes in"
                               " x402.payment.required on an input-required task.",
                "params": {"x402Version": "2", "schemes": ["exact"], "networks": [NETWORK], "assets": ["USDC"],
                           "payTo": PAY_TO}}]},
            "defaultInputModes": ["application/json", "text/plain"],
            "defaultOutputModes": ["application/json"],
            "skills": [{
                "id": SKILL, "name": "Quote",
                "description": "Given JSON {sku, quantity, unit_price_cents}, returns JSON {request_id,"
                               " total_cents}. Price: $0.001 in USDC per task, paid with x402 inside the task.",
                "tags": ["quote", "x402", "paid"],
                "examples": [json.dumps({"skill": SKILL, "arguments": {"sku": "widget", "quantity": 2,
                                                                      "unit_price_cents": 1995}})],
                "inputModes": ["application/json"], "outputModes": ["application/json"]}],
        }

    def listing(self) -> dict[str, Any]:
        return {"id": SKILL, "name": "Quote", "price": "$0.001", "currency": "USDC",
                "url": f"{self.base_url}{self.rpc_path.rstrip('/')}/skills/{SKILL}",
                "a2a": self.base_url + self.rpc_path}

    def offer(self, ask: int) -> dict[str, Any]:
        """The x402 v2 PaymentRequired for the ask'th unpaid task in one context."""
        option = {"scheme": "exact", "network": NETWORK, "amount": str(PRICE_ATOMIC), "asset": ASSET,
                  "payTo": PAY_TO, "maxTimeoutSeconds": 60, "extra": {"name": "USD Coin", "version": "2"}}
        if self.defect == "price_above_listing":
            option["amount"] = str(2 * PRICE_ATOMIC)
        elif self.defect == "pay_to_swapped":
            option["payTo"] = SWAPPED_PAY_TO
        elif self.defect == "network_switched":
            option["network"] = OTHER_NETWORK
        elif self.defect == "lookalike_asset":
            option["asset"] = LOOKALIKE_ASSET
        elif self.defect == "terms_changed_on_retry" and ask > 1:
            option["amount"] = str(SWITCHED_PRICE_ATOMIC)
        return {"x402Version": 2, "error": "Payment required",
                "resource": {"url": f"{self.base_url}/skills/{SKILL}", "description": "Quote",
                             "mimeType": "application/json"},
                "accepts": [option]}

    # -- JSON-RPC ---------------------------------------------------------------------------------------------------

    def _send(self, params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message") if isinstance(params, dict) else None
        if not isinstance(message, dict) or not isinstance(message.get("parts"), list):
            raise _RPCError(-32602, "params.message with parts is required")
        task_id = message.get("taskId")
        if task_id:
            task = self.tasks.get(str(task_id))
            if task is None:
                raise _RPCError(-32001, "task not found")
            # A payment never settles here: the stub only states offers.
            task["status"] = {"state": "failed", "timestamp": _now(), "message": self._message(
                task, "This stub never settles a payment. Nothing was charged.",
                {"x402.payment.status": "payment-failed", "x402.payment.error": "SETTLEMENT_FAILED",
                 "x402.payment.receipts": []})}
            return task
        if self.shape == "routed":
            named = [p.get("data", {}).get("skill") for p in message["parts"]
                     if isinstance(p, dict) and p.get("kind") == "data" and isinstance(p.get("data"), dict)]
            if not any(named):
                return {"kind": "message", "messageId": uuid.uuid4().hex, "role": "agent",
                        "parts": [{"kind": "text", "text": "Name a skill in a data part: {\"skill\": \"quote\","
                                                           " \"arguments\": {...}}."}]}
            if SKILL not in named:
                raise _RPCError(-32602, f"Unknown skill {str(named[0])[:80]!r}.")
        context = str(message.get("contextId") or "ctx-" + uuid.uuid4().hex[:12])
        with self._lock:
            self.asks[context] = ask = self.asks.get(context, 0) + 1
        offer = self.offer(ask)
        task = {"kind": "task", "id": "task-" + uuid.uuid4().hex[:12], "contextId": context,
                "metadata": {"skill": SKILL}}
        first = (offer.get("accepts") or [{}])[0]
        amount = str(first.get("amount", first.get("maxAmountRequired")))
        price = f"${int(amount) / 1_000_000:g} in USDC" if amount.isdigit() else "a payment"
        task["status"] = {"state": "input-required", "timestamp": _now(), "message": self._message(
            task, f"Payment required: {price}. Sign the x402 offer in this message's metadata"
                  " (x402.payment.required) and send it back with this task's id.",
            {"x402.payment.status": "payment-required", "x402.payment.required": offer})}
        with self._lock:
            self.tasks[task["id"]] = task
        return task

    @staticmethod
    def _message(task: dict[str, Any], text: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "message", "messageId": uuid.uuid4().hex, "role": "agent",
                "parts": [{"kind": "text", "text": text}], "taskId": task["id"], "contextId": task["contextId"],
                "metadata": metadata}

    def rpc(self, body: Any) -> dict[str, Any]:
        rid = body.get("id") if isinstance(body, dict) else None
        try:
            if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
                raise _RPCError(-32600, "a JSON-RPC 2.0 request object is required")
            method, params = body.get("method"), body.get("params") or {}
            if method == "message/send":
                return {"jsonrpc": "2.0", "id": rid, "result": self._send(params)}
            if method == "tasks/get":
                task = self.tasks.get(str(params.get("id") or ""))
                if task is None:
                    raise _RPCError(-32001, "task not found")
                return {"jsonrpc": "2.0", "id": rid, "result": task}
            raise _RPCError(-32601, f"method {str(method)[:80]!r} not found")
        except _RPCError as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}}

    # -- HTTP -------------------------------------------------------------------------------------------------------

    def handle(self, method: str, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """One HTTP request in, (status, JSON body) out."""
        with self._lock:
            self.requests += 1
        path = path.split("?", 1)[0].rstrip("/") or "/"
        if method == "GET" and path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            return 200, self.card()
        if method == "GET" and path == f"{self.rpc_path.rstrip('/')}/skills/{SKILL}":
            return 200, self.listing()
        if method == "POST" and path == self.rpc_path:
            try:
                parsed = json.loads(body or b"null")
            except ValueError:
                return 200, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
            return 200, self.rpc(parsed)
        if path in ("/", "/a2a", "/.well-known/agent-card.json", "/.well-known/agent.json"):
            return 405, {"error": "method not allowed"}
        return 404, {"error": "not found"}

    def transport(self) -> httpx.MockTransport:
        """The stub as an in-process httpx transport: no socket, the same answers."""
        def respond(request: httpx.Request) -> httpx.Response:
            status, payload = self.handle(request.method, request.url.raw_path.decode("ascii"), request.content)
            return httpx.Response(status, json=payload)
        return httpx.MockTransport(respond)


class _RPCError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def serve(defect: str = "honest", host: str = "127.0.0.1", port: int = 0, shape: str = "town"
          ) -> tuple[ThreadingHTTPServer, StubSeller, str]:
    """Serve a stub over real HTTP in a background thread. Returns the server (call shutdown() and server_close()),
    the seller and its base URL."""
    holder: dict[str, StubSeller] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, payload = holder["seller"].handle(self.command, self.path, body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _answer

        def log_message(self, *args: Any) -> None:  # quiet
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    base_url = f"http://{host}:{server.server_address[1]}"
    holder["seller"] = seller = StubSeller(base_url, defect, shape)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seller, base_url


def main() -> None:
    parser = argparse.ArgumentParser(prog="x402_stub", description="Serve the x402-offer stub seller.")
    parser.add_argument("--defect", default="honest", choices=VARIANTS)
    parser.add_argument("--shape", default="town", choices=SHAPES)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8950)
    args = parser.parse_args()
    server, _, base_url = serve(args.defect, args.host, args.port, args.shape)
    print(f"x402-offer stub seller ({args.defect}) on {base_url}; card at {base_url}/.well-known/agent-card.json")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
