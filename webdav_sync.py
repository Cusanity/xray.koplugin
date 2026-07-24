#!/usr/bin/env python3
"""WebDAV sync for the X-Ray Generator (PC side).

This mirrors the on-device ``sync.lua`` remote layout so data uploaded from a PC
is picked up by KOReader's X-Ray *Cloud Sync* (and vice-versa). For every book
the remote tree is::

    <base>/<sdr_name>/xray_analysis/xray_data.json

where ``<sdr_name>`` is KOReader's ``.sdr`` folder name (``get_sdr_name``) and
``<base>`` is the WebDAV folder the user points both sides at.

Only the Python standard library is used (``urllib``), so the CLI/GUI keep their
dependency-light footprint. Authentication is HTTP Basic, which covers the
common self-hosted stacks (Nextcloud, Apache mod_dav, rclone serve, …).
"""

from __future__ import annotations

import base64
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

# Status codes returned by :func:`book_status` (mapped to labels/colors in the GUI).
STATUS_UNCONFIGURED = "unconfigured"  # no server set
STATUS_NONE = "none"                  # neither local nor remote copy
STATUS_NOT_UPLOADED = "not_uploaded"  # local exists, remote missing
STATUS_REMOTE_ONLY = "remote_only"    # remote exists, local missing
STATUS_SYNCED = "synced"              # both exist, same size
STATUS_DIFFERS = "differs"            # both exist, different size
STATUS_ERROR = "error"                # server unreachable / auth failed

ANALYSIS_SUBDIR = "xray_analysis"
JSON_NAME = "xray_data.json"

_TIMEOUT = 20


@dataclass
class WebDavConfig:
    """Connection settings for a WebDAV endpoint."""

    base_url: str = ""
    username: str = ""
    password: str = ""
    verify_ssl: bool = True

    def is_configured(self) -> bool:
        return bool(self.base_url.strip())

    @property
    def root(self) -> str:
        """Base URL without a trailing slash."""
        return self.base_url.strip().rstrip("/")


# =============================================================================
# URL helpers
# =============================================================================


def _quote_segment(name: str) -> str:
    """Percent-encode a single path segment (keeps it safe inside a URL)."""
    return urllib.parse.quote(name, safe="")


def _book_dir_url(cfg: WebDavConfig, sdr_name: str) -> str:
    return f"{cfg.root}/{_quote_segment(sdr_name)}"


def _analysis_dir_url(cfg: WebDavConfig, sdr_name: str) -> str:
    return f"{_book_dir_url(cfg, sdr_name)}/{_quote_segment(ANALYSIS_SUBDIR)}"


def json_url(cfg: WebDavConfig, sdr_name: str) -> str:
    """Full URL of a book's ``xray_data.json`` on the server."""
    return f"{_analysis_dir_url(cfg, sdr_name)}/{_quote_segment(JSON_NAME)}"


# =============================================================================
# Low-level request plumbing
# =============================================================================


def _ssl_context(cfg: WebDavConfig) -> ssl.SSLContext | None:
    if cfg.verify_ssl:
        return None  # urllib uses a verifying default context
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _request(
    cfg: WebDavConfig,
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = _TIMEOUT,
) -> tuple[int, dict[str, str], bytes]:
    """Perform one authenticated request. Returns (status, headers, body).

    Never raises for HTTP error statuses — those come back as the status code so
    callers can treat e.g. 404/405 as normal control flow.
    """
    req_headers = dict(headers or {})
    if cfg.username or cfg.password:
        token = base64.b64encode(
            f"{cfg.username}:{cfg.password}".encode()
        ).decode("ascii")
        req_headers["Authorization"] = f"Basic {token}"

    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    ctx = _ssl_context(cfg)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read()
            return resp.status, dict(resp.headers.items()), body
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return e.code, dict(e.headers.items() if e.headers else {}), body


# =============================================================================
# WebDAV operations
# =============================================================================


def ensure_collection(cfg: WebDavConfig, url: str) -> bool:
    """MKCOL a collection, treating "already exists" as success."""
    status, _headers, _body = _request(cfg, "MKCOL", url)
    # 201 Created; 405/301/302 => already there; 200 some servers.
    return status in (200, 201, 301, 302, 405)


def _propfind_size(cfg: WebDavConfig, url: str) -> int | None:
    """PROPFIND depth 0 → content length, or None if the resource is absent."""
    body = (
        b'<?xml version="1.0"?>'
        b'<a:propfind xmlns:a="DAV:"><a:prop>'
        b"<a:getcontentlength/><a:getetag/><a:getlastmodified/>"
        b"</a:prop></a:propfind>"
    )
    status, _headers, resp = _request(
        cfg, "PROPFIND", url,
        data=body,
        headers={"Depth": "0", "Content-Type": "application/xml"},
    )
    if status == 404:
        return None
    if status not in (207, 200):
        raise WebDavError(f"PROPFIND {status}")
    text = resp.decode("utf-8", errors="ignore")
    # Grab the first <...:getcontentlength>N</...> we can find.
    import re

    m = re.search(r"<[^:>]*:?getcontentlength[^>]*>(\d+)<", text)
    return int(m.group(1)) if m else 0


class WebDavError(RuntimeError):
    """Raised for unexpected WebDAV responses (auth failure, server error)."""


# =============================================================================
# High-level, per-book API used by the GUI
# =============================================================================


def test_connection(cfg: WebDavConfig) -> tuple[bool, str]:
    """Probe the base URL. Returns (ok, human-readable message)."""
    if not cfg.is_configured():
        return False, "No WebDAV URL configured."
    try:
        status, _headers, _body = _request(
            cfg, "PROPFIND", cfg.root + "/",
            data=b'<?xml version="1.0"?><a:propfind xmlns:a="DAV:">'
                 b"<a:prop><a:resourcetype/></a:prop></a:propfind>",
            headers={"Depth": "0", "Content-Type": "application/xml"},
        )
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        return False, str(getattr(e, "reason", e))
    if status in (200, 207):
        return True, "Connected."
    if status in (401, 403):
        return False, f"Authentication failed ({status})."
    if status == 404:
        return False, "Base folder not found (404)."
    return False, f"Server returned {status}."


def upload_book(cfg: WebDavConfig, sdr_name: str, local_json_path: str) -> None:
    """Create the remote tree and PUT a book's ``xray_data.json``.

    Raises :class:`WebDavError` on failure so the worker can report it.
    """
    if not os.path.exists(local_json_path):
        raise WebDavError("No local xray_data.json to upload.")
    ensure_collection(cfg, _book_dir_url(cfg, sdr_name))
    ensure_collection(cfg, _analysis_dir_url(cfg, sdr_name))
    with open(local_json_path, "rb") as f:
        payload = f.read()
    status, _headers, _body = _request(
        cfg, "PUT", json_url(cfg, sdr_name),
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    if status not in (200, 201, 204):
        raise WebDavError(f"Upload failed ({status}).")


def download_book(cfg: WebDavConfig, sdr_name: str, local_dest: str) -> None:
    """GET a book's remote ``xray_data.json`` into ``local_dest``."""
    status, _headers, body = _request(cfg, "GET", json_url(cfg, sdr_name))
    if status == 404:
        raise WebDavError("Remote xray_data.json not found.")
    if status != 200:
        raise WebDavError(f"Download failed ({status}).")
    os.makedirs(os.path.dirname(local_dest), exist_ok=True)
    with open(local_dest, "wb") as f:
        f.write(body)


def delete_book(cfg: WebDavConfig, sdr_name: str) -> None:
    """DELETE the remote book directory (collection) for *sdr_name*.

    Idempotent: a 404 response (resource already absent) is treated as success.
    Raises :class:`WebDavError` for any other unexpected status.
    """
    url = _book_dir_url(cfg, sdr_name)
    status, _headers, _body = _request(cfg, "DELETE", url)
    if status not in (200, 204, 207, 404):
        raise WebDavError(f"Delete failed ({status}).")


def book_status(
    cfg: WebDavConfig, sdr_name: str, local_json_path: str
) -> str:
    """Classify a book's local-vs-remote state into a STATUS_* code."""
    if not cfg.is_configured():
        return STATUS_UNCONFIGURED
    local_exists = os.path.exists(local_json_path)
    local_size = os.path.getsize(local_json_path) if local_exists else None
    try:
        remote_size = _propfind_size(cfg, json_url(cfg, sdr_name))
    except (WebDavError, urllib.error.URLError, OSError, ssl.SSLError):
        return STATUS_ERROR

    if remote_size is None:
        return STATUS_NOT_UPLOADED if local_exists else STATUS_NONE
    if not local_exists:
        return STATUS_REMOTE_ONLY
    # Both present: a size match is a good-enough "in sync" heuristic (the JSON
    # is re-serialized deterministically, so identical content => identical size).
    if local_size == remote_size:
        return STATUS_SYNCED
    return STATUS_DIFFERS


def config_from_prefs(prefs: dict[str, Any]) -> WebDavConfig:
    """Build a :class:`WebDavConfig` from the GUI preferences dict."""
    return WebDavConfig(
        base_url=str(prefs.get("webdav_url", "") or ""),
        username=str(prefs.get("webdav_user", "") or ""),
        password=str(prefs.get("webdav_pass", "") or ""),
        verify_ssl=bool(prefs.get("webdav_verify_ssl", True)),
    )


def list_children(cfg: WebDavConfig, url: str) -> list[tuple[str, str]]:
    """List direct sub-collections of *url* via PROPFIND depth=1.

    Returns ``(child_url, display_name)`` pairs sorted case-insensitively by
    display name.  The parent folder itself is excluded from the results.

    Raises :class:`WebDavError` on HTTP or XML parse errors.
    """
    import xml.etree.ElementTree as ET

    body = (
        b'<?xml version="1.0"?>'
        b'<a:propfind xmlns:a="DAV:"><a:prop>'
        b"<a:resourcetype/><a:displayname/>"
        b"</a:prop></a:propfind>"
    )
    canonical = url.rstrip("/")
    status, _headers, resp = _request(
        cfg, "PROPFIND", canonical + "/",
        data=body,
        headers={"Depth": "1", "Content-Type": "application/xml"},
    )
    if status not in (207, 200):
        raise WebDavError(f"PROPFIND listing failed ({status})")

    try:
        root_el = ET.fromstring(resp)
    except ET.ParseError as exc:
        raise WebDavError(f"Failed to parse server response: {exc}") from exc

    dav = "DAV:"
    parsed_base = urllib.parse.urlparse(canonical)
    parent_norm = urllib.parse.unquote(parsed_base.path).rstrip("/")

    results: list[tuple[str, str]] = []
    for response in root_el.iter(f"{{{dav}}}response"):
        href_el = response.find(f"{{{dav}}}href")
        if href_el is None or not href_el.text:
            continue
        href = href_el.text.strip()

        # Only keep collections (folders).
        rt = response.find(f".//{{{dav}}}resourcetype")
        if rt is None or rt.find(f"{{{dav}}}collection") is None:
            continue

        # Resolve href → full URL and normalised path for comparison.
        if href.startswith("http://") or href.startswith("https://"):
            href_norm = urllib.parse.unquote(
                urllib.parse.urlparse(href).path
            ).rstrip("/")
            child_url = href.rstrip("/")
        else:
            href_norm = urllib.parse.unquote(href).rstrip("/")
            child_url = (
                f"{parsed_base.scheme}://{parsed_base.netloc}"
                + href.rstrip("/")
            )

        # Skip the parent directory itself.
        if href_norm == parent_norm:
            continue

        # Use <displayname> when available; fall back to the last path segment.
        dn_el = response.find(f".//{{{dav}}}displayname")
        display_name = (dn_el.text or "").strip() if dn_el is not None else ""
        if not display_name:
            display_name = urllib.parse.unquote(
                href.rstrip("/").rsplit("/", 1)[-1]
            )

        results.append((child_url, display_name))

    return sorted(results, key=lambda x: x[1].lower())
