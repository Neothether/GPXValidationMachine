"""Gemini wrapper: structured output, key rotation on rate limits, and call metadata for the audit trail."""
import itertools
import os
import sys
import time

from dotenv import find_dotenv, load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from .db import sha256

load_dotenv(find_dotenv(usecwd=True))

_clients = None
_cycle = None


def _next_client() -> genai.Client:
    global _clients, _cycle
    if _clients is None:
        keys = [k.strip() for k in os.environ["GEMINI_API_KEYS"].split(",") if k.strip()]
        _clients = [genai.Client(api_key=k) for k in keys]
        _cycle = itertools.cycle(_clients)
    return next(_cycle)


def model_for(tier: str) -> str:
    return os.environ["GEMINI_MODEL_PRO" if tier == "pro" else "GEMINI_MODEL_FAST"]


def generate(agent: str, system: str, prompt: str, schema: type[BaseModel], tier: str = "pro"):
    """Return (parsed, meta). `meta` is written to the audit trail by the caller."""
    model = model_for(tier)
    config = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                         response_schema=schema, temperature=0.1)
    attempts = max(7, 2 * len(_clients or [1]))
    for attempt in range(attempts):
        client = _next_client()
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            parsed = resp.parsed if resp.parsed is not None else schema.model_validate_json(resp.text)
            meta = {"agent": agent, "model": model, "prompt_sha256": sha256(system + "\n" + prompt),
                    "output_sha256": sha256(resp.text or "")}
            return parsed, meta
        except errors.APIError as e:
            if e.code not in (429, 500, 503) or attempt == attempts - 1:
                raise
            time.sleep(min(3 * 2 ** attempt, 40))


def embed(texts: list[str]) -> list[list[float]]:
    resp = _next_client().models.embed_content(
        model=os.environ.get("GEMINI_MODEL_EMBED", "gemini-embedding-001"), contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=768))
    return [e.values for e in resp.embeddings]


if __name__ == "__main__" and "--list-models" in sys.argv:
    for m in _next_client().models.list():
        print(m.name)
