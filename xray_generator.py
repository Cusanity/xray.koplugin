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

AVAILABLE_MODELS = (
    "gemini-3-flash",
    "gemini-3-pro-high",
    "gemini-2.5-flash",
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

SKIP_NAME_PATTERNS = (
    "的父亲",
    "的母亲",
    "的朋友",
    "的儿子",
    "的女儿",
    "的妻子",
    "的丈夫",
)

XML_NS_CONTAINER = {"n": "urn:oasis:names:tc:opendocument:xmlns:container"}
XML_NS_OPF = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
XML_NS_NCX = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}

# =============================================================================
# Global State
# =============================================================================

_ai_client: OpenAI | None = None
_selected_model: str = ""
_book_title: str = ""
_current_pct: int = 0

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
    name = re.sub(r"[（(][^）)]*[）)]", "", name).strip()

    for pattern in SKIP_NAME_PATTERNS:
        if pattern in name:
            return None

    for prefix in NAME_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            name = name[len(prefix) :]
            break

    for suffix in NAME_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break

    return name.strip() if name.strip() else original


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


def call_ai_with_retry(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 65536,
    retries: int = 3,
    delay: float = 2.0,
) -> Any:
    """Call AI with retry logic for timeouts and API errors."""
    current_delay = delay
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                timeout=AI_TIMEOUT_SECONDS,
            )
            return response
        except Exception as e:
            if attempt == retries - 1:
                print(f"    [AI Error] Final attempt failed: {e}")
                raise
            print(
                f"    [AI Error] Attempt {attempt + 1}/{retries} failed: {e}. Retrying in {current_delay}s..."
            )
            time.sleep(current_delay)
            current_delay *= 2


def consolidate_description_with_ai(
    client: OpenAI,
    entity_type: str,
    name: str,
    combined_desc: str,
) -> str:
    """Call AI to consolidate a long description."""
    type_cn = "人物" if entity_type == "character" else "地点"
    prompt = CONSOLIDATE_DESC_PROMPT % (type_cn, name, combined_desc)

    try:
        response = call_ai_with_retry(
            client,
            _selected_model,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            retries=3,
        )
        content = response.choices[0].message.content
        if content:
            content = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            return result.get("description", combined_desc)
    except Exception as e:
        print(f"    [Consolidation Error] {name}: {e}")
    return combined_desc


def consolidate_summary_with_ai(
    client: OpenAI, book_title: str, combined_summary: str
) -> str:
    """Call AI to consolidate a long summary."""
    prompt = CONSOLIDATE_SUMMARY_PROMPT % (book_title, combined_summary)

    try:
        response = call_ai_with_retry(
            client,
            _selected_model,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            retries=3,
        )
        content = response.choices[0].message.content
        if content:
            content = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            return result.get("summary", combined_summary)
    except Exception as e:
        print(f"    [Summary Consolidation Error]: {e}")
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
    for event in data.get("timeline", []):
        event.pop("importance", None)
        event.pop("book_position_pct", None)

    for i, event in enumerate(data.get("timeline", [])):
        event["sequence"] = i + 1

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
                    "consolidated": None,
                }
            if desc:
                # Also convert description to simplified
                desc = _T2S_CONVERTER.convert(desc)
                self.characters[simplified_name]["descriptions"].append(desc)
                self.characters[simplified_name]["consolidated"] = None

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
            if desc:
                # Also convert description to simplified
                desc = _T2S_CONVERTER.convert(desc)
                self.locations[simplified_name]["descriptions"].append(desc)
                self.locations[simplified_name]["consolidated"] = None

    def _merge_themes(self, themes: list[str]) -> None:
        for theme in themes:
            if theme and theme not in META_THEMES:
                self.themes.add(theme)

    def _merge_events(self, events: list[Any]) -> None:
        for e in events:
            if isinstance(e, str) and e.strip():
                self.events.append(e.strip())
            elif isinstance(e, dict) and e.get("event"):
                self.events.append(e.get("event"))

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
            if val["consolidated"] is None:
                combined = " ".join(val["descriptions"])
                if (len(val["descriptions"]) > 1 and len(combined) > 300) or len(
                    combined
                ) > 500:
                    chars_needing_help.append((key, combined))

        locs_needing_help = []
        for key, val in self.locations.items():
            if val["consolidated"] is None:
                combined = " ".join(val["descriptions"])
                if (len(val["descriptions"]) > 1 and len(combined) > 300) or len(
                    combined
                ) > 500:
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
                    "_score": score_importance(data),
                }
            )

        char_items.sort(key=lambda x: x["_score"], reverse=True)
        characters = [
            {"name": c["name"], "description": c["description"]} for c in char_items
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
        for i, event in enumerate(self.events):
            event_text = event if isinstance(event, str) else event.get("event", "")
            timeline.append({"sequence": i + 1, "event": event_text})

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
) -> str | None:
    """Display interactive paginated book list and let user select."""
    if not books:
        print("No books found in Calibre library.")
        return None

    total = len(books)
    current_page = 0
    total_pages = (total + page_size - 1) // page_size

    while True:
        start_idx = current_page * page_size
        end_idx = min(start_idx + page_size, total)

        print(f"\n{'=' * 60}")
        print(
            f"Calibre Library - Page {current_page + 1}/{total_pages} ({total} books)"
        )
        print(f"{'=' * 60}\n")

        for i in range(start_idx, end_idx):
            book = books[i]
            display_title = (
                book["title"][:42] + "..." if len(book["title"]) > 45 else book["title"]
            )
            print(f"  [{i + 1:3d}] {display_title}")
            print(f"        by {book['author']}")

        print(f"\n{'─' * 60}")
        print("Commands: [n]ext page, [p]rev page, [q]uit, or enter book number")

        try:
            user_input = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return None

        if user_input == "q":
            print("Cancelled.")
            return None
        elif user_input == "n" and current_page < total_pages - 1:
            current_page += 1
        elif user_input == "p" and current_page > 0:
            current_page -= 1
        else:
            try:
                book_num = int(user_input)
                if 1 <= book_num <= total:
                    selected = books[book_num - 1]
                    print(f"\nSelected: {selected['title']} by {selected['author']}")
                    return selected["epub_path"]
                else:
                    print(f"Invalid book number. Enter 1-{total}.")
            except ValueError:
                print("Invalid input. Enter a book number, n, p, or q.")


# =============================================================================
# EPUB Reader
# =============================================================================


def get_sdr_name(epub_path: str) -> str:
    """Extract author/title from EPUB metadata and generate KOReader .sdr folder name."""
    with zipfile.ZipFile(epub_path) as z:
        container = z.read("META-INF/container.xml")
        root = ET.fromstring(container)
        opf_path = root.find(".//n:rootfile", XML_NS_CONTAINER).attrib["full-path"]

        opf_data = z.read(opf_path)
        opf_root = ET.fromstring(opf_data)
        metadata = opf_root.find(".//{http://www.idpf.org/2007/opf}metadata")

        title = "Unknown"
        author = "Unknown"

        if metadata is not None:
            t = metadata.find(".//{http://purl.org/dc/elements/1.1/}title")
            c = metadata.find(".//{http://purl.org/dc/elements/1.1/}creator")
            if t is not None and t.text:
                title = t.text
            if c is not None and c.text:
                author = c.text

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


def process_chunk(
    client: OpenAI,
    master: MasterData,
    chunk_text: str,
    title: str,
    author: str,
    pct: int,
    model: str,
) -> bool:
    """Process a single chunk through AI and merge into master. Returns True on success."""
    global _current_pct
    _current_pct = pct

    prompt = CHUNK_SUMMARY_PROMPT % (title, author, pct, chunk_text)

    print(f"  AI Summary... (Prompt len: {len(prompt)})")

    try:
        response = call_ai_with_retry(
            client,
            model,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            retries=MAX_RETRIES + 1,
        )

        # Check for truncated response (after all token scaling attempts)
        if response.choices[0].finish_reason == "length":
            print("  ⚠ Response still truncated at max tokens. Skipping...")
            return False

        content = response.choices[0].message.content
        if content is None:
            print("  ⚠ Safety filter triggered. Skipping chunk...")
            return False

        content = content.replace("```json", "").replace("```", "").strip()

        try:
            chunk_data = json.loads(content)
            master.merge_chunk(chunk_data)
            stats = master.get_stats()
            print(
                f"  [Merged] Chars: {stats['characters']}, Locs: {stats['locations']}, Events: {stats['events']}"
            )
            return True
        except json.JSONDecodeError as e:
            print(f"  JSON Error: {e}")
            return False

    except Exception as e:
        print(f"  API Error: {e}")
        return False


def consolidate_pending_items(client: OpenAI, master: MasterData) -> None:
    """Check and consolidate items needing AI consolidation."""
    chars_to_consolidate, locs_to_consolidate = master.get_items_needing_consolidation()

    if chars_to_consolidate or locs_to_consolidate:
        print(
            f"  [Consolidation] {len(chars_to_consolidate)} chars, {len(locs_to_consolidate)} locs need merging"
        )

        for name, combined_desc in chars_to_consolidate:
            consolidated = consolidate_description_with_ai(
                client, "character", name, combined_desc
            )
            master.apply_consolidation("character", name, consolidated)
            print(
                f"    ✓ [Char] {name}: {len(combined_desc)} -> {len(consolidated)} chars"
            )

        for name, combined_desc in locs_to_consolidate:
            consolidated = consolidate_description_with_ai(
                client, "location", name, combined_desc
            )
            master.apply_consolidation("location", name, consolidated)
            print(
                f"    ✓ [Loc] {name}: {len(combined_desc)} -> {len(consolidated)} chars"
            )

    if master.needs_summary_consolidation():
        master.consolidate_summary(client)


# =============================================================================
# Main Entry Point
# =============================================================================


def display_model_selector() -> str | None:
    """Display model selection menu and return selected model name."""
    print(f"\n{'=' * 60}")
    print("Select AI Model")
    print(f"{'=' * 60}\n")

    for i, model in enumerate(AVAILABLE_MODELS, 1):
        default_marker = " (default)" if model == MODEL_NAME else ""
        print(f"  [{i}] {model}{default_marker}")

    print(f"\n{'─' * 60}")
    print("Enter model number, or press Enter for default")

    try:
        user_input = input("\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return None

    if not user_input:
        return MODEL_NAME

    try:
        model_num = int(user_input)
        if 1 <= model_num <= len(AVAILABLE_MODELS):
            selected = AVAILABLE_MODELS[model_num - 1]
            print(f"\nSelected model: {selected}")
            return selected
        else:
            print(f"Invalid model number. Using default: {MODEL_NAME}")
            return MODEL_NAME
    except ValueError:
        print(f"Invalid input. Using default: {MODEL_NAME}")
        return MODEL_NAME


def main() -> None:
    global _ai_client, _book_title, _selected_model

    target_path = _get_target_path()
    if target_path is None:
        return

    selected_model = display_model_selector()
    if selected_model is None:
        return

    _selected_model = selected_model

    print(f"\nUsing API: {API_BASE_URL}")
    print(f"Using Model: {selected_model}")
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

    resume_pct, resume_data = find_resume_checkpoint(output_dir)

    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY, timeout=AI_TIMEOUT_SECONDS)
    _ai_client = client
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

    print(f"\n=== Starting Analysis with Python-Maintained Data Architecture ===")

    for i in range(start_step, total_chunks + 1):
        chapter_titles, chunk_text, end_pos = chunks[i - 1]

        chapter_display = (
            " → ".join(chapter_titles) if len(chapter_titles) > 1 else chapter_titles[0]
        )

        if not chunk_text.strip():
            print(f"Skipping empty chunk ({i}/{total_chunks})")
            continue

        print(f"\n=== Chunk {i}/{total_chunks}: 《{chapter_display}》 ===")

        pct = math.ceil(end_pos * 100 / total_len)

        if not process_chunk(
            client, master, chunk_text, title, author, pct, selected_model
        ):
            continue

        consolidate_pending_items(client, master)

        output_data = master.to_output_json(pct)
        filename = os.path.join(output_dir, f"{pct}%.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"  Saved {filename}")

    _finalize_output(master, output_dir)


def _get_target_path() -> str | None:
    """Get target EPUB path from CLI args or Calibre browser."""
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
        target_path = display_library_browser(books)
        if target_path is None:
            return None
        print()
        return target_path

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

    return target_path


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
