#!/usr/bin/env python3
"""
X-Ray Generator for EPUB Books

A standalone Python script to generate X-Ray analysis data for EPUB books
using OpenAI-compatible AI APIs. Produces progressive JSON cache files
compatible with the KOReader X-Ray plugin.

Usage:
    python xray_generator.py <epub_file_path>

Configuration:
    Set environment variables or edit the CONFIG section below:
    - XRAY_API_BASE: API endpoint (default: http://localhost:8080/v1)
    - XRAY_API_KEY: Your API key
    - XRAY_MODEL: Model name (default: gemini-2.5-flash-lite)
"""

import sys
import os
import time
import json
import zipfile
import re
import math
import xml.etree.ElementTree as ET
from openai import OpenAI

# Optional: OpenCC for Traditional/Simplified Chinese normalization
try:
    import opencc

    _t2s_converter = opencc.OpenCC("t2s")  # Traditional to Simplified
except ImportError:
    _t2s_converter = None
    print("Note: opencc not installed. Traditional/Simplified deduplication disabled.")

# === Configuration ===
# Override via environment variables or edit directly
API_BASE_URL = os.environ.get("XRAY_API_BASE", "http://127.0.0.1:8045/v1")
API_KEY = os.environ.get("XRAY_API_KEY", "sk-e80a5d77e693448cadce981aa5b752de")
MODEL_NAME = os.environ.get("XRAY_MODEL", "gemini-3-pro-high")

TEMPERATURE = 0.4
TOP_P = 0.95

# Output limits for curation
# Note: No hard caps on character/location counts - UI handles display

# Calibre Library Path (for library browsing feature)
CALIBRE_LIBRARY = os.environ.get(
    "CALIBRE_LIBRARY", r"C:\Users\-GALACTUS-\Calibre Library"
)


# === Calibre Library Browser ===
def scan_calibre_library(library_path):
    """
    Scan Calibre library and return list of books with metadata.
    Returns list of dicts: {title, author, epub_path, folder_path}
    """
    books = []

    if not os.path.isdir(library_path):
        print(f"Error: Calibre library not found at {library_path}")
        return books

    # Calibre organizes books as: Library/Author/BookFolder/files
    for author_dir in os.listdir(library_path):
        author_path = os.path.join(library_path, author_dir)

        # Skip non-directories and hidden/system folders
        if not os.path.isdir(author_path) or author_dir.startswith("."):
            continue

        # Scan book folders within author directory
        for book_dir in os.listdir(author_path):
            book_path = os.path.join(author_path, book_dir)

            if not os.path.isdir(book_path):
                continue

            # Look for metadata.opf and epub file
            metadata_path = os.path.join(book_path, "metadata.opf")
            if not os.path.exists(metadata_path):
                continue

            # Find EPUB file in the book folder
            epub_file = None
            for f in os.listdir(book_path):
                if f.lower().endswith(".epub"):
                    epub_file = os.path.join(book_path, f)
                    break

            if not epub_file:
                continue

            # Parse metadata.opf to get actual title and author
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
                # Fallback to folder/file names if parsing fails
                print(f"Warning: Could not parse {metadata_path}: {e}")

    # Sort by added date (newest first)
    books.sort(key=lambda b: b["added_date"], reverse=True)
    return books


def parse_metadata_opf(opf_path):
    """Parse Calibre's metadata.opf file to extract title, author, and added date."""
    tree = ET.parse(opf_path)
    root = tree.getroot()

    ns = {
        "opf": "http://www.idpf.org/2007/opf",
        "dc": "http://purl.org/dc/elements/1.1/",
    }

    title = "Unknown Title"
    author = "Unknown Author"
    added_date = "1970-01-01T00:00:00+00:00"  # Default for books without timestamp

    # Find metadata element
    metadata = root.find("opf:metadata", ns)
    if metadata is None:
        # Try without namespace prefix (some OPF files)
        metadata = root.find(".//{http://www.idpf.org/2007/opf}metadata")

    if metadata is not None:
        title_elem = metadata.find("dc:title", ns)
        if title_elem is None:
            title_elem = metadata.find(".//{http://purl.org/dc/elements/1.1/}title")
        if title_elem is not None and title_elem.text:
            title = title_elem.text.strip()

        creator_elem = metadata.find("dc:creator", ns)
        if creator_elem is None:
            creator_elem = metadata.find(".//{http://purl.org/dc/elements/1.1/}creator")
        if creator_elem is not None and creator_elem.text:
            author = creator_elem.text.strip()

        # Extract calibre:timestamp (when book was added to library)
        for meta in metadata.findall(".//{http://www.idpf.org/2007/opf}meta"):
            if meta.get("name") == "calibre:timestamp":
                added_date = meta.get("content", added_date)
                break

    return title, author, added_date


def display_library_browser(books, page_size=20):
    """
    Display interactive paginated book list and let user select.
    Returns the selected book's epub_path or None if cancelled.
    """
    if not books:
        print("No books found in Calibre library.")
        return None

    total = len(books)
    current_page = 0
    total_pages = (total + page_size - 1) // page_size

    while True:
        # Calculate page bounds
        start_idx = current_page * page_size
        end_idx = min(start_idx + page_size, total)

        # Display header
        print(f"\n{'=' * 60}")
        print(
            f"Calibre Library - Page {current_page + 1}/{total_pages} ({total} books)"
        )
        print(f"{'=' * 60}\n")

        # Display books on this page
        for i in range(start_idx, end_idx):
            book = books[i]
            # Truncate title if too long
            display_title = book["title"]
            if len(display_title) > 45:
                display_title = display_title[:42] + "..."
            print(f"  [{i + 1:3d}] {display_title}")
            print(f"        by {book['author']}")

        # Display navigation options
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
            # Try to parse as book number
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


# Meta-themes to filter out (structural/narrative terms, not reader-relevant)
META_THEMES = {
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


# === Load Prompts from Shared JSON ===
def load_prompts():
    """Load prompts from shared JSON file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    prompts_path = os.path.join(script_dir, "prompts", "zh.json")

    with open(prompts_path, "r", encoding="utf-8") as f:
        return json.load(f)


# === SDR Folder Name Generation ===
def get_sdr_name(epub_path):
    """Extract author/title from EPUB metadata and generate KOReader .sdr folder name."""
    ns = {
        "n": "urn:oasis:names:tc:opendocument:xmlns:container",
    }

    with zipfile.ZipFile(epub_path) as z:
        # Find OPF file
        container = z.read("META-INF/container.xml")
        root = ET.fromstring(container)
        opf_path = root.find(".//n:rootfile", ns).attrib["full-path"]

        # Read OPF metadata
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

    # Sanitize for filesystem
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)
    safe_author = re.sub(r'[<>:"/\\|?*]', "_", author)

    return f"{safe_author} - {safe_title}.epub.sdr"


PROMPTS = load_prompts()

SYSTEM_PROMPT = PROMPTS["system_instruction"]
CHUNK_SUMMARY_PROMPT = PROMPTS["chunk_summary"]
CONSOLIDATE_DESC_PROMPT = PROMPTS["consolidate_description"]
CONSOLIDATE_SUMMARY_PROMPT = PROMPTS["consolidate_summary"]
EVENT_CONSOLIDATION_PROMPT = PROMPTS["event_consolidation"]

# Global client reference for curation (set in main)
_ai_client = None
_book_title = None
_current_pct = 0


# === Text Sanitization Patterns ===
# Remove phrases that indicate incremental/chunk-based processing
# Users should perceive the analysis as done in one pass
INCREMENTAL_MARKERS = [
    # Ordered by length (longest first) to prevent partial matches
    # "本片段" variations (most common issue)
    "本片段包含",
    "本片段中",
    "本片段",
    "此片段包含",
    "此片段中",
    "此片段",
    "该片段",
    "当前片段",
    # "新文本/片段" variations
    "在新文本中，",
    "在新文本中",
    "新文本中，",
    "新文本中",
    "在新片段中",
    "新片段中",
    # "本段/此段" variations
    "在本段中，",
    "在本段中",
    "在此段中",
    "本段中",
    "此段中",
    # "章节" variations
    "本章节中",
    "此章节中",
    "本节中",
    # Generic indicators
    "新情节中",
    "新文本",
    "新片段",
    "片段中",
]


# === AI Helper with Retry ===
def call_ai_with_retry(
    client, model, messages, temperature=0.3, max_tokens=None, retries=3, delay=2
):
    """Call AI with retry logic for timeouts and API errors."""
    for attempt in range(retries):
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens

            response = client.chat.completions.create(**kwargs)
            return response
        except Exception as e:
            if attempt == retries - 1:
                # Last attempt failed, re-raise or return None depending on strategy
                # Here we raise to let caller handle or just print
                print(f"    [AI Error] Final attempt failed: {e}")
                raise e

            print(
                f"    [AI Error] Attempt {attempt + 1}/{retries} failed: {e}. Retrying in {delay}s..."
            )
            time.sleep(delay)
            delay *= 2  # Exponential backoff
    return None


def sanitize_text(text):
    """Remove incremental processing markers from text."""
    if not isinstance(text, str):
        return text
    for marker in INCREMENTAL_MARKERS:
        text = text.replace(marker, "")
    # Clean up any resulting double spaces or punctuation issues
    text = re.sub(r"，，+", "，", text)
    text = re.sub(r"。。+", "。", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


class MasterData:
    """
    Python-maintained master data structure.
    AI only summarizes chunks; Python handles all merging and consolidation.
    """

    # Patterns to strip from character names
    NAME_PREFIXES = [
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
    ]
    NAME_SUFFIXES = [
        # Titles
        "先生",
        "太太",
        "小姐",
        "女士",
        "夫人",
        "阁下",
        # Professions
        "律师",
        "医生",
        "教授",
        "老师",
        "博士",
        "神父",
        "牧师",
        # Family relations
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
    ]

    @staticmethod
    def normalize_name(name):
        """Normalize character name by stripping titles, relations, parenthetical content."""
        if not name:
            return name

        original = name

        # Remove parenthetical content: "胡安娜（帕拉太太）" -> "胡安娜"
        name = re.sub(r"[（(][^）)]*[）)]", "", name).strip()

        # Skip generic references that can't be resolved
        skip_patterns = [
            "的父亲",
            "的母亲",
            "的朋友",
            "的儿子",
            "的女儿",
            "的妻子",
            "的丈夫",
        ]
        for pattern in skip_patterns:
            if pattern in name:
                return None

        # Remove prefixes
        for prefix in MasterData.NAME_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix) :]
                break

        # Remove suffixes
        for suffix in MasterData.NAME_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[: -len(suffix)]
                break

        return name.strip() if name.strip() else original

    @staticmethod
    def normalize_for_dedup(name):
        """Normalize name for deduplication (convert Traditional to Simplified Chinese)."""
        if not name:
            return name
        # Convert Traditional to Simplified if opencc available
        if _t2s_converter:
            name = _t2s_converter.convert(name)
        return name.strip()

    @staticmethod
    def normalize_location_name(name):
        """Normalize location name for deduplication (hyphens + T2S)."""
        if not name:
            return name
        # Normalize various dash characters to standard hyphen
        name = name.replace("－", "-").replace("—", "-").replace("–", "-")
        # Convert Traditional to Simplified if opencc available
        if _t2s_converter:
            name = _t2s_converter.convert(name)
        return name.strip()

    def __init__(self, book_title="", author="", author_bio=""):
        self.book_title = book_title
        self.author = author
        self.author_bio = author_bio

        # Characters/locations: normalized_key -> {"display_name": str, "descriptions": [...], "consolidated": None}
        # We use normalized keys for deduplication but preserve first-seen display name
        self.characters = {}
        self.locations = {}

        self.themes = set()
        self.events = []  # All events collected, will be curated at end
        self.summary_parts = []  # Chunk summaries to be merged

    def merge_chunk(self, chunk_data):
        """Merge a chunk summary into master data (blind append)."""

        # === CHARACTERS ===
        for char in chunk_data.get("characters", []):
            raw_name = char.get("name", "").strip()
            if not raw_name:
                continue

            # Normalize the name (strip titles, relations, etc.)
            name = self.normalize_name(raw_name)
            if not name:
                # Skip generic references like "XX的父亲"
                continue

            desc = char.get("description", "").strip()

            # Use normalized key for deduplication
            dedup_key = self.normalize_for_dedup(name)

            if dedup_key not in self.characters:
                self.characters[dedup_key] = {
                    "display_name": name,  # Preserve first-seen display name
                    "descriptions": [],
                    "consolidated": None,
                }
            if desc:
                self.characters[dedup_key]["descriptions"].append(desc)
                self.characters[dedup_key]["consolidated"] = None  # Invalidate

        # === LOCATIONS ===
        for loc in chunk_data.get("locations", []):
            name = loc.get("name", "").strip()
            if not name:
                continue
            desc = loc.get("description", "").strip()

            # Use normalized key for deduplication
            dedup_key = self.normalize_location_name(name)

            if dedup_key not in self.locations:
                self.locations[dedup_key] = {
                    "display_name": name,  # Preserve first-seen display name
                    "descriptions": [],
                    "consolidated": None,
                }
            if desc:
                self.locations[dedup_key]["descriptions"].append(desc)
                self.locations[dedup_key]["consolidated"] = None  # Invalidate

        # === THEMES ===
        for theme in chunk_data.get("themes", []):
            if theme and theme not in META_THEMES:  # Filter meta-themes
                self.themes.add(theme)

        # === EVENTS (now simple strings) ===
        new_events = []
        for e in chunk_data.get("events", []):
            if isinstance(e, str) and e.strip():
                new_events.append(e.strip())
            elif isinstance(e, dict) and e.get("event"):
                new_events.append(e.get("event"))

        if new_events:
            # Call AI to consolidate new events with existing events
            consolidated = self.consolidate_events(new_events)
            if consolidated is not None:
                self.events = consolidated
            else:
                # Fallback: just append
                self.events.extend(new_events)

        # === SUMMARY ===
        summary = chunk_data.get("summary", "").strip()
        if summary:
            self.summary_parts.append(summary)

        # Update metadata if provided
        if chunk_data.get("book_title"):
            self.book_title = chunk_data["book_title"]
        if chunk_data.get("author"):
            self.author = chunk_data["author"]
        if chunk_data.get("author_bio"):
            self.author_bio = chunk_data["author_bio"]

    def get_items_needing_consolidation(self):
        """Return lists of items with multiple descriptions that need AI consolidation."""
        # Re-implementing logic:
        chars_needing_help = []
        for key, val in self.characters.items():
            # Only consolidate if not already consolidated
            if val["consolidated"] is None:
                combined = " ".join(val["descriptions"])
                # Consolidate if length > 300 and multiple parts, or if just excessively long > 500
                if (len(val["descriptions"]) > 1 and len(combined) > 300) or len(
                    combined
                ) > 500:
                    chars_needing_help.append((key, combined))

        locs_needing_help = []
        for key, val in self.locations.items():
            # Only consolidate if not already consolidated
            if val["consolidated"] is None:
                combined = " ".join(val["descriptions"])
                if (len(val["descriptions"]) > 1 and len(combined) > 300) or len(
                    combined
                ) > 500:
                    locs_needing_help.append((key, combined))

        return chars_needing_help, locs_needing_help

    def needs_summary_consolidation(self):
        """Check if summary needs consolidation."""
        # Only consolidate if there are multiple parts or if the single part is very long
        if len(self.summary_parts) > 1:
            return True
        elif len(self.summary_parts) == 1:
            return (
                len(self.summary_parts[0]) > 1500
            )  # Consolidate if > 1500 chars (approx 300-500 words)
        return False

    def consolidate_summary(self, client):
        """Consolidate accumulated summary parts into one."""
        if not self.summary_parts:
            return

        combined = " ".join(self.summary_parts)
        print(f"  [Summary] Consolidating {len(combined)} chars...")

        consolidated = consolidate_summary_with_ai(client, self.book_title, combined)
        # Replace parts with the consolidated version
        self.summary_parts = [consolidated]
        print(f"  [Summary] Consolidated to {len(consolidated)} chars")

    def apply_consolidation(self, entity_type, name, consolidated_desc):
        """Apply AI-consolidated description."""
        if entity_type == "character":
            if name in self.characters:
                self.characters[name]["consolidated"] = consolidated_desc
                self.characters[name]["descriptions"] = []  # Clear raw fragments
        elif entity_type == "location":
            if name in self.locations:
                self.locations[name]["consolidated"] = consolidated_desc
                self.locations[name]["descriptions"] = []

    def to_output_json(self, progress_pct):
        """Convert to final output JSON format with importance-based limiting."""

        def _score_importance(data):
            """Score entity importance: description length + fragment count weight."""
            desc = data.get("consolidated") or " ".join(data.get("descriptions", []))
            fragment_count = len(data.get("descriptions", []))
            return len(desc) + (fragment_count * 50)  # Weight multiple mentions

        # Build character list with importance scoring
        char_items = []
        for key, data in self.characters.items():
            if data["consolidated"]:
                desc = data["consolidated"]
            elif data["descriptions"]:
                desc = " ".join(data["descriptions"])
            else:
                desc = ""

            # Use display_name if available, fallback to key
            display_name = data.get("display_name", key)
            score = _score_importance(data)
            char_items.append(
                {"name": display_name, "description": desc, "_score": score}
            )

        # Sort by importance (most-mentioned first)
        char_items.sort(key=lambda x: x["_score"], reverse=True)
        characters = [
            {"name": c["name"], "description": c["description"]} for c in char_items
        ]

        # Build location list with importance scoring
        loc_items = []
        for key, data in self.locations.items():
            if data["consolidated"]:
                desc = data["consolidated"]
            elif data["descriptions"]:
                desc = " ".join(data["descriptions"])
            else:
                desc = ""

            # Use display_name if available, fallback to key
            display_name = data.get("display_name", key)
            score = _score_importance(data)
            loc_items.append(
                {"name": display_name, "description": desc, "_score": score}
            )

        # Sort by importance (most-mentioned first)
        loc_items.sort(key=lambda x: x["_score"], reverse=True)
        locations = [
            {"name": l["name"], "description": l["description"]} for l in loc_items
        ]

        # Build summary (join parts)
        summary = " ".join(self.summary_parts)

        # Build timeline from all events (simple strings now)
        timeline = []
        for i, event in enumerate(self.events):
            event_text = event if isinstance(event, str) else event.get("event", "")
            timeline.append(
                {
                    "sequence": i + 1,
                    "event": event_text,
                }
            )

        # Filter themes (already filtered during merge, but ensure limit)
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

    def consolidate_events(self, new_events):
        """Call AI to consolidate new events with existing events."""
        global _ai_client, _current_pct

        if _ai_client is None:
            return None

        if not self.events and not new_events:
            return []

        # If no existing events, just return new events
        if not self.events:
            return new_events

        # If no new events, keep existing
        if not new_events:
            return self.events

        existing_json = json.dumps(self.events, ensure_ascii=False)
        new_json = json.dumps(new_events, ensure_ascii=False)

        # Format: title, progress_pct, existing_events, new_events
        prompt = EVENT_CONSOLIDATION_PROMPT % (
            self.book_title,
            _current_pct,
            existing_json,
            new_json,
        )

        try:
            response = call_ai_with_retry(
                _ai_client,
                MODEL_NAME,
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
                consolidated = result.get("events", [])
                if consolidated:
                    print(
                        f"  [Events] Consolidated: {len(self.events)} + {len(new_events)} -> {len(consolidated)}"
                    )
                    return consolidated
        except Exception as e:
            print(f"  [Events] Consolidation error: {e}")

        # Fallback: just append
        return None

    def get_stats(self):
        """Return current stats for logging."""
        return {
            "characters": len(self.characters),
            "locations": len(self.locations),
            "themes": len(self.themes),
            "events": len(self.events),
            "summary_parts": len(self.summary_parts),
        }


def consolidate_description_with_ai(client, entity_type, name, combined_desc):
    """Call AI to consolidate a long description."""
    # entity_type: "人物" or "地点"
    type_cn = "人物" if entity_type == "character" else "地点"

    prompt = CONSOLIDATE_DESC_PROMPT % (type_cn, name, combined_desc)

    try:
        response = call_ai_with_retry(
            client,
            MODEL_NAME,
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

    # Fallback: return original
    return combined_desc


def consolidate_summary_with_ai(client, book_title, combined_summary):
    """Call AI to consolidate a long summary."""
    prompt = CONSOLIDATE_SUMMARY_PROMPT % (book_title, combined_summary)

    try:
        response = call_ai_with_retry(
            client,
            MODEL_NAME,
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

    # Fallback: return original
    return combined_summary


def cleanup_data(data, current_pct):
    """Remove unrequested fields and enforce limits after AI response"""

    # Sanitize summary
    if "summary" in data:
        data["summary"] = sanitize_text(data["summary"])

    # Remove gender from characters and sanitize descriptions
    for char in data.get("characters", []):
        char.pop("gender", None)
        if "description" in char:
            char["description"] = sanitize_text(char["description"])

    # === DEDUPLICATE CHARACTERS ===
    # Merge duplicates by name, keeping the longer description
    chars = data.get("characters", [])
    if chars:
        char_map = {}  # name -> character dict
        for char in chars:
            name = char.get("name", "").strip()
            if not name:
                continue
            if name in char_map:
                # Keep the longer description
                existing_desc = char_map[name].get("description", "")
                new_desc = char.get("description", "")
                if len(new_desc) > len(existing_desc):
                    char_map[name]["description"] = new_desc
            else:
                char_map[name] = char
        data["characters"] = list(char_map.values())

    # Remove type from locations and sanitize descriptions
    for loc in data.get("locations", []):
        loc.pop("type", None)
        if "description" in loc:
            loc["description"] = sanitize_text(loc["description"])

    # === DEDUPLICATE LOCATIONS ===
    # Merge duplicates by name, keeping the longer description
    locs = data.get("locations", [])
    if locs:
        loc_map = {}  # name -> location dict
        for loc in locs:
            name = loc.get("name", "").strip()
            if not name:
                continue
            if name in loc_map:
                # Keep the longer description
                existing_desc = loc_map[name].get("description", "")
                new_desc = loc.get("description", "")
                if len(new_desc) > len(existing_desc):
                    loc_map[name]["description"] = new_desc
            else:
                loc_map[name] = loc
        data["locations"] = list(loc_map.values())

    # === DEDUPLICATE THEMES ===
    themes = data.get("themes", [])
    if themes:
        seen = set()
        unique_themes = []
        for theme in themes:
            if theme and theme not in seen:
                seen.add(theme)
                unique_themes.append(theme)
        data["themes"] = unique_themes

    # Remove unrequested fields from timeline
    for event in data.get("timeline", []):
        event.pop("importance", None)
        event.pop("book_position_pct", None)

    # Ensure timeline has sequence numbers
    for i, event in enumerate(data.get("timeline", [])):
        event["sequence"] = i + 1

    # Clean up pending_events
    for event in data.get("pending_events", []):
        event.pop("importance", None)
        # Ensure book_position_pct is int
        if "book_position_pct" in event:
            try:
                event["book_position_pct"] = int(event["book_position_pct"])
            except (ValueError, TypeError):
                event["book_position_pct"] = current_pct

    # Trim themes to max 8
    if len(data.get("themes", [])) > 8:
        data["themes"] = data["themes"][:8]

    # Timeline: no hard limit - per-chunk AI consolidation handles deduplication

    return data


# === EPUB Reader Class ===
class EpubReader:
    def __init__(self, epub_path):
        self.epub_path = epub_path
        self.ns = {
            "n": "urn:oasis:names:tc:opendocument:xmlns:container",
            "pkg": "http://www.idpf.org/2007/opf",
            "dc": "http://purl.org/dc/elements/1.1/",
        }

    def get_chapters(self):
        """Extract chapters as list of (title, text) tuples in reading order."""
        try:
            with zipfile.ZipFile(self.epub_path) as z:
                # 1. Find OPF file path
                txt = z.read("META-INF/container.xml")
                root = ET.fromstring(txt)
                opf_path = root.find(".//n:rootfile", self.ns).attrib["full-path"]

                # 2. Read OPF
                opf_data = z.read(opf_path)
                opf_root = ET.fromstring(opf_data)
                opf_dir = os.path.dirname(opf_path)

                # Extract Metadata (Title/Author)
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

                print(f"Book: {book_title} by {author}")

                # 3. Parse Manifest and Spine
                manifest = {}
                for item in opf_root.findall(
                    ".//{http://www.idpf.org/2007/opf}manifest/{http://www.idpf.org/2007/opf}item"
                ):
                    manifest[item.attrib["id"]] = item.attrib["href"]

                spine = []
                for itemref in opf_root.findall(
                    ".//{http://www.idpf.org/2007/opf}spine/{http://www.idpf.org/2007/opf}itemref"
                ):
                    spine.append(itemref.attrib["idref"])

                # 4. Extract TOC from NCX or NAV for chapter titles
                toc_map = {}  # Map from file path -> chapter title

                # Try NCX first
                ncx_id = None
                for item_id, href in manifest.items():
                    if href.endswith(".ncx"):
                        ncx_id = item_id
                        break

                if ncx_id:
                    ncx_path = os.path.join(opf_dir, manifest[ncx_id]).replace(
                        "\\", "/"
                    )
                    try:
                        ncx_data = z.read(ncx_path)
                        ncx_root = ET.fromstring(ncx_data)
                        ncx_ns = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}

                        for nav_point in ncx_root.findall(".//ncx:navPoint", ncx_ns):
                            text_elem = nav_point.find("ncx:navLabel/ncx:text", ncx_ns)
                            content_elem = nav_point.find("ncx:content", ncx_ns)
                            if text_elem is not None and content_elem is not None:
                                nav_title = (
                                    text_elem.text.strip() if text_elem.text else None
                                )
                                nav_src = content_elem.attrib.get("src", "")
                                # Remove fragment identifier (e.g., #chapter1)
                                nav_file = nav_src.split("#")[0]
                                if nav_title and nav_file:
                                    # Normalize path
                                    full_path = os.path.join(opf_dir, nav_file).replace(
                                        "\\", "/"
                                    )
                                    toc_map[full_path] = nav_title
                    except Exception as e:
                        print(f"Warning: Could not parse NCX: {e}")

                print(f"Found {len(toc_map)} TOC entries")

                # 5. Extract Chapters with Titles
                chapters = []
                chapter_index = 0

                for item_id in spine:
                    if item_id in manifest:
                        file_path = os.path.join(opf_dir, manifest[item_id]).replace(
                            "\\", "/"
                        )
                        try:
                            content = z.read(file_path).decode("utf-8")

                            # Try to get title from TOC first
                            toc_title = toc_map.get(file_path)

                            # Extract chapter with TOC title hint
                            chapter_title, text = self.extract_chapter(
                                content, chapter_index, book_title, toc_title
                            )
                            if text.strip():
                                chapters.append((chapter_title, text))
                                chapter_index += 1
                        except KeyError:
                            print(f"Warning: File {file_path} not found in archive.")
                        except Exception as e:
                            print(f"Error extracting {file_path}: {e}")

                return chapters, book_title, author
        except Exception as e:
            print(f"Fatal error reading EPUB: {e}")
            return None, None, None

    def extract_chapter(self, html, fallback_index, book_title=None, toc_title=None):
        """Extract chapter title and text from HTML content."""

        # Priority 1: Use TOC title if provided and it's not the book title
        if toc_title and toc_title != book_title:
            text = self.html_to_text(html)
            return toc_title, text

        chapter_title = None

        # Priority 2: Try first <h1>, <h2>, or <h3> (more likely to be chapter title)
        for h_level in ["h1", "h2", "h3"]:
            h_match = re.search(
                rf"<{h_level}[^>]*>(.*?)</{h_level}>", html, re.DOTALL | re.IGNORECASE
            )
            if h_match:
                raw_title = h_match.group(1).strip()
                raw_title = re.sub(r"<[^>]+>", "", raw_title)
                raw_title = (
                    raw_title.replace("&nbsp;", " ").replace("&amp;", "&").strip()
                )
                # Skip if it's the book title or too long
                if raw_title and len(raw_title) < 100 and raw_title != book_title:
                    chapter_title = raw_title
                    break

        # Priority 3: Try <title> tag only if it's different from book title
        if not chapter_title:
            title_match = re.search(
                r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE
            )
            if title_match:
                raw_title = title_match.group(1).strip()
                raw_title = re.sub(r"<[^>]+>", "", raw_title)
                raw_title = (
                    raw_title.replace("&nbsp;", " ").replace("&amp;", "&").strip()
                )
                if raw_title and len(raw_title) < 100 and raw_title != book_title:
                    chapter_title = raw_title

        # Fallback to generic chapter name
        if not chapter_title:
            chapter_title = f"第{fallback_index + 1}节"

        # Extract text content
        text = self.html_to_text(html)

        return chapter_title, text

    def get_text(self):
        """Legacy method: Extracts all text as a single string (for backward compatibility)."""
        chapters, book_title, author = self.get_chapters()
        if chapters:
            full_text = "\n".join([text for _, text in chapters])
            return full_text, book_title, author
        return None, None, None

    def html_to_text(self, html):
        """Rudimentary HTML to text converter."""
        # Remove head
        html = re.sub(r"<head.*?>.*?</head>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Replace block tags with newlines
        html = re.sub(r"<(p|div|h[1-6]|li|br).*?>", "\n", html, flags=re.IGNORECASE)
        # Remove all other tags
        html = re.sub(r"<[^>]+>", "", html)
        # Decode entities (basic)
        html = (
            html.replace("&nbsp;", " ")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
            .replace("&quot;", '"')
        )
        # Normalize whitespace
        return re.sub(r"\n\s*\n", "\n\n", html).strip()


# === Main Logic ===
def main():
    # Check for --browse flag or no arguments
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
            return

        print(f"Found {len(books)} EPUB books.\n")

        # Launch interactive browser
        target_path = display_library_browser(books)
        if target_path is None:
            return  # User cancelled

        print()  # Blank line before processing
    else:
        target_path = sys.argv[1]

        if not os.path.exists(target_path):
            print(f"File not found: {target_path}")
            print("\nUsage:")
            print("  python xray_generator.py <epub_file_path>")
            print("  python xray_generator.py --browse  (browse Calibre library)")
            print("\nConfiguration via environment variables:")
            print(
                "  XRAY_API_BASE    - API endpoint (default: http://localhost:8080/v1)"
            )
            print("  XRAY_API_KEY     - Your API key")
            print("  XRAY_MODEL       - Model name (default: gemini-2.5-flash-lite)")
            print("  CALIBRE_LIBRARY  - Path to Calibre library")
            return

    print(f"Using API: {API_BASE_URL}")
    print(f"Using Model: {MODEL_NAME}")
    print(f"\nReading {target_path}...")

    reader = EpubReader(target_path)
    chapters, title, author = reader.get_chapters()

    if not chapters:
        print("Failed to extract chapters.")
        return

    # Calculate total text length for percentage calculations
    total_len = sum(len(text) for _, text in chapters)
    print(f"Total text length: {total_len} characters")
    print(f"Book Title: {title}")
    print(f"Found {len(chapters)} chapters")

    # Create Output Directory inside xray.koplugin/xray/
    # Use SDR naming convention: "Author - Title.epub.sdr"
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
            return
    else:
        print(f"Using output directory: {output_dir}")

    # === Resume Logic: Detect existing checkpoint ===
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

    # Initialize OpenAI Client with 60s timeout
    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY, timeout=60.0)

    # Set global references for AI curation during cleanup
    global _ai_client, _book_title, _current_pct
    _ai_client = client
    _book_title = title

    # === Chapter-based Chunking ===
    # Respect 10k character limit, split large chapters into segments
    # Each segment keeps chapter title context with (续) marker for continuations
    MAX_CHUNK_SIZE = 15000

    chunks = []  # List of (chapter_titles, combined_text, end_char_position)
    current_chunk_titles = []
    current_chunk_text = ""
    chars_processed = 0

    for chapter_title, chapter_text in chapters:
        chapter_len = len(chapter_text)

        # If chapter is larger than MAX_CHUNK_SIZE, split it
        if chapter_len > MAX_CHUNK_SIZE:
            # First, flush any pending content
            if current_chunk_text.strip():
                chunks.append(
                    (current_chunk_titles, current_chunk_text.strip(), chars_processed)
                )
                current_chunk_titles = []
                current_chunk_text = ""

            # Split the large chapter into segments
            segment_idx = 0
            start = 0
            while start < chapter_len:
                end = min(start + MAX_CHUNK_SIZE, chapter_len)

                # Try to split at a paragraph boundary (newline) if possible
                if end < chapter_len:
                    # Look for a newline in the last 500 chars of the segment
                    search_start = max(end - 500, start)
                    last_newline = chapter_text.rfind("\n", search_start, end)
                    if last_newline > start:
                        end = last_newline + 1

                segment_text = chapter_text[start:end]

                # Add chapter title header, with (续) marker for continuation segments
                if segment_idx == 0:
                    header = f"【{chapter_title}】"
                else:
                    header = f"【{chapter_title}（续{segment_idx}）】"

                segment_with_header = f"{header}\n{segment_text}\n\n"

                chunks.append(
                    (
                        [
                            f"{chapter_title}"
                            if segment_idx == 0
                            else f"{chapter_title}（续{segment_idx}）"
                        ],
                        segment_with_header.strip(),
                        chars_processed + end,
                    )
                )

                segment_idx += 1
                start = end

            chars_processed += chapter_len
        else:
            # Normal chapter - use existing logic
            chapter_with_header = f"【{chapter_title}】\n{chapter_text}\n\n"

            # If adding this chapter would exceed limit, flush current chunk first
            if (
                current_chunk_text
                and len(current_chunk_text) + len(chapter_with_header) > MAX_CHUNK_SIZE
            ):
                chunks.append(
                    (current_chunk_titles, current_chunk_text.strip(), chars_processed)
                )
                current_chunk_titles = []
                current_chunk_text = ""

            # Add chapter to current chunk
            current_chunk_titles.append(chapter_title)
            current_chunk_text += chapter_with_header
            chars_processed += chapter_len

    # Don't forget the last chunk
    if current_chunk_text.strip():
        chunks.append(
            (current_chunk_titles, current_chunk_text.strip(), chars_processed)
        )

    total_chunks = len(chunks)
    print(
        f"Will process in {total_chunks} chapter-based chunks (max {MAX_CHUNK_SIZE} chars each)"
    )

    # Initialize MasterData
    master = MasterData(book_title=title, author=author)

    # Calculate starting chunk based on resume percentage
    start_step = 1
    if resume_pct > 0 and resume_data:
        # Find which chunk corresponds to this percentage
        for idx, (_, _, end_pos) in enumerate(chunks):
            chunk_pct = int((end_pos / total_len) * 100)
            if chunk_pct >= resume_pct:
                start_step = idx + 2  # Start from next chunk (1-indexed)
                break
        else:
            start_step = total_chunks + 1  # Already complete

        # Restore MasterData from checkpoint
        for char in resume_data.get("characters", []):
            name = char.get("name", "").strip()
            if name:
                dedup_key = master.normalize_for_dedup(name)
                master.characters[dedup_key] = {
                    "display_name": name,
                    "descriptions": [char.get("description", "")],
                    "consolidated": char.get("description", ""),
                }

        for loc in resume_data.get("locations", []):
            name = loc.get("name", "").strip()
            if name:
                dedup_key = master.normalize_location_name(name)
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

        if start_step > total_chunks:
            print(f"Analysis already complete at {resume_pct}%!")
            return

        print(f"Resuming from chunk {start_step}/{total_chunks}")

    print(f"\n=== Starting Analysis with Python-Maintained Data Architecture ===")

    for i in range(start_step, total_chunks + 1):
        chapter_titles, chunk_text, end_pos = chunks[i - 1]

        # Format chapter title(s) for display in console
        if len(chapter_titles) == 1:
            chapter_display = chapter_titles[0]
        else:
            chapter_display = " → ".join(chapter_titles)

        if not chunk_text.strip():
            print(f"Skipping empty chunk ({i}/{total_chunks})")
            continue

        print(f"\n=== Chunk {i}/{total_chunks}: 《{chapter_display}》 ===")

        # Calculate reading progress percentage
        pct = math.ceil(end_pos * 100 / total_len)

        # === STEP 1: AI summarizes just this chunk ===
        # params: title, author, reading_progress, chunk_text
        prompt = CHUNK_SUMMARY_PROMPT % (title, author, pct, chunk_text)

        # Retry Loop
        MAX_RETRIES = 2
        chunk_data = None

        for attempt in range(MAX_RETRIES + 1):
            print(
                f"  AI Summary (Attempt {attempt + 1})... (Prompt len: {len(prompt)})"
            )

            try:
                response = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=TEMPERATURE,
                    top_p=TOP_P,
                    response_format={"type": "json_object"},
                )

                content = response.choices[0].message.content
                if content is None:
                    print(f"  ⚠ Safety filter triggered. Skipping chunk...")
                    break

                content = content.replace("```json", "").replace("```", "").strip()

                try:
                    chunk_data = json.loads(content)
                    break  # Success
                except json.JSONDecodeError as e:
                    print(f"  JSON Error: {e}")
                    if attempt < MAX_RETRIES:
                        print("  Retrying...")
                    continue

            except Exception as e:
                print(f"  API Error: {e}")
                if attempt < MAX_RETRIES:
                    print("  Retrying...")
                continue

        if not chunk_data:
            print(f"  Failed to get chunk summary. Skipping...")
            continue

        # Update global progress for event consolidation
        global _current_pct
        _current_pct = pct

        # === STEP 2: Python merges chunk data into master ===
        master.merge_chunk(chunk_data)
        stats = master.get_stats()
        print(
            f"  [Merged] Chars: {stats['characters']}, Locs: {stats['locations']}, Events: {stats['events']}"
        )

        # === STEP 3: Check for items needing consolidation ===
        chars_to_consolidate, locs_to_consolidate = (
            master.get_items_needing_consolidation()
        )

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

        # Check summary consolidation
        if master.needs_summary_consolidation():
            master.consolidate_summary(client)

        # === STEP 4: Save progress ===
        output_data = master.to_output_json(pct)
        filename = os.path.join(output_dir, f"{pct}%.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"  Saved {filename}")

    # Generate final 100% output (no additional curation - per-chunk consolidation already handled)
    final_data = master.to_output_json(100)
    print(f"\n=== Final Analysis: {len(final_data['timeline'])} timeline events ===")

    # Save final 100% file
    filename = os.path.join(output_dir, "100%.json")
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
    print(f"\nSaved final analysis to {filename}")

    # === Copy first chunk as 0%.json for users at book start ===
    import shutil

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
