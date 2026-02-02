#!/usr/bin/env python3
"""
X-Ray Generator for EPUB Books

A standalone Python script to generate X-Ray analysis data for EPUB books
using OpenAI-compatible AI APIs. Produces progressive JSON cache files
compatible with the KOReader X-Ray plugin.

Usage:
    python xray_generator.py <epub_file_path>
    python xray_generator.py --browse  (browse Calibre library)

Configuration:
    Set environment variables or edit the CONFIG section below:
    - XRAY_API_BASE: API endpoint (default: http://localhost:8080/v1)
    - XRAY_API_KEY: Your API key
    - XRAY_MODEL: Model name (default: gemini-2.5-flash-lite)
    - CALIBRE_LIBRARY: Path to Calibre library
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

from openai import OpenAI

# =============================================================================
# Required Dependencies
# =============================================================================

try:
    import opencc

    _T2S_CONVERTER = opencc.OpenCC("t2s")
except ImportError:
    print(
        "Error: opencc is required. Install with: pip install opencc-python-reimplemented"
    )
    sys.exit(1)

# =============================================================================
# Configuration
# =============================================================================

API_BASE_URL = os.environ.get("XRAY_API_BASE", "http://127.0.0.1:8045/v1")
API_KEY = os.environ.get("XRAY_API_KEY", "sk-e80a5d77e693448cadce981aa5b752de")
MODEL_NAME = os.environ.get("XRAY_MODEL", "gemini-3-pro-high")
TEMPERATURE = 0.4
TOP_P = 0.95

CALIBRE_LIBRARY = os.environ.get(
    "CALIBRE_LIBRARY", r"C:\Users\-GALACTUS-\Calibre Library"
)

MAX_CHUNK_SIZE = 15000
MAX_RETRIES = 2
AI_TIMEOUT_SECONDS = 120.0
MAX_WORKERS = 5

AVAILABLE_MODELS = (
    "gemini-3-flash",
    "gemini-3-pro-low",
    "gemini-3-pro-high",
    "claude-sonnet-4-5",
    "claude-opus-4-5-thinking",
)

# =============================================================================
# Constants
# =============================================================================

META_THEMES = frozenset(
    {
        "文本过渡",
        "多重视角",
        "叙事结构",
        "文本结构",
        "视角转换",
        "章节划分",
        "结构特征",
        "叙事视角",
        "文本特点",
        "行文风格",
        "写作手法",
        "叙述方式",
    }
)

INCREMENTAL_MARKERS = (
    "本片段包含",
    "本片段中",
    "本片段",
    "此片段包含",
    "此片段中",
    "此片段",
    "该片段",
    "当前片段",
    "在新文本中，",
    "在新文本中",
    "新文本中，",
    "新文本中",
    "在新片段中",
    "新片段中",
    "在本段中，",
    "在本段中",
    "在此段中",
    "本段中",
    "此段中",
    "本章节中",
    "此章节中",
    "本节中",
    "新情节中",
    "新文本",
    "新片段",
    "片段中",
)

NAME_PREFIXES = (
    "后妈",
    "继母",
    "生母",
    "亲妈",
    "外婆",
    "奶奶",
    "爷爷",
    "外公",
    "老",
    "小",
    "大",
)

NAME_SUFFIXES = (
    "先生",
    "太太",
    "小姐",
    "女士",
    "夫人",
    "阁下",
    "律师",
    "医生",
    "教授",
    "老师",
    "博士",
    "神父",
    "牧师",
    "爸爸",
    "妈妈",
    "父亲",
    "母亲",
    "舅舅",
    "姨父",
    "姨妈",
    "叔叔",
    "阿姨",
    "姑姑",
    "姑父",
    "伯父",
    "伯母",
    "哥哥",
    "弟弟",
    "姐姐",
    "妹妹",
    "表哥",
    "表弟",
    "表姐",
    "表妹",
    "堂哥",
    "堂弟",
    "堂姐",
    "堂妹",
)

SKIP_NAME_PATTERNS: tuple[
    str, ...
] = ()  # Formerly skipped generic relationships, now allowed for completeness

XML_NS_CONTAINER = {"n": "urn:oasis:names:tc:opendocument:xmlns:container"}
XML_NS_OPF = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
XML_NS_NCX = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}

# =============================================================================
# Preferences Persistence
# =============================================================================

_PREFS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".xray_prefs.json"
)


def _load_preferences() -> dict[str, Any]:
    """Load preferences from JSON file."""
    if os.path.exists(_PREFS_FILE):
        try:
            with open(_PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_preferences(prefs: dict[str, Any]) -> None:
    """Save preferences to JSON file."""
    try:
        with open(_PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # Silent fail - preferences are not critical


# =============================================================================
# Global State
# =============================================================================

_ai_client: OpenAI | None = None
_selected_api: str = "openai"
_selected_model: str = ""
_book_title: str = ""
_current_pct: int = 0
_cache_dir: str | None = None


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
# Prompt Loading
# =============================================================================


def _load_prompts() -> dict[str, str]:
    """Load prompts from shared JSON file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    prompts_path = os.path.join(script_dir, "prompts", "zh.json")
    with open(prompts_path, "r", encoding="utf-8") as f:
        return json.load(f)


_PROMPTS = _load_prompts()
SYSTEM_PROMPT = _PROMPTS["system_instruction"]
CHUNK_SUMMARY_PROMPT = _PROMPTS["chunk_summary"]
CONSOLIDATE_DESC_PROMPT = _PROMPTS["consolidate_description"]
CONSOLIDATE_SUMMARY_PROMPT = _PROMPTS["consolidate_summary"]

# =============================================================================
# Text Utilities
# =============================================================================


def sanitize_text(text: str) -> str:
    """Remove incremental processing markers from text."""
    if not isinstance(text, str):
        return text
    for marker in INCREMENTAL_MARKERS:
        text = text.replace(marker, "")
    text = re.sub(r"，，+", "，", text)
    text = re.sub(r"。。+", "。", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


def html_to_text(html: str) -> str:
    """Convert HTML to plain text."""
    html = re.sub(r"<head.*?>.*?</head>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<(p|div|h[1-6]|li|br).*?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    html = html.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\n\s*\n", "\n\n", html).strip()


def strip_html_tags(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&nbsp;", " ").replace("&amp;", "&").strip()


def sanitize_filename(name: str) -> str:
    """Sanitize string for use in filenames."""
    return re.sub(r'[<>:"/\\|?*]', "_", name)


# =============================================================================
# Name Normalization
# =============================================================================


def normalize_character_name(name: str) -> str | None:
    """Normalize character name by stripping titles, relations, parenthetical content."""
    if not name:
        return name

    original = name
    # Remove parenthetical content like "Juan (his friend)" -> "Juan"
    name = re.sub(r"[（(][^）)]*[）)]", "", name).strip()

    # If the name is a specific case that looks like a relationship but is allowed by AI instruction
    # (e.g., "Jesús's father"), we should avoid stripping the suffix if it would leave just "X's"
    potential_name = name
    for prefix in NAME_PREFIXES:
        if potential_name.startswith(prefix) and len(potential_name) > len(prefix):
            # Only strip if it doesn't leave something like "的..." (unlikely for prefix)
            test_name = potential_name[len(prefix) :].strip()
            if test_name:
                potential_name = test_name
                break

    for suffix in NAME_SUFFIXES:
        if potential_name.endswith(suffix) and len(potential_name) > len(suffix):
            # Don't strip if it leaves a possessive like "赫苏斯的"
            test_name = potential_name[: -len(suffix)].strip()
            if test_name and not test_name.endswith("的"):
                potential_name = test_name
                break

    return potential_name if potential_name else original


def normalize_for_dedup(name: str) -> str:
    """Normalize name for deduplication (convert Traditional to Simplified Chinese)."""
    if not name:
        return name
    return _T2S_CONVERTER.convert(name).strip()


def normalize_location_name(name: str) -> str:
    """Normalize location name for deduplication (hyphens + T2S)."""
    if not name:
        return name
    name = name.replace("－", "-").replace("—", "-").replace("–", "-")
    return _T2S_CONVERTER.convert(name).strip()


# =============================================================================
# AI Communication
# =============================================================================


class MockResponse:
    def __init__(self, content: str):
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


def call_ai_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 8192,
    retries: int = 3,
    delay: float = 2.0,
) -> Any:
    """Call AI with retry logic for timeouts and API errors."""

    # Infinite retry loop for both API errors and JSON parsing errors
    attempt = 0
    while True:
        attempt += 1
        try:
            if _selected_api == "cusanity":
                # Extract prompts
                system_prompt = next(
                    (m["content"] for m in messages if m["role"] == "system"), ""
                )
                user_prompt = next(
                    (m["content"] for m in messages if m["role"] == "user"), ""
                )

                import cusanity

                # Using private _gemini_completion via public wrapper
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

                # Check JSON validity to force retry on bad output
                try:
                    text_to_check = (
                        content.replace("```json", "").replace("```", "").strip()
                    )
                    json.loads(text_to_check)
                except json.JSONDecodeError:
                    raise ValueError(f"Invalid JSON from Cusanity: {content[:100]}...")

                return MockResponse(content)

            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    timeout=AI_TIMEOUT_SECONDS,
                )

                # Check content and JSON validity to force retry on bad output
                content = response.choices[0].message.content
                if not content:
                    raise ValueError("Response content is None")

                try:
                    text_to_check = (
                        content.replace("```json", "").replace("```", "").strip()
                    )
                    json.loads(text_to_check)
                except json.JSONDecodeError:
                    raise ValueError(f"Invalid JSON from OpenAI: {content[:100]}...")

                return response

        except Exception as e:
            # Attempt to extract full response body from OpenAI/API error
            full_error = str(e)
            if hasattr(e, "response") and hasattr(e.response, "text"):
                full_error = f"{e}\nResponse Body: {e.response.text}"
            elif hasattr(e, "body"):
                full_error = f"{e}\nError Body: {e.body}"

            error_str = full_error
            # Check for specific 403 Gemini subscription errors
            if (
                "SUBSCRIPTION_REQUIRED" in error_str
                or "Gemini Code Assist license" in error_str
                or "3501" in error_str
            ):
                print(
                    f"\nGemini Code Assist license required. Last error: HTTP 403: {error_str}"
                )
                # Use os._exit to immediately kill all threads and the process
                os._exit(1)

            print(
                f"    [AI Error] Attempt {attempt} failed on {model}: {e}.\n    Waiting 15s before retry..."
            )
            time.sleep(15)


def consolidate_description_with_ai(
    client: OpenAI,
    entity_type: str,
    name: str,
    combined_desc: str,
) -> str:
    """Call AI to consolidate a long description."""
    type_cn = "人物" if entity_type == "character" else "地点"
    prompt = CONSOLIDATE_DESC_PROMPT % (type_cn, name, combined_desc)

    cached_data = get_ai_cache(prompt)
    if cached_data:
        return cached_data.get("description", combined_desc)

    try:
        response = call_ai_with_retry(
            client,
            _selected_model,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
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
    client: OpenAI, book_title: str, combined_summary: str
) -> str:
    """Call AI to consolidate a long summary."""
    prompt = CONSOLIDATE_SUMMARY_PROMPT % (book_title, combined_summary)

    cached_data = get_ai_cache(prompt)
    if cached_data:
        return cached_data.get("summary", combined_summary)

    try:
        response = call_ai_with_retry(
            client,
            _selected_model,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
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


# =============================================================================
# Data Cleanup
# =============================================================================


def cleanup_data(data: dict[str, Any], current_pct: int) -> dict[str, Any]:
    """Remove unrequested fields and enforce limits after AI response."""
    if "summary" in data:
        data["summary"] = sanitize_text(data["summary"])

    # Characters
    for char in data.get("characters", []):
        char.pop("gender", None)
        if "description" in char:
            char["description"] = sanitize_text(char["description"])

    chars = data.get("characters", [])
    if chars:
        char_map: dict[str, dict] = {}
        for char in chars:
            name = char.get("name", "").strip()
            if not name:
                continue
            if name in char_map:
                existing_desc = char_map[name].get("description", "")
                new_desc = char.get("description", "")
                if len(new_desc) > len(existing_desc):
                    char_map[name]["description"] = new_desc
            else:
                char_map[name] = char
        data["characters"] = list(char_map.values())

    # Locations
    for loc in data.get("locations", []):
        loc.pop("type", None)
        if "description" in loc:
            loc["description"] = sanitize_text(loc["description"])

    locs = data.get("locations", [])
    if locs:
        loc_map: dict[str, dict] = {}
        for loc in locs:
            name = loc.get("name", "").strip()
            if not name:
                continue
            if name in loc_map:
                existing_desc = loc_map[name].get("description", "")
                new_desc = loc.get("description", "")
                if len(new_desc) > len(existing_desc):
                    loc_map[name]["description"] = new_desc
            else:
                loc_map[name] = loc
        data["locations"] = list(loc_map.values())

    # Themes
    themes = data.get("themes", [])
    if themes:
        seen: set[str] = set()
        unique_themes = []
        for theme in themes:
            if theme and theme not in seen:
                seen.add(theme)
                unique_themes.append(theme)
        data["themes"] = unique_themes[:8]

    # Timeline
    # Rebuilt from character events in to_output_json
    pass

    for event in data.get("pending_events", []):
        event.pop("importance", None)
        if "book_position_pct" in event:
            try:
                event["book_position_pct"] = int(event["book_position_pct"])
            except (ValueError, TypeError):
                event["book_position_pct"] = current_pct

    return data


# =============================================================================
# MasterData
# =============================================================================


class MasterData:
    """Python-maintained master data structure for X-Ray analysis."""

    def __init__(
        self, book_title: str = "", author: str = "", author_bio: str = ""
    ) -> None:
        self.book_title = book_title
        self.author = author
        self.author_bio = author_bio
        self.characters: dict[str, dict[str, Any]] = {}
        self.locations: dict[str, dict[str, Any]] = {}
        self.themes: set[str] = set()
        self.events: list[str] = []
        self.summary_parts: list[str] = []

    def merge_chunk(self, chunk_data: dict[str, Any]) -> None:
        """Merge a chunk summary into master data."""
        self._merge_characters(chunk_data.get("characters", []))
        self._merge_locations(chunk_data.get("locations", []))
        self._merge_themes(chunk_data.get("themes", []))
        self._merge_events(chunk_data.get("events", []))
        self._merge_summary(chunk_data.get("summary", ""))
        self._merge_metadata(chunk_data)

    def _merge_characters(self, characters: list[dict[str, Any]]) -> None:
        for char in characters:
            raw_name = char.get("name", "").strip()
            if not raw_name:
                continue

            name = normalize_character_name(raw_name)
            if not name:
                continue

            desc = char.get("description", "").strip()
            # Convert to simplified Chinese for both key and display
            simplified_name = normalize_for_dedup(name)

            if simplified_name not in self.characters:
                self.characters[simplified_name] = {
                    "display_name": simplified_name,
                    "descriptions": [],
                    "events": [],
                    "consolidated": None,
                }

            # If we already have a consolidated description, move it back to fragments
            # so the next consolidation pass includes it as context.
            if self.characters[simplified_name]["consolidated"]:
                self.characters[simplified_name]["descriptions"].insert(
                    0, self.characters[simplified_name]["consolidated"]
                )
                self.characters[simplified_name]["consolidated"] = None

            if desc:
                # Also convert description to simplified
                desc = _T2S_CONVERTER.convert(desc)
                self.characters[simplified_name]["descriptions"].append(desc)

            # Defensive initialization for events if missing (e.g. from old checkpoint)
            if "events" not in self.characters[simplified_name]:
                self.characters[simplified_name]["events"] = []

            # Merge events
            for event in char.get("events", []):
                if event.get("event") and "absolute_percent" in event:
                    # Clean up event text: remove trailing percentages like " (22%)" or "（4.5%）"
                    raw_event = _T2S_CONVERTER.convert(event["event"])
                    # Match standard () and full-width （）
                    clean_event = re.sub(
                        r"\s*[(\uff08]\d+(?:\.\d+)?%[)\uff09]$", "", raw_event
                    )

                    self.characters[simplified_name]["events"].append(
                        {
                            "event": clean_event,
                            "percent": event["absolute_percent"],
                        }
                    )

    def _merge_locations(self, locations: list[dict[str, Any]]) -> None:
        for loc in locations:
            name = loc.get("name", "").strip()
            if not name:
                continue
            desc = loc.get("description", "").strip()
            # Convert to simplified Chinese for both key and display
            simplified_name = normalize_location_name(name)

            if simplified_name not in self.locations:
                self.locations[simplified_name] = {
                    "display_name": simplified_name,
                    "descriptions": [],
                    "consolidated": None,
                }

            # If we already have a consolidated description, move it back to fragments
            if self.locations[simplified_name]["consolidated"]:
                self.locations[simplified_name]["descriptions"].insert(
                    0, self.locations[simplified_name]["consolidated"]
                )
                self.locations[simplified_name]["consolidated"] = None

            if desc:
                # Also convert description to simplified
                desc = _T2S_CONVERTER.convert(desc)
                self.locations[simplified_name]["descriptions"].append(desc)

    def _merge_themes(self, themes: list[str]) -> None:
        for theme in themes:
            if theme and theme not in META_THEMES:
                self.themes.add(theme)

    def _merge_events(self, events: list[Any]) -> None:
        # Legacy: Global event parsing is disabled to save tokens
        # We now build timeline from character events
        pass

    def _merge_summary(self, summary: str) -> None:
        summary = summary.strip()
        if summary:
            self.summary_parts.append(summary)

    def _merge_metadata(self, chunk_data: dict[str, Any]) -> None:
        if chunk_data.get("book_title"):
            self.book_title = chunk_data["book_title"]
        if chunk_data.get("author"):
            self.author = chunk_data["author"]
        if chunk_data.get("author_bio"):
            self.author_bio = chunk_data["author_bio"]

    def get_items_needing_consolidation(
        self,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Return lists of items with multiple descriptions that need AI consolidation."""
        chars_needing_help = []
        for key, val in self.characters.items():
            if val["consolidated"] is None and len(val["descriptions"]) > 1:
                combined = " ".join(val["descriptions"])
                chars_needing_help.append((key, combined))

        locs_needing_help = []
        for key, val in self.locations.items():
            if val["consolidated"] is None and len(val["descriptions"]) > 1:
                combined = " ".join(val["descriptions"])
                locs_needing_help.append((key, combined))

        return chars_needing_help, locs_needing_help

    def needs_summary_consolidation(self) -> bool:
        """Check if summary needs consolidation."""
        if len(self.summary_parts) > 1:
            return True
        if len(self.summary_parts) == 1:
            return len(self.summary_parts[0]) > 1500
        return False

    def consolidate_summary(self, client: OpenAI) -> None:
        """Consolidate accumulated summary parts into one."""
        if not self.summary_parts:
            return

        combined = " ".join(self.summary_parts)
        print(f"  [Summary] Consolidating {len(combined)} chars...")
        consolidated = consolidate_summary_with_ai(client, self.book_title, combined)
        self.summary_parts = [consolidated]
        print(f"  [Summary] Consolidated to {len(consolidated)} chars")

    def apply_consolidation(
        self, entity_type: str, name: str, consolidated_desc: str
    ) -> None:
        """Apply AI-consolidated description."""
        target = self.characters if entity_type == "character" else self.locations
        if name in target:
            target[name]["consolidated"] = consolidated_desc
            target[name]["descriptions"] = []

    def to_output_json(self, progress_pct: int) -> dict[str, Any]:
        """Convert to final output JSON format with importance-based limiting."""

        def score_importance(data: dict[str, Any]) -> int:
            desc = data.get("consolidated") or " ".join(data.get("descriptions", []))
            fragment_count = len(data.get("descriptions", []))
            return len(desc) + (fragment_count * 50)

        # Characters
        char_items = []
        for key, data in self.characters.items():
            desc = (
                data["consolidated"]
                if data["consolidated"]
                else " ".join(data["descriptions"])
            )
            display_name = data.get("display_name", key)
            char_items.append(
                {
                    "name": display_name,
                    "description": desc,
                    "events": sorted(
                        data.get("events", []), key=lambda x: x["percent"]
                    ),
                    "_score": score_importance(data),
                }
            )

        char_items.sort(key=lambda x: x["_score"], reverse=True)
        characters = [
            {
                "name": c["name"],
                "description": c["description"],
                "events": c["events"],
            }
            for c in char_items
        ]

        # Locations
        loc_items = []
        for key, data in self.locations.items():
            desc = (
                data["consolidated"]
                if data["consolidated"]
                else " ".join(data["descriptions"])
            )
            display_name = data.get("display_name", key)
            loc_items.append(
                {
                    "name": display_name,
                    "description": desc,
                    "_score": score_importance(data),
                }
            )

        loc_items.sort(key=lambda x: x["_score"], reverse=True)
        locations = [
            {"name": loc["name"], "description": loc["description"]}
            for loc in loc_items
        ]

        # Summary
        summary = " ".join(self.summary_parts)

        # Timeline
        timeline = []
        all_events = []
        for key, data in self.characters.items():
            char_name = data.get("display_name", key)
            for event in data.get("events", []):
                # Ensure we have a valid percentage
                pct = event.get("percent", 0)
                text = event.get("event", "").strip()
                if text:
                    # Prepend character name
                    full_text = f"{char_name}{text}"
                    all_events.append({"event": full_text, "percent": pct})

        # Sort by percentage
        all_events.sort(key=lambda x: x["percent"])

        # Add sequence numbers
        for i, event in enumerate(all_events):
            timeline.append(
                {
                    "sequence": i + 1,
                    "event": event["event"],
                    "percent": event["percent"],
                }
            )

        # Themes
        filtered_themes = [t for t in self.themes if t not in META_THEMES]

        return {
            "book_title": self.book_title,
            "author": self.author,
            "author_bio": self.author_bio,
            "summary": summary,
            "characters": characters,
            "locations": locations,
            "themes": filtered_themes[:8],
            "timeline": timeline,
            "analysis_progress": progress_pct,
        }

    def get_stats(self) -> dict[str, int]:
        """Return current stats for logging."""
        return {
            "characters": len(self.characters),
            "locations": len(self.locations),
            "themes": len(self.themes),
            "events": len(self.events),
            "summary_parts": len(self.summary_parts),
        }


# =============================================================================
# Calibre Library
# =============================================================================


def parse_metadata_opf(opf_path: str) -> tuple[str, str, str]:
    """Parse Calibre's metadata.opf file to extract title, author, and added date."""
    tree = ET.parse(opf_path)
    root = tree.getroot()

    title = "Unknown Title"
    author = "Unknown Author"
    added_date = "1970-01-01T00:00:00+00:00"

    metadata = root.find("opf:metadata", XML_NS_OPF)
    if metadata is None:
        metadata = root.find(".//{http://www.idpf.org/2007/opf}metadata")

    if metadata is not None:
        title_elem = metadata.find("dc:title", XML_NS_OPF)
        if title_elem is None:
            title_elem = metadata.find(".//{http://purl.org/dc/elements/1.1/}title")
        if title_elem is not None and title_elem.text:
            title = title_elem.text.strip()

        creator_elem = metadata.find("dc:creator", XML_NS_OPF)
        if creator_elem is None:
            creator_elem = metadata.find(".//{http://purl.org/dc/elements/1.1/}creator")
        if creator_elem is not None and creator_elem.text:
            author = creator_elem.text.strip()

        for meta in metadata.findall(".//{http://www.idpf.org/2007/opf}meta"):
            if meta.get("name") == "calibre:timestamp":
                added_date = meta.get("content", added_date)
                break

    return title, author, added_date


def scan_calibre_library(library_path: str) -> list[dict[str, str]]:
    """Scan Calibre library and return list of books with metadata."""
    books = []

    if not os.path.isdir(library_path):
        print(f"Error: Calibre library not found at {library_path}")
        return books

    for author_dir in os.listdir(library_path):
        author_path = os.path.join(library_path, author_dir)
        if not os.path.isdir(author_path) or author_dir.startswith("."):
            continue

        for book_dir in os.listdir(author_path):
            book_path = os.path.join(author_path, book_dir)
            if not os.path.isdir(book_path):
                continue

            metadata_path = os.path.join(book_path, "metadata.opf")
            if not os.path.exists(metadata_path):
                continue

            epub_file = None
            for f in os.listdir(book_path):
                if f.lower().endswith(".epub"):
                    epub_file = os.path.join(book_path, f)
                    break

            if not epub_file:
                continue

            try:
                title, author, added_date = parse_metadata_opf(metadata_path)
                books.append(
                    {
                        "title": title,
                        "author": author,
                        "added_date": added_date,
                        "epub_path": epub_file,
                        "folder_path": book_path,
                    }
                )
            except Exception as e:
                print(f"Warning: Could not parse {metadata_path}: {e}")

    books.sort(key=lambda b: b["added_date"], reverse=True)
    return books


def display_library_browser(
    books: list[dict[str, str]], page_size: int = 20
) -> list[str] | None:
    """Display interactive paginated book list and let user select."""
    if not books:
        print("No books found in Calibre library.")
        return None

    # Use a copy of the books list that can be filtered
    all_books = books
    filtered_books = all_books
    search_query = ""

    # Load last selection preferences
    prefs = _load_preferences()
    last_book_path = prefs.get("last_book_path", "")
    last_book_num = prefs.get("last_book_num", 0)

    # Find the page containing the last selected book (if no filter is active)
    current_page = 0
    if last_book_path:
        for i, book in enumerate(all_books):
            if book["epub_path"] == last_book_path:
                current_page = i // page_size
                last_book_num = i + 1
                break

    while True:
        total = len(filtered_books)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        current_page = max(0, min(current_page, total_pages - 1))

        start_idx = current_page * page_size
        end_idx = min(start_idx + page_size, total)

        print(f"\n{'=' * 60}")
        header = (
            f"Calibre Library - Page {current_page + 1}/{total_pages} ({total} books)"
        )
        if search_query:
            header += f" | Filter: '{search_query}'"
        print(header)
        print(f"{'=' * 60}\n")

        if total == 0:
            print("  No books match your search.")
        else:
            for i in range(start_idx, end_idx):
                book = filtered_books[i]
                display_title = (
                    book["title"][:42] + "..."
                    if len(book["title"]) > 45
                    else book["title"]
                )
                # Mark last selected book (only if it matches the current filtered list and path)
                marker = ""
                if last_book_path == book["epub_path"]:
                    marker = " *"

                print(f"  [{i + 1:3d}] {display_title}{marker}")
                print(f"        by {book['author']}")

        print(f"\n{'─' * 60}")
        hint = ""
        # Only suggest last book if it's in the CURRENT filtered view
        current_last_book_idx = -1
        if last_book_path:
            for i, b in enumerate(filtered_books):
                if b["epub_path"] == last_book_path:
                    current_last_book_idx = i
                    break

        if current_last_book_idx != -1:
            hint = f" [Enter={current_last_book_idx + 1}]"

        print(
            f"Commands: [n]ext, [p]rev, [s]earch, [c]lear search, [q]uit, or # {hint}"
        )

        try:
            raw_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return None

        user_input = raw_input.lower()

        if user_input == "q":
            print("Cancelled.")
            return None
        elif user_input == "n":
            if current_page < total_pages - 1:
                current_page += 1
            else:
                print("Already at last page.")
        elif user_input == "p":
            if current_page > 0:
                current_page -= 1
            else:
                print("Already at first page.")
        elif user_input == "s":
            query = input("Enter search term (title or author): ").strip()
            if query:
                search_query = query
                filtered_books = [
                    b
                    for b in all_books
                    if query.lower() in b["title"].lower()
                    or query.lower() in b["author"].lower()
                ]
                current_page = 0
            else:
                print("Search cancelled.")
        elif user_input == "c":
            search_query = ""
            filtered_books = all_books
            print("Filter cleared.")
        elif not user_input and current_last_book_idx != -1:
            # Press Enter to select last book
            selected = filtered_books[current_last_book_idx]
            print(f"\nSelected: {selected['title']} by {selected['author']}")
            return [selected["epub_path"]]
        else:
            try:
                # Support multiple selection (e.g. "1, 2, 5-7")
                is_selection = True
                cleaned = raw_input.replace("\uff0c", ",").replace(" ", ",")
                parts = [p.strip() for p in cleaned.split(",") if p.strip()]

                indices = []
                for p in parts:
                    if "-" in p:
                        try:
                            s, e = map(int, p.split("-"))
                            if s > e:
                                s, e = e, s
                            indices.extend(range(s, e + 1))
                        except ValueError:
                            is_selection = False
                            break
                    else:
                        try:
                            indices.append(int(p))
                        except ValueError:
                            is_selection = False
                            break

                if not is_selection or not indices:
                    raise ValueError("Not a selection")  # Fall to implicit search

                valid_indices = sorted(
                    list(set([i for i in indices if 1 <= i <= total]))
                )

                if not valid_indices:
                    print(f"  [!] No valid book numbers in range (1-{total}).")
                    continue

                print()
                selected_paths = []
                for idx in valid_indices:
                    sel = filtered_books[idx - 1]
                    print(f"  [+] Selected: {filtered_books[idx - 1]['title']}")
                    selected_paths.append(filtered_books[idx - 1]["epub_path"])

                if selected_paths:
                    # Save preference of the last valid selection to maintain continuity
                    last_idx = valid_indices[-1]
                    last_sel = filtered_books[last_idx - 1]
                    prefs["last_book_path"] = last_sel["epub_path"]
                    for global_idx, b in enumerate(all_books):
                        if b["epub_path"] == last_sel["epub_path"]:
                            prefs["last_book_num"] = global_idx + 1
                            break
                    _save_preferences(prefs)

                    # Deduplicate paths while preserving order
                    seen = set()
                    unique_paths = []
                    for p in selected_paths:
                        if p not in seen:
                            seen.add(p)
                            unique_paths.append(p)
                    return unique_paths

            except ValueError:
                # Implicit search
                search_query = raw_input
                print(f"Searching for: '{search_query}'")
                filtered_books = [
                    b
                    for b in all_books
                    if search_query.lower() in b["title"].lower()
                    or search_query.lower() in b["author"].lower()
                ]
                current_page = 0


# =============================================================================
# EPUB Reader
# =============================================================================


def get_sdr_name(epub_path: str) -> str:
    """Extract author/title from EPUB metadata and generate KOReader .sdr folder name."""
    title = "Unknown"
    author = "Unknown"

    # 1. Try Calibre's metadata.opf first (most reliable for original characters)
    metadata_opf = os.path.join(os.path.dirname(epub_path), "metadata.opf")
    if os.path.exists(metadata_opf):
        try:
            m_title, m_author, _ = parse_metadata_opf(metadata_opf)
            if m_title != "Unknown Title":
                title = m_title
            if m_author != "Unknown Author":
                author = m_author
        except Exception:
            pass

    # 2. Fallback to internal EPUB metadata if still unknown
    if title == "Unknown" or author == "Unknown":
        try:
            with zipfile.ZipFile(epub_path) as z:
                container = z.read("META-INF/container.xml")
                root = ET.fromstring(container)
                rootfile = root.find(".//n:rootfile", XML_NS_CONTAINER)
                if rootfile is not None:
                    opf_path = rootfile.attrib["full-path"]
                    opf_data = z.read(opf_path)
                    opf_root = ET.fromstring(opf_data)
                    metadata = opf_root.find(
                        ".//{http://www.idpf.org/2007/opf}metadata"
                    )

                    if metadata is not None:
                        t = metadata.find(".//{http://purl.org/dc/elements/1.1/}title")
                        c = metadata.find(
                            ".//{http://purl.org/dc/elements/1.1/}creator"
                        )
                        if t is not None and t.text and title == "Unknown":
                            title = t.text.strip()
                        if c is not None and c.text and author == "Unknown":
                            author = c.text.strip()
        except Exception:
            pass

    safe_title = sanitize_filename(title)
    safe_author = sanitize_filename(author)
    return f"{safe_author} - {safe_title}.epub.sdr"


class EpubReader:
    """EPUB file reader for extracting text content."""

    def __init__(self, epub_path: str) -> None:
        self.epub_path = epub_path

    def get_chapters(
        self,
    ) -> tuple[list[tuple[str, str]] | None, str | None, str | None]:
        """Extract chapters as list of (title, text) tuples in reading order."""
        try:
            with zipfile.ZipFile(self.epub_path) as z:
                opf_path = self._get_opf_path(z)
                opf_data = z.read(opf_path)
                opf_root = ET.fromstring(opf_data)
                opf_dir = os.path.dirname(opf_path)

                book_title, author = self._extract_book_metadata(opf_root)
                print(f"Book: {book_title} by {author}")

                manifest = self._parse_manifest(opf_root)
                spine = self._parse_spine(opf_root)
                toc_map = self._parse_toc(z, opf_dir, manifest)
                print(f"Found {len(toc_map)} TOC entries")

                chapters = self._extract_chapters(
                    z, opf_dir, manifest, spine, toc_map, book_title
                )
                return chapters, book_title, author

        except Exception as e:
            print(f"Fatal error reading EPUB: {e}")
            return None, None, None

    def _get_opf_path(self, z: zipfile.ZipFile) -> str:
        txt = z.read("META-INF/container.xml")
        root = ET.fromstring(txt)
        return root.find(".//n:rootfile", XML_NS_CONTAINER).attrib["full-path"]

    def _extract_book_metadata(self, opf_root: ET.Element) -> tuple[str, str]:
        book_title = "Unknown Title"
        author = "Unknown Author"

        metadata = opf_root.find(".//{http://www.idpf.org/2007/opf}metadata")
        if metadata is not None:
            t = metadata.find(".//{http://purl.org/dc/elements/1.1/}title")
            c = metadata.find(".//{http://purl.org/dc/elements/1.1/}creator")
            if t is not None and t.text:
                book_title = t.text
            if c is not None and c.text:
                author = c.text

        return book_title, author

    def _parse_manifest(self, opf_root: ET.Element) -> dict[str, str]:
        manifest = {}
        for item in opf_root.findall(
            ".//{http://www.idpf.org/2007/opf}manifest/{http://www.idpf.org/2007/opf}item"
        ):
            manifest[item.attrib["id"]] = item.attrib["href"]
        return manifest

    def _parse_spine(self, opf_root: ET.Element) -> list[str]:
        spine = []
        for itemref in opf_root.findall(
            ".//{http://www.idpf.org/2007/opf}spine/{http://www.idpf.org/2007/opf}itemref"
        ):
            spine.append(itemref.attrib["idref"])
        return spine

    def _parse_toc(
        self, z: zipfile.ZipFile, opf_dir: str, manifest: dict[str, str]
    ) -> dict[str, str]:
        toc_map: dict[str, str] = {}

        ncx_id = None
        for item_id, href in manifest.items():
            if href.endswith(".ncx"):
                ncx_id = item_id
                break

        if ncx_id:
            ncx_path = os.path.join(opf_dir, manifest[ncx_id]).replace("\\", "/")
            try:
                ncx_data = z.read(ncx_path)
                ncx_root = ET.fromstring(ncx_data)

                for nav_point in ncx_root.findall(".//ncx:navPoint", XML_NS_NCX):
                    text_elem = nav_point.find("ncx:navLabel/ncx:text", XML_NS_NCX)
                    content_elem = nav_point.find("ncx:content", XML_NS_NCX)
                    if text_elem is not None and content_elem is not None:
                        nav_title = text_elem.text.strip() if text_elem.text else None
                        nav_src = content_elem.attrib.get("src", "")
                        nav_file = nav_src.split("#")[0]
                        if nav_title and nav_file:
                            full_path = os.path.join(opf_dir, nav_file).replace(
                                "\\", "/"
                            )
                            toc_map[full_path] = nav_title
            except Exception as e:
                print(f"Warning: Could not parse NCX: {e}")

        return toc_map

    def _extract_chapters(
        self,
        z: zipfile.ZipFile,
        opf_dir: str,
        manifest: dict[str, str],
        spine: list[str],
        toc_map: dict[str, str],
        book_title: str,
    ) -> list[tuple[str, str]]:
        chapters = []
        chapter_index = 0

        for item_id in spine:
            if item_id not in manifest:
                continue

            file_path = os.path.join(opf_dir, manifest[item_id]).replace("\\", "/")
            try:
                content = z.read(file_path).decode("utf-8")
                toc_title = toc_map.get(file_path)
                chapter_title, text = self._extract_chapter(
                    content, chapter_index, book_title, toc_title
                )
                if text.strip():
                    chapters.append((chapter_title, text))
                    chapter_index += 1
            except KeyError:
                print(f"Warning: File {file_path} not found in archive.")
            except Exception as e:
                print(f"Error extracting {file_path}: {e}")

        return chapters

    def _extract_chapter(
        self,
        html: str,
        fallback_index: int,
        book_title: str | None,
        toc_title: str | None,
    ) -> tuple[str, str]:
        """Extract chapter title and text from HTML content."""
        if toc_title and toc_title != book_title:
            return toc_title, html_to_text(html)

        chapter_title = None

        for h_level in ["h1", "h2", "h3"]:
            h_match = re.search(
                rf"<{h_level}[^>]*>(.*?)</{h_level}>", html, re.DOTALL | re.IGNORECASE
            )
            if h_match:
                raw_title = strip_html_tags(h_match.group(1))
                if raw_title and len(raw_title) < 100 and raw_title != book_title:
                    chapter_title = raw_title
                    break

        if not chapter_title:
            title_match = re.search(
                r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE
            )
            if title_match:
                raw_title = strip_html_tags(title_match.group(1))
                if raw_title and len(raw_title) < 100 and raw_title != book_title:
                    chapter_title = raw_title

        if not chapter_title:
            chapter_title = f"第{fallback_index + 1}节"

        return chapter_title, html_to_text(html)

    def get_text(self) -> tuple[str | None, str | None, str | None]:
        """Legacy method: Extracts all text as a single string."""
        chapters, book_title, author = self.get_chapters()
        if chapters:
            full_text = "\n".join([text for _, text in chapters])
            return full_text, book_title, author
        return None, None, None


# =============================================================================
# Chunk Processing
# =============================================================================


def build_chunks(chapters: list[tuple[str, str]]) -> list[tuple[list[str], str, int]]:
    """Build text chunks from chapters respecting size limits."""
    chunks: list[tuple[list[str], str, int]] = []
    current_titles: list[str] = []
    current_text = ""
    chars_processed = 0

    for chapter_title, chapter_text in chapters:
        chapter_len = len(chapter_text)

        if chapter_len > MAX_CHUNK_SIZE:
            if current_text.strip():
                chunks.append((current_titles, current_text.strip(), chars_processed))
                current_titles = []
                current_text = ""

            segment_idx = 0
            start = 0
            while start < chapter_len:
                end = min(start + MAX_CHUNK_SIZE, chapter_len)

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

                segment_with_header = f"{header}\n{segment_text}\n\n"
                chunks.append(
                    ([title], segment_with_header.strip(), chars_processed + end)
                )

                segment_idx += 1
                start = end

            chars_processed += chapter_len
        else:
            chapter_with_header = f"【{chapter_title}】\n{chapter_text}\n\n"

            if (
                current_text
                and len(current_text) + len(chapter_with_header) > MAX_CHUNK_SIZE
            ):
                chunks.append((current_titles, current_text.strip(), chars_processed))
                current_titles = []
                current_text = ""

            current_titles.append(chapter_title)
            current_text += chapter_with_header
            chars_processed += chapter_len

    if current_text.strip():
        chunks.append((current_titles, current_text.strip(), chars_processed))

    return chunks


def find_resume_checkpoint(output_dir: str) -> tuple[int, dict[str, Any] | None]:
    """Find and load the latest checkpoint from output directory."""
    resume_pct = 0
    resume_data = None

    for filename in os.listdir(output_dir):
        if filename.endswith(".json") and "%" in filename:
            try:
                pct = int(filename.replace("%.json", ""))
                if pct > resume_pct:
                    resume_pct = pct
            except ValueError:
                continue

    if resume_pct > 0:
        checkpoint_file = os.path.join(output_dir, f"{resume_pct}%.json")
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                resume_data = json.load(f)
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
            master.characters[dedup_key] = {
                "display_name": name,
                "descriptions": [char.get("description", "")],
                "consolidated": char.get("description", ""),
                "events": char.get("events", []),
            }

    for loc in resume_data.get("locations", []):
        name = loc.get("name", "").strip()
        if name:
            dedup_key = normalize_location_name(name)
            master.locations[dedup_key] = {
                "display_name": name,
                "descriptions": [loc.get("description", "")],
                "consolidated": loc.get("description", ""),
            }

    for theme in resume_data.get("themes", []):
        if theme and theme not in META_THEMES:
            master.themes.add(theme)

    for event in resume_data.get("timeline", []):
        master.events.append(event)

    if resume_data.get("summary"):
        master.summary_parts.append(resume_data["summary"])

    master.book_title = resume_data.get("book_title", title)
    master.author = resume_data.get("author", author)
    master.author_bio = resume_data.get("author_bio", "")

    print(
        f"Restored {len(master.characters)} characters, {len(master.locations)} locations from checkpoint"
    )


def _process_chunk_worker(
    client: OpenAI,
    chunk_text: str,
    title: str,
    author: str,
    start_pct: int,
    end_pct: int,
    model: str,
    chunk_index: int,
    total_chunks: int,
    chapter_display: str,
) -> dict[str, Any] | None:
    """Worker function to process a single chunk independent of master state."""
    prompt = CHUNK_SUMMARY_PROMPT % (title, author, end_pct, chunk_text)

    # Check cache first
    cached_data = get_ai_cache(prompt)
    if cached_data:
        print(f"  [Chunk {chunk_index}] ✓ Using cached AI response")
        # Re-calculate absolute percentages for consistent data mapping
        for char in cached_data.get("characters", []):
            for event in char.get("events", []):
                rel_pct = event.get("relative_percent", 0)
                try:
                    rel_pct = float(rel_pct)
                except (ValueError, TypeError):
                    rel_pct = 0
                abs_pct = start_pct + (rel_pct / 100.0) * (end_pct - start_pct)
                event["absolute_percent"] = round(abs_pct, 1)
        return cached_data

    print(
        f"  [Chunk {chunk_index}/{total_chunks}] AI Request sent... ({len(prompt)} chars)"
    )

    try:
        response = call_ai_with_retry(
            client,
            model,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            max_tokens=16384,
            retries=MAX_RETRIES + 1,
        )

        # Check for truncated response
        if response.choices[0].finish_reason == "length":
            print(f"  [Chunk {chunk_index}] ⚠ Response truncated on {model}. Stopping.")
            os._exit(1)

        content = response.choices[0].message.content
        if content is None:
            print(
                f"  [Chunk {chunk_index}] ⚠ Safety filter triggered on {model}. Stopping."
            )
            os._exit(1)

        content = content.replace("```json", "").replace("```", "").strip()

        try:
            chunk_data = json.loads(content)
            save_ai_cache(prompt, chunk_data)

            # Post-process character events to add absolute percentage
            for char in chunk_data.get("characters", []):
                for event in char.get("events", []):
                    rel_pct = event.get("relative_percent", 0)
                    try:
                        rel_pct = float(rel_pct)
                    except (ValueError, TypeError):
                        rel_pct = 0

                    # Interpolate absolute percentage
                    abs_pct = start_pct + (rel_pct / 100.0) * (end_pct - start_pct)
                    event["absolute_percent"] = round(abs_pct, 1)

            print(f"  [Chunk {chunk_index}] ✓ Received AI response")
            return chunk_data

        except json.JSONDecodeError as e:
            print(f"  [Chunk {chunk_index}] JSON Error from {model}: {e}")
            print(f"  Raw content was:\n{content}")
            os._exit(1)

    except Exception as e:
        # Note: call_ai_with_retry already handles final exit on error,
        # but we catch just in case of unexpected exceptions.
        print(f"  [Chunk {chunk_index}] unexpected error on {model}: {e}")
        os._exit(1)

    return None


def consolidate_pending_items(client: OpenAI, master: MasterData) -> None:
    """Check and consolidate items needing AI consolidation using ThreadPoolExecutor."""
    chars_to_consolidate, locs_to_consolidate = master.get_items_needing_consolidation()

    if not chars_to_consolidate and not locs_to_consolidate:
        if master.needs_summary_consolidation():
            master.consolidate_summary(client)
        return

    print(
        f"  [Consolidation] Parallel processing: {len(chars_to_consolidate)} chars, {len(locs_to_consolidate)} locs"
    )

    def consolidate_worker(
        entity_type: str, name: str, desc: str
    ) -> tuple[str, str, str]:
        consolidated = consolidate_description_with_ai(client, entity_type, name, desc)
        return entity_type, name, consolidated

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for name, combined_desc in chars_to_consolidate:
            futures.append(
                executor.submit(consolidate_worker, "character", name, combined_desc)
            )
        for name, combined_desc in locs_to_consolidate:
            futures.append(
                executor.submit(consolidate_worker, "location", name, combined_desc)
            )

        for future in concurrent.futures.as_completed(futures):
            try:
                etype, name, consolidated = future.result()
                master.apply_consolidation(etype, name, consolidated)
                print(f"    ✓ [{etype[:4].capitalize()}] {name} updated")
            except Exception as e:
                print(f"    [Consolidation Error]: {e}")

    if master.needs_summary_consolidation():
        master.consolidate_summary(client)


# =============================================================================
# Main Entry Point
# =============================================================================


def display_api_selector() -> str:
    """Display API selection menu."""
    global _selected_api

    prefs = _load_preferences()
    last_api = prefs.get("last_api", "openai")

    print(f"\n{'=' * 60}")
    print("Select API Provider")
    print(f"{'=' * 60}\n")

    options = [("openai", "OpenAI (Standard)"), ("cusanity", "Cusanity (Gemini Proxy)")]

    default_idx = -1
    for i, (key, label) in enumerate(options, 1):
        marker = (
            " (last used)"
            if key == last_api
            else (" (default)" if key == "openai" and last_api != "cusanity" else "")
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
    _selected_api = selected
    return selected


def display_model_selector() -> str | None:
    """Display model selection menu and return selected model name."""
    # Load last selected model preference
    prefs = _load_preferences()
    last_model = prefs.get("last_model", "")

    # Combine available models with Cusanity fallbacks if enabled
    current_models = list(AVAILABLE_MODELS)
    if _selected_api == "cusanity":
        try:
            import cusanity

            if hasattr(cusanity, "DEFAULT_GEMINI_FALLBACK_MODELS"):
                # Append unique models from fallback list
                for m in cusanity.DEFAULT_GEMINI_FALLBACK_MODELS:
                    if m not in current_models:
                        current_models.append(m)
        except ImportError:
            pass

    # Determine which model to show as default (prefer last used over env default)
    effective_default = last_model if last_model in current_models else MODEL_NAME
    # If effective_default is not in the list (e.g. env var set to something else), add it momentarily
    # or just accept it might not be in the numbered list but is "default".
    # For simplicity, if it's not in the list, we might want to default to the first available or keep it.

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

    # Default to last used/effective default on Enter
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
            # Save preference
            prefs["last_model"] = selected
            _save_preferences(prefs)
            return selected
        else:
            print(f"Invalid model number. Using: {effective_default}")
            return effective_default
    except ValueError:
        print(f"Invalid input. Using: {effective_default}")
        return effective_default


def process_book(target_path: str, client: OpenAI | None, selected_model: str) -> None:
    """Process a single book: analyze and generate X-Ray."""
    global _ai_client, _book_title, _cache_dir, _selected_model

    _ai_client = client
    _selected_model = selected_model

    print(f"\nReading {target_path}...")

    reader = EpubReader(target_path)
    chapters, title, author = reader.get_chapters()

    if not chapters:
        print("Failed to extract chapters.")
        return

    total_len = sum(len(text) for _, text in chapters)
    print(f"Total text length: {total_len} characters")
    print(f"Book Title: {title}")
    print(f"Found {len(chapters)} chapters")

    output_dir = _setup_output_directory(target_path)
    if output_dir is None:
        return

    _cache_dir = os.path.join(output_dir, ".ai_cache")
    os.makedirs(_cache_dir, exist_ok=True)

    resume_pct, resume_data = find_resume_checkpoint(output_dir)

    _book_title = title

    chunks = build_chunks(chapters)
    total_chunks = len(chunks)
    print(
        f"Will process in {total_chunks} chapter-based chunks (max {MAX_CHUNK_SIZE} chars each)"
    )

    master = MasterData(book_title=title, author=author)
    start_step = _calculate_start_step(
        resume_pct, resume_data, chunks, total_len, master, title, author, total_chunks
    )

    if start_step is None:
        return

    print("\n=== Starting Analysis with Python-Maintained Data Architecture ===")
    print(f"    (Parallel Execution with {MAX_WORKERS} workers)")

    # Prepare chunk parameters list
    chunk_tasks = []
    for i in range(start_step, total_chunks + 1):
        chapter_titles, chunk_text, end_pos = chunks[i - 1]

        # Calculate start and end percentages for this chunk
        prev_end_pos = chunks[i - 2][2] if i > 1 else 0
        start_pct = math.floor(prev_end_pos * 100 / total_len)
        end_pct = math.ceil(end_pos * 100 / total_len)

        chapter_display = (
            " → ".join(chapter_titles) if len(chapter_titles) > 1 else chapter_titles[0]
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
            }
        )

    # Execute chunks in parallel but merge in order
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
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
                    # Sequential Merge
                    print(
                        f"\n=== Merging Chunk {idx}/{total_chunks}: 《{task['chapter_display']}》 ==="
                    )
                    master.merge_chunk(chunk_data)

                    stats = master.get_stats()
                    print(
                        f"  [Merged] Chars: {stats['characters']}, Locs: {stats['locations']}, Events: {stats['events']}"
                    )

                    # Intermediate consolidation to keep checkpoints clean
                    consolidate_pending_items(client, master)

                    # Save Checkpoint
                    output_data = master.to_output_json(task["end_pct"])
                    filename = os.path.join(output_dir, f"{task['end_pct']}%.json")
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(output_data, f, ensure_ascii=False, indent=2)
                    print(f"  Saved {filename}")
                else:
                    print(f"  [Chunk {idx}] Skipped due to AI failure/filtering.")
            except Exception as e:
                print(f"  [Chunk {idx}] Fatal Error in worker: {e}")

    _finalize_output(master, output_dir)


def main() -> None:
    global _selected_api, _selected_model

    target_paths = _get_target_paths()
    if not target_paths:
        return

    _selected_api = display_api_selector()
    if _selected_api == "cusanity":
        # Attempt to import cusanity by adding repo to path (assuming adjacent folder structure)
        # ../KOReader/xray.koplugin/xray_generator.py -> ../../cusanity_py
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        cusanity_path = os.path.join(repo_root, "cusanity_py")
        if cusanity_path not in sys.path:
            sys.path.append(cusanity_path)

        try:
            import cusanity

            print(f"Using Cusanity Provider (v{cusanity.__version__})")
        except ImportError:
            print(f"Error: Could not import 'cusanity' from {cusanity_path}.")
            print("Ensure the cusanity_py repo is checked out at that location.")
            return

    selected_model = display_model_selector()
    if selected_model is None:
        return

    client = None
    if _selected_api != "cusanity":
        client = OpenAI(
            base_url=API_BASE_URL, api_key=API_KEY, timeout=AI_TIMEOUT_SECONDS
        )

    print(f"\n=== Batch Processing {len(target_paths)} Books ===")
    print(f"API: {_selected_api} | Model: {selected_model}")

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


def _get_target_paths() -> list[str] | None:
    """Get target EPUB path(s) from CLI args or Calibre browser."""
    if len(sys.argv) < 2 or sys.argv[1] in ("--browse", "-b", "--list", "-l"):
        print("X-Ray Generator - Calibre Library Browser")
        print(f"Scanning library: {CALIBRE_LIBRARY}")
        print("Please wait...\n")

        books = scan_calibre_library(CALIBRE_LIBRARY)
        if not books:
            print("\nNo EPUB books found in Calibre library.")
            print(f"Check that CALIBRE_LIBRARY path is correct: {CALIBRE_LIBRARY}")
            print("\nAlternatively, specify an EPUB file directly:")
            print("  python xray_generator.py <epub_file_path>")
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
        print("  python xray_generator.py <epub_file_path>")
        print("  python xray_generator.py --browse  (browse Calibre library)")
        print("\nConfiguration via environment variables:")
        print("  XRAY_API_BASE    - API endpoint (default: http://localhost:8080/v1)")
        print("  XRAY_API_KEY     - Your API key")
        print("  XRAY_MODEL       - Model name (default: gemini-2.5-flash-lite)")
        print("  CALIBRE_LIBRARY  - Path to Calibre library")
        return None

    return [target_path]


def _setup_output_directory(target_path: str) -> str | None:
    """Create output directory structure."""
    sdr_name = get_sdr_name(target_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    xray_base_dir = os.path.join(script_dir, "xray")
    output_dir = os.path.join(xray_base_dir, sdr_name, "xray_analysis")

    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            print(f"Created output directory: {output_dir}")
        except OSError as e:
            print(f"Error creating directory {output_dir}: {e}")
            return None
    else:
        print(f"Using output directory: {output_dir}")

    return output_dir


def _calculate_start_step(
    resume_pct: int,
    resume_data: dict[str, Any] | None,
    chunks: list[tuple[list[str], str, int]],
    total_len: int,
    master: MasterData,
    title: str,
    author: str,
    total_chunks: int,
) -> int | None:
    """Calculate starting chunk based on resume state."""
    start_step = 1

    if resume_pct > 0 and resume_data:
        for idx, (_, _, end_pos) in enumerate(chunks):
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


def _finalize_output(master: MasterData, output_dir: str) -> None:
    """Generate and save final output files."""
    final_data = master.to_output_json(100)
    print(f"\n=== Final Analysis: {len(final_data['timeline'])} timeline events ===")

    filename = os.path.join(output_dir, "100%.json")
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
    print(f"\nSaved final analysis to {filename}")

    json_files = sorted(
        [f for f in os.listdir(output_dir) if f.endswith("%.json") and f != "0%.json"],
        key=lambda x: int(x.replace("%.json", "")),
    )
    if json_files:
        first_file = os.path.join(output_dir, json_files[0])
        zero_file = os.path.join(output_dir, "0%.json")
        shutil.copy2(first_file, zero_file)
        print(f"Copied {json_files[0]} → 0%.json for users at book start")

    print("\n=== Done! ===")
    print(f"Output directory: {output_dir}")
    print("Copy the *.json files to your book's .sdr/xray_analysis/ folder.")


if __name__ == "__main__":
    main()
