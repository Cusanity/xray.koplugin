"""
AI client abstraction for X-Ray Generator.

Handles all AI provider communication (OpenAI, Claude, Cusanity) with
unified JSON validation, retry logic, and caching.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from typing import Any

from openai import OpenAI

import copilot_auth
import retry_config
from retry_config import (
    CooldownRegistry,
    RetryChain,
    RetryEntry,
)

try:
    import anthropic
except ImportError:
    anthropic = None

# =============================================================================
# Configuration (loaded from environment)
# =============================================================================

API_BASE_URL = os.environ.get("XRAY_API_BASE", "http://localhost:8080/v1")
API_KEY = os.environ.get("XRAY_API_KEY", "")
MODEL_NAME = os.environ.get("XRAY_MODEL", "gemini-2.5-flash-lite")
TEMPERATURE = 0.4
TOP_P = 0.95
AI_TIMEOUT_SECONDS = 120.0

# Default per-chunk input budget (characters) when no override applies.
DEFAULT_CHUNK_SIZE = 15_000

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_BASE = "https://api.groq.com/openai/v1"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = "https://api.deepseek.com"
COPILOT_API_BASE = copilot_auth.COPILOT_API_BASE
COPILOT_DEFAULT_MODEL = copilot_auth.COPILOT_DEFAULT_MODEL
MODELS_ENDPOINT = os.environ.get("XRAY_MODELS_ENDPOINT", "http://localhost:8045/v1/models")


def parse_headers(text: str) -> dict[str, str]:
    """Parse custom headers from a JSON object or ``Key: Value`` lines."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (ValueError, TypeError):
        pass
    headers: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key:
            headers[key] = value.strip()
    return headers


# OpenAI-compatible endpoint extras (proxies/gateways often need these).
API_DEFAULT_HEADERS: dict[str, str] = parse_headers(os.environ.get("XRAY_API_HEADERS", ""))

_claude_models_cache: list[str] | None = None
_groq_models_cache: list[str] | None = None
_openai_models_cache: list[str] | None = None
_gemini_models_cache: list[str] | None = None
_deepseek_models_cache: list[str] | None = None
_copilot_models_cache: list[str] | None = None


def _fetch_openai_models() -> list[str]:
    """Fetch available models from the OpenAI-compatible /v1/models endpoint."""
    global _openai_models_cache
    if _openai_models_cache is not None:
        return _openai_models_cache

    try:
        import requests
        resp = requests.get(MODELS_ENDPOINT, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        models = [
            m["id"] for m in data.get("data", [])
            if not m["id"].endswith("*")
            and "image" not in m["id"]
            and "gpt" not in m["id"]
        ]
        if models:
            _openai_models_cache = models
            print(f"  [Models] Fetched {len(models)} models from {MODELS_ENDPOINT}")
            return models
    except Exception as e:
        print(f"  [Models] Could not fetch from {MODELS_ENDPOINT}: {e}")

    # No hard-coded fallback: models are always sourced live from the provider.
    return []


class _DynamicModels:
    """Provides a tuple-like object that fetches models on first access."""

    def __iter__(self):
        return iter(_fetch_openai_models())

    def __contains__(self, item):
        return item in _fetch_openai_models()

    def __len__(self):
        return len(_fetch_openai_models())


AVAILABLE_MODELS = _DynamicModels()

# =============================================================================
# Module State
# =============================================================================

_selected_api: str = "openai"
_selected_model: str = ""
_cache_dir: str | None = None

# The retry chain is the single source of truth for fallback/retry behaviour.
_retry_chain: RetryChain | None = None
# Per-provider client cache (built lazily as the chain visits each provider).
_client_cache: dict[str, Any] = {}
# (provider, model) pairs proven permanently unusable this run (403/404/license).
_unavailable_model_keys: set[tuple[str, str]] = set()
# Thread-safe per-model cooldown enforcement (chunks run concurrently).
_cooldown = CooldownRegistry()

# Per-provider performance overrides (empty entry = use the built-in default).
_max_workers_overrides: dict[str, int] = {}
_max_chunk_overrides: dict[str, int] = {}

# Max entities (characters + locations + summary) merged into one batched
# consolidation request. Larger values mean fewer requests but bigger payloads;
# 0 (or unset) falls back to the built-in default.
CONSOLIDATE_BATCH_SIZE_DEFAULT = 15
_consolidate_batch_size = CONSOLIDATE_BATCH_SIZE_DEFAULT
_consolidate_batch_dynamic = False

# Token usage tracking (accumulated per batch run; keyed by "provider/model").
_token_lock = threading.Lock()
_token_usage: dict[str, dict[str, int]] = {}


def reset_token_usage() -> None:
    """Clear accumulated token counts (call before starting a new batch)."""
    with _token_lock:
        _token_usage.clear()


def get_token_usage() -> dict[str, dict[str, int]]:
    """Return a snapshot of {provider/model: {prompt: int, completion: int}}."""
    with _token_lock:
        return {k: dict(v) for k, v in _token_usage.items()}


def _record_tokens(provider: str, model: str, prompt: int, completion: int, chars: int = 0) -> None:
    if prompt <= 0 and completion <= 0:
        return
    key = f"{provider}/{model}"
    with _token_lock:
        entry = _token_usage.setdefault(key, {"prompt": 0, "completion": 0, "chars": 0})
        entry["prompt"] += prompt
        entry["completion"] += completion
        entry["chars"] += chars


def configure(
    *,
    selected_api: str = "openai",
    selected_model: str = "",
    cache_dir: str | None = None,
    retry_chain: RetryChain | None = None,
) -> None:
    """Configure the AI client module. Called once at startup.

    The retry chain drives all fallback/retry behaviour. When no chain is
    passed it is loaded from ``.xray_prefs.json``; if that yields nothing a
    single-entry chain is seeded from ``selected_api`` / ``selected_model``.
    """
    global _selected_api, _selected_model, _cache_dir, _retry_chain

    if retry_chain is None:
        retry_chain = retry_config.load_retry_chain(allow_bootstrap=False)
    if retry_chain.is_empty():
        retry_chain = RetryChain(
            entries=[RetryEntry(provider=selected_api, model=selected_model)],
            options=retry_chain.options,
        )
    _retry_chain = retry_chain

    # Keep legacy globals aligned with the primary entry so the chunk-sizing /
    # concurrency helpers and the consolidate_* callers have sensible defaults.
    primary = retry_chain.primary
    _selected_api = primary.provider if primary else selected_api
    _selected_model = primary.model if primary else selected_model

    _cache_dir = cache_dir
    _client_cache.clear()
    _unavailable_model_keys.clear()
    _cooldown.reset()
    _load_performance_from_prefs()


def _load_performance_from_prefs() -> None:
    """Load per-provider worker/chunk overrides from ``.xray_prefs.json``."""
    global _max_workers_overrides, _max_chunk_overrides
    global _consolidate_batch_size, _consolidate_batch_dynamic
    try:
        from calibre_browser import _load_preferences

        prefs = _load_preferences()
    except Exception:
        prefs = {}

    def _clean(raw: Any) -> dict[str, int]:
        out: dict[str, int] = {}
        for k, v in (raw or {}).items():
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if iv > 0:
                out[str(k)] = iv
        return out

    _max_workers_overrides = _clean(prefs.get("max_workers"))
    _max_chunk_overrides = _clean(prefs.get("max_chunk_size"))
    _consolidate_batch_size = _clean_batch_size(prefs.get("consolidation_batch_size"))
    _consolidate_batch_dynamic = bool(prefs.get("consolidation_batch_dynamic", False))


def _clean_batch_size(value: Any) -> int:
    """Coerce a consolidation batch-size pref to a positive int (0/invalid = default)."""
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return CONSOLIDATE_BATCH_SIZE_DEFAULT
    return iv if iv > 0 else CONSOLIDATE_BATCH_SIZE_DEFAULT


def configure_performance(
    *,
    max_workers: dict[str, Any] | None = None,
    max_chunk_size: dict[str, Any] | None = None,
    consolidate_batch_size: int | None = None,
    consolidate_batch_dynamic: bool | None = None,
) -> None:
    """Set per-provider worker/chunk-size overrides at runtime.

    Values <= 0 (or non-numeric) are treated as "use the built-in default".
    """
    global _max_workers_overrides, _max_chunk_overrides
    global _consolidate_batch_size, _consolidate_batch_dynamic

    def _clean(raw: dict[str, Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        for k, v in raw.items():
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if iv > 0:
                out[str(k)] = iv
        return out

    if max_workers is not None:
        _max_workers_overrides = _clean(max_workers)
    if max_chunk_size is not None:
        _max_chunk_overrides = _clean(max_chunk_size)
    if consolidate_batch_size is not None:
        _consolidate_batch_size = _clean_batch_size(consolidate_batch_size)
    if consolidate_batch_dynamic is not None:
        _consolidate_batch_dynamic = bool(consolidate_batch_dynamic)


def get_max_workers_overrides() -> dict[str, int]:
    """Return the active per-provider max-workers overrides."""
    return dict(_max_workers_overrides)


def get_max_chunk_overrides() -> dict[str, int]:
    """Return the active per-provider max-chunk-size overrides."""
    return dict(_max_chunk_overrides)


def is_consolidate_batch_dynamic() -> bool:
    """Return whether dynamic consolidation batching is enabled."""
    return _consolidate_batch_dynamic


def get_consolidate_batch_size(
    provider: str | None = None, model: str | None = None
) -> int:
    """Return entities merged into one consolidation request.

    Dynamic mode is handled by the caller with exact request-size packing based
    on rendered prompt payload size; this getter returns the configured static
    fallback size used when dynamic mode is off.
    """
    _ = provider, model
    return _consolidate_batch_size


def get_retry_chain() -> RetryChain:
    """Return the active retry chain (loading from prefs if not configured)."""
    global _retry_chain
    if _retry_chain is None:
        _retry_chain = retry_config.load_retry_chain()
    return _retry_chain


def set_cache_dir(cache_dir: str | None) -> None:
    """Update cache directory (e.g. when processing a new book)."""
    global _cache_dir
    _cache_dir = cache_dir


def get_selected_model() -> str:
    """Return the currently selected model."""
    return _selected_model


def get_selected_api() -> str:
    """Return the currently selected API."""
    return _selected_api


def get_max_workers(provider: str | None = None, model: str | None = None) -> int:
    """Return max chunk-processing workers for the given provider.

    A per-provider override (from settings) wins; otherwise the built-in
    default is used. Defaults to the primary chain entry / selected API when no
    provider is passed, so the value stays dynamic as the chain is reconfigured.
    """
    provider = provider or _selected_api
    if provider in _max_workers_overrides:
        return max(1, _max_workers_overrides[provider])
    return auto_max_workers(provider)


def auto_max_workers(provider: str | None = None) -> int:
    """Return the built-in worker default for a provider, ignoring overrides."""
    provider = provider or _selected_api
    return MAX_WORKERS_DEFAULTS.get(provider, MAX_WORKERS_DEFAULTS["_default"])


# Built-in per-provider worker defaults (used when there is no override).
MAX_WORKERS_DEFAULTS: dict[str, int] = {
    "openai": 1,     # local endpoints are usually single-stream
    "copilot": 1,    # Copilot tokens/rate limits are per-account; keep sequential
    "groq": 1,       # Groq free tier: 6-12K TPM, must be sequential
    "gemini": 3,
    "deepseek": 5,
    "claude": 5,
    "cusanity": 5,
    "_default": 5,
}


# Groq per-model TPM limits (tokens per minute, on-demand free tier).
# Source: https://console.groq.com/docs/rate-limits
_GROQ_MODEL_TPM: dict[str, int] = {
    "llama-3.1-8b-instant": 6_000,
    "llama-3.3-70b-versatile": 12_000,
    "openai/gpt-oss-120b": 8_000,
    "openai/gpt-oss-20b": 8_000,
    "openai/gpt-oss-safeguard-20b": 8_000,
    "qwen/qwen3.6-27b": 8_000,
    "groq/compound": 70_000,
    "groq/compound-mini": 70_000,
}
_GROQ_DEFAULT_TPM = 12_000  # conservative default for unknown Groq models
_CHARS_PER_TOKEN = 2.0       # rough estimate for Chinese/mixed text


def get_max_chunk_size(provider: str | None = None, model: str | None = None) -> int:
    """Return the safe maximum chunk size in characters for a provider/model.

    A per-provider override (from settings) wins outright. Otherwise, for Groq
    the limit is derived from the model's TPM budget minus output tokens and
    prompt overhead; all other providers use the global default. Defaults to
    the primary chain entry / selected API when not passed, keeping the value
    dynamic as the chain is reconfigured.
    """
    provider = provider or _selected_api
    model = model or _selected_model

    override = _max_chunk_overrides.get(provider, 0)
    if override and override > 0:
        return override

    return auto_max_chunk_size(provider, model)


def auto_max_chunk_size(
    provider: str | None = None, model: str | None = None
) -> int:
    """Return the auto-derived chunk size for a provider/model, ignoring overrides."""
    provider = provider or _selected_api
    model = model or _selected_model

    _DEFAULT = DEFAULT_CHUNK_SIZE
    if provider != "groq" or not model:
        return _DEFAULT

    tpm = _GROQ_MODEL_TPM.get(model, _GROQ_DEFAULT_TPM)
    # Groq compound models have generous limits — no need to constrain them.
    if tpm >= 50_000:
        return _DEFAULT

    # Reserve output tokens (capped at what we actually request).
    output_reserved = 8192
    # Prompt template + system instruction overhead in tokens.
    overhead_tokens = 600
    available_input_tokens = tpm - output_reserved - overhead_tokens
    if available_input_tokens <= 0:
        return 2000  # hard floor
    # Convert tokens → characters with a 10% safety margin.
    return max(2000, int(available_input_tokens * _CHARS_PER_TOKEN * 0.90))


# =============================================================================
# Gemini Model Fetching
# =============================================================================


# =============================================================================
# DeepSeek Model Fetching
# =============================================================================


def fetch_deepseek_models() -> list[str]:
    """Return available DeepSeek models (alphabetical). Always sourced live
    from the provider; returns an empty list when no key / on error."""
    global _deepseek_models_cache
    if _deepseek_models_cache is not None:
        return _deepseek_models_cache

    if not DEEPSEEK_API_KEY:
        return []

    try:
        import requests

        resp = requests.get(
            f"{DEEPSEEK_API_BASE}/models",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        models = [m["id"] for m in data.get("data", [])]
        if models:
            _deepseek_models_cache = sorted(models)
            print(f"  [DeepSeek] Found {len(_deepseek_models_cache)} models: {', '.join(_deepseek_models_cache)}")
            return _deepseek_models_cache
    except Exception as e:
        print(f"  [DeepSeek] Failed to fetch models: {e}")

    # No hard-coded fallback: models are always sourced live from the provider.
    return []


def _sort_gemini_flash_models(models: list[str]) -> list[str]:
    """Sort Gemini flash models cheapest-first: flash-lite before flash, newer versions first."""
    lite = sorted([m for m in models if "flash-lite" in m or "flash_lite" in m], reverse=True)
    flash = sorted([m for m in models if "flash" in m and "lite" not in m], reverse=True)
    return lite + flash


def fetch_gemini_models() -> list[str]:
    """Fetch available models from Google Gemini API.

    Returns only flash/flash-lite models ordered by cost (cheapest first):
    flash-lite 2.5 → flash-lite 3.x → flash 2.5 → flash 3.x → ...
    """
    global _gemini_models_cache
    if _gemini_models_cache is not None:
        return _gemini_models_cache

    if not GEMINI_API_KEY:
        return []

    try:
        import requests

        resp = requests.get(
            f"{GEMINI_API_BASE}models",
            headers={"Authorization": f"Bearer {GEMINI_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # Keep only flash/flash-lite models; exclude everything else to
        # avoid experimental junk (e.g. "nano-banana-pro-preview") and
        # expensive pro/ultra models.
        models = [
            m["id"]
            for m in data.get("data", [])
            if "flash" in m["id"]
            and "embedding" not in m["id"]
            and "vision" not in m["id"]
            and "veo" not in m["id"]
            and "imagen" not in m["id"]
            and "tts" not in m["id"]
        ]
        if models:
            _gemini_models_cache = _sort_gemini_flash_models(models)
            print(f"  [Gemini] Found {len(_gemini_models_cache)} flash models: {', '.join(_gemini_models_cache)}")
            return _gemini_models_cache
    except Exception as e:
        print(f"  [Gemini] Failed to fetch models: {e}")

    # No hard-coded fallback: models are always sourced live from the provider.
    return []


# =============================================================================
# Claude Model Fetching
# =============================================================================


def fetch_claude_models() -> list[str]:
    """Fetch available models from Anthropic API."""
    global _claude_models_cache
    if _claude_models_cache is not None:
        return _claude_models_cache

    if not CLAUDE_API_KEY or anthropic is None:
        return []

    try:
        if CLAUDE_API_KEY.startswith("sk-ant-oat"):
            client = anthropic.Anthropic(
                auth_token=CLAUDE_API_KEY,
                default_headers={"anthropic-beta": "oauth-2025-04-20"},
            )
        else:
            client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

        page = client.models.list(limit=20)
        models = [m.id for m in page.data]

        if models:
            _claude_models_cache = models
            print(f"  [Claude] Found {len(models)} models: {', '.join(models)}")
            return models

    except Exception as e:
        print(f"  [Claude] Failed to fetch models: {e}")

    # No hard-coded fallback: models are always sourced live from the provider.
    return []


# =============================================================================
# Groq Model Fetching
# =============================================================================


def fetch_groq_models() -> list[str]:
    """Fetch available models from Groq API."""
    global _groq_models_cache
    if _groq_models_cache is not None:
        return _groq_models_cache

    if not GROQ_API_KEY:
        return []

    try:
        import requests

        resp = requests.get(
            f"{GROQ_API_BASE}/models",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        models = [
            m["id"]
            for m in data.get("data", [])
            if m.get("active", True)
            and "whisper" not in m["id"]
            and "tts" not in m["id"]
            and "guard" not in m["id"]
            and "orpheus" not in m["id"]
        ]
        if models:
            _groq_models_cache = sorted(models)
            print(f"  [Groq] Found {len(models)} models")
            return _groq_models_cache
    except Exception as e:
        print(f"  [Groq] Failed to fetch models: {e}")

    # No hard-coded fallback: models are always sourced live from the provider.
    return []


def fetch_copilot_models() -> list[str]:
    """Fetch available GitHub Copilot chat models for the logged-in account."""
    global _copilot_models_cache
    if _copilot_models_cache is not None:
        return _copilot_models_cache

    if not copilot_auth.has_github_token():
        return []

    try:
        models = copilot_auth.fetch_models()
        if models:
            _copilot_models_cache = models
            print(f"  [Copilot] Found {len(models)} models")
            return _copilot_models_cache
    except Exception as e:
        print(f"  [Copilot] Failed to fetch models: {e}")

    return [COPILOT_DEFAULT_MODEL] if COPILOT_DEFAULT_MODEL else []


# =============================================================================
# Client Factory
# =============================================================================


def create_client(selected_api: str) -> Any:
    """Create the appropriate AI client for the selected API."""
    if selected_api == "claude":
        if anthropic is None:
            print("Error: anthropic module not found. Please install it.")
            return None
        if not CLAUDE_API_KEY:
            print("Error: CLAUDE_API_KEY environment variable not set.")
            return None

        if CLAUDE_API_KEY.startswith("sk-ant-oat"):
            return anthropic.Anthropic(
                auth_token=CLAUDE_API_KEY,
                default_headers={"anthropic-beta": "oauth-2025-04-20"},
            )
        else:
            return anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    elif selected_api == "groq":
        if not GROQ_API_KEY:
            print("Error: GROQ_API_KEY environment variable not set.")
            return None
        return OpenAI(
            base_url=GROQ_API_BASE,
            api_key=GROQ_API_KEY,
            timeout=AI_TIMEOUT_SECONDS,
            max_retries=0,  # our retry chain owns retries
        )
    elif selected_api == "gemini":
        if not GEMINI_API_KEY:
            print("Error: GEMINI_API_KEY environment variable not set.")
            return None
        return OpenAI(
            base_url=GEMINI_API_BASE,
            api_key=GEMINI_API_KEY,
            timeout=AI_TIMEOUT_SECONDS,
            max_retries=0,  # our retry chain owns retries
        )
    elif selected_api == "deepseek":
        if not DEEPSEEK_API_KEY:
            print("Error: DEEPSEEK_API_KEY environment variable not set.")
            return None
        return OpenAI(
            base_url=DEEPSEEK_API_BASE,
            api_key=DEEPSEEK_API_KEY,
            timeout=AI_TIMEOUT_SECONDS,
            max_retries=0,  # our retry chain owns retries
        )
    elif selected_api == "copilot":
        try:
            token = copilot_auth.get_copilot_token(auto_login=True)
        except Exception as e:
            print(f"Error: GitHub Copilot login failed: {e}")
            return None
        return OpenAI(
            base_url=COPILOT_API_BASE,
            api_key=token,
            timeout=AI_TIMEOUT_SECONDS,
            max_retries=0,  # our retry chain owns retries
            default_headers=copilot_auth.copilot_headers(),
        )
    elif selected_api != "cusanity":
        # OpenAI-compatible endpoint: base URL + key, plus optional
        # organization / project / custom headers for proxies and gateways.
        kwargs: dict[str, Any] = {
            "base_url": API_BASE_URL,
            "api_key": API_KEY,
            "timeout": AI_TIMEOUT_SECONDS,
            "max_retries": 0,  # our retry chain owns retries
        }
        if API_DEFAULT_HEADERS:
            kwargs["default_headers"] = API_DEFAULT_HEADERS
        return OpenAI(**kwargs)
    return None


# =============================================================================
# Response Helpers
# =============================================================================


class MockResponse:
    """Mimics OpenAI response structure for non-OpenAI providers."""

    def __init__(self, content: str, trimmed_chars: int = 0):
        self.choices = [
            type(
                "obj",
                (object,),
                {
                    "message": type("obj", (object,), {"content": content}),
                    "finish_reason": "stop",
                },
            )
        ]
        # Non-zero when call_ai_with_retry had to truncate the user message.
        # Callers can use this to re-process the skipped tail.
        self.trimmed_chars: int = trimmed_chars


# =============================================================================
# JSON Validation (single shared implementation for ALL providers)
# =============================================================================


def extract_json_content(content: str) -> str:
    """Extract JSON content from a string robustly."""
    # 1. Try to find markdown code block first
    json_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE
    )
    if json_match:
        return json_match.group(1).strip()

    # 2. If no code block, try to find the outermost JSON object
    json_match = re.search(r"(\{.*\})", content, re.DOTALL)
    if json_match:
        return json_match.group(1).strip()

    # 3. Fallback: just strip whitespace
    return content.strip()


def repair_json_quotes(text: str) -> str:
    """Fix unescaped double quotes inside JSON string values.

    AI models often output Chinese text with unescaped " inside strings,
    e.g. "被评为"模范犯人"" — the inner quotes break json.loads.
    This iteratively finds the error position and escapes the offending quote.
    """
    max_attempts = 30
    for _ in range(max_attempts):
        try:
            json.loads(text)
            return text  # Valid JSON
        except json.JSONDecodeError as e:
            pos = e.pos
            quote_pos = text.rfind('"', 0, pos)
            if quote_pos > 0 and text[quote_pos - 1] != "\\":
                text = text[:quote_pos] + '\\"' + text[quote_pos + 1 :]
            else:
                break  # Can't fix
    return text


def _is_truncated_json(text: str, error: json.JSONDecodeError) -> bool:
    """Return True if JSON looks truncated (token limit) rather than just malformed."""
    stripped = text.rstrip()
    if stripped and not stripped[-1] in ("}" , "]", '"'):
        return True
    trunc_hints = ("Unterminated string", "Expecting value", "Expecting property name")
    return any(hint in str(error) for hint in trunc_hints)


def validate_response_json(content: str) -> str:
    """Extract and validate JSON from AI response content.

    This is the SINGLE validation path used by all providers. Any future
    JSON repair heuristics only need to be added here.

    Returns cleaned JSON string.
    Raises ValueError if JSON is invalid after all repair attempts.
    Raises TruncatedJSONError (subclass of ValueError) if truncation is detected.
    """
    text = extract_json_content(content)
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError as first_err:
        if _is_truncated_json(text, first_err):
            raise TruncatedJSONError(
                f"Truncated JSON response (token limit?): {text[:80]}..."
            ) from first_err
        # Try repairing unescaped quotes (common in Chinese text from all providers)
        text = repair_json_quotes(text)
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON after repair: {content[:100]}...")


class TruncatedJSONError(ValueError):
    """Raised when the AI response JSON is truncated (likely hit token limit)."""
    pass


# =============================================================================
# AI Caching
# =============================================================================


def get_ai_cache(prompt: str) -> dict[str, Any] | None:
    """Check if AI response for prompt is cached."""
    if not _cache_dir:
        return None

    prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    cache_file = os.path.join(_cache_dir, f"{prompt_hash}.json")

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_ai_cache(prompt: str, response_data: dict[str, Any]) -> None:
    """Save AI response to cache."""
    if not _cache_dir:
        return

    prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    cache_file = os.path.join(_cache_dir, f"{prompt_hash}.json")

    try:
        os.makedirs(_cache_dir, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(response_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save AI cache: {e}")


# =============================================================================
# Provider-Specific Call Functions
# =============================================================================


def _call_cusanity(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
) -> str:
    """Call Cusanity (Gemini proxy) and return raw response content."""
    import cusanity

    system_prompt = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    user_prompt = next(
        (m["content"] for m in messages if m["role"] == "user"), ""
    )

    content = cusanity.ai_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        provider=cusanity.Provider.GEMINI,
        model=model,
        temperature=temperature,
        top_p=TOP_P,
        json_mode=True,
        google_search=False,
    )
    return content


def _call_claude(
    client: Any,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    _provider: str = "claude",
) -> str:
    """Call Anthropic Claude and return raw response content."""
    if anthropic is None:
        raise ValueError("anthropic module is not available")
    if not isinstance(client, anthropic.Anthropic):
        raise ValueError("Client is not an Anthropic instance")

    system_prompt = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    user_messages: list[Any] = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m["role"] != "system"
    ]

    claude_max_tokens = max(max_tokens, 16384)
    collected_text = ""
    stop_reason = None
    with client.messages.stream(
        model=model,
        max_tokens=claude_max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=user_messages,
    ) as stream:
        for text in stream.text_stream:
            collected_text += text
        final_message = stream.get_final_message()
        stop_reason = final_message.stop_reason
        usage = getattr(final_message, "usage", None)
        if usage is not None:
            _record_tokens(
                _provider, model,
                getattr(usage, "input_tokens", 0) or 0,
                getattr(usage, "output_tokens", 0) or 0,
                sum(len(m.get("content", "")) for m in messages),
            )

    if stop_reason == "max_tokens":
        raise ValueError(
            f"Claude response truncated (hit {claude_max_tokens} token limit). "
            "Response may be too long for this chunk."
        )

    return collected_text


def _call_openai(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float | None = None,
    _provider: str = "openai",
) -> str:
    """Call OpenAI-compatible API and return raw response content."""
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout if timeout is not None else AI_TIMEOUT_SECONDS,
    }
    if _provider != "copilot":
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    usage = getattr(response, "usage", None)
    if usage is not None:
        _record_tokens(
            _provider, model,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            sum(len(m.get("content", "")) for m in messages),
        )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("Response content is None")

    return content


# =============================================================================
# Unified Retry Logic
# =============================================================================


def _available_providers() -> set[str]:
    """Return the set of providers that currently have usable credentials."""
    provs: set[str] = {"openai", "cusanity"}
    if CLAUDE_API_KEY and anthropic is not None:
        provs.add("claude")
    if GROQ_API_KEY:
        provs.add("groq")
    if GEMINI_API_KEY:
        provs.add("gemini")
    if DEEPSEEK_API_KEY:
        provs.add("deepseek")
    if copilot_auth.has_github_token():
        provs.add("copilot")
    return provs


def _client_for(provider: str) -> Any:
    """Return a cached client for a provider, building it on first use."""
    if provider == "cusanity":
        return None
    if provider == "copilot":
        return create_client(provider)
    client = _client_cache.get(provider)
    if client is None:
        client = create_client(provider)
        _client_cache[provider] = client
    return client


def _is_moderation_error(error_str: str) -> bool:
    """True when the provider deterministically refused the content."""
    markers = (
        "Content Exists Risk",   # DeepSeek
        "Response content is None",  # safety-filtered empty completion
        "content_policy",
        "content_filter",
    )
    return any(m in error_str for m in markers)


def _is_oversized_error(e: Exception, error_str: str, status_code: int | None) -> bool:
    """True when the request payload exceeded the model's token budget."""
    return (
        status_code == 413
        or "413" in str(e)
        or "Request too large" in error_str
        or (
            "tokens per minute" in error_str.lower()
            and "reduce your message size" in error_str.lower()
        )
        or '"type": "tokens"' in error_str
    )


class _ChainExhaustedError(RuntimeError):
    """Raised when every entry in the retry chain has failed."""


class _ChainRunner:
    """Executes a single request against the configured retry chain.

    Walks the chain in order, retrying each entry up to its own budget and
    honouring its per-model cooldown. Content-moderation refusals and
    permission/not-found errors advance to the next entry immediately.
    """

    def __init__(
        self,
        chain: RetryChain,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        delay: float,
        seed_client: Any = None,
    ) -> None:
        self.chain = chain
        self.opts = chain.options
        self.messages = [dict(m) for m in messages]
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.delay = delay
        self.trimmed_chars = 0

        available = _available_providers()
        self.entries = [e for e in chain.entries if e.provider in available]
        # ``seed_client`` is accepted for signature compatibility but not used:
        # clients are built per-provider via _client_for so a reordered chain
        # can never dispatch a request through the wrong provider's client.
        _ = seed_client

    # ---- public ---------------------------------------------------------

    def run(self) -> Any:
        if not self.entries:
            raise RuntimeError(
                "Retry chain has no usable entries (missing API keys?). "
                "Configure the fallback chain in the generator settings."
            )

        for cycle in range(1, self.opts.max_cycles + 1):
            for idx, entry in enumerate(self.entries):
                if entry.key() in _unavailable_model_keys:
                    continue
                if idx > 0 or cycle > 1:
                    print(f"    [Fallback] Trying {entry.provider}:{entry.model}")
                response = self._try_entry(entry, cycle)
                if response is not None:
                    return response
            if cycle < self.opts.max_cycles and self.opts.inter_cycle_wait > 0:
                print(
                    f"    [Cycle {cycle}/{self.opts.max_cycles}] chain exhausted. "
                    f"Waiting {self.opts.inter_cycle_wait:.0f}s before retrying..."
                )
                time.sleep(self.opts.inter_cycle_wait)

        return self._on_exhausted()

    # ---- per-entry ------------------------------------------------------

    def _try_entry(self, entry: RetryEntry, cycle: int) -> Any:
        """Return a MockResponse on success, or None to advance to next entry."""
        provider, model = entry.provider, entry.model
        temperature = (
            entry.temperature if entry.temperature is not None else self.temperature
        )
        max_tokens = entry.max_tokens or self.max_tokens

        attempt = 0
        while attempt < entry.retries:
            attempt += 1
            _cooldown.reserve(provider, model, entry.cooldown)

            try:
                raw_content = self._call_provider(
                    provider, model, temperature, max_tokens, entry.timeout
                )
            except Exception as e:  # noqa: BLE001 - classified below
                action = self._classify_error(e, entry, attempt)
                if action == "next":
                    return None
                # "retry": consume the attempt and loop (unless budget spent)
                if attempt < entry.retries:
                    continue
                print(
                    f"    [AI Error] {provider}:{model} exhausted "
                    f"{entry.retries} attempt(s). Advancing to next model..."
                )
                return None

            # Validate JSON through the single shared path.
            try:
                cleaned = validate_response_json(raw_content)
            except TruncatedJSONError:
                # Deterministic truncation — retrying the same model won't help.
                print(
                    f"    [AI Error] {provider}:{model} truncated output "
                    "(token limit). Advancing to next model..."
                )
                return None
            except ValueError:
                if provider == "claude":
                    print("\n--- Claude Full Response (failed JSON validation) ---")
                    print(raw_content)
                    print("--- End of Response ---\n")
                if attempt < entry.retries:
                    print(
                        f"    [AI Error] Invalid JSON from {provider}:{model}, "
                        f"retrying ({entry.retries - attempt} left)..."
                    )
                    time.sleep(self.delay)
                    continue
                print(
                    f"    [AI Error] {provider}:{model} kept returning invalid "
                    "JSON. Advancing to next model..."
                )
                return None

            return MockResponse(cleaned, trimmed_chars=self.trimmed_chars)

        return None

    def _call_provider(
        self,
        provider: str,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: float | None,
    ) -> str:
        if provider == "cusanity":
            return _call_cusanity(model, self.messages, temperature)
        client = _client_for(provider)
        if provider == "claude":
            return _call_claude(client, model, self.messages, temperature, max_tokens, _provider=provider)
        # "openai", "copilot", "groq", "gemini", "deepseek" all use the OpenAI-compatible API.
        return _call_openai(
            client, model, self.messages, temperature, max_tokens, timeout, _provider=provider
        )

    # ---- error handling -------------------------------------------------

    def _classify_error(self, e: Exception, entry: RetryEntry, attempt: int) -> str:
        """Return 'next' (advance entry) or 'retry' (same entry, consume attempt)."""
        provider, model = entry.provider, entry.model

        full_error = str(e)
        resp = getattr(e, "response", None)
        if resp is not None and hasattr(resp, "text"):
            full_error = f"{e}\nResponse Body: {resp.text}"
        elif hasattr(e, "body"):
            full_error = f"{e}\nError Body: {getattr(e, 'body', '')}"
        error_str = full_error
        status_code = getattr(resp, "status_code", None) if resp else None

        # (4) Content moderation refusal -> go to the next provider in the chain.
        if _is_moderation_error(error_str):
            print(
                f"    [Moderation] {provider}:{model} refused the content. "
                "Advancing to next provider..."
            )
            return "next"

        # Oversized payload -> truncate the user message and retry same model.
        if _is_oversized_error(e, error_str, status_code):
            self._truncate_user_message(error_str, provider, model)
            return "retry"

        # Rate limit -> optionally honour Retry-After, then retry same model.
        if status_code == 429 or "429" in str(e) or "rate_limit" in str(e).lower():
            wait_secs = self._retry_after_secs(resp)
            print(
                f"    [Rate Limit] {provider}:{model} hit rate limit. "
                f"Waiting {wait_secs:.0f}s..."
            )
            time.sleep(wait_secs)
            return "retry"

        # Permission / license / not-found -> permanently skip this model.
        permanent = (
            "SUBSCRIPTION_REQUIRED" in error_str
            or "Gemini Code Assist license" in error_str
            or "3501" in error_str
            or ("403" in str(e) and "permission" in error_str.lower())
            or "404" in str(e)
            or status_code in (403, 404)
        )
        if permanent:
            _unavailable_model_keys.add(entry.key())
            print(
                f"    [Unavailable] {provider}:{model} (403/404/license). "
                "Skipping for the rest of this run. Advancing to next model..."
            )
            return "next"

        # Generic transient error -> retry the same model.
        print(f"    [AI Error] {provider}:{model} failed: {e}")
        return "retry"

    def _truncate_user_message(self, error_str: str, provider: str, model: str) -> None:
        limit_m = re.search(r"Limit (\d+)", error_str)
        req_m = re.search(r"Requested (\d+)", error_str)
        limit_val = int(limit_m.group(1)) if limit_m else 0
        req_val = int(req_m.group(1)) if req_m else 0
        if limit_m and req_m and req_val:
            ratio = max(0.4, (limit_val / req_val) * 0.85)
        else:
            ratio = 0.65  # conservative fallback

        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i]["role"] == "user":
                orig_len = len(self.messages[i]["content"])
                new_len = int(orig_len * ratio)
                self.messages[i]["content"] = self.messages[i]["content"][:new_len]
                print(
                    f"    [Token Limit] {provider}:{model}: request too large "
                    + (f"({req_val} > {limit_val} TPM). " if limit_m and req_m else "")
                    + f"Truncating input to {ratio:.0%} "
                    f"({new_len}/{orig_len} chars) and retrying..."
                )
                self.trimmed_chars = orig_len - new_len
                break

    def _retry_after_secs(self, resp: Any) -> float:
        if self.opts.honor_retry_after and resp is not None and hasattr(resp, "headers"):
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    return float(retry_after)
                except (ValueError, TypeError):
                    pass
        return 10.0

    def _on_exhausted(self) -> Any:
        mode = self.opts.on_exhausted
        summary = (
            f"All {len(self.entries)} model(s) in the retry chain failed after "
            f"{self.opts.max_cycles} cycle(s)."
        )
        if mode == "exit":
            print(f"\n[FATAL] {summary}")
            os._exit(1)
        if mode == "skip":
            print(f"\n[WARNING] {summary} Skipping this request (empty result).")
            return MockResponse("{}", trimmed_chars=self.trimmed_chars)
        raise _ChainExhaustedError(summary)


def call_ai_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 8192,
    retries: int = 3,
    delay: float = 2.0,
) -> Any:
    """Call AI, walking the configured retry chain for fallback/retries.

    The retry chain (loaded from ``.xray_prefs.json``) is the single source of
    truth for which models are tried, how many times, and with what cooldown.
    The ``client`` / ``model`` / ``retries`` arguments are retained for
    backward compatibility: ``client`` seeds the client cache for the primary
    provider, while ordering and retry budgets come entirely from the chain.
    """
    chain = get_retry_chain()
    runner = _ChainRunner(
        chain,
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        delay=delay,
        seed_client=client,
    )
    return runner.run()



# =============================================================================
# High-Level AI Operations
# =============================================================================


def consolidate_description_with_ai(
    client: OpenAI,
    entity_type: str,
    name: str,
    combined_desc: str,
    system_prompt: str,
    consolidate_desc_prompt: str,
) -> str:
    """Call AI to consolidate a long description."""
    type_cn = "人物" if entity_type == "character" else "地点"
    prompt = consolidate_desc_prompt % (type_cn, name, combined_desc)

    cached_data = get_ai_cache(prompt)
    if cached_data:
        return cached_data.get("description", combined_desc)

    content = ""
    try:
        response = call_ai_with_retry(
            client,
            _selected_model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2048,
            retries=3,
        )
        content = response.choices[0].message.content
        if content:
            content = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            save_ai_cache(prompt, result)
            return result.get("description", combined_desc)
    except json.JSONDecodeError as e:
        print(
            f"    [Consolidation Error] JSON Parsing failed on {_selected_model}: {e}"
        )
        print(f"    Raw content was:\n{content}")
        os._exit(1)
    except Exception as e:
        print(f"    [Consolidation Error] {name} on {_selected_model}: {e}")
        os._exit(1)

    return combined_desc


def consolidate_summary_with_ai(
    client: OpenAI,
    book_title: str,
    combined_summary: str,
    system_prompt: str,
    consolidate_summary_prompt: str,
) -> str:
    """Call AI to consolidate a long summary."""
    prompt = consolidate_summary_prompt % (book_title, combined_summary)

    cached_data = get_ai_cache(prompt)
    if cached_data:
        return cached_data.get("summary", combined_summary)

    content = ""
    try:
        response = call_ai_with_retry(
            client,
            _selected_model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
            retries=3,
        )
        content = response.choices[0].message.content
        if content:
            content = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            save_ai_cache(prompt, result)
            return result.get("summary", combined_summary)
    except json.JSONDecodeError as e:
        print(
            f"    [Summary Consolidation Error] JSON Parsing failed on {_selected_model}: {e}"
        )
        print(f"    Raw content was:\n{content}")
        os._exit(1)
    except Exception as e:
        print(f"    [Summary Consolidation Error] on {_selected_model}: {e}")
        os._exit(1)

    return combined_summary


def consolidate_descriptions_batch(
    client: OpenAI,
    items: list[dict],
    system_prompt: str,
    batch_prompt: str,
) -> dict[int, str]:
    """Consolidate many descriptions (and the summary) in a single AI request.

    ``items`` is a list of dicts, each ``{"id": int, "kind": str, "name": str,
    "text": str}`` where ``kind`` is one of ``人物`` / ``地点`` / ``概要``.

    Returns a mapping ``{id: consolidated_text}``.  On any failure the mapping
    simply omits the affected ids, leaving the caller's data untouched so the
    pipeline can continue (the items get retried on a later chunk).
    """
    if not items:
        return {}

    payload = json.dumps(items, ensure_ascii=False)
    prompt = batch_prompt % payload

    cached = get_ai_cache(prompt)
    if cached:
        return {int(r["id"]): r.get("text", "") for r in cached.get("results", [])}

    # Scale the output budget with the batch size; each entry is ≤300 chars.
    max_tokens = min(16384, 700 * len(items) + 1024)

    content = ""
    try:
        response = call_ai_with_retry(
            client,
            _selected_model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=max_tokens,
            retries=3,
        )
        content = response.choices[0].message.content
        if not content:
            print(
                f"    [Batch Consolidation] Empty response on {_selected_model}; "
                "skipping this round."
            )
            return {}

        content = content.replace("```json", "").replace("```", "").strip()
        result = json.loads(content)
        save_ai_cache(prompt, result)

        out: dict[int, str] = {}
        for r in result.get("results", []):
            try:
                rid = int(r["id"])
            except (KeyError, TypeError, ValueError):
                continue
            text = (r.get("text") or "").strip()
            if text:
                out[rid] = text
        return out

    except json.JSONDecodeError as e:
        print(
            f"    [Batch Consolidation] JSON parsing failed on {_selected_model}: {e}"
        )
        print(f"    Raw content was:\n{content}")
        return {}
    except Exception as e:
        print(f"    [Batch Consolidation] error on {_selected_model}: {e}")
        return {}
