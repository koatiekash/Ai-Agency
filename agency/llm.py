"""Single entry point for every agent's call to Claude.

- Structured output: each call passes a JSON schema, so replies are always valid JSON.
- Refusal fallback: server-side fallback is enabled, so a safety decline is retried
  on a fallback model automatically.
- Offline mode: with no Anthropic credentials, calls return schema-shaped placeholder
  data marked "simulated": true, so the pipeline and the 3D city still run end to end.
  Simulated output is never a basis for a live action (the executors check this flag).
"""
from __future__ import annotations

import json
import logging
import os
import random
from typing import Any

log = logging.getLogger("agency.llm")

MODEL = os.environ.get("AGENCY_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("AGENCY_EFFORT", "medium")          # low | medium | high | xhigh | max
COMPLIANCE_EFFORT = os.environ.get("AGENCY_COMPLIANCE_EFFORT", "high")

_client = None


def _has_credentials() -> bool:
    if os.environ.get("AGENCY_OFFLINE") == "1":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.path.isdir(os.path.expanduser("~/.config/anthropic")))


def online() -> bool:
    return _has_credentials()


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=3)
    return _client


def ask_json(system: str, prompt: str, schema: dict, *, effort: str | None = None,
             max_tokens: int = 16000) -> dict:
    """Ask Claude for a JSON object that matches `schema`."""
    if not _has_credentials():
        out = simulate(schema)
        out["simulated"] = True
        return out

    import anthropic
    client = _get_client()
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort or EFFORT,
                           "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as e:
        log.warning("rate limited after retries: %s", e)
        raise
    except anthropic.APIStatusError as e:
        log.error("Claude API error %s: %s", e.status_code, e.message)
        raise
    except anthropic.APIConnectionError as e:
        log.error("could not reach the Claude API: %s", e)
        raise

    if resp.stop_reason == "refusal":
        cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
        return {"refused": True, "refusal_category": cat, "summary": "Request declined by the model."}
    if resp.stop_reason == "max_tokens":
        log.warning("hit max_tokens; output may be incomplete")
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return json.loads(text)


# --------------------------------------------------------------------------- #
# Offline simulator: builds a schema-valid object with placeholder content.
# --------------------------------------------------------------------------- #
_WORDS = ["momentum", "margin", "demand", "signal", "buyer", "offer", "hook", "trend",
          "risk", "volume", "spread", "comps", "creator", "conversion", "regime"]


def simulate(schema: dict, key: str = "") -> Any:
    t = schema.get("type")
    if isinstance(t, list):                       # nullable, e.g. ["number", "null"]
        t = next((x for x in t if x != "null"), "string")
    if "enum" in schema:
        return random.choice(schema["enum"])
    if t == "object":
        return {k: simulate(v, k) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [simulate(schema.get("items", {"type": "string"}), key) for _ in range(random.randint(1, 3))]
    if t == "number":
        return round(random.uniform(0, 100), 2)
    if t == "integer":
        return random.randint(0, 10)
    if t == "boolean":
        return random.random() > 0.3
    return f"[simulated {key or 'text'}] " + " ".join(random.sample(_WORDS, 3))
