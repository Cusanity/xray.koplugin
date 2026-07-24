"""
Master data model for X-Ray Generator.

Handles accumulating, merging, deduplicating, and serializing X-Ray analysis data.
"""

from __future__ import annotations

import re
from typing import Any

from openai import OpenAI

from text_utils import (
    META_THEMES,
    normalize_character_name,
    normalize_for_dedup,
    normalize_location_name,
    sanitize_text,
    t2s_convert,
)


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

    # Timeline — rebuilt from character events in to_output_json

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

        # Prompt strings — set by xray_generator before use
        self._system_prompt: str = ""
        self._consolidate_desc_prompt: str = ""
        self._consolidate_summary_prompt: str = ""

    def set_prompts(
        self,
        system_prompt: str,
        consolidate_desc_prompt: str,
        consolidate_summary_prompt: str,
    ) -> None:
        """Set prompt templates needed for AI consolidation."""
        self._system_prompt = system_prompt
        self._consolidate_desc_prompt = consolidate_desc_prompt
        self._consolidate_summary_prompt = consolidate_summary_prompt

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
            simplified_name = normalize_for_dedup(name)

            if simplified_name not in self.characters:
                self.characters[simplified_name] = {
                    "display_name": simplified_name,
                    "descriptions": [],
                    "historic_descriptions": [],
                    "events": [],
                    "consolidated": None,
                }

            if self.characters[simplified_name]["consolidated"]:
                self.characters[simplified_name]["descriptions"].insert(
                    0, self.characters[simplified_name]["consolidated"]
                )
                self.characters[simplified_name]["consolidated"] = None

            if desc:
                desc = t2s_convert(desc)
                self.characters[simplified_name]["descriptions"].append(desc)

            if "events" not in self.characters[simplified_name]:
                self.characters[simplified_name]["events"] = []

            for event in char.get("events", []):
                if event.get("event") and "absolute_percent" in event:
                    raw_event = t2s_convert(event["event"])
                    clean_event = re.sub(
                        r"\s*[(\uff08]\d+(?:\.\d+)?%[)\uff09]$", "", raw_event
                    )

                    entry: dict[str, Any] = {"event": clean_event}
                    if "xref" in event:
                        entry["xref"] = event["xref"]
                    # anchor is a verbatim source-text quote; preserve it unchanged
                    # (must NOT be passed through t2s_convert)
                    raw_anchor = event.get("anchor", "").strip()
                    if raw_anchor:
                        entry["anchor"] = raw_anchor
                    self.characters[simplified_name]["events"].append(entry)

    def _merge_locations(self, locations: list[dict[str, Any]]) -> None:
        for loc in locations:
            name = loc.get("name", "").strip()
            if not name:
                continue
            desc = loc.get("description", "").strip()
            simplified_name = normalize_location_name(name)

            if simplified_name not in self.locations:
                self.locations[simplified_name] = {
                    "display_name": simplified_name,
                    "descriptions": [],
                    "historic_descriptions": [],
                    "consolidated": None,
                }

            if self.locations[simplified_name]["consolidated"]:
                self.locations[simplified_name]["descriptions"].insert(
                    0, self.locations[simplified_name]["consolidated"]
                )
                self.locations[simplified_name]["consolidated"] = None

            if desc:
                desc = t2s_convert(desc)
                self.locations[simplified_name]["descriptions"].append(desc)

    def _merge_themes(self, themes: list[str]) -> None:
        for theme in themes:
            # AI sometimes returns theme objects instead of plain strings.
            if isinstance(theme, dict):
                theme = theme.get("theme") or theme.get("name") or ""
            if theme and isinstance(theme, str) and theme not in META_THEMES:
                self.themes.add(theme)

    def _merge_events(self, events: list[Any]) -> None:
        # Legacy: Global event parsing is disabled to save tokens
        # We now build timeline from character events
        pass

    def _merge_summary(self, summary) -> None:
        # AI occasionally returns summary as a dict (e.g. {"description": "..."})
        # instead of a plain string — normalise before appending.
        if isinstance(summary, dict):
            summary = (
                summary.get("description")
                or summary.get("text")
                or summary.get("summary") or ""
            )
        summary = (summary or "").strip()
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
        from ai_client import consolidate_summary_with_ai

        if not self.summary_parts:
            return

        combined = " ".join(self.summary_parts)
        print(f"  [Summary] Consolidating {len(combined)} chars...")
        consolidated = consolidate_summary_with_ai(
            client, self.book_title, combined,
            self._system_prompt, self._consolidate_summary_prompt,
        )
        self.summary_parts = [consolidated]
        print(f"  [Summary] Consolidated to {len(consolidated)} chars")

    def get_combined_summary(self) -> str:
        """Return all accumulated summary parts joined into one string."""
        return " ".join(self.summary_parts)

    def apply_summary_consolidation(self, consolidated_summary: str) -> None:
        """Replace accumulated summary parts with a single consolidated summary."""
        if consolidated_summary:
            self.summary_parts = [consolidated_summary]

    def apply_consolidation(
        self, entity_type: str, name: str, consolidated_desc: str, current_pct: int = 0
    ) -> None:
        """Apply AI-consolidated description and save it to history with percent."""
        target = self.characters if entity_type == "character" else self.locations
        if name in target:
            target[name]["consolidated"] = consolidated_desc
            target[name]["descriptions"] = []
            if "historic_descriptions" not in target[name]:
                target[name]["historic_descriptions"] = []
            target[name]["historic_descriptions"].append(
                {"percent": current_pct, "text": consolidated_desc}
            )

    def to_output_json(self, progress_pct: int) -> dict[str, Any]:
        """Convert to final output JSON format with progressive descriptions."""

        def score_importance(data: dict[str, Any]) -> int:
            historic = data.get("historic_descriptions", [])
            if historic:
                total_len = sum(len(d.get("text", "")) for d in historic)
                return total_len + len(historic) * 50
            desc = data.get("consolidated") or " ".join(data.get("descriptions", []))
            return len(desc)

        # Characters
        char_items = []
        for key, data in self.characters.items():
            display_name = data.get("display_name", key)

            historic = data.get("historic_descriptions", [])
            if not historic and (data.get("consolidated") or data.get("descriptions")):
                current_text = data.get("consolidated") or " ".join(
                    data.get("descriptions", [])
                )
                if current_text:
                    historic = [{"percent": progress_pct, "text": current_text}]

            char_items.append(
                {
                    "name": display_name,
                    "descriptions": historic,
                    "events": sorted(
                        data.get("events", []), key=_xref_sort_key
                    ),
                    "_score": score_importance(data),
                }
            )

        char_items.sort(key=lambda x: x["_score"], reverse=True)
        characters = [
            {
                "name": c["name"],
                "descriptions": c["descriptions"],
                "events": c["events"],
            }
            for c in char_items
        ]

        # Locations
        loc_items = []
        for key, data in self.locations.items():
            display_name = data.get("display_name", key)

            historic = data.get("historic_descriptions", [])
            if not historic and (data.get("consolidated") or data.get("descriptions")):
                current_text = data.get("consolidated") or " ".join(
                    data.get("descriptions", [])
                )
                if current_text:
                    historic = [{"percent": progress_pct, "text": current_text}]

            loc_items.append(
                {
                    "name": display_name,
                    "descriptions": historic,
                    "_score": score_importance(data),
                }
            )

        loc_items.sort(key=lambda x: x["_score"], reverse=True)
        locations = [
            {"name": loc["name"], "descriptions": loc["descriptions"]}
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
                pct = event.get("percent", 0)
                text = event.get("event", "").strip()
                if text:
                    entry: dict[str, Any] = {"event": text, "character": char_name}
                    if "xref" in event:
                        entry["xref"] = event["xref"]
                    if event.get("anchor"):
                        entry["anchor"] = event["anchor"]
                    all_events.append(entry)

        all_events.sort(key=_xref_sort_key)

        for i, event in enumerate(all_events):
            entry: dict[str, Any] = {
                "sequence": i + 1,
                "event": event["event"],
                "character": event.get("character", ""),
            }
            if "xref" in event:
                entry["xref"] = event["xref"]
            if event.get("anchor"):
                entry["anchor"] = event["anchor"]
            timeline.append(entry)

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
# Character Deduplication
# =============================================================================


def _xref_sort_key(event: dict[str, Any]) -> int:
    """Sort key for events using spine+offset; falls back to 0 for legacy data."""
    xref = event.get("xref")
    if xref:
        return xref.get("spine", 0) * 10_000_000 + xref.get("offset", 0)
    # Legacy: fall back to percent if present, else 0
    return int(event.get("percent", 0) * 1000)


def _merge_character_data(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge descriptions and events from source to target."""
    existing_desc_texts = {d["text"] for d in target.get("descriptions", [])}
    for desc in source.get("descriptions", []):
        if desc["text"] not in existing_desc_texts:
            target.setdefault("descriptions", []).append(desc)
            existing_desc_texts.add(desc["text"])

    existing_events = {e["event"] for e in target.get("events", [])}
    for event in source.get("events", []):
        if event["event"] not in existing_events:
            target.setdefault("events", []).append(event)
            existing_events.add(event["event"])


def _sort_character_data(char: dict[str, Any]) -> None:
    """Sort descriptions and events by xref position (or percent for legacy)."""
    if "descriptions" in char:
        char["descriptions"].sort(key=lambda x: x.get("percent", 0))
    if "events" in char:
        char["events"].sort(key=_xref_sort_key)


def deduplicate_characters(data: dict[str, Any]) -> dict[str, Any]:
    """
    Deduplicate characters by merging fragments into main entries.

    Logic:
    1. Merge exact name duplicates.
    2. Identify 'Target' characters (containing '•') and 'Candidate' characters (others).
    3. Merge Candidate into Target if:
       - Candidate name is a prefix of Target name AND Target name continues with '•'
       - OR Candidate name equals Target name after replacing '·' with '•'
    4. Guard against Ambiguity: If a Candidate matches > 1 Target, DO NOT merge.
    5. Sort all character events/descriptions by percent.
    """
    characters = data.get("characters", [])
    if not characters:
        return data

    # 1. Deduplicate by exact name first
    unique_characters_map = {}
    for char in characters:
        name = char.get("name", "").strip()
        if not name:
            continue

        if name in unique_characters_map:
            _merge_character_data(unique_characters_map[name], char)
        else:
            unique_characters_map[name] = char

    # 2. Separate into targets (contain '•') and candidates
    targets = []
    others = []

    working_chars = list(unique_characters_map.values())

    for char in working_chars:
        name = char.get("name", "").strip()
        if "•" in name:
            targets.append(char)
        else:
            others.append(char)

    target_map = {t["name"]: t for t in targets}

    # 3. Identify and Perform Merges
    final_characters = list(targets)
    merged_count = 0

    for char in others:
        name = char.get("name", "").strip()

        matches = []
        for t_name, t_char in target_map.items():
            is_prefix = False
            if t_name.startswith(name):
                if len(t_name) > len(name) and t_name[len(name)] == "•":
                    is_prefix = True

            is_dot_match = name.replace("·", "•") == t_name

            if is_prefix or is_dot_match:
                matches.append(t_char)

        if len(matches) == 1:
            target = matches[0]
            print(f"  [Dedup] Merging '{name}' -> '{target['name']}'")
            _merge_character_data(target, char)
            merged_count += 1
        elif len(matches) > 1:
            print(
                f"  [Dedup] ⚠ Ambiguous: '{name}' matches "
                f"{[m['name'] for m in matches]}. Skipping merge."
            )
            final_characters.append(char)
        else:
            final_characters.append(char)

    # 4. Sort ALL character data
    for char in final_characters:
        _sort_character_data(char)

    if merged_count > 0:
        print(f"  [Dedup] Merged {merged_count} character entries.")

    data["characters"] = final_characters
    return data
