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
import json
import zipfile
import re
import math
import xml.etree.ElementTree as ET
from openai import OpenAI

# === Configuration ===
# Override via environment variables or edit directly
API_BASE_URL = os.environ.get("XRAY_API_BASE", "http://127.0.0.1:8045/v1")
API_KEY = os.environ.get("XRAY_API_KEY", "sk-e80a5d77e693448cadce981aa5b752de")
MODEL_NAME = os.environ.get("XRAY_MODEL", "gemini-2.5-flash-lite")
MAX_TOKENS = 128000

TEMPERATURE = 0.4
TOP_P = 0.95


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
TEXT_BASED_PROMPT = PROMPTS["text_based"]
INCREMENTAL_PROMPT = PROMPTS["incremental"]  # Legacy, kept for compatibility
TIMELINE_CURATION_PROMPT = PROMPTS["timeline_curation"]
TIMELINE_CONSOLIDATION_PROMPT = PROMPTS["timeline_consolidation"]
CHUNK_SUMMARY_PROMPT = PROMPTS["chunk_summary"]
CONSOLIDATE_DESC_PROMPT = PROMPTS["consolidate_description"]
CONSOLIDATE_SUMMARY_PROMPT = PROMPTS["consolidate_summary"]

# Global client reference for curation (set in main)
_ai_client = None
_book_title = None
_current_pct = 0


# === Text Sanitization Patterns ===
# Remove phrases that indicate incremental processing
INCREMENTAL_MARKERS = [
    # Ordered by length (longest first) to prevent partial matches
    "在新文本中，",
    "在新文本中",
    "新文本中，",
    "新文本中",
    "在新片段中",
    "在本段中，",
    "在本段中",
    "在此段中",
    "新情节中",
    "新文本",
    "新片段",
]


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

    DESC_LIMIT = 200
    SUMMARY_LIMIT = 300

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

    def __init__(self, book_title="", author="", author_bio=""):
        self.book_title = book_title
        self.author = author
        self.author_bio = author_bio

        # Characters/locations: name -> list of description fragments
        self.characters = {}  # {name: {"descriptions": [...], "consolidated": None}}
        self.locations = {}  # {name: {"descriptions": [...], "consolidated": None}}

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
            if name not in self.characters:
                self.characters[name] = {"descriptions": [], "consolidated": None}
            if desc:
                self.characters[name]["descriptions"].append(desc)
                self.characters[name]["consolidated"] = None  # Invalidate

        # === LOCATIONS ===
        for loc in chunk_data.get("locations", []):
            name = loc.get("name", "").strip()
            if not name:
                continue
            desc = loc.get("description", "").strip()
            if name not in self.locations:
                self.locations[name] = {"descriptions": [], "consolidated": None}
            if desc:
                self.locations[name]["descriptions"].append(desc)
                self.locations[name]["consolidated"] = None  # Invalidate

        # === THEMES ===
        for theme in chunk_data.get("themes", []):
            if theme:
                self.themes.add(theme)

        # === EVENTS ===
        for event in chunk_data.get("events", []):
            if event.get("event"):
                self.events.append(event)

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
        chars_to_consolidate = []
        locs_to_consolidate = []

        for name, data in self.characters.items():
            # Consolidate whenever we have 2+ description fragments
            if data["consolidated"] is None and len(data["descriptions"]) > 1:
                combined = " ".join(data["descriptions"])
                chars_to_consolidate.append((name, combined))

        for name, data in self.locations.items():
            if data["consolidated"] is None and len(data["descriptions"]) > 1:
                combined = " ".join(data["descriptions"])
                locs_to_consolidate.append((name, combined))

        return chars_to_consolidate, locs_to_consolidate

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
        """Convert to final output JSON format."""

        # Build character list
        characters = []
        for name, data in self.characters.items():
            if data["consolidated"]:
                desc = data["consolidated"]
            elif data["descriptions"]:
                desc = " ".join(data["descriptions"])
                if len(desc) > self.DESC_LIMIT:
                    desc = desc[: self.DESC_LIMIT - 3] + "..."
            else:
                desc = ""
            characters.append({"name": name, "description": desc})

        # Build location list
        locations = []
        for name, data in self.locations.items():
            if data["consolidated"]:
                desc = data["consolidated"]
            elif data["descriptions"]:
                desc = " ".join(data["descriptions"])
                if len(desc) > self.DESC_LIMIT:
                    desc = desc[: self.DESC_LIMIT - 3] + "..."
            else:
                desc = ""
            locations.append({"name": name, "description": desc})

        # Build summary (join parts, truncate if needed)
        summary = " ".join(self.summary_parts)
        if len(summary) > self.SUMMARY_LIMIT:
            summary = summary[: self.SUMMARY_LIMIT - 3] + "..."

        # Build timeline from events (limit to 20)
        timeline = []
        for i, event in enumerate(self.events[:20]):
            timeline.append(
                {
                    "sequence": i + 1,
                    "event": event.get("event", ""),
                    "arc_type": event.get("arc_type", "rising"),
                }
            )

        return {
            "book_title": self.book_title,
            "author": self.author,
            "author_bio": self.author_bio,
            "summary": summary,
            "characters": characters,
            "locations": locations,
            "themes": list(self.themes)[:8],
            "timeline": timeline,
            "analysis_progress": progress_pct,
        }

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
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=500,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        if content:
            content = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            return result.get("description", combined_desc[:200])
    except Exception as e:
        print(f"    [Consolidation Error] {name}: {e}")

    # Fallback: truncate
    return combined_desc[:197] + "..." if len(combined_desc) > 200 else combined_desc


# === Data Cleanup Function ===
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

    # === ENFORCE TIMELINE LIMIT (max 20) via AI Curation ===
    timeline = data.get("timeline", [])
    if len(timeline) > 20:
        print(
            f"  [Curation] Timeline has {len(timeline)} entries, asking AI to consolidate..."
        )
        curated = curate_timeline_with_ai(timeline, data.get("pending_events", []))
        if curated and len(curated) <= 20:
            data["timeline"] = curated
            print(f"  [Curation] Timeline consolidated to {len(curated)} entries")
        else:
            # Fallback: naive priority-based truncation
            print(
                f"  [Curation] AI curation failed or still too long, using fallback truncation"
            )
            arc_priority = {
                "climax": 0,
                "resolution": 1,
                "falling": 2,
                "setup": 3,
                "rising": 4,
            }

            def sort_key(event):
                arc = event.get("arc_type", "rising")
                priority = arc_priority.get(arc, 4)
                seq = event.get("sequence", 0)
                if priority >= 3:
                    return (priority, -seq)
                return (priority, seq)

            sorted_timeline = sorted(timeline, key=sort_key)
            kept = sorted_timeline[:20]
            kept.sort(key=lambda e: e.get("sequence", 0))
            for i, event in enumerate(kept):
                event["sequence"] = i + 1
            data["timeline"] = kept
            print(f"  [Cleanup] Timeline truncated to 20 entries")

    # === ENFORCE PENDING_EVENTS LIMIT (max 10) ===
    pending = data.get("pending_events", [])
    if len(pending) > 10:
        # Keep most recent (highest book_position_pct)
        pending.sort(key=lambda e: e.get("book_position_pct", 0), reverse=True)
        data["pending_events"] = pending[:10]
        print(f"  [Cleanup] Pending events truncated from {len(pending)} to 10 entries")

    # Removed naive truncation for summary
    # if "summary" in data and len(data["summary"]) > 500:
    #    data["summary"] = data["summary"][:497] + "..."

    return data


# === AI Timeline Curation ===
def curate_timeline_with_ai(timeline, pending_events=None):
    """Ask AI to consolidate and merge timeline events to stay within limit."""
    global _ai_client, _book_title, _current_pct

    if _ai_client is None or _book_title is None:
        return None

    timeline_count = len(timeline)
    if timeline_count <= 20:
        return timeline

    timeline_json = json.dumps(timeline, ensure_ascii=False)

    # Use the consolidation prompt which has specific merge examples
    # Format: title, progress_pct, timeline_count, timeline_json
    consolidation_prompt = TIMELINE_CONSOLIDATION_PROMPT % (
        _book_title,
        _current_pct,
        timeline_count,
        timeline_json,
    )

    try:
        response = _ai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": consolidation_prompt},
            ],
            temperature=TEMPERATURE,
            top_p=TOP_P,
            max_tokens=MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        if content:
            content = content.replace("```json", "").replace("```", "").strip()
            curated_result = json.loads(content)

            # Handle both array and object responses
            if isinstance(curated_result, list):
                curated_timeline = curated_result
            elif isinstance(curated_result, dict) and "timeline" in curated_result:
                curated_timeline = curated_result["timeline"]
            else:
                return None

            # Renumber sequences
            for i, event in enumerate(curated_timeline):
                event["sequence"] = i + 1

            return curated_timeline
    except Exception as e:
        print(f"  [Curation] AI curation error: {e}")
        return None

    return None


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
    if len(sys.argv) < 2:
        print("Usage: python xray_generator.py <epub_file_path>")
        print("\nConfiguration via environment variables:")
        print("  XRAY_API_BASE  - API endpoint (default: http://localhost:8080/v1)")
        print("  XRAY_API_KEY   - Your API key")
        print("  XRAY_MODEL     - Model name (default: gemini-2.5-flash-lite)")
        return

    target_path = sys.argv[1]

    if not os.path.exists(target_path):
        print(f"File not found: {target_path}")
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
        # Cleanup old partial analysis files to ensure a fresh start
        print("Cleaning up old analysis files...")
        for filename in os.listdir(output_dir):
            if filename.endswith(".json") and "%" in filename:
                try:
                    os.remove(os.path.join(output_dir, filename))
                    print(f"Deleted {filename}")
                except OSError as e:
                    print(f"Error deleting {filename}: {e}")

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

    # Resume Logic - for now, we start fresh (resume not supported in new architecture)
    # TODO: Add resume support by serializing MasterData
    start_step = 1

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
                    max_tokens=MAX_TOKENS,
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
                    f"    ✓ {name}: {len(combined_desc)} -> {len(consolidated)} chars"
                )

            for name, combined_desc in locs_to_consolidate:
                consolidated = consolidate_description_with_ai(
                    client, "location", name, combined_desc
                )
                master.apply_consolidation("location", name, consolidated)
                print(
                    f"    ✓ {name}: {len(combined_desc)} -> {len(consolidated)} chars"
                )

        # === STEP 4: Save progress ===
        output_data = master.to_output_json(pct)
        filename = os.path.join(output_dir, f"{pct}%.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"  Saved {filename}")

    # === Final Timeline Curation at 100% ===
    final_data = master.to_output_json(100)
    if len(master.events) > 20:
        print(
            f"\n=== Running Final Timeline Curation ({len(master.events)} events -> 20) ==="
        )

        events_json = json.dumps(master.events, ensure_ascii=False)
        timeline_json = json.dumps(final_data["timeline"], ensure_ascii=False)
        curation_prompt = TIMELINE_CURATION_PROMPT % (title, events_json, timeline_json)

        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": curation_prompt},
                ],
                temperature=TEMPERATURE,
                top_p=TOP_P,
                max_tokens=MAX_TOKENS,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if content:
                content = content.replace("```json", "").replace("```", "").strip()
                try:
                    curated_result = json.loads(content)
                    if (
                        isinstance(curated_result, dict)
                        and "timeline" in curated_result
                    ):
                        final_data["timeline"] = curated_result["timeline"]
                        print(
                            f"  Timeline curated to {len(final_data['timeline'])} events"
                        )
                except json.JSONDecodeError as e:
                    print(f"  Warning: Failed to parse curation response: {e}")
        except Exception as e:
            print(f"  Warning: Timeline curation failed: {e}")

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
