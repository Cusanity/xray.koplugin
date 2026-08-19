"""GitHub Copilot device-flow authentication helpers."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"

COPILOT_API_BASE = os.environ.get("XRAY_COPILOT_API_BASE", "https://api.githubcopilot.com")
COPILOT_CLIENT_ID = os.environ.get("XRAY_COPILOT_CLIENT_ID", "Iv1.b507a08c87ecfe98")
COPILOT_SCOPE = os.environ.get("XRAY_COPILOT_SCOPE", "read:user")
COPILOT_DEFAULT_MODEL = os.environ.get("XRAY_COPILOT_MODEL", "gpt-4.1")

_EXPIRY_MARGIN_SECONDS = 120


def _user_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _cache_path() -> Path:
    return Path(os.environ.get("XRAY_COPILOT_AUTH_FILE", _user_dir() / ".xray_copilot_auth.json"))


def _load_cache() -> dict[str, Any]:
    try:
        with _cache_path().open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _post_oauth(url: str, payload: dict[str, str]) -> dict[str, Any]:
    resp = requests.post(
        url,
        data=payload,
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("GitHub returned an invalid OAuth response.")
    return data


def has_github_token() -> bool:
    return bool(os.environ.get("XRAY_COPILOT_GITHUB_TOKEN") or _load_cache().get("github_token"))


def auth_status() -> dict[str, Any]:
    """Return non-secret cached Copilot auth state for UI/status display."""
    cache = _load_cache()
    github_token = os.environ.get("XRAY_COPILOT_GITHUB_TOKEN") or cache.get("github_token")
    copilot_token = os.environ.get("XRAY_COPILOT_TOKEN") or cache.get("copilot_token")
    try:
        expires_at = int(cache.get("copilot_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    now = int(time.time())
    refresh_at = max(0, expires_at - _EXPIRY_MARGIN_SECONDS)
    return {
        "has_github_token": bool(github_token),
        "has_copilot_token": bool(copilot_token),
        "copilot_expires_at": expires_at,
        "copilot_valid": bool(copilot_token and expires_at > now),
        "refresh_needed": bool(copilot_token and refresh_at <= now),
        "seconds_remaining": max(0, expires_at - now) if copilot_token else 0,
    }


def login_device_flow(message_callback=None) -> dict[str, Any]:
    """Run GitHub OAuth device flow and cache the resulting GitHub token."""
    data = _post_oauth(
        GITHUB_DEVICE_CODE_URL,
        {"client_id": COPILOT_CLIENT_ID, "scope": COPILOT_SCOPE},
    )
    if "error" in data:
        raise RuntimeError(data.get("error_description") or data["error"])

    verification_uri = str(data["verification_uri"])
    user_code = str(data["user_code"])
    device_code = str(data["device_code"])
    expires_at = time.time() + int(data.get("expires_in", 900))
    interval = int(data.get("interval", 5))

    message = (
        "GitHub Copilot login required.\n"
        f"Open {verification_uri} and enter code: {user_code}"
    )
    if message_callback is not None:
        message_callback(verification_uri, user_code, message)
    else:
        print(message)

    while time.time() < expires_at:
        time.sleep(interval)
        token_data = _post_oauth(
            GITHUB_ACCESS_TOKEN_URL,
            {
                "client_id": COPILOT_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        error = token_data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = int(token_data.get("interval", interval + 5))
            continue
        if error:
            raise RuntimeError(token_data.get("error_description") or str(error))

        github_token = str(token_data.get("access_token") or "")
        if not github_token:
            raise RuntimeError("GitHub did not return an access token.")
        cache = _load_cache()
        cache.update({"github_token": github_token})
        _save_cache(cache)
        os.environ["XRAY_COPILOT_GITHUB_TOKEN"] = github_token
        return cache

    raise TimeoutError("GitHub device login expired before authorization completed.")


def _github_token(auto_login: bool) -> str:
    token = os.environ.get("XRAY_COPILOT_GITHUB_TOKEN") or _load_cache().get("github_token")
    if token:
        return str(token)
    if auto_login:
        return str(login_device_flow().get("github_token") or "")
    raise RuntimeError("GitHub Copilot is not logged in. Run Copilot device login first.")


def get_copilot_token(auto_login: bool = True) -> str:
    """Return a fresh Copilot API token, logging in with device flow if needed."""
    cache = _load_cache()
    token = os.environ.get("XRAY_COPILOT_TOKEN") or cache.get("copilot_token")
    expires_at = int(cache.get("copilot_expires_at") or 0)
    if token and expires_at - _EXPIRY_MARGIN_SECONDS > int(time.time()):
        return str(token)

    github_token = _github_token(auto_login)
    resp = requests.get(
        GITHUB_COPILOT_TOKEN_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"token {github_token}",
            "User-Agent": "xray-koplugin",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    copilot_token = str(data.get("token") or "")
    if not copilot_token:
        raise RuntimeError("GitHub did not return a Copilot API token.")
    cache.update(
        {
            "github_token": github_token,
            "copilot_token": copilot_token,
            "copilot_expires_at": int(data.get("expires_at") or (time.time() + 25 * 60)),
        }
    )
    _save_cache(cache)
    os.environ["XRAY_COPILOT_TOKEN"] = copilot_token
    return copilot_token


def copilot_headers() -> dict[str, str]:
    return {
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.99.0",
        "Editor-Plugin-Version": "copilot-chat/0.26.0",
    }


def fetch_models() -> list[str]:
    token = get_copilot_token(auto_login=False)
    resp = requests.get(
        f"{COPILOT_API_BASE.rstrip('/')}/models",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            **copilot_headers(),
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    models = [
        str(m.get("id"))
        for m in data.get("data", [])
        if isinstance(m, dict) and m.get("id")
    ]
    return sorted(models)