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

"""A small adapter between NandaTown's agent test and an A2A agent with many skills, recorded beside each result.

NandaTown's path runner (6f9a7c0) fetches the card from <url>/.well-known/agent-card.json and posts JSON-RPC to <url>/,
the host's root, whatever the card's url says, with one text part, a JSON quote request that names no skill. An agent
whose JSON-RPC endpoint is elsewhere (Agoranome's is /a2a), or that picks a skill from the message (Agoranome reads a
data part {"skill", "arguments"}), can't be reached as it stands. NandaTown's guide to testing an existing agent says
such agents need a small adapter, recorded with the result. This is that adapter, in two modes:

  passthrough   every request goes upstream to the same path, exactly as NandaTown sent it
  skill         as passthrough, except that message/send goes to the JSON-RPC URL the card names, with one data part
                added after NandaTown's own text part: {"skill": <skill>, "arguments": <the card's example for it>}

Either way the only header changed is the User-Agent, the adapter's own (Agoranome counts a client named agoranome-...
as its own tool, never as a buyer). Nothing is paid: NandaTown sends no payment, and the adapter adds none. Every
forwarded request is logged (method, path, upstream URL, status, and for message/send the task's state and its
x402.payment.status) for the report.

  python town_adapter.py --upstream https://api.agoranome.com --mode skill --skill price_position --port 8941
  nandatown test-agent --url http://127.0.0.1:8941 --path-profile a2a-capability-fulfillment@0.3
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

VERSION = "0.1.0"
USER_AGENT = f"agoranome-e1-town-adapter/{VERSION} (+https://github.com/Agoranome/fair-offer)"
MODES = ("passthrough", "skill")
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
MAX_BODY = 1_048_576


class Adapter:
    def __init__(self, upstream: str, mode: str = "passthrough", skill: str | None = None,
                 arguments: dict[str, Any] | None = None, http: httpx.Client | None = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode == "skill" and not skill:
            raise ValueError("skill mode needs a skill")
        self.upstream = upstream.rstrip("/")
        self.mode, self.skill, self.arguments = mode, skill, arguments
        self.http = http or httpx.Client(timeout=20.0, follow_redirects=False, headers={"User-Agent": USER_AGENT})
        self.log: list[dict[str, Any]] = []
        self._rpc_url: str | None = None
        self._lock = threading.Lock()

    def rpc_url(self) -> str:
        """The JSON-RPC URL the upstream card names (its url, else a JSON-RPC supportedInterface), read once."""
        if self._rpc_url is None:
            card = self.http.get(self.upstream + CARD_PATHS[0]).json()
            url = card.get("url")
            if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
                url = next((i["url"] for i in card.get("supportedInterfaces") or []
                            if isinstance(i, dict) and str(i.get("protocolBinding", "")).upper() == "JSONRPC"
                            and isinstance(i.get("url"), str)), self.upstream + "/")
            if self.arguments is None:
                self.arguments = _example(card, self.skill)
            self._rpc_url = url
        return self._rpc_url

    def forward(self, method: str, path: str, body: bytes, content_type: str | None) -> tuple[int, str, bytes]:
        target = self.upstream + path
        note: dict[str, Any] = {}
        if method == "POST" and self.mode == "skill" and path.split("?", 1)[0] in ("", "/"):
            target = self.rpc_url()
            try:
                request = json.loads(body or b"null")
            except ValueError:
                request = None
            if isinstance(request, dict) and request.get("method") in ("message/send", "SendMessage"):
                message = (request.get("params") or {}).get("message")
                if isinstance(message, dict) and isinstance(message.get("parts"), list):
                    message["parts"].append({"kind": "data", "data": {"skill": self.skill,
                                                                      "arguments": self.arguments or {}}})
                    note["added_part"] = {"skill": self.skill, "arguments": self.arguments or {}}
                body = json.dumps(request).encode()
        headers = {"Content-Type": content_type} if content_type else {}
        started = time.time()
        try:
            response = self.http.request(method, target, content=body if method != "GET" else None,
                                         headers=headers)
            status, data = response.status_code, response.content[:MAX_BODY]
            kind = response.headers.get("content-type", "application/json")
        except httpx.HTTPError as exc:
            status, data, kind = 502, json.dumps({"adapter_error": type(exc).__name__}).encode(), "application/json"
        entry = {"at": started, "method": method, "path": path, "upstream": target, "status": status,
                 "ms": round((time.time() - started) * 1000), **note}
        if method == "POST":
            entry.update(_task_summary(status, data))
        with self._lock:
            self.log.append(entry)
        return status, kind, data


def _example(card: dict[str, Any], skill: str | None) -> dict[str, Any]:
    for entry in card.get("skills") or []:
        if isinstance(entry, dict) and entry.get("id") == skill:
            for example in entry.get("examples") or []:
                try:
                    parsed = json.loads(example) if isinstance(example, str) else example
                except ValueError:
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("arguments"), dict):
                    return parsed["arguments"]
    return {}


def _task_summary(status: int, data: bytes) -> dict[str, Any]:
    """What a JSON-RPC answer was, for the log: an HTTP error, an RPC error code, or the result's kind, state and
    x402 status."""
    try:
        payload = json.loads(data)
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or "jsonrpc" not in payload:
        return {"answer": "http_error" if status >= 400 else "not JSON-RPC"}
    if "error" in payload:
        error = payload["error"] if isinstance(payload["error"], dict) else {}
        return {"answer": "rpc_error", "code": error.get("code")}
    result = payload.get("result")
    if not isinstance(result, dict):
        return {"answer": "no result"}
    task = result.get("task") if isinstance(result.get("task"), dict) else result
    status = task.get("status") if isinstance(task.get("status"), dict) else {}
    message = status.get("message") if isinstance(status.get("message"), dict) else {}
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    return {"answer": task.get("kind") or ("task" if status else "message"), "state": status.get("state"),
            "x402_status": metadata.get("x402.payment.status")}


def serve(adapter: Adapter, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, kind, data = adapter.forward(self.command, self.path, body, self.headers.get("Content-Type"))
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _answer

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}"


def main() -> None:
    parser = argparse.ArgumentParser(prog="town_adapter", description=__doc__.split("\n")[0])
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--mode", choices=MODES, default="passthrough")
    parser.add_argument("--skill", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8941)
    args = parser.parse_args()
    adapter = Adapter(args.upstream, args.mode, args.skill)
    server, url = serve(adapter, args.host, args.port)
    print(f"adapter ({args.mode}) for {args.upstream} on {url}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
        print(json.dumps(adapter.log, indent=2))


if __name__ == "__main__":
    main()
