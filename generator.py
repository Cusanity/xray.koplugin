#!/usr/bin/env python3
"""
X-Ray Generator for EPUB Books

A standalone Python script to generate X-Ray analysis data for EPUB books
using OpenAI-compatible AI APIs. Produces progressive JSON cache files
compatible with the KOReader X-Ray plugin.

Usage:
    python generator.py <epub_file_path>
    python generator.py --browse  (browse Calibre library)

Configuration:
    Set environment variables or edit .env file:
    - XRAY_API_BASE: API endpoint (default: http://localhost:8080/v1)
    - XRAY_API_KEY: Your API key
    - XRAY_MODEL: Model name (default: gemini-2.5-flash-lite)
    - CALIBRE_LIBRARY: Path to Calibre library
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import sys
import threading
import time
from typing import Any, NoReturn

# =============================================================================
# Load .env file (if python-dotenv is installed)
# =============================================================================

try:
    from dotenv import load_dotenv

    _env_base = (
        os.path.dirname(sys.executable)
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__))
    )
    load_dotenv(os.path.join(_env_base, ".env"))
except ImportError:
    pass

# =============================================================================
# Frozen-aware path helpers (PyInstaller support)
# =============================================================================


def _get_bundle_dir() -> str:
    """Return directory containing bundled read-only assets (prompts, etc.).

    When running as a PyInstaller bundle this is ``sys._MEIPASS``; during
    normal development it is the directory that contains this source file.
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def _get_user_dir() -> str:
    """Return directory for user-writable data (xray output, .env).

    When running as a PyInstaller bundle this is the directory that contains
    the .exe so that output survives across restarts; during development it is
    the directory that contains this source file.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_xray_base_dir() -> str:
    """Return the root directory where X-Ray data folders are stored.

    Can be overridden by setting XRAY_OUTPUT_DIR in the environment or .env.
    Falls back to <user_dir>/xray.
    """
    override = os.environ.get("XRAY_OUTPUT_DIR", "").strip()
    if override:
        return override
    return os.path.join(_get_user_dir(), "xray")

# =============================================================================
# Local Module Imports
# =============================================================================

from ai_client import (
    AI_TIMEOUT_SECONDS,
    AVAILABLE_MODELS,
    CLAUDE_API_KEY,
    DEEPSEEK_API_KEY,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    TEMPERATURE,
    call_ai_with_retry,
    configure as configure_ai,
    consolidate_descriptions_batch,
    create_client,
    fetch_claude_models,
    fetch_deepseek_models,
    fetch_gemini_models,
    fetch_groq_models,
    get_ai_cache,
    get_max_chunk_size,
    get_max_workers,
    get_consolidate_batch_size,
    get_selected_api,
    get_selected_model,
    save_ai_cache,
    set_cache_dir,
)
from calibre_browser import (
    _load_preferences,
    _save_preferences,
    cleanup_ghost_folders,
    display_library_browser,
    scan_calibre_library,
)
from epub_reader import EpubReader, get_sdr_name
from master_data import MasterData, deduplicate_characters
from text_utils import META_THEMES, normalize_for_dedup, normalize_location_name
import retry_config
from retry_config import RetryChain, RetryEntry

try:
    import anthropic
except ImportError:
    anthropic = None

# =============================================================================
# Configuration
# =============================================================================

CALIBRE_LIBRARY = os.environ.get("CALIBRE_LIBRARY", "")
MAX_CHUNK_SIZE = 15000  # overridden per-call by get_max_chunk_size() when API is configured
MAX_RETRIES = 2

# =============================================================================
# Global State
# =============================================================================

_book_title: str = ""


# =============================================================================
# GUI Integration Hooks
# =============================================================================
#
# A PyQt6 front-end (generator_gui.py) can install these hooks so that progress
# updates are delivered to the UI and unrecoverable chunk errors raise an
# exception (which the batch loop can catch) instead of hard-exiting the whole
# process. In CLI mode both stay at their defaults and behavior is unchanged.

_gui_progress_hook = None  # callable(dict) -> None
_fatal_raises = False      # when True, _fatal_stop() raises instead of os._exit(1)
_stop_requested = False    # when True, emit_progress raises UserStoppedError


class FatalChunkError(RuntimeError):
    """Raised (in GUI mode) when a chunk fails unrecoverably."""


class UserStoppedError(RuntimeError):
    """Raised when the user requests an immediate stop via request_stop()."""


def set_gui_hooks(progress_hook=None, fatal_raises: bool = False) -> None:
    """Install GUI integration hooks. Called once by the PyQt6 front-end."""
    global _gui_progress_hook, _fatal_raises, _stop_requested
    _gui_progress_hook = progress_hook
    _fatal_raises = fatal_raises
    _stop_requested = False


def request_stop() -> None:
    """Signal that the user wants to stop immediately. Thread-safe."""
    global _stop_requested
    _stop_requested = True


def _fatal_stop(message: str = "X-Ray chunk processing failed") -> NoReturn:
    """Handle an unrecoverable chunk error.

    In CLI mode this hard-exits the process (original behavior). When a GUI has
    installed hooks, it raises instead so the batch loop can mark the book as
    failed and continue with the next one.
    """
    if _fatal_raises:
        raise FatalChunkError(message)
    os._exit(1)


def emit_progress(
    book: str = "",
    pct: int = 0,
    chunk: int = 0,
    total: int = 0,
    op: str = "",
    stats: dict | None = None,
) -> None:
    """Emit progress update for web monitor. Raises UserStoppedError if stop was requested."""
    if _stop_requested:
        raise UserStoppedError("Stopped by user")
    if _gui_progress_hook is not None:
        try:
            _gui_progress_hook(
                {
                    "book": book,
                    "pct": pct,
                    "chunk": chunk,
                    "total": total,
                    "op": op,
                    "stats": stats,
                }
            )
        except Exception:
            pass

    parts = []
    if book:
        parts.append(f"book={book}")
    if pct >= 0:
        parts.append(f"pct={pct}")
    if chunk >= 0:
        parts.append(f"chunk={chunk}")
    if total >= 0:
        parts.append(f"total={total}")
    if op:
        parts.append(f"op={op}")

    if parts:
        print(f"[PROGRESS] {' '.join(parts)}", flush=True)


# =============================================================================
# Prompt Loading
# =============================================================================


def _load_prompts() -> dict[str, str]:
    """Load prompts from shared JSON file."""
    script_dir = _get_bundle_dir()
    prompts_path = os.path.join(script_dir, "prompts", "zh.json")
    with open(prompts_path, "r", encoding="utf-8") as f:
        return json.load(f)


_PROMPTS = _load_prompts()
SYSTEM_PROMPT = _PROMPTS["system_instruction"]
CHUNK_SUMMARY_PROMPT = _PROMPTS["chunk_summary"]
CONSOLIDATE_DESC_PROMPT = _PROMPTS["consolidate_description"]
CONSOLIDATE_SUMMARY_PROMPT = _PROMPTS["consolidate_summary"]
CONSOLIDATE_BATCH_PROMPT = _PROMPTS["consolidate_descriptions_batch"]

# Max entities (characters + locations + summary) merged in one batched request
# is configurable via settings; see ai_client.get_consolidate_batch_size(). The
# default keeps a single call's output within model token limits while collapsing
# what used to be one request per entity into just a few requests per chunk.


# =============================================================================
# Chunk Processing
# =============================================================================


# SpineRange describes how a slice of the concatenated book text maps back to
# a specific EPUB spine item. abs_start/abs_end are character offsets in the
# full concatenated book text; chapter_len is the total plain-text length of
# that spine item (needed so the Lua side can compute an intra-chapter ratio).
SpineRange = dict  # {spine: int, abs_start: int, abs_end: int, chapter_len: int}


def build_chunks(
    chapters: list[tuple[str, str, int]],
    max_chunk_size: int = MAX_CHUNK_SIZE,
) -> list[tuple[list[str], str, int, list[SpineRange]]]:
    """Build text chunks from chapters respecting size limits.

    Accepts chapters as (title, text, spine_idx) triples from EpubReader.
    Returns (titles, chunk_text, abs_end_pos, spine_ranges) per chunk.
    spine_ranges lets callers map any relative-percent position within the
    chunk back to the exact spine item and character offset.
    """
    chunks: list[tuple[list[str], str, int, list[SpineRange]]] = []
    current_titles: list[str] = []
    current_text = ""
    current_spine_ranges: list[SpineRange] = []
    chars_processed = 0

    for chapter_title, chapter_text, spine_idx in chapters:
        chapter_len = len(chapter_text)
        abs_chapter_start = chars_processed  # position of this chapter in the book

        if chapter_len > max_chunk_size:
            if current_text.strip():
                chunks.append(
                    (current_titles, current_text.strip(), chars_processed, current_spine_ranges)
                )
                current_titles = []
                current_text = ""
                current_spine_ranges = []

            segment_idx = 0
            start = 0
            while start < chapter_len:
                end = min(start + max_chunk_size, chapter_len)

                if end < chapter_len:
                    search_start = max(end - 500, start)
                    last_newline = chapter_text.rfind("\n", search_start, end)
                    if last_newline > start:
                        end = last_newline + 1

                segment_text = chapter_text[start:end]
                if segment_idx == 0:
                    header = f"【{chapter_title}】"
                    title = chapter_title
                else:
                    header = f"【{chapter_title}（续{segment_idx}）】"
                    title = f"{chapter_title}（续{segment_idx}）"

                seg_abs_start = abs_chapter_start + start
                seg_abs_end = abs_chapter_start + end
                # chunk_text_start: where segment_text begins inside the chunk string.
                # chunk_text = f"{header}\n{segment_text}" (after .strip())
                seg_chunk_text_start = len(header) + 1
                segment_with_header = f"{header}\n{segment_text}\n\n"
                chunks.append(
                    (
                        [title],
                        segment_with_header.strip(),
                        seg_abs_end,
                        [{
                            "spine": spine_idx,
                            "abs_start": seg_abs_start,
                            "abs_end": seg_abs_end,
                            "chapter_abs_start": abs_chapter_start,
                            "chapter_len": chapter_len,
                            "chunk_text_start": seg_chunk_text_start,
                        }],
                    )
                )

                segment_idx += 1
                start = end

            chars_processed += chapter_len
        else:
            chapter_with_header = f"【{chapter_title}】\n{chapter_text}\n\n"

            if (
                current_text
                and len(current_text) + len(chapter_with_header) > max_chunk_size
            ):
                chunks.append(
                    (current_titles, current_text.strip(), chars_processed, current_spine_ranges)
                )
                current_titles = []
                current_text = ""
                current_spine_ranges = []

            current_titles.append(chapter_title)
            current_text += chapter_with_header
            current_spine_ranges.append({
                "spine": spine_idx,
                "abs_start": abs_chapter_start,
                "abs_end": abs_chapter_start + chapter_len,
                "chapter_abs_start": abs_chapter_start,
                "chapter_len": chapter_len,
                # chunk_text_start: where this chapter's plain text begins inside
                # the accumulated chunk string (after the "【title】\n" header).
                "chunk_text_start": len(current_text) - chapter_len - 2,
                # -chapter_len - 2 because we just did current_text += header\n + chapter_text + \n\n
                # so chapter_text starts at len(current_text) - chapter_len - 2 (the \n\n tail)
            })
            chars_processed += chapter_len

    if current_text.strip():
        chunks.append(
            (current_titles, current_text.strip(), chars_processed, current_spine_ranges)
        )

    return chunks


def _compute_xref(
    rel_pct: float,
    abs_start: int,
    abs_end: int,
    spine_ranges: list[SpineRange],
) -> dict[str, int] | None:
    """Map a relative-percent position within a chunk to a spine xref.

    Returns {spine, offset, chapter_len} where:
      spine       — 0-based EPUB spine index (CREngine DocFragment[spine+1])
      offset      — character offset within that spine item's plain text
      chapter_len — total plain-text length of the spine item
    """
    chunk_len = abs_end - abs_start
    if chunk_len <= 0 or not spine_ranges:
        return None

    target_abs = abs_start + (rel_pct / 100.0) * chunk_len

    for sr in spine_ranges:
        if target_abs <= sr["abs_end"] or sr is spine_ranges[-1]:
            # Offset is from the chapter's own start, not the chunk slice start.
            chapter_origin = sr.get("chapter_abs_start", sr["abs_start"])
            raw_offset = target_abs - chapter_origin
            offset = max(0, min(round(raw_offset), sr["chapter_len"]))
            return {
                "spine": sr["spine"],
                "offset": offset,
                "chapter_len": sr["chapter_len"],
            }

    return None


def find_resume_checkpoint(output_dir: str) -> tuple[int, dict[str, Any] | None]:
    """Find and load checkpoint from xray_data.json if it exists."""
    resume_pct = 0
    resume_data = None

    checkpoint_file = os.path.join(output_dir, "xray_data.json")
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                resume_data = json.load(f)
            resume_pct = resume_data.get("analysis_progress", 0)
            if resume_pct > 0:
                print(f"Found checkpoint at {resume_pct}%. Resuming from there...")
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load checkpoint {checkpoint_file}: {e}")
            print("Starting fresh...")
            resume_pct = 0
            resume_data = None

    return resume_pct, resume_data


def restore_master_from_checkpoint(
    master: MasterData,
    resume_data: dict[str, Any],
    title: str,
    author: str,
) -> None:
    """Restore MasterData state from checkpoint data."""
    for char in resume_data.get("characters", []):
        name = char.get("name", "").strip()
        if name:
            dedup_key = normalize_for_dedup(name)
            descriptions = char.get("descriptions", [])
            master.characters[dedup_key] = {
                "display_name": name,
                "descriptions": [],
                "historic_descriptions": descriptions,
                "consolidated": descriptions[-1]["text"] if descriptions else "",
                "events": char.get("events", []),
            }

    for loc in resume_data.get("locations", []):
        name = loc.get("name", "").strip()
        if name:
            dedup_key = normalize_location_name(name)
            descriptions = loc.get("descriptions", [])
            master.locations[dedup_key] = {
                "display_name": name,
                "descriptions": [],
                "historic_descriptions": descriptions,
                "consolidated": descriptions[-1]["text"] if descriptions else "",
            }

    for theme in resume_data.get("themes", []):
        # AI sometimes stores theme objects instead of plain strings in checkpoints.
        if isinstance(theme, dict):
            theme = theme.get("theme") or theme.get("name") or ""
        if theme and isinstance(theme, str) and theme not in META_THEMES:
            master.themes.add(theme)

    for event in resume_data.get("timeline", []):
        master.events.append(event)

    if resume_data.get("summary"):
        master.summary_parts.append(resume_data["summary"])

    master.book_title = resume_data.get("book_title", title)
    master.author = resume_data.get("author", author)
    master.author_bio = resume_data.get("author_bio", "")

    print(
        f"Restored {len(master.characters)} characters, "
        f"{len(master.locations)} locations from checkpoint"
    )


def _process_chunk_worker(
    client: Any,
    chunk_text: str,
    title: str,
    author: str,
    start_pct: int,
    end_pct: int,
    model: str,
    chunk_index: int,
    total_chunks: int,
    chapter_display: str,
    abs_start: int = 0,
    abs_end: int = 0,
    spine_ranges: list | None = None,
) -> dict[str, Any] | None:
    """Worker function to process a single chunk independent of master state."""
    prompt = CHUNK_SUMMARY_PROMPT % (title, author, end_pct, chunk_text)

    def _annotate_events(characters: list) -> None:
        """Annotate each event with absolute_percent, xref, and anchor in-place.

        The AI now returns an ``anchor`` field — a verbatim quote from the source
        text.  We locate that quote in ``chunk_text`` to get an exact character
        position, then derive both ``absolute_percent`` and ``xref`` from it.
        Falls back to mid-chunk when the anchor is absent or not found.
        """
        for char in characters:
            for event in char.get("events", []):
                anchor = (event.get("anchor") or "").strip()
                pos = chunk_text.find(anchor) if anchor else -1

                if pos >= 0 and spine_ranges:
                    # Find which spine range the anchor falls in by chunk_text offset.
                    found_sr = spine_ranges[-1]  # default to last range
                    for sr in spine_ranges:
                        ct_start = sr.get("chunk_text_start", 0)
                        ct_end = ct_start + (sr["abs_end"] - sr["abs_start"])
                        if pos < ct_end:
                            found_sr = sr
                            break

                    ct_start = found_sr.get("chunk_text_start", 0)
                    within_slice = max(0, pos - ct_start)
                    chapter_offset = (
                        (found_sr["abs_start"] - found_sr.get("chapter_abs_start", found_sr["abs_start"]))
                        + within_slice
                    )
                    chapter_offset = min(chapter_offset, found_sr["chapter_len"])

                    event["xref"] = {
                        "spine": found_sr["spine"],
                        "offset": chapter_offset,
                        "chapter_len": found_sr["chapter_len"],
                    }
                    # absolute_percent: interpolate using real abs book position
                    abs_book_pos = found_sr.get("chapter_abs_start", found_sr["abs_start"]) + chapter_offset
                    chunk_span = abs_end - abs_start
                    if chunk_span > 0:
                        frac = (abs_book_pos - abs_start) / chunk_span
                        frac = max(0.0, min(1.0, frac))
                    else:
                        frac = 0.5
                    event["absolute_percent"] = round(
                        start_pct + frac * (end_pct - start_pct), 1
                    )
                else:
                    # Anchor missing or not found — fall back to mid-chunk.
                    if anchor and pos < 0:
                        event["_anchor_miss"] = True  # debug flag, not persisted
                    mid_frac = 0.5
                    event["absolute_percent"] = round(
                        start_pct + mid_frac * (end_pct - start_pct), 1
                    )
                    if spine_ranges and abs_end > abs_start:
                        xref = _compute_xref(50.0, abs_start, abs_end, spine_ranges)
                        if xref:
                            event["xref"] = xref

    cached_data = get_ai_cache(prompt)
    if cached_data:
        print(f"  [Chunk {chunk_index}] ✓ Using cached AI response")
        _annotate_events(cached_data.get("characters", []))
        return cached_data

    print(
        f"  [Chunk {chunk_index}/{total_chunks}] AI Request sent... ({len(prompt)} chars)"
    )

    try:
        # Groq free tier has very low TPM (6-12K); cap output tokens
        request_max_tokens = 8192 if get_selected_api() == "groq" else 16384
        response = call_ai_with_retry(
            client,
            model,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            max_tokens=request_max_tokens,
            retries=MAX_RETRIES + 1,
        )

        if response.choices[0].finish_reason == "length":
            print(
                f"  [Chunk {chunk_index}] ⚠ Response truncated on {model}. Stopping."
            )
            _fatal_stop(f"Chunk {chunk_index}: response truncated on {model}")

        content = response.choices[0].message.content
        if content is None:
            print(
                f"  [Chunk {chunk_index}] ⚠ Safety filter triggered on {model}. "
                "Stopping."
            )
            _fatal_stop(f"Chunk {chunk_index}: safety filter triggered on {model}")

        content = content.replace("```json", "").replace("```", "").strip()

        try:
            chunk_data = json.loads(content)
            save_ai_cache(prompt, chunk_data)

            # If the user message was truncated, re-process the lost chunk_text tail.
            trimmed = getattr(response, 'trimmed_chars', 0)
            if trimmed > 0:
                # The prompt template has a fixed suffix after the chunk_text
                # placeholder (closing tag + JSON output schema ~1520 chars).
                # suffix_len = length of that fixed tail in the template.
                suffix_len = len(CHUNK_SUMMARY_PROMPT.split('%s')[-1])
                trimmed_from_chunk = max(0, trimmed - suffix_len)
                if trimmed_from_chunk > 0:
                    surviving = len(chunk_text) - trimmed_from_chunk
                    remainder_text = chunk_text[surviving:]
                    print(
                        f"  [Chunk {chunk_index}] ↳ Re-processing "
                        f"{trimmed_from_chunk}-char remainder..."
                    )
                    remainder_data = _process_chunk_worker(
                        client, remainder_text, title, author,
                        start_pct, end_pct, model,
                        chunk_index, total_chunks, chapter_display,
                        abs_start + surviving, abs_end, spine_ranges,
                    )
                    if remainder_data:
                        # remainder characters were already annotated by the
                        # recursive call — just concatenate the lists.
                        chunk_data["characters"] = (
                            chunk_data.get("characters", [])
                            + remainder_data.get("characters", [])
                        )
                        chunk_data["locations"] = (
                            chunk_data.get("locations", [])
                            + remainder_data.get("locations", [])
                        )
                        chunk_data["themes"] = (
                            chunk_data.get("themes", [])
                            + remainder_data.get("themes", [])
                        )
                        r_summary = remainder_data.get("summary")
                        if isinstance(r_summary, dict):
                            r_summary = (
                                r_summary.get("description")
                                or r_summary.get("text")
                                or r_summary.get("summary") or ""
                            )
                        if r_summary:
                            existing = chunk_data.get("summary") or ""
                            if isinstance(existing, dict):
                                existing = (
                                    existing.get("description")
                                    or existing.get("text")
                                    or existing.get("summary") or ""
                                )
                            chunk_data["summary"] = (
                                existing + " " + r_summary
                            ).strip()

            _annotate_events(chunk_data.get("characters", []))
            print(f"  [Chunk {chunk_index}] ✓ Received AI response")
            return chunk_data

        except json.JSONDecodeError as e:
            print(f"  [Chunk {chunk_index}] JSON Error from {model}: {e}")
            print(f"  Raw content was:\n{content}")
            _fatal_stop(f"Chunk {chunk_index}: JSON decode error from {model}")

    except FatalChunkError:
        raise
    except Exception as e:
        print(f"  [Chunk {chunk_index}] unexpected error on {model}: {e}")
        _fatal_stop(f"Chunk {chunk_index}: unexpected error on {model}: {e}")

    return None


def consolidate_pending_items(
    client: Any, master: MasterData, current_pct: int = 0
) -> None:
    """Consolidate all pending items in a single batched AI request per batch.

    Instead of one AI call per character/location (plus one for the summary),
    every entity that needs consolidation this chunk — characters, locations,
    and the running summary — is packed into one payload and sent together.
    Large casts are split into batches of ``get_consolidate_batch_size()`` items.
    """
    chars_to_consolidate, locs_to_consolidate = master.get_items_needing_consolidation()
    summary_needed = master.needs_summary_consolidation()

    if not chars_to_consolidate and not locs_to_consolidate and not summary_needed:
        return

    batch_size = get_consolidate_batch_size()

    # Build one flat list of work items with a stable integer id, plus a
    # parallel list of "how to apply the result" instructions.
    items: list[dict[str, Any]] = []
    apply_ops: list[tuple[str, str | None]] = []

    for name, combined_desc in chars_to_consolidate:
        items.append(
            {"id": len(items), "kind": "人物", "name": name, "text": combined_desc}
        )
        apply_ops.append(("character", name))

    for name, combined_desc in locs_to_consolidate:
        items.append(
            {"id": len(items), "kind": "地点", "name": name, "text": combined_desc}
        )
        apply_ops.append(("location", name))

    if summary_needed:
        items.append(
            {
                "id": len(items),
                "kind": "概要",
                "name": f"《{master.book_title}》",
                "text": master.get_combined_summary(),
            }
        )
        apply_ops.append(("summary", None))

    print(
        f"  [Consolidation] Batching {len(chars_to_consolidate)} chars, "
        f"{len(locs_to_consolidate)} locs"
        f"{', summary' if summary_needed else ''} "
        f"into {math.ceil(len(items) / batch_size)} request(s)"
    )

    # Run each batch as its own request; batches themselves run concurrently.
    batches = [
        items[i : i + batch_size]
        for i in range(0, len(items), batch_size)
    ]

    results: dict[int, str] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=get_max_workers()
    ) as executor:
        futures = [
            executor.submit(
                consolidate_descriptions_batch,
                client,
                batch,
                SYSTEM_PROMPT,
                CONSOLIDATE_BATCH_PROMPT,
            )
            for batch in batches
        ]
        for future in concurrent.futures.as_completed(futures):
            try:
                results.update(future.result())
            except Exception as e:
                print(f"    [Consolidation Error]: {e}")

    # Apply results back onto master data in original order.
    for item_id, (etype, name) in enumerate(apply_ops):
        text = results.get(item_id)
        if not text:
            continue
        if etype == "summary":
            master.apply_summary_consolidation(text)
            print("    ✓ [Summ] summary updated")
        else:
            if name is None:
                continue
            master.apply_consolidation(etype, name, text, current_pct)
            print(f"    ✓ [{etype[:4].capitalize()}] {name} updated")


# =============================================================================
# UI Selectors
# =============================================================================


def display_api_selector() -> str:
    """Display API selection menu."""
    prefs = _load_preferences()
    last_api = prefs.get("last_api", "openai")

    print(f"\n{'=' * 60}")
    print("Select API Provider")
    print(f"{'=' * 60}\n")

    options = [
        ("openai", "OpenAI (Standard)"),
        ("claude", "Anthropic Claude (Official)"),
        ("groq", "Groq (Fast Inference)"),
        ("gemini", "Google Gemini (Official)"),
        ("deepseek", "DeepSeek (Official)"),
    ]

    default_idx = -1
    for i, (key, label) in enumerate(options, 1):
        marker = (
            " (last used)"
            if key == last_api
            else (" (default)" if key == "openai" else "")
        )
        if key == last_api:
            default_idx = i
        print(f"  [{i}] {label}{marker}")

    print(f"\n{'─' * 60}")
    print("Enter number, or press Enter for default")

    try:
        user_input = input("\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        return "openai"

    selected = last_api
    if user_input:
        try:
            idx = int(user_input)
            if 1 <= idx <= len(options):
                selected = options[idx - 1][0]
        except ValueError:
            pass

    prefs["last_api"] = selected
    _save_preferences(prefs)
    return selected


def display_model_selector(selected_api: str) -> str | None:
    """Display model selection menu and return selected model name."""
    from ai_client import MODEL_NAME

    prefs = _load_preferences()
    last_model = prefs.get("last_model", "")

    if selected_api == "claude":
        current_models = fetch_claude_models()
    elif selected_api == "groq":
        current_models = fetch_groq_models()
    elif selected_api == "gemini":
        current_models = fetch_gemini_models()
    elif selected_api == "deepseek":
        current_models = fetch_deepseek_models()
    else:
        current_models = list(AVAILABLE_MODELS)

    effective_default = last_model if last_model in current_models else MODEL_NAME

    print(f"\n{'=' * 60}")
    print("Select AI Model")
    print(f"{'=' * 60}\n")

    default_idx = -1
    for i, model in enumerate(current_models, 1):
        markers = []
        if model == effective_default:
            markers.append("last used" if model == last_model else "default")
            default_idx = i
        marker_str = f" ({', '.join(markers)})" if markers else ""
        print(f"  [{i}] {model}{marker_str}")

    print(f"\n{'─' * 60}")
    hint = f" [Enter={default_idx}]" if default_idx > 0 else ""
    print(f"Enter model number, or press Enter for last used{hint}")

    try:
        user_input = input("\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return None

    if not user_input:
        prefs["last_model"] = effective_default
        _save_preferences(prefs)
        print(f"\nSelected model: {effective_default}")
        return effective_default

    try:
        model_num = int(user_input)
        if 1 <= model_num <= len(current_models):
            selected = current_models[model_num - 1]
            print(f"\nSelected model: {selected}")
            prefs["last_model"] = selected
            _save_preferences(prefs)
            return selected
        else:
            print(f"Invalid model number. Using: {effective_default}")
            return effective_default
    except ValueError:
        print(f"Invalid input. Using: {effective_default}")
        return effective_default


# =============================================================================
# Book Processing
# =============================================================================


def _setup_output_directory(target_path: str, create: bool = True) -> str | None:
    """Create output directory structure."""
    sdr_name = get_sdr_name(target_path)
    xray_base_dir = get_xray_base_dir()
    output_dir = os.path.join(xray_base_dir, sdr_name, "xray_analysis")

    if create and not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            print(f"Created output directory: {output_dir}")
        except OSError as e:
            print(f"Error creating directory {output_dir}: {e}")
            return None
    elif create and os.path.exists(output_dir):
        print(f"Using output directory: {output_dir}")

    return output_dir


def _calculate_start_step(
    resume_pct: int,
    resume_data: dict[str, Any] | None,
    chunks: list[tuple[list[str], str, int, list]],
    total_len: int,
    master: MasterData,
    title: str,
    author: str,
    total_chunks: int,
) -> int | None:
    """Calculate starting chunk based on resume state."""
    start_step = 1

    if resume_pct > 0 and resume_data:
        for idx, (_, _, end_pos, _) in enumerate(chunks):
            chunk_pct = int((end_pos / total_len) * 100)
            if chunk_pct >= resume_pct:
                start_step = idx + 2
                break
        else:
            start_step = total_chunks + 1

        restore_master_from_checkpoint(master, resume_data, title, author)

        if start_step > total_chunks:
            print(f"Analysis already complete at {resume_pct}%!")
            return None

        print(f"Resuming from chunk {start_step}/{total_chunks}")

    return start_step


def process_book(target_path: str, client: Any, selected_model: str) -> None:
    """Process a single book: analyze and generate X-Ray."""
    global _book_title

    print(f"\nReading {target_path}...")

    # Check for existing completed X-Ray data before reading the book
    output_dir = _setup_output_directory(target_path, create=False)
    if output_dir and os.path.exists(output_dir):
        _, resume_data = find_resume_checkpoint(output_dir)
        if resume_data and resume_data.get("analysis_progress", 0) == 100:
            print(f"Skipping {target_path}: X-Ray data already complete (100%).")
            return

    reader = EpubReader(target_path)
    chapters, title, author = reader.get_chapters()

    if not chapters:
        print("Failed to extract chapters.")
        return

    total_len = sum(len(text) for _, text, _ in chapters)
    print(f"Total text length: {total_len} characters")
    print(f"Book Title: {title}")
    print(f"Found {len(chapters)} chapters")

    if not output_dir:
        output_dir = _setup_output_directory(target_path, create=True)

    if output_dir is None:
        return

    cache_dir = os.path.join(output_dir, ".ai_cache")
    os.makedirs(cache_dir, exist_ok=True)
    set_cache_dir(cache_dir)

    resume_pct, resume_data = find_resume_checkpoint(output_dir)

    _book_title = title

    chunk_size = get_max_chunk_size()
    chunks = build_chunks(chapters, max_chunk_size=chunk_size)
    total_chunks = len(chunks)
    print(
        f"Will process in {total_chunks} chapter-based chunks "
        f"(max {chunk_size} chars each)"
    )

    emit_progress(book=title, pct=0, chunk=0, total=total_chunks, op="initializing")

    master = MasterData(book_title=title, author=author)
    master.set_prompts(SYSTEM_PROMPT, CONSOLIDATE_DESC_PROMPT, CONSOLIDATE_SUMMARY_PROMPT)

    start_step = _calculate_start_step(
        resume_pct, resume_data, chunks, total_len, master, title, author, total_chunks
    )

    if start_step is None:
        return

    print("\n=== Starting Analysis with Python-Maintained Data Architecture ===")
    print(f"    (Parallel Execution with {get_max_workers()} workers)")

    emit_progress(
        book=title, pct=0, chunk=start_step, total=total_chunks, op="analyzing"
    )

    # Prepare chunk parameters list
    chunk_tasks = []
    for i in range(start_step, total_chunks + 1):
        chapter_titles, chunk_text, end_pos, spine_ranges = chunks[i - 1]

        prev_end_pos = chunks[i - 2][2] if i > 1 else 0
        start_pct = math.floor(prev_end_pos * 100 / total_len)
        end_pct = math.ceil(end_pos * 100 / total_len)

        chapter_display = (
            " → ".join(chapter_titles)
            if len(chapter_titles) > 1
            else chapter_titles[0]
        )

        chunk_tasks.append(
            {
                "chunk_index": i,
                "chunk_text": chunk_text,
                "title": title,
                "author": author,
                "start_pct": start_pct,
                "end_pct": end_pct,
                "chapter_display": chapter_display,
                "abs_start": prev_end_pos,
                "abs_end": end_pos,
                "spine_ranges": spine_ranges,
            }
        )

    # Execute chunks in parallel but merge in order
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=get_max_workers()
    ) as executor:
        futures = {}
        for task in chunk_tasks:
            if not task["chunk_text"].strip():
                print(f"Skipping empty chunk ({task['chunk_index']}/{total_chunks})")
                continue

            future = executor.submit(
                _process_chunk_worker,
                client,
                task["chunk_text"],
                task["title"],
                task["author"],
                task["start_pct"],
                task["end_pct"],
                selected_model,
                task["chunk_index"],
                total_chunks,
                task["chapter_display"],
                task["abs_start"],
                task["abs_end"],
                task["spine_ranges"],
            )
            futures[task["chunk_index"]] = future

        # Process results in order to maintain sequential data integrity
        for task in chunk_tasks:
            idx = task["chunk_index"]
            if idx not in futures:
                continue

            future = futures[idx]
            try:
                chunk_data = future.result()
                if chunk_data:
                    print(
                        f"\n=== Merging Chunk {idx}/{total_chunks}: "
                        f"《{task['chapter_display']}》 ==="
                    )
                    master.merge_chunk(chunk_data)

                    stats = master.get_stats()
                    print(
                        f"  [Merged] Chars: {stats['characters']}, "
                        f"Locs: {stats['locations']}, Events: {stats['events']}"
                    )

                    emit_progress(
                        book=title,
                        pct=task["end_pct"],
                        chunk=idx,
                        total=total_chunks,
                        op="merging",
                        stats=stats,
                    )

                    emit_progress(
                        book=title,
                        pct=task["end_pct"],
                        chunk=idx,
                        total=total_chunks,
                        op="consolidating",
                    )
                    consolidate_pending_items(client, master, task["end_pct"])

                    output_data = master.to_output_json(task["end_pct"])
                    filename = os.path.join(output_dir, "xray_data.json")
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(output_data, f, ensure_ascii=False, indent=2)
                    print(f"  Saved {filename}")

                    emit_progress(
                        book=title,
                        pct=task["end_pct"],
                        chunk=idx,
                        total=total_chunks,
                        op="saved_checkpoint",
                    )
                else:
                    print(f"  [Chunk {idx}] Skipped due to AI failure/filtering.")
            except Exception as e:
                print(f"  [Chunk {idx}] Fatal Error in worker: {e}")

    emit_progress(
        book=title, pct=100, chunk=total_chunks, total=total_chunks, op="finalizing"
    )
    _finalize_output(master, output_dir)
    emit_progress(
        book=title, pct=100, chunk=total_chunks, total=total_chunks, op="completed"
    )


def push_to_koreader(json_path: str, device: str) -> None:
    """Push xray_data.json to a running KOReader XRayReceiver via plain HTTP POST.

    device — 'ip' or 'ip:port' (default port 8763).
    Uses only stdlib, no extra dependencies.
    """
    import socket as _socket

    host, _, port_str = device.partition(":")
    port = int(port_str) if port_str else 8763

    with open(json_path, "rb") as f:
        body = f.read()

    request = (
        "POST /xray_result HTTP/1.0\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode() + body

    print(f"\nPushing xray_data.json to KOReader at {host}:{port}…")
    try:
        with _socket.create_connection((host, port), timeout=15) as s:
            s.sendall(request)
            response = s.recv(256).decode(errors="ignore")
            if "200" in response:
                print("✓ X-Ray data pushed to device successfully.")
            else:
                print(f"⚠ Device responded: {response[:80]}")
    except Exception as e:
        print(f"✗ Push failed: {e}")
        print("  Ensure KOReader has 'Receive from PC' active (X-Ray → Cloud Sync).")


def _finalize_output(master: MasterData, output_dir: str) -> None:
    """Generate and save final output file."""
    final_data = master.to_output_json(100)

    print("\n=== Finalizing: Deduplicating and Sorting Characters ===")
    final_data = deduplicate_characters(final_data)

    print(f"\n=== Final Analysis: {len(final_data['timeline'])} timeline events ===")

    filename = os.path.join(output_dir, "xray_data.json")
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
    print(f"\nSaved final analysis to {filename}")

    print("\n=== Done! ===")
    print(f"Output directory: {output_dir}")


# =============================================================================
# KOReader Sync
# =============================================================================


def _ask_koreader_device() -> str | None:
    """Ask for KOReader device IP at startup, verify the port is open.

    Returns the device string (ip or ip:port) to use for pushing, or None to skip.
    If XRAY_DEVICE env var is set it is returned immediately without prompting.
    """
    import socket as _socket

    env_device = os.environ.get("XRAY_DEVICE", "").strip()
    if env_device:
        return env_device

    print(f"\n{'─' * 60}")
    print("Sync results to KOReader when done? (optional)")
    print("  On KOReader: X-Ray menu \u2192 Cloud Sync \u2192 Receive from PC")
    print("  Enter device IP[:port]  (e.g. 192.168.1.42  or  192.168.2.2:8763)")
    print("  Press Enter to skip")
    try:
        device = input("\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped.")
        return None

    if not device:
        return None

    host, _, port_str = device.partition(":")
    port = int(port_str) if port_str else 8763

    print(f"  Checking {host}:{port}\u2026", end="", flush=True)
    try:
        with _socket.create_connection((host, port), timeout=3):
            print(" \u2713 Port is open.")
            return device
    except ConnectionRefusedError:
        print(" \u2717 Connection refused.")
        print("  Make sure 'Receive from PC' is active on KOReader first.")
    except OSError as e:
        print(f" \u2717 {e}")
        print("  Check the IP and that the device is on the same network.")

    try:
        cont = input("  Continue anyway and push when done? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        cont = "n"
    return device if cont == "y" else None


def _push_all_to_koreader(target_paths: list[str], device: str) -> None:
    """Push xray_data.json for each processed book to the given KOReader device."""
    xray_base_dir = get_xray_base_dir()

    for i, path in enumerate(target_paths):
        if len(target_paths) > 1:
            print(f"\nBook {i + 1}/{len(target_paths)}: {os.path.basename(path)}")
            print("  Open this book on KOReader, then press Enter to push\u2026")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                print("  Skipped.")
                continue
        sdr_name = get_sdr_name(path)
        json_path = os.path.join(xray_base_dir, sdr_name, "xray_analysis", "xray_data.json")
        if os.path.exists(json_path):
            push_to_koreader(json_path, device)
        else:
            print(f"\u26a0 No xray_data.json found for {os.path.basename(path)}, skipping.")


# =============================================================================
# Main Entry Point
# =============================================================================


def _get_target_paths() -> list[str] | None:
    """Get target EPUB path(s) from CLI args or Calibre browser."""
    if len(sys.argv) < 2 or sys.argv[1] in ("--browse", "-b", "--list", "-l"):
        print("X-Ray Generator - Calibre Library Browser")
        print(f"Scanning library: {CALIBRE_LIBRARY}")
        print("Please wait...\n")

        books = scan_calibre_library(CALIBRE_LIBRARY)
        cleanup_ghost_folders(CALIBRE_LIBRARY)

        if not books:
            print("\nNo EPUB books found in Calibre library.")
            print(f"Check that CALIBRE_LIBRARY path is correct: {CALIBRE_LIBRARY}")
            print("\nAlternatively, specify an EPUB file directly:")
            print("  python generator.py <epub_file_path>")
            return None

        print(f"Found {len(books)} EPUB books.\n")
        target_paths = display_library_browser(books)
        if not target_paths:
            return None
        print()
        return target_paths

    target_path = sys.argv[1]
    if not os.path.exists(target_path):
        print(f"File not found: {target_path}")
        print("\nUsage:")
        print("  python generator.py <epub_file_path>")
        print("  python generator.py --browse  (browse Calibre library)")
        print("\nConfiguration via environment variables:")
        print("  XRAY_API_BASE    - API endpoint (default: http://localhost:8080/v1)")
        print("  XRAY_API_KEY     - Your API key")
        print("  XRAY_MODEL       - Model name (default: gemini-2.5-flash-lite)")
        print("  CALIBRE_LIBRARY  - Path to Calibre library")
        print("  XRAY_DEVICE      - Push result to KOReader: ip or ip:port (default port 8763)")
        return None

    return [target_path]



def _get_target_paths_from_browser() -> list[str] | None:
    """Get target EPUB path(s) from Calibre browser (no CLI args)."""
    print("X-Ray Generator - Calibre Library Browser")
    print(f"Scanning library: {CALIBRE_LIBRARY}")
    print("Please wait...\n")

    books = scan_calibre_library(CALIBRE_LIBRARY)
    cleanup_ghost_folders(CALIBRE_LIBRARY)

    if not books:
        print("\nNo EPUB books found in Calibre library.")
        print(f"Check that CALIBRE_LIBRARY path is correct: {CALIBRE_LIBRARY}")
        return None

    print(f"Found {len(books)} EPUB books.\n")
    target_paths = display_library_browser(books)
    if not target_paths:
        return None
    print()
    return target_paths


def main() -> None:
    """Main entry point."""
    # Ask for KOReader device upfront so user can set it up before analysis starts
    koreader_device = _ask_koreader_device()

    # Select API provider (once per session)
    selected_api = display_api_selector()
    if selected_api == "groq":
        if not GROQ_API_KEY:
            print("Error: GROQ_API_KEY environment variable not set.")
            print("Get your API key at: https://console.groq.com/keys")
            return
        print("Using Groq API (fast inference)")
    elif selected_api == "gemini":
        if not GEMINI_API_KEY:
            print("Error: GEMINI_API_KEY environment variable not set.")
            print("Get your API key at: https://aistudio.google.com/apikey")
            return
        print("Using Google Gemini API")
    elif selected_api == "deepseek":
        if not DEEPSEEK_API_KEY:
            print("Error: DEEPSEEK_API_KEY environment variable not set.")
            print("Get your API key at: https://platform.deepseek.com/api_keys")
            return
        print("Using DeepSeek API")

    # Select model (once per session)
    selected_model = display_model_selector(selected_api)
    if selected_model is None:
        return

    # Configure AI client module. The CLI's interactive pick is treated as a
    # "quick" single-model run: it builds a one-entry retry chain (preserving
    # the chain-level options from prefs). Rich fallback chains are configured
    # in the GUI, which persists them to .xray_prefs.json.
    cli_chain = RetryChain(
        entries=[RetryEntry(provider=selected_api, model=selected_model)],
        options=retry_config.load_retry_chain().options,
    )
    configure_ai(
        selected_api=selected_api,
        selected_model=selected_model,
        retry_chain=cli_chain,
    )

    # Create client
    client = create_client(selected_api)
    if selected_api in ("claude", "openai", "groq", "gemini", "deepseek") and client is None:
        return

    # First iteration: use CLI path if given, then always show browser
    cli_path = (
        sys.argv[1]
        if len(sys.argv) >= 2 and sys.argv[1] not in ("--browse", "-b", "--list", "-l")
        else None
    )
    first_iteration = True

    while True:
        if first_iteration and cli_path:
            if not os.path.exists(cli_path):
                print(f"File not found: {cli_path}")
                return
            target_paths = [cli_path]
        else:
            target_paths = _get_target_paths_from_browser()
            if not target_paths:
                break

        first_iteration = False

        print(f"\n=== Batch Processing {len(target_paths)} Books ===")
        print(f"API: {selected_api} | Model: {selected_model}")

        for i, path in enumerate(target_paths):
            print(f"\n{'#' * 60}")
            print(f"Processing Book {i + 1}/{len(target_paths)}")
            print(f"{'#' * 60}")
            try:
                process_book(path, client, selected_model)
            except Exception as e:
                print(f"\nERROR processing {path}: {e}")
                import traceback

                traceback.print_exc()

        print("\nBatch processing complete.")

        if koreader_device:
            _push_all_to_koreader(target_paths, koreader_device)


if __name__ == "__main__":
    main()
