"""Gemini wrapper: structured output, key rotation on rate limits, and call metadata for the audit trail."""
import itertools
import os
import sys
import time

import httpx

from . import config
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from .db import sha256


_clients = None
_cycle = None


def _next_client() -> genai.Client:
    global _clients, _cycle
    if _clients is None:
        keys = [k.strip() for k in config.required("GEMINI_API_KEYS").split(",") if k.strip()]
        _clients = [genai.Client(api_key=k) for k in keys]
        _cycle = itertools.cycle(_clients)
    if not _clients:
        raise config.ConfigurationError("Set at least one Gemini API key in .env, then restart the server.")
    return next(_cycle)


def model_for(tier: str) -> str:
    return os.environ.get("GEMINI_MODEL_PRO" if tier == "pro" else "GEMINI_MODEL_FAST", "gemini-flash-latest")


class ModelUnavailable(RuntimeError):
    """Every configured key and model refused the request."""


# Free-tier keys have a small per-model daily quota, so a spent model falls through to the next one.
DEFAULT_FALLBACKS = ("gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-2.5-flash,"
                     "gemini-flash-lite-latest,gemini-3.5-flash-lite,gemini-2.5-flash-lite")
_spent: set[tuple[int, str]] = set()    # (key index, model) pairs with no quota left today, or not offered


def models_for(tier: str) -> list[str]:
    fallbacks = os.environ.get("GEMINI_MODEL_FALLBACKS", DEFAULT_FALLBACKS).split(",")
    return list(dict.fromkeys(m.strip() for m in [model_for(tier), *fallbacks] if m.strip()))


def generate(agent: str, system: str, prompt: str, schema: type[BaseModel], tier: str = "pro"):
    """Return (parsed, meta). `meta` is written to the audit trail by the caller, and names the model used."""
    request = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                          response_schema=schema, temperature=0.1)
    _next_client()
    reason = "no model was tried"
    for model in models_for(tier):
        for key, client in enumerate(_clients):
            if (key, model) in _spent:
                continue
            for attempt in range(4):
                try:
                    resp = client.models.generate_content(model=model, contents=prompt, config=request)
                    parsed = resp.parsed if resp.parsed is not None else schema.model_validate_json(resp.text)
                    meta = {"agent": agent, "model": model, "prompt_sha256": sha256(system + "\n" + prompt),
                            "output_sha256": sha256(resp.text or "")}
                    return parsed, meta
                except errors.APIError as e:
                    reason = f"{model}: {e.code} {e.status}"
                    if e.code == 404 or (e.code == 429 and "PerDay" in str(e)):
                        _spent.add((key, model))
                        break
                    if e.code not in (429, 500, 503):
                        raise
                    time.sleep(min(3 * 2 ** attempt, 30))
                except httpx.TransportError as e:     # dropped connection or timeout: try again
                    reason = f"{model}: {type(e).__name__}"
                    time.sleep(min(3 * 2 ** attempt, 30))
    raise ModelUnavailable(
        f"Gemini could not serve this request with any configured key or model (last: {reason}). "
        "Daily free-tier quota is probably spent: add another key to GEMINI_API_KEYS or enable billing, then restart.")


def embed(texts: list[str]) -> list[list[float]]:
    resp = _next_client().models.embed_content(
        model=os.environ.get("GEMINI_MODEL_EMBED", "gemini-embedding-001"), contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=768))
    return [e.values for e in resp.embeddings]


if __name__ == "__main__" and "--list-models" in sys.argv:
    for m in _next_client().models.list():
        print(m.name)
