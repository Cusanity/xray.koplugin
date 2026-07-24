"""
Retry-chain configuration for the X-Ray Generator.

This module is the single source of truth for how AI requests are retried and
how the generator falls back between models/providers. It fully replaces the
old hard-coded logic that lived inside ``ai_client.call_ai_with_retry``.

The chain is an ordered list of entries; each entry names a provider + model
and carries its own retry budget and per-model cooldown. Execution walks the
chain in order:

  * try an entry up to ``retries`` times (honouring its ``cooldown``),
  * on a permanent/blocking failure (moderation, 403/404) advance to the next
    entry immediately,
  * when the whole chain is exhausted, repeat up to ``max_cycles`` times and
    then apply ``on_exhausted``.

Configuration is persisted in ``.xray_prefs.json`` (shared with the rest of the
Python side) under the ``retry_chain`` / ``retry_chain_options`` keys.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Providers understood by ai_client.create_client / the call dispatcher.
VALID_PROVIDERS: tuple[str, ...] = (
    "openai",
    "claude",
    "groq",
    "gemini",
    "deepseek",
    "cusanity",
)

# Per-entry defaults.
DEFAULT_RETRIES = 3
DEFAULT_COOLDOWN = 0.0

# Chain-level defaults.
DEFAULT_MAX_CYCLES = 3
DEFAULT_INTER_CYCLE_WAIT = 5.0
DEFAULT_ON_EXHAUSTED = "raise"  # "raise" | "exit" | "skip"
VALID_ON_EXHAUSTED = ("raise", "exit", "skip")


# =============================================================================
# Data model
# =============================================================================


@dataclass
class RetryEntry:
    """A single link in the fallback chain."""

    provider: str
    model: str
    retries: int = DEFAULT_RETRIES
    cooldown: float = DEFAULT_COOLDOWN
    # Optional per-entry overrides; ``None`` means "use the caller's default".
    timeout: float | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    def key(self) -> tuple[str, str]:
        """Identity used for cooldown tracking and permanent-skip sets."""
        return (self.provider, self.model)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "retries": self.retries,
            "cooldown": self.cooldown,
        }
        if self.timeout is not None:
            data["timeout"] = self.timeout
        if self.temperature is not None:
            data["temperature"] = self.temperature
        if self.max_tokens is not None:
            data["max_tokens"] = self.max_tokens
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetryEntry":
        provider = str(data.get("provider", "")).strip()
        model = str(data.get("model", "")).strip()

        def _int(name: str, default: int) -> int:
            try:
                return int(data[name])
            except (KeyError, TypeError, ValueError):
                return default

        def _float(name: str, default: float) -> float:
            try:
                return float(data[name])
            except (KeyError, TypeError, ValueError):
                return default

        def _opt_float(name: str) -> float | None:
            if data.get(name) in (None, ""):
                return None
            try:
                return float(data[name])
            except (TypeError, ValueError):
                return None

        def _opt_int(name: str) -> int | None:
            if data.get(name) in (None, ""):
                return None
            try:
                return int(data[name])
            except (TypeError, ValueError):
                return None

        return cls(
            provider=provider,
            model=model,
            retries=max(1, _int("retries", DEFAULT_RETRIES)),
            cooldown=max(0.0, _float("cooldown", DEFAULT_COOLDOWN)),
            timeout=_opt_float("timeout"),
            temperature=_opt_float("temperature"),
            max_tokens=_opt_int("max_tokens"),
        )

    def is_valid(self) -> bool:
        return self.provider in VALID_PROVIDERS and bool(self.model)


@dataclass
class RetryChainOptions:
    """Chain-level behaviour that is independent of any single entry."""

    max_cycles: int = DEFAULT_MAX_CYCLES
    inter_cycle_wait: float = DEFAULT_INTER_CYCLE_WAIT
    on_exhausted: str = DEFAULT_ON_EXHAUSTED
    honor_retry_after: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_cycles": self.max_cycles,
            "inter_cycle_wait": self.inter_cycle_wait,
            "on_exhausted": self.on_exhausted,
            "honor_retry_after": self.honor_retry_after,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RetryChainOptions":
        data = data or {}
        try:
            max_cycles = max(1, int(data.get("max_cycles", DEFAULT_MAX_CYCLES)))
        except (TypeError, ValueError):
            max_cycles = DEFAULT_MAX_CYCLES
        try:
            inter = max(0.0, float(data.get("inter_cycle_wait", DEFAULT_INTER_CYCLE_WAIT)))
        except (TypeError, ValueError):
            inter = DEFAULT_INTER_CYCLE_WAIT
        on_exhausted = str(data.get("on_exhausted", DEFAULT_ON_EXHAUSTED))
        if on_exhausted not in VALID_ON_EXHAUSTED:
            on_exhausted = DEFAULT_ON_EXHAUSTED
        return cls(
            max_cycles=max_cycles,
            inter_cycle_wait=inter,
            on_exhausted=on_exhausted,
            honor_retry_after=bool(data.get("honor_retry_after", True)),
        )


@dataclass
class RetryChain:
    """A validated, ready-to-run fallback chain."""

    entries: list[RetryEntry] = field(default_factory=list)
    options: RetryChainOptions = field(default_factory=RetryChainOptions)

    @property
    def primary(self) -> RetryEntry | None:
        """First entry — used to size chunking/concurrency up front."""
        return self.entries[0] if self.entries else None

    def is_empty(self) -> bool:
        return not self.entries


# =============================================================================
# Persistence (.xray_prefs.json via calibre_browser helpers)
# =============================================================================


def _load_prefs() -> dict[str, Any]:
    from calibre_browser import _load_preferences

    return _load_preferences()


def _save_prefs(prefs: dict[str, Any]) -> None:
    from calibre_browser import _save_preferences

    _save_preferences(prefs)


def _bootstrap_from_legacy(prefs: dict[str, Any]) -> list[RetryEntry]:
    """Build a single-entry chain from the legacy last_api/last_model keys.

    Used the first time a user runs after upgrading, before they have built a
    chain in the GUI. Keeps existing setups working without configuration.
    """
    provider = str(prefs.get("last_api", "openai")).strip() or "openai"
    model = str(prefs.get("last_model", "")).strip()
    if provider not in VALID_PROVIDERS:
        provider = "openai"
    return [RetryEntry(provider=provider, model=model)]


def load_retry_chain(allow_bootstrap: bool = True) -> RetryChain:
    """Load the retry chain from ``.xray_prefs.json``.

    When an explicit ``retry_chain`` key is present it is used. Otherwise, if
    ``allow_bootstrap`` is True, a single-entry chain is derived from the legacy
    ``last_api`` / ``last_model`` keys; if False, an empty chain (with the saved
    options) is returned so callers can supply their own primary entry.
    """
    prefs = _load_prefs()
    raw_entries = prefs.get("retry_chain")
    options = RetryChainOptions.from_dict(prefs.get("retry_chain_options"))

    if isinstance(raw_entries, list) and raw_entries:
        entries = [RetryEntry.from_dict(e) for e in raw_entries if isinstance(e, dict)]
    elif allow_bootstrap:
        entries = _bootstrap_from_legacy(prefs)
    else:
        entries = []

    entries = [e for e in entries if e.is_valid()]
    return RetryChain(entries=entries, options=options)


def save_retry_chain(chain: RetryChain) -> None:
    """Persist the retry chain back to ``.xray_prefs.json``."""
    prefs = _load_prefs()
    prefs["retry_chain"] = [e.to_dict() for e in chain.entries]
    prefs["retry_chain_options"] = chain.options.to_dict()
    # Keep legacy keys in sync with the primary entry so older code paths and
    # the "last used" selectors still behave sensibly.
    primary = chain.primary
    if primary is not None:
        prefs["last_api"] = primary.provider
        prefs["last_model"] = primary.model
    _save_prefs(prefs)


# =============================================================================
# Thread-safe per-model cooldown
# =============================================================================


class CooldownRegistry:
    """Enforces a minimum spacing between requests to the same (provider, model).

    Chunk processing runs on a ThreadPoolExecutor, so multiple workers may hit
    the same model concurrently. ``reserve`` hands out sequential time slots
    spaced by ``cooldown`` seconds without holding the lock while sleeping.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_free: dict[tuple[str, str], float] = {}

    def reserve(self, provider: str, model: str, cooldown: float) -> None:
        """Block until this worker is allowed to call (provider, model)."""
        if cooldown <= 0:
            return
        key = (provider, model)
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_free.get(key, 0.0))
            self._next_free[key] = start + cooldown
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def reset(self) -> None:
        with self._lock:
            self._next_free.clear()
