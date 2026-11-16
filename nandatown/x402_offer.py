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

"""x402-offer: check that a paid A2A agent states honest x402 terms, and keep the result as a NandaTown evidence bundle.

NandaTown's agent test (`nandatown test-agent`, path profile a2a-capability-fulfillment@0.3) needs a completed task, so
an agent that asks to be paid reads to it as one that failed, and none of its stages reads the offer. This is the
missing profile, a2a-x402-offer@0.1. It asks one paid skill twice without paying, and passes only when

  x402_extension_declared   the agent card declares the a2a-x402 extension,
  payment_request           the unpaid task stops in input-required with an x402 payment request,
  price_within_listing      every option the offer gives asks no more than the skill's listed price,
  pay_to_matches_listing    and pays a pay-to the listing names,
  network_matches_listing   on a network the listing names,
  asset_matches_listing     in an asset the listing names,
  repeat_same_terms         and the same request, asked again, gets the same terms.

Nothing is signed and nothing is paid. Each observation is an attributed event, and the stages are worked out from
the events alone, so anyone holding the bundle can replay the judgment (`python x402_offer.py verify BUNDLE`).

The bundle is NandaTown's path bundle (tested against NandaTown 6f9a7c0): profile.json, run.json, intents.jsonl,
events.jsonl and result.json, fingerprinted in manifest.json with the same canonical JSON and SHA-256, plus two views
that aren't records, as NandaTown keeps its report: report.md, and evidence.json, one NandaTown evidence record per
stage. NandaTown's own verify checks every hash and binding in it; it can't replay the evaluator, which it doesn't
have.

The listing is what the buyer was told before the task. By default it is read from the agent: the network, asset and
pay-to from the params of the card's a2a-x402 extension (the extension's spec defines no params, so this follows the
form Agoranome's card uses: networks, assets, payTo), and the price from the skill's own listing (GET <card url>/skills
/<skill>, answering {"price": "$0.001"}) or, failing that, "Price: $0.001" in the skill's description. A listing file
or URL (--listing, schemas/x402_offer_listing.schema.json) replaces it.

  python x402_offer.py check --url https://api.agoranome.com --skill price_position
  python x402_offer.py verify runs/<run id>

Needs httpx only. Apache 2.0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

VERSION = "0.1.0"
PROFILE_ID = "a2a-x402-offer"
PROFILE_VERSION = "0.1"
PROFILE_REF = f"{PROFILE_ID}@{PROFILE_VERSION}"
EVALUATOR = "x402-offer-evaluator@0.1"
EVALUATOR_VERSION = "x402-offer-0.1"
OBSERVER = "x402-offer.v1"
REQUESTER = "x402-offer-requester"
# Whose bundle format this is: NandaTown's path bundle as of the commit the plugin is tested at.
BUNDLE_FORMAT = "nandatown 0.2.0 path bundle (6f9a7c0)"
USER_AGENT = f"agoranome-x402-offer/{VERSION} (+https://github.com/Agoranome/fair-offer)"

# The a2a-x402 extension: v0.1 under google-a2a, v0.2 under google-agentic-commerce. A card declares a version by its
# URI; any URI under either repository counts as declaring the extension, and the one found is recorded.
X402_EXTENSION_PREFIXES = ("https://github.com/google-a2a/a2a-x402/",
                           "https://github.com/google-agentic-commerce/a2a-x402/")
STATUS_KEY = "x402.payment.status"
REQUIRED_KEY = "x402.payment.required"
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")

# Circle's USDC per network (CAIP-2), as x402's default assets name it; all six decimals.
USDC = {
    "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",       # Base
    "eip155:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",      # Base Sepolia
    "eip155:1": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",          # Ethereum
    "eip155:137": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",        # Polygon
    "eip155:42161": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",      # Arbitrum
    "eip155:10": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",         # Optimism
    "eip155:143": "0x754704Bc059F8C67012fEd69BC8A327a5aafb603",        # Monad
    "eip155:10143": "0x534b2f3A21130d7a60830c2Df862319e593943A3",      # Monad testnet
    "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # Solana
    "sui:mainnet": "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC",
    "sui:testnet": "0xa1ec7fc00a6f40db9693ad1415d0c193ad3906494428cf252621037bd7117e29::usdc::USDC",
}
USDC_DECIMALS = 6
# x402 v1 names its networks where v2 writes CAIP-2.
V1_NETWORKS = {"base": "eip155:8453", "base-sepolia": "eip155:84532", "ethereum": "eip155:1",
               "polygon": "eip155:137", "arbitrum": "eip155:42161", "optimism": "eip155:10",
               "solana": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"}
# A2A 1.0 writes task states as proto enum names, 0.3 in kebab case.
STATES = {"TASK_STATE_SUBMITTED": "submitted", "TASK_STATE_WORKING": "working",
          "TASK_STATE_INPUT_REQUIRED": "input-required", "TASK_STATE_COMPLETED": "completed",
          "TASK_STATE_FAILED": "failed", "TASK_STATE_CANCELED": "canceled", "TASK_STATE_REJECTED": "rejected",
          "TASK_STATE_AUTH_REQUIRED": "auth-required"}
# The terms a repeat must keep, as FO-8 compares them: price, asset, network and pay-to, with the scheme.
TERMS = ("scheme", "network", "amount", "asset", "pay_to")
PRICE_RE = re.compile(r"Price:\s*\$\s*([0-9]+(?:\.[0-9]+)?)")

# The contract, frozen and fingerprinted, in the shape of a NandaTown PathProfile so NandaTown can read it.
PROFILE: dict[str, Any] = {
    "profile_id": PROFILE_ID,
    "version": PROFILE_VERSION,
    "protocol": "a2a",
    "capability": "x402-offer",
    "request": {"method": "message/send", "attempts": 2, "payment": "none",
                "part": {"kind": "data", "data": {"skill": "<skill>", "arguments": "<arguments>"}}},
    "expected": {"extension": "a2a-x402", "task_state": "input-required", "x402_status": "payment-required",
                 "amount": "at most the listed price", "pay_to": "listed", "network": "listed",
                 "asset": "listed", "repeat": list(TERMS)},
    "controlled_condition": "duplicate_request",
    "limits": {"timeout_seconds": 15.0, "max_response_bytes": 1048576},
    "evaluator": EVALUATOR,
}

STAGE_ORDER = ["resolution", "agent_card_retrieval", "x402_extension_declared", "listing_retrieval",
               "payment_request", "price_within_listing", "pay_to_matches_listing", "network_matches_listing",
               "asset_matches_listing", "repeat_same_terms"]
# What each stage stands for, in one line, for reports and evidence records.
STAGES = {
    "resolution": "the subject URL is an absolute http(s) URL",
    "agent_card_retrieval": "the agent card answers",
    "x402_extension_declared": "the card declares the a2a-x402 extension",
    "listing_retrieval": "the skill's listed price, networks, assets and pay-tos are known",
    "payment_request": "an unpaid task stops in input-required with an x402 payment request",
    "price_within_listing": "no option asks more than the listed price",
    "pay_to_matches_listing": "every option pays a listed pay-to",
    "network_matches_listing": "every option is on a listed network",
    "asset_matches_listing": "every option is in a listed asset",
    "repeat_same_terms": "the same request asked again gets the same terms",
}


# --- Canonical JSON, as NandaTown's records.py writes it -------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """Sorted keys, compact separators, UTF-8: NandaTown's canonical form."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


# --- Networks, assets and addresses ---------------------------------------------------------------------------------

def caip2(network: Any) -> Any:
    """A network as CAIP-2: v2's ids as they are, v1's names translated, anything else as given."""
    if isinstance(network, str) and network in V1_NETWORKS:
        return V1_NETWORKS[network]
    return network


def _evm(network: Any) -> bool:
    return isinstance(network, str) and network.startswith("eip155:")


def same_address(network: Any, a: Any, b: Any) -> bool:
    """Whether two addresses (or asset ids) are the same: hex is compared without case on EVM chains, where the
    checksum is only case; anything else (base58 on Solana, Move types on Sui) exactly."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return a.lower() == b.lower() if _evm(network) or (a.startswith("0x") and b.startswith("0x")) else a == b


def _asset_address(network: str, asset: Any) -> Any:
    """A listed asset as an address: "USDC" becomes Circle's USDC on that network; an address stays as it is."""
    if isinstance(asset, str) and asset.strip().upper() == "USDC":
        return USDC.get(network)
    return asset


def _decimals_of(network: str, asset: Any) -> int | None:
    known = USDC.get(network)
    return USDC_DECIMALS if known and same_address(network, known, asset) else None


def to_atomic(usd: Any, decimals: int) -> int | None:
    """A dollar price ("$0.001", "0.001", 0.001) in the asset's smallest units, rounded up; None if it isn't one."""
    if isinstance(usd, bool) or usd is None:
        return None
    text = str(usd).strip().lstrip("$").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    return int((value * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_CEILING))


def _int_amount(value: Any) -> int | None:
    """An x402 amount: a string of digits (or an int), in the asset's smallest units."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _short(text: Any, keep: int = 10) -> str:
    text = str(text)
    return text if len(text) <= 2 * keep + 1 else f"{text[:keep]}…{text[-6:]}"


# --- The listing ----------------------------------------------------------------------------------------------------

class ListingError(ValueError):
    pass


def normalize_listing(raw: Any, source: str) -> dict[str, Any]:
    """A listing in the check's form, from the file form (schemas/x402_offer_listing.schema.json):

    {"skill": "...", "price_usd": "0.001" | "price_atomic": "1000", "decimals": 6,
     "options": [{"network": "eip155:8453", "asset": "USDC" | "0x...", "pay_to": "0x..."}], "source": "..."}
    """
    if not isinstance(raw, dict):
        raise ListingError("a listing is a JSON object")
    options = []
    for item in raw.get("options") or []:
        if not isinstance(item, dict):
            raise ListingError("each listing option is a JSON object")
        network = caip2(item.get("network"))
        asset = _asset_address(network, item.get("asset")) if isinstance(network, str) else None
        pay_to = item.get("pay_to", item.get("payTo"))
        if not (isinstance(network, str) and network and isinstance(asset, str) and asset
                and isinstance(pay_to, str) and pay_to):
            raise ListingError(f"listing option {item!r} needs a network, an asset known on it and a pay_to")
        options.append({"network": network, "asset": asset, "pay_to": pay_to})
    if not options:
        raise ListingError("a listing names at least one option (network, asset and pay_to)")
    decimals = raw.get("decimals")
    if decimals is None:
        found = {_decimals_of(o["network"], o["asset"]) for o in options}
        decimals = found.pop() if len(found) == 1 and None not in found else None
    if not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 36:
        raise ListingError("the listing's decimals aren't known: give \"decimals\" for an asset that isn't USDC")
    if raw.get("price_atomic") is not None:
        price_atomic = _int_amount(raw["price_atomic"])
    else:
        price_atomic = to_atomic(raw.get("price_usd"), decimals)
    if price_atomic is None:
        raise ListingError("the listing needs a price: price_usd (\"0.001\") or price_atomic (\"1000\")")
    usd = Decimal(price_atomic) / (Decimal(10) ** decimals)
    return {"skill": raw.get("skill"), "price_atomic": price_atomic, "price_usd": format(usd.normalize(), "f"),
            "decimals": decimals, "options": options, "source": str(raw.get("source") or source)}


def load_listing(spec: str, http: httpx.Client | None = None) -> dict[str, Any]:
    """A listing from a file path or an http(s) URL."""
    if spec.startswith(("http://", "https://")):
        client = http or httpx.Client(timeout=15.0, headers={"User-Agent": USER_AGENT})
        try:
            raw = client.get(spec).json()
        finally:
            if http is None:
                client.close()
    else:
        with open(spec, encoding="utf-8") as f:
            raw = json.load(f)
    return normalize_listing(raw, spec)


def x402_extensions(card: dict[str, Any]) -> list[dict[str, Any]]:
    """The card's a2a-x402 extension declarations (A2A 0.3 and 1.0 keep them in capabilities.extensions)."""
    capabilities = card.get("capabilities")
    found = []
    for ext in (capabilities.get("extensions") if isinstance(capabilities, dict) else None) or []:
        if isinstance(ext, dict) and isinstance(ext.get("uri"), str) \
                and ext["uri"].startswith(X402_EXTENSION_PREFIXES):
            found.append({"uri": ext["uri"], "required": ext.get("required"),
                          "params": ext.get("params") if isinstance(ext.get("params"), dict) else None})
    return found


def card_options(extensions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The options a card's x402 params list. Three forms are read: networks, assets and payTo (one address, or one
    per network); or a list of {network, asset, payTo} under accepts or options."""
    options: list[dict[str, str]] = []

    def add(network: Any, asset: Any, pay_to: Any) -> None:
        network = caip2(network)
        if not isinstance(network, str):
            return
        address = _asset_address(network, asset)
        if isinstance(address, str) and isinstance(pay_to, str) and pay_to:
            option = {"network": network, "asset": address, "pay_to": pay_to}
            if option not in options:
                options.append(option)

    for ext in extensions:
        params = ext.get("params") or {}
        listed = params.get("accepts", params.get("options"))
        if isinstance(listed, list):
            for item in listed:
                if isinstance(item, dict):
                    add(item.get("network"), item.get("asset"), item.get("payTo", item.get("pay_to")))
            continue
        networks = params.get("networks") or ([params["network"]] if params.get("network") else [])
        assets = params.get("assets") or ([params["asset"]] if params.get("asset") else [])
        pay_to = params.get("payTo", params.get("pay_to"))
        for network in networks if isinstance(networks, list) else []:
            target = pay_to.get(network) if isinstance(pay_to, dict) else pay_to
            for asset in assets if isinstance(assets, list) else []:
                add(network, asset, target)
    return options


def _card_skill(card: dict[str, Any], skill: str) -> dict[str, Any] | None:
    for entry in card.get("skills") or []:
        if isinstance(entry, dict) and entry.get("id") == skill:
            return entry
    return None


def rpc_endpoint(card: dict[str, Any], subject: str) -> tuple[str, str]:
    """Where the card says JSON-RPC goes: its url (A2A 0.3), else a JSON-RPC supportedInterface (1.0), else the
    subject itself. Returns the URL and which of the three it came from."""
    url = card.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url, "card.url"
    for interface in card.get("supportedInterfaces") or []:
        if isinstance(interface, dict) and str(interface.get("protocolBinding", "")).upper() == "JSONRPC" \
                and isinstance(interface.get("url"), str):
            return interface["url"], "card.supportedInterfaces"
    return subject, "subject"


# --- HTTP, bounded as NandaTown's a2a_transport bounds it -----------------------------------------------------------

class SubjectError(ValueError):
    """Something the subject did: the reason is recorded and charged to the subject, never to the checker."""


def _read_json(client: httpx.Client, method: str, url: str, *, timeout: float, max_bytes: int,
               body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    try:
        with client.stream(method, url, json=body, headers={"Accept-Encoding": "identity", **(headers or {})},
                           follow_redirects=False, timeout=timeout) as response:
            raw = bytearray()
            for chunk in response.iter_bytes():
                if len(raw) + len(chunk) > max_bytes:
                    raise SubjectError("response_budget_exceeded")
                raw.extend(chunk)
            status = response.status_code
    except httpx.TimeoutException:
        raise SubjectError("timeout") from None
    except httpx.HTTPError as exc:
        raise SubjectError(f"transport_error: {type(exc).__name__}") from None
    if not 200 <= status < 300:
        return status, None
    try:
        return status, json.loads(bytes(raw))
    except (ValueError, UnicodeError, RecursionError):
        raise SubjectError(f"invalid_json (HTTP {status})") from None


def _withhold_credentials(url: Any) -> Any:
    """A URL as recorded: user information, if any, is replaced, so a bundle never carries credentials."""
    if not isinstance(url, str):
        return url
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc="<credentials withheld>@" + parts.netloc.rsplit("@", 1)[1]))


def _absolute_http(url: Any) -> bool:
    if not isinstance(url, str) or any(ch.isspace() for ch in url):
        return False
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, UnicodeError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.host)


# --- Reading the A2A answer ----------------------------------------------------------------------------------------

def task_of(result: Any) -> tuple[str | None, dict[str, Any] | None]:
    """The kind ("task" or "message") and the object of a message/send result, in A2A 0.3's or 1.0's form."""
    if not isinstance(result, dict):
        return None, None
    for kind in ("task", "message"):
        if isinstance(result.get(kind), dict) and "kind" not in result:
            return kind, result[kind]
    kind = result.get("kind")
    if kind in ("task", "message"):
        return kind, result
    if isinstance(result.get("status"), dict) and "id" in result:
        return "task", result
    return (kind if isinstance(kind, str) else None), result


def state_of(task: dict[str, Any]) -> Any:
    status = task.get("status")
    state = status.get("state") if isinstance(status, dict) else None
    return STATES.get(state, state) if isinstance(state, str) else state


def x402_metadata(task: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """The a2a-x402 metadata: on the status message (where the extension puts it), else on the task."""
    status = task.get("status")
    message = status.get("message") if isinstance(status, dict) else None
    for where, holder in (("status.message.metadata", message), ("task.metadata", task)):
        metadata = holder.get("metadata") if isinstance(holder, dict) else None
        if isinstance(metadata, dict) and (STATUS_KEY in metadata or REQUIRED_KEY in metadata):
            return where, metadata
    return None, {}


def offer_terms(required: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """The options of an x402 PaymentRequired (v2's amount, or v1's maxAmountRequired), each as the five terms, with
    the problems that make it malformed."""
    problems: list[str] = []
    if not isinstance(required, dict):
        return [], ["x402.payment.required is not an object"]
    accepts = required.get("accepts")
    if not isinstance(accepts, list) or not accepts:
        return [], ["x402.payment.required lists no accepts"]
    options = []
    for index, item in enumerate(accepts, start=1):
        if not isinstance(item, dict):
            problems.append(f"option {index} is not an object")
            continue
        raw_amount = item.get("amount", item.get("maxAmountRequired"))
        option = {"scheme": item.get("scheme"), "network": caip2(item.get("network")),
                  "amount": _int_amount(raw_amount), "asset": item.get("asset"), "pay_to": item.get("payTo")}
        if item.get("network") != option["network"]:
            option["network_as_sent"] = item.get("network")
        missing = [k for k in ("scheme", "network", "asset", "pay_to") if not isinstance(option[k], str)
                   or not option[k]]
        if option["amount"] is None:
            missing.append("amount")
        if missing:
            problems.append(f"option {index} has no valid {', '.join(missing)}")
        options.append(option)
    return options, problems


def terms_key(options: list[dict[str, Any]]) -> list[list[Any]]:
    """The terms of an offer, in a stable order, with EVM hex compared without case."""
    rows = []
    for option in options:
        row = []
        for term in TERMS:
            value = option.get(term)
            if term in ("asset", "pay_to") and _evm(option.get("network")) and isinstance(value, str):
                value = value.lower()
            row.append(value)
        rows.append(row)
    return sorted(rows, key=canonical_json)


# --- The run --------------------------------------------------------------------------------------------------------

class _Recorder:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        self.intents: list[dict[str, Any]] = []

    def emit(self, kind: str, subject: str, detail: dict[str, Any] | None = None,
             observer: str = OBSERVER) -> str:
        event_id = f"ev-{len(self.events) + 1}"
        self.events.append({"event_id": event_id, "run_id": self.run_id, "at": time.time(), "observer": observer,
                            "kind": kind, "subject": _withhold_credentials(subject), "detail": detail or {}})
        return event_id

    def intend(self, action: str, payload: dict[str, Any]) -> None:
        self.intents.append({"intent_id": f"in-{len(self.intents) + 1}", "run_id": self.run_id, "at": time.time(),
                             "actor": REQUESTER, "action": action, "payload": payload})


def _client(http: httpx.Client | None, timeout: float, user_agent: str) -> tuple[httpx.Client, bool]:
    if http is not None:
        return http, False
    return httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": user_agent}), True


def _listing_from_agent(client: httpx.Client, card: dict[str, Any], extensions: list[dict[str, Any]], skill: str,
                        rpc_url: str, card_url: str, *, timeout: float, max_bytes: int) -> dict[str, Any]:
    """The listing a buyer reads from the agent before any task: options from the card, price from the skill."""
    options = card_options(extensions)
    if not options:
        raise ListingError("the card's a2a-x402 extension lists no network, asset and pay-to in its params")
    shown_card = _withhold_credentials(card_url)
    sources = {"options": f"{shown_card}#capabilities.extensions[a2a-x402].params"}
    price_usd = None
    listing_url = rpc_url.rstrip("/") + "/skills/" + quote(skill, safe="")
    shown_listing = _withhold_credentials(listing_url)
    try:
        status, page = _read_json(client, "GET", listing_url, timeout=timeout, max_bytes=max_bytes)
    except SubjectError:
        status, page = None, None
    if isinstance(page, dict) and page.get("id", skill) == skill and page.get("price") is not None:
        price_usd = "0" if str(page["price"]).strip().lower() == "free" else page["price"]
        sources["price"] = shown_listing
    if price_usd is None:
        entry = _card_skill(card, skill)
        match = PRICE_RE.search(str((entry or {}).get("description") or ""))
        if match:
            price_usd = match.group(1)
            sources["price"] = f"{shown_card}#skills[{skill}].description"
    if price_usd is None:
        raise ListingError(f"no listed price for skill {skill!r}: {shown_listing} answered"
                           f" {status if status is not None else 'nothing usable'}, and the card's skill entry"
                           " names no \"Price: $…\"")
    listing = normalize_listing({"skill": skill, "price_usd": price_usd, "options": options}, "agent")
    listing["source"] = sources
    return listing


def run_check(subject_url: str, skill: str, *, arguments: dict[str, Any] | None = None,
              listing: dict[str, Any] | None = None, out_dir: str = "runs", http: httpx.Client | None = None,
              repeat_delay: float = 1.0, user_agent: str = USER_AGENT, write: bool = True,
              ) -> tuple[str | None, dict[str, Any]]:
    """Run a2a-x402-offer@0.1 once against the agent at subject_url (its card's base URL) for one skill.

    Returns the bundle directory (None with write=False) and the result. `http` may be an injected httpx.Client, as
    NandaTown's path runner allows: requests go out as absolute URLs. `listing`, if given, replaces the agent's own
    (normalize_listing's form). Nothing is signed or paid."""
    timeout = float(PROFILE["limits"]["timeout_seconds"])
    max_bytes = int(PROFILE["limits"]["max_response_bytes"])
    run_id = "x402o-" + uuid.uuid4().hex[:12]
    context_id = "ctx-" + uuid.uuid4().hex[:12]
    rec = _Recorder(run_id)
    shown_subject = _withhold_credentials(subject_url)
    client, owned = _client(http, timeout, user_agent)
    card_url = rpc_url = None
    try:
        rec.intend("resolve", {"url": shown_subject})
        if not _absolute_http(subject_url):
            rec.emit("resolution_failed", str(shown_subject), {"reason": "invalid endpoint URL: expected an absolute"
                                                                         " http(s) URL"})
        else:
            rec.emit("resolution_hop", shown_subject, {"kind": "direct", "url": shown_subject})
            card = None
            base = subject_url.rstrip("/")
            rec.intend("fetch_card", {"url": shown_subject, "paths": list(CARD_PATHS)})
            reasons = []
            for path in CARD_PATHS:
                try:
                    status, body = _read_json(client, "GET", base + path, timeout=timeout, max_bytes=max_bytes)
                except SubjectError as exc:
                    reasons.append(f"{path}: {exc}")
                    break
                if status in (404, 410):
                    reasons.append(f"{path}: HTTP {status}")
                    continue
                if body is None:
                    reasons.append(f"{path}: HTTP {status}")
                    break
                if not isinstance(body, dict):
                    reasons.append(f"{path}: not a JSON object")
                    break
                card, card_url = body, base + path
                break
            if card is None:
                rec.emit("card_fetch_failed", shown_subject, {"reason": "; ".join(reasons) or "a2a_card_missing"})
            else:
                extensions = x402_extensions(card)
                rpc_url, rpc_source = rpc_endpoint(card, subject_url)
                entry = _card_skill(card, skill)
                card_event = rec.emit("card_retrieved", _withhold_credentials(card_url), {
                    "digest": fingerprint(card), "name": card.get("name"), "version": card.get("version"),
                    "rpc_url": _withhold_credentials(rpc_url), "rpc_url_from": rpc_source,
                    "x402_extensions": extensions, "skill": skill, "skill_listed": entry is not None,
                    "skills": len(card.get("skills") or [])})
                _ = card_event
                # The listing: the operator's, or the agent's own.
                if listing is not None:
                    expected = dict(listing)
                    rec.emit("listing_expected", skill, {**expected, "from": "operator",
                                                         "digest": fingerprint(expected)})
                else:
                    rec.intend("fetch_listing", {"skill": skill})
                    try:
                        expected = _listing_from_agent(client, card, extensions, skill, rpc_url, card_url,
                                                       timeout=timeout, max_bytes=max_bytes)
                        rec.emit("listing_expected", skill, {**expected, "from": "agent",
                                                             "digest": fingerprint(expected)})
                    except ListingError as exc:
                        rec.emit("listing_unavailable", skill, {"reason": str(exc)})
                _exchanges(rec, client, rpc_url, skill, arguments if arguments is not None else _example(entry),
                           context_id, [e["uri"] for e in extensions], timeout=timeout, max_bytes=max_bytes,
                           repeat_delay=repeat_delay)
    finally:
        if owned:
            client.close()

    result = evaluate(run_id, rec.events)
    run = {
        "run_id": run_id,
        "profile_name": PROFILE_REF,
        "profile_fingerprint": fingerprint(PROFILE),
        "created_at": time.time(),
        "participants": [{"name": REQUESTER, "role": "requester"},
                         {"name": str(shown_subject or "?"), "role": "subject"}],
        "releases": {"nandatown": BUNDLE_FORMAT, "evaluator": EVALUATOR_VERSION, "x402-offer": VERSION,
                     "python": sys.version.split()[0]},
        "config": {"mode": "path", "subject": shown_subject, "profile": PROFILE_REF, "skill": skill,
                   "arguments": arguments, "listing_from": "operator" if listing is not None else "agent",
                   "context_id": context_id, "repeat_delay_seconds": repeat_delay, "user_agent": user_agent,
                   "client_ownership": "owned" if owned else "injected", "payment": "none: nothing signed or paid",
                   "rerun_command": " ".join(["python", "x402_offer.py", "check", "--url", str(shown_subject),
                                              "--skill", skill])},
    }
    directory = write_bundle(os.path.join(out_dir, run_id), run, rec.intents, rec.events, result) if write \
        else None
    return directory, result


def _example(entry: dict[str, Any] | None) -> dict[str, Any]:
    """The arguments the card's own example for the skill uses, else none."""
    for example in (entry or {}).get("examples") or []:
        try:
            parsed = json.loads(example) if isinstance(example, str) else example
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("arguments"), dict):
            return parsed["arguments"]
    return {}


def _exchanges(rec: _Recorder, client: httpx.Client, rpc_url: str, skill: str, arguments: dict[str, Any],
               context_id: str, extension_uris: list[str], *, timeout: float, max_bytes: int,
               repeat_delay: float) -> None:
    """Ask for the skill twice, unpaid, in one context: the second ask is the repeat."""
    headers = {"X-A2A-Extensions": ", ".join(extension_uris)} if extension_uris else {}
    for attempt in (1, 2):
        if attempt == 2 and repeat_delay > 0:
            time.sleep(repeat_delay)
        body = {"jsonrpc": "2.0", "id": attempt, "method": "message/send",
                "params": {"message": {"kind": "message", "role": "user", "messageId": "m-" + uuid.uuid4().hex[:12],
                                       "contextId": context_id,
                                       "parts": [{"kind": "data", "data": {"skill": skill,
                                                                           "arguments": arguments}}]}}}
        rec.intend("message_send", {"attempt": attempt, "url": _withhold_credentials(rpc_url), "skill": skill,
                                    "arguments": arguments, "payment": None})
        try:
            status, payload = _read_json(client, "POST", rpc_url, body=body, headers=headers, timeout=timeout,
                                         max_bytes=max_bytes)
            if payload is None:
                raise SubjectError(f"a2a_http_status_{status}")
            if not isinstance(payload, dict):
                raise SubjectError("a2a_json_not_object")
            if "error" in payload:
                error = payload.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                message = str(error.get("message"))[:200] if isinstance(error, dict) else str(error)[:200]
                raise SubjectError(f"a2a_rpc_error {code}: {message}")
            kind, task = task_of(payload.get("result"))
            if task is None:
                raise SubjectError("a2a_rpc_invalid_result")
            where, metadata = x402_metadata(task)
            required = metadata.get(REQUIRED_KEY)
            options, problems = offer_terms(required) if required is not None else ([], [])
            detail = {"attempt": attempt, "ok": True, "kind": kind, "state": state_of(task),
                      "task_id": task.get("id"), "x402_status": metadata.get(STATUS_KEY),
                      "x402_metadata_at": where, "x402_version": (required or {}).get("x402Version")
                      if isinstance(required, dict) else None,
                      "options": options, "offer_problems": problems,
                      "terms": terms_key(options) if options and not problems else None,
                      "offer_digest": fingerprint(required) if required is not None else None}
            rec.emit("protocol_exchange", skill, detail)
            if not (kind == "task" and detail["state"] == "input-required"
                    and detail["x402_status"] == "payment-required" and options and not problems):
                break
        except SubjectError as exc:
            rec.emit("protocol_exchange", skill, {"attempt": attempt, "ok": False, "reason": str(exc)})
            break
        except Exception as exc:  # the checker's own fault: an error, never the subject's failure
            rec.emit("checker_error", skill, {"attempt": attempt, "reason": f"{type(exc).__name__}: {exc}"})
            break


# --- The evaluator: stages from events alone ------------------------------------------------------------------------

def _stage(name: str, status: str, evidence: list[str] | None = None, note: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "evidence": evidence or [], "note": note}


def cascade_unreached(stages: list[dict[str, Any]]) -> None:
    """NandaTown's rule: after the first failed or errored stage, an inconclusive later stage was never reached."""
    broken = False
    for stage in stages:
        if broken and stage["status"] == "not_enough_evidence":
            stage["status"] = "not_tested"
            stage["note"] = "not reached: an earlier boundary broke" + (f" ({stage['note']})" if stage["note"]
                                                                         else "")
        if stage["status"] in ("failed", "error"):
            broken = True


def stage_verdict(stages: list[dict[str, Any]]) -> str:
    """NandaTown's rule: an error outranks a failure; missing evidence never becomes a pass."""
    applicable = [s for s in stages if s["status"] != "not_tested"]
    if any(s["status"] == "error" for s in applicable):
        return "error"
    if any(s["status"] == "failed" for s in applicable):
        return "failed"
    if applicable and all(s["status"] == "passed" for s in applicable):
        return "passed"
    return "incomplete"


def _listed(listing: dict[str, Any], network: Any, field: str) -> list[str]:
    """The listed values of a field (asset or pay_to) for a network, or for every listed network when the network
    itself isn't listed, so each defect fails only its own stage."""
    on_network = [o[field] for o in listing["options"] if o["network"] == network]
    return on_network or [o[field] for o in listing["options"]]


def _option_name(index: int, option: dict[str, Any]) -> str:
    return f"option {index} ({option.get('network_as_sent') or option.get('network')})"


def evaluate(run_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """The stage results, worked out from the recorded events alone, so any holder of the bundle can replay them."""

    def find(kind: str, **conditions: Any) -> list[dict[str, Any]]:
        return [e for e in events if e.get("kind") == kind
                and all((e.get("detail") or {}).get(k) == v for k, v in conditions.items())]

    stages: list[dict[str, Any]] = []
    hops, unresolved = find("resolution_hop"), find("resolution_failed")
    if hops:
        stages.append(_stage("resolution", "passed", [hops[0]["event_id"]]))
    elif unresolved:
        stages.append(_stage("resolution", "failed", [unresolved[0]["event_id"]],
                             unresolved[0]["detail"].get("reason", "")))
    else:
        stages.append(_stage("resolution", "not_enough_evidence", note="no resolution was attempted"))

    cards, card_failures = find("card_retrieved"), find("card_fetch_failed")
    card = cards[0] if cards else None
    if card:
        stages.append(_stage("agent_card_retrieval", "passed", [card["event_id"]],
                             f"observed card {card['detail'].get('digest', '')[:23]}; JSON-RPC at"
                             f" {card['detail'].get('rpc_url')} ({card['detail'].get('rpc_url_from')})"))
    elif card_failures:
        stages.append(_stage("agent_card_retrieval", "failed", [card_failures[0]["event_id"]],
                             card_failures[0]["detail"].get("reason", "")))
    else:
        stages.append(_stage("agent_card_retrieval", "not_enough_evidence", note="retrieval was never reached"))

    if card:
        uris = [e.get("uri") for e in card["detail"].get("x402_extensions") or []]
        if uris:
            stages.append(_stage("x402_extension_declared", "passed", [card["event_id"]],
                                 "declares " + ", ".join(str(u) for u in uris)))
        else:
            stages.append(_stage("x402_extension_declared", "failed", [card["event_id"]],
                                 "the card declares no a2a-x402 extension in capabilities.extensions"))
    else:
        stages.append(_stage("x402_extension_declared", "not_enough_evidence", note="no card to read"))

    listings, unlisted = find("listing_expected"), find("listing_unavailable")
    listing_event = listings[0] if listings else None
    listing = listing_event["detail"] if listing_event else None
    if listing_event:
        stages.append(_stage("listing_retrieval", "passed", [listing_event["event_id"]],
                             f"listed ${listing.get('price_usd')} ({listing.get('price_atomic')} units) on "
                             + ", ".join(f"{o['network']} to {_short(o['pay_to'])}" for o in listing["options"])))
    elif unlisted:
        stages.append(_stage("listing_retrieval", "failed", [unlisted[0]["event_id"]],
                             unlisted[0]["detail"].get("reason", "")))
    else:
        stages.append(_stage("listing_retrieval", "not_enough_evidence", note="no listing was read"))

    checker_errors = find("checker_error")
    first = find("protocol_exchange", attempt=1)
    exchange = first[0] if len(first) == 1 else None
    offered = False
    if checker_errors:
        stages.append(_stage("payment_request", "error", [checker_errors[0]["event_id"]],
                             "the checker's own code failed: " + checker_errors[0]["detail"].get("reason", "")
                             + "; this run is an error, not the subject's failure"))
    elif len(first) > 1:
        stages.append(_stage("payment_request", "failed", [e["event_id"] for e in first],
                             f"expected one exchange for attempt 1, observed {len(first)}"))
    elif exchange is None:
        stages.append(_stage("payment_request", "not_enough_evidence", note="no task was asked for"))
    else:
        d = exchange["detail"]
        why = None
        if not d.get("ok"):
            why = d.get("reason", "the request failed")
        elif d.get("kind") != "task":
            why = f"the agent answered with a {d.get('kind')!r}, not a task"
        elif d.get("state") != "input-required":
            why = (f"the unpaid task ended {d.get('state')!r}, not 'input-required'"
                   + ("; it completed without asking for payment" if d.get("state") == "completed" else ""))
        elif d.get("x402_status") != "payment-required":
            why = f"{STATUS_KEY} is {d.get('x402_status')!r}, not 'payment-required'"
        elif not d.get("options"):
            why = f"no x402 offer in {REQUIRED_KEY}"
        elif d.get("offer_problems"):
            why = "the offer is malformed: " + "; ".join(d["offer_problems"])
        if why:
            stages.append(_stage("payment_request", "failed", [exchange["event_id"]], why))
        else:
            offered = True
            stages.append(_stage("payment_request", "passed", [exchange["event_id"]],
                                 f"task {d.get('task_id')} asks for payment ({d.get('x402_metadata_at')}):"
                                 f" {len(d['options'])} option(s), x402 v{d.get('x402_version')}"))

    if offered and listing:
        options = exchange["detail"]["options"]
        evidence = [listing_event["event_id"], exchange["event_id"]]
        price = listing["price_atomic"]
        over = [f"{_option_name(i, o)} asks {o['amount']}, listed {price}"
                + (f" ({o['amount'] / price:.2f}x)" if price else "")
                for i, o in enumerate(options, start=1) if o["amount"] > price]
        stages.append(_stage("price_within_listing", "failed" if over else "passed", evidence,
                             "; ".join(over) if over else f"every option asks at most {price} units"
                                                          f" (${listing.get('price_usd')})"))
        checks = (
            ("pay_to_matches_listing", "pay_to", "pay-to"),
            ("network_matches_listing", "network", "network"),
            ("asset_matches_listing", "asset", "asset"),
        )
        listed_networks = [o["network"] for o in listing["options"]]
        for name, field, label in checks:
            wrong = []
            for i, o in enumerate(options, start=1):
                if field == "network":
                    if o["network"] not in listed_networks:
                        wrong.append(f"{_option_name(i, o)} is not a listed network"
                                     f" ({', '.join(sorted(set(listed_networks)))})")
                    continue
                allowed = _listed(listing, o["network"], field)
                if not any(same_address(o["network"], o[field], a) for a in allowed):
                    wrong.append(f"{_option_name(i, o)}: {label} {o[field]} is not the listing's"
                                 f" {' or '.join(allowed)}")
            stages.append(_stage(name, "failed" if wrong else "passed", evidence,
                                 "; ".join(wrong) if wrong else f"every option's {label} is listed"))
    else:
        missing = "no offer to compare" if not offered else "no listing to compare with"
        for name in ("price_within_listing", "pay_to_matches_listing", "network_matches_listing",
                     "asset_matches_listing"):
            stages.append(_stage(name, "not_enough_evidence", note=missing))

    second = find("protocol_exchange", attempt=2)
    if not offered or checker_errors:
        stages.append(_stage("repeat_same_terms", "not_enough_evidence",
                             note="the controlled condition was never reached"))
    elif len(second) != 1:
        stages.append(_stage("repeat_same_terms", "failed" if second else "not_enough_evidence",
                             [e["event_id"] for e in second],
                             f"expected one exchange for attempt 2, observed {len(second)}"))
    else:
        d2 = second[0]["detail"]
        evidence = [exchange["event_id"], second[0]["event_id"]]
        if not d2.get("ok"):
            stages.append(_stage("repeat_same_terms", "failed", evidence,
                                 "the repeated request failed: " + str(d2.get("reason"))))
        elif d2.get("kind") != "task" or d2.get("state") != "input-required" \
                or d2.get("x402_status") != "payment-required" or not d2.get("terms"):
            stages.append(_stage("repeat_same_terms", "failed", evidence,
                                 f"the repeat didn't ask for payment the same way: kind {d2.get('kind')!r}, state"
                                 f" {d2.get('state')!r}, {STATUS_KEY} {d2.get('x402_status')!r}"))
        elif d2["terms"] != exchange["detail"]["terms"]:
            before = [r for r in exchange["detail"]["terms"] if r not in d2["terms"]]
            after = [r for r in d2["terms"] if r not in exchange["detail"]["terms"]]
            stages.append(_stage("repeat_same_terms", "failed", evidence,
                                 f"the repeat changed the terms: {_terms_text(before)} became {_terms_text(after)}"))
        else:
            stages.append(_stage("repeat_same_terms", "passed", evidence,
                                 f"the same {len(d2['terms'])} option(s) both times"))

    cascade_unreached(stages)
    return {"run_id": run_id, "evaluator_version": EVALUATOR_VERSION, "stages": stages,
            "verdict": stage_verdict(stages), "evaluated_at": time.time()}


def _terms_text(rows: list[list[Any]]) -> str:
    return "; ".join("/".join(_short(v) for v in row) for row in rows) or "nothing"


# --- The bundle ------------------------------------------------------------------------------------------------------

RECORD_FILES = ["profile.json", "run.json", "intents.jsonl", "events.jsonl", "result.json"]


def evidence_records(result: dict[str, Any], run: dict[str, Any], events: list[dict[str, Any]]
                     ) -> list[dict[str, Any]]:
    """One NandaTown evidence record per stage (schemas/evidence_record.schema.json): the observer, the exact subject
    (the card at its digest, and the skill), the capability, the stage as the test, and the events it rests on.
    NandaTown's record has no not_tested, so a stage not reached is not_enough_evidence; an error stage, the
    checker's own fault, gets no record."""
    cards = [e for e in events if e.get("kind") == "card_retrieved"]
    subject = run["config"].get("subject") or "?"
    if cards:
        subject = f"{cards[0]['subject']}@{cards[0]['detail'].get('digest', '')[:19]}"
    subject = f"{subject}#skill={run['config'].get('skill')}"
    records = []
    for index, stage in enumerate(result["stages"], start=1):
        if stage["status"] == "error":
            continue
        status = stage["status"] if stage["status"] in ("passed", "failed") else "not_enough_evidence"
        records.append({"record_id": f"{run['run_id']}-{index}", "observer": OBSERVER, "subject": subject,
                        "capability": PROFILE["capability"], "test": stage["name"], "result": status,
                        "at": result["evaluated_at"], "evidence": list(stage["evidence"])})
    return records


def write_bundle(directory: str, run: dict[str, Any], intents: list[dict[str, Any]], events: list[dict[str, Any]],
                 result: dict[str, Any]) -> str:
    os.makedirs(directory, exist_ok=True)

    def put(name: str, text: str) -> None:
        with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
            f.write(text)

    put("profile.json", json.dumps(PROFILE, indent=2))
    put("run.json", json.dumps(run, indent=2))
    put("intents.jsonl", "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in intents))
    put("events.jsonl", "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in events))
    put("result.json", json.dumps(result, indent=2))
    files = {name: _sha256_file(os.path.join(directory, name)) for name in RECORD_FILES}
    manifest = {"mode": "path", "files": files, "bundle_fingerprint": fingerprint(files), "created_at": time.time(),
                "nandatown_version": run["releases"]["nandatown"], "evaluator_version": result["evaluator_version"]}
    put("manifest.json", json.dumps(manifest, indent=2))
    put("evidence.json", json.dumps({"bundle_fingerprint": manifest["bundle_fingerprint"],
                                     "records": evidence_records(result, run, events)}, indent=2))
    put("report.md", render_report(run, events, result, manifest))
    return directory


def _jsonl(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.split("\n") if line]


def load_bundle(directory: str) -> dict[str, Any]:
    def read(name: str) -> str:
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            return f.read()

    return {"directory": directory, "manifest": json.loads(read("manifest.json")),
            "profile": json.loads(read("profile.json")), "run": json.loads(read("run.json")),
            "intents": _jsonl(read("intents.jsonl")), "events": _jsonl(read("events.jsonl")),
            "result": json.loads(read("result.json"))}


def verify_bundle(directory: str) -> list[str]:
    """Integrity and replay: the manifest's hashes, the records' bindings (as NandaTown's verify checks them), and a
    fresh evaluation of the recorded events equal to result.json. Returns the problems; none means it holds."""
    problems: list[str] = []
    try:
        with open(os.path.join(directory, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as exc:
        return [f"manifest.json unreadable: {exc}"]
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or set(files) != set(RECORD_FILES):
        return ["the manifest must name exactly the five records"]
    if manifest.get("bundle_fingerprint") != fingerprint(files):
        problems.append("bundle fingerprint mismatch")
    for name, expected in files.items():
        try:
            actual = _sha256_file(os.path.join(directory, name))
        except OSError as exc:
            problems.append(f"{name} unreadable: {exc}")
            continue
        if actual != expected:
            problems.append(f"{name} hash mismatch")
    if problems:
        return problems
    try:
        bundle = load_bundle(directory)
    except (OSError, ValueError) as exc:
        return [f"records unreadable: {exc}"]
    run, result, profile = bundle["run"], bundle["result"], bundle["profile"]
    if run.get("run_id") != result.get("run_id"):
        problems.append("run and result name different run ids")
    if run.get("profile_name") != f"{profile.get('profile_id')}@{profile.get('version')}":
        problems.append("run profile name does not match profile")
    if run.get("profile_fingerprint") != fingerprint(profile):
        problems.append("run profile fingerprint does not match profile")
    if any(i.get("run_id") != run.get("run_id") for i in bundle["intents"]):
        problems.append("intent names a different run id")
    if any(e.get("run_id") != run.get("run_id") for e in bundle["events"]):
        problems.append("event names a different run id")
    if manifest.get("nandatown_version") != (run.get("releases") or {}).get("nandatown"):
        problems.append("manifest bundle format does not match run release")
    if (run.get("releases") or {}).get("evaluator") != result.get("evaluator_version") \
            or manifest.get("evaluator_version") != result.get("evaluator_version"):
        problems.append("evaluator versions disagree")
    if (run.get("config") or {}).get("mode") != manifest.get("mode"):
        problems.append("run mode does not match manifest mode")
    for kind, ids in (("intent", [i.get("intent_id") for i in bundle["intents"]]),
                      ("event", [e.get("event_id") for e in bundle["events"]])):
        if len(ids) != len(set(ids)):
            problems.append(f"duplicate {kind} ids")
    if result.get("evaluator_version") != EVALUATOR_VERSION:
        problems.append(f"evaluator version {result.get('evaluator_version')!r} is not this checker's"
                        f" {EVALUATOR_VERSION!r}; replay not checked")
        return problems
    replay = evaluate(result["run_id"], bundle["events"])
    strip = lambda r: {k: v for k, v in r.items() if k != "evaluated_at"}  # noqa: E731
    if strip(replay) != strip(result):
        problems.append("evaluator replay mismatch: result.json is not what the recorded events give")
    try:
        with open(os.path.join(directory, "evidence.json"), encoding="utf-8") as f:
            evidence = json.load(f)
        if evidence.get("bundle_fingerprint") != manifest.get("bundle_fingerprint") or \
                evidence.get("records") != evidence_records(result, run, bundle["events"]):
            problems.append("evidence.json does not match the result")
    except (OSError, ValueError):
        problems.append("evidence.json unreadable")
    return problems


# --- The report ------------------------------------------------------------------------------------------------------

def render_report(run: dict[str, Any], events: list[dict[str, Any]], result: dict[str, Any],
                  manifest: dict[str, Any]) -> str:
    config = run["config"]
    lines = [f"# {PROFILE_REF}: {config.get('subject')}, skill {config.get('skill')}", "",
             f"Verdict: **{result['verdict']}**. Evaluator {result['evaluator_version']}, run {run['run_id']},"
             f" bundle {manifest['bundle_fingerprint'][:23]}. Nothing was signed or paid.", "",
             "| Stage | Status | Note |", "|---|---|---|"]
    for stage in result["stages"]:
        note = str(stage["note"]).replace("|", "\\|")
        lines.append(f"| {stage['name']} | {stage['status']} | {note} |")
    listing = next((e["detail"] for e in events if e["kind"] == "listing_expected"), None)
    if listing:
        lines += ["", f"Listing ({listing.get('from')}): ${listing.get('price_usd')} = {listing.get('price_atomic')}"
                      f" units; " + "; ".join(f"{o['network']}, asset {o['asset']}, pay-to {o['pay_to']}"
                                             for o in listing["options"])]
    for e in events:
        if e["kind"] == "protocol_exchange" and e["detail"].get("options"):
            d = e["detail"]
            lines.append(f"Offer, attempt {d['attempt']}: " + "; ".join(
                f"{o.get('scheme')} {o.get('amount')} of {o.get('asset')} on {o.get('network')} to {o.get('pay_to')}"
                for o in d["options"]))
    lines += ["", f"Replay with `python x402_offer.py verify {run['run_id']}`. NandaTown's `nandatown verify` checks"
                  " the same hashes and bindings and reports that it has no x402-offer evaluator to replay."]
    return "\n".join(lines) + "\n"


# --- Command line ----------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="x402_offer", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="ask one paid skill twice, unpaid, and judge its offer")
    check.add_argument("--url", required=True, help="the agent's base URL, where its card is")
    check.add_argument("--skill", required=True)
    check.add_argument("--arguments", default=None, help="the skill's arguments as JSON (default: the card's example)")
    check.add_argument("--listing", default=None, help="a listing file or URL to judge against instead of the agent's")
    check.add_argument("--out", default="runs")
    check.add_argument("--repeat-delay", type=float, default=1.0, help="seconds between the two asks")
    verify = sub.add_parser("verify", help="check a bundle's hashes and replay its stages")
    verify.add_argument("bundle")
    report = sub.add_parser("report", help="print a bundle's report")
    report.add_argument("bundle")
    args = parser.parse_args(argv)

    if args.command == "check":
        try:
            arguments = json.loads(args.arguments) if args.arguments else None
            listing = load_listing(args.listing) if args.listing else None
        except (ValueError, OSError, httpx.HTTPError) as exc:
            print(f"x402_offer: {exc}", file=sys.stderr)
            return 2
        directory, result = run_check(args.url, args.skill, arguments=arguments, listing=listing, out_dir=args.out,
                                      repeat_delay=args.repeat_delay)
        with open(os.path.join(directory, "report.md"), encoding="utf-8") as f:
            print(f.read())
        print(f"Evidence bundle: {directory}")
        return 0 if result["verdict"] == "passed" else 1
    if args.command == "verify":
        problems = verify_bundle(args.bundle)
        for problem in problems:
            print(problem)
        print("verified: hashes, bindings and replay hold" if not problems else f"{len(problems)} problem(s)")
        return 0 if not problems else 1
    with open(os.path.join(args.bundle, "report.md"), encoding="utf-8") as f:
        print(f.read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
