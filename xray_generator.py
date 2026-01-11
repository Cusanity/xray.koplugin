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
INCREMENTAL_PROMPT = PROMPTS["incremental"]
TIMELINE_CURATION_PROMPT = PROMPTS["timeline_curation"]
TIMELINE_CONSOLIDATION_PROMPT = PROMPTS["timeline_consolidation"]

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
        # Truncate description to 200 chars if needed
    # Cap character list to 50 items (safety net)
    if len(data.get("characters", [])) > 50:
        data["characters"] = data["characters"][:50]

    # Remove type from locations and sanitize descriptions
    for loc in data.get("locations", []):
        loc.pop("type", None)
        if "description" in loc:
            loc["description"] = sanitize_text(loc["description"])

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

    def get_text(self):
        """Extracts text from all spine items in reading order."""
        try:
            with zipfile.ZipFile(self.epub_path) as z:
                # 1. Find OPF file path
                txt = z.read("META-INF/container.xml")
                root = ET.fromstring(txt)
                opf_path = root.find(".//n:rootfile", self.ns).attrib["full-path"]

                # 2. Read OPF
                opf_data = z.read(opf_path)
                opf_root = ET.fromstring(opf_data)

                # Extract Metadata (Title/Author)
                title = "Unknown Title"
                author = "Unknown Author"

                metadata = opf_root.find(".//{http://www.idpf.org/2007/opf}metadata")
                if metadata is not None:
                    t = metadata.find(".//{http://purl.org/dc/elements/1.1/}title")
                    c = metadata.find(".//{http://purl.org/dc/elements/1.1/}creator")
                    if t is not None:
                        title = t.text
                    if c is not None:
                        author = c.text

                print(f"Book: {title} by {author}")

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

                # 4. Extract Text
                full_text = []
                opf_dir = os.path.dirname(opf_path)

                for item_id in spine:
                    if item_id in manifest:
                        file_path = os.path.join(opf_dir, manifest[item_id]).replace(
                            "\\", "/"
                        )
                        try:
                            content = z.read(file_path).decode("utf-8")
                            text = self.html_to_text(content)
                            if text.strip():
                                full_text.append(text)
                        except KeyError:
                            print(f"Warning: File {file_path} not found in archive.")
                        except Exception as e:
                            print(f"Error extracting {file_path}: {e}")

                return "\n".join(full_text), title, author
        except Exception as e:
            print(f"Fatal error reading EPUB: {e}")
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
    full_text, title, author = reader.get_text()

    if not full_text:
        print("Failed to extract text.")
        return

    print(f"Total text length: {len(full_text)} characters")
    print(f"Book Title: {title}")

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

    # Initialize OpenAI Client
    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY)

    # Set global references for AI curation during cleanup
    global _ai_client, _book_title, _current_pct
    _ai_client = client
    _book_title = title

    # Chunking Logic (fixed 10k character chunks, but minimum 10 chunks for small books)
    MIN_CHUNKS = 10
    DEFAULT_CHUNK_SIZE = 10000
    total_len = len(full_text)

    if total_len < DEFAULT_CHUNK_SIZE * MIN_CHUNKS:
        # Small book: force 10 chunks
        total_chunks = MIN_CHUNKS
        chunk_size = math.ceil(total_len / MIN_CHUNKS)
        print(
            f"Small book detected. Will process in {total_chunks} chunks ({chunk_size} chars each)"
        )
    else:
        # Normal: use 10k chunks
        chunk_size = DEFAULT_CHUNK_SIZE
        total_chunks = math.ceil(total_len / chunk_size)
        print(f"Will process in {total_chunks} chunks ({chunk_size} chars each)")

    current_data = None

    # Resume Logic (check for existing N%.json in output_dir)
    start_step = 1
    for i in range(total_chunks, 0, -1):
        # Calculate percentage for this chunk
        end_idx = min(i * chunk_size, total_len)
        pct = math.ceil(end_idx * 100 / total_len)
        filename = os.path.join(output_dir, f"{pct}%.json")
        if os.path.exists(filename):
            print(f"Found existing progress at {pct}%. Resuming from chunk {i + 1}...")
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    current_data = json.load(f)
                start_step = i + 1
                break
            except json.JSONDecodeError:
                print(f"Warning: Corrupt JSON at {filename}, ignoring...")
                continue

    for i in range(start_step, total_chunks + 1):
        start_idx = (i - 1) * chunk_size
        end_idx = min(i * chunk_size, total_len)

        # If this is the second-to-last chunk combine them into one
        if i == total_chunks - 1:
            end_idx = total_len  # Extend to include the final chunk
            print("Combining last 2 chunks")

        chunk_text = full_text[start_idx:end_idx]

        if not chunk_text.strip():
            print(f"Skipping empty chunk ({i}/{total_chunks})")
            continue

        print(
            f"\n=== Processing Chunk {i}/{total_chunks} ({start_idx}-{end_idx}/{total_len}) ==="
        )

        # Prepare Prompt
        # Calculate reading progress percentage
        pct = math.ceil(end_idx * 100 / total_len)

        if current_data is None:
            # First Chunk - params: title, author, reading_progress, chunk_text
            prompt = TEXT_BASED_PROMPT % (title, author, pct, chunk_text)
        else:
            # Incremental - params: title, author, reading_progress, existing_json, chunk_text, final_progress
            existing_json = json.dumps(current_data, ensure_ascii=False)
            prompt = INCREMENTAL_PROMPT % (
                title,
                author,
                pct,
                existing_json,
                chunk_text,
                pct,
            )

        # Retry Loop
        MAX_RETRIES = 2
        for attempt in range(MAX_RETRIES + 1):
            print(
                f"Sending request (Attempt {attempt + 1})... (Prompt len: {len(prompt)})"
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
                # Check for empty response (safety filter, etc.)
                if content is None:
                    print(
                        f"\n⚠ Safety filter triggered at {pct}%. Skipping this chunk..."
                    )
                    break  # Skip to next chunk
                # Clean markdown if present
                content = content.replace("```json", "").replace("```", "").strip()

                try:
                    new_data = json.loads(content)
                    # Set global progress for consolidation prompt
                    _current_pct = pct
                    # Clean up unrequested fields and enforce limits
                    new_data = cleanup_data(new_data, pct)
                    current_data = new_data
                    # Use percentage calculated earlier
                    current_data["analysis_progress"] = pct

                    # Save progress
                    filename = os.path.join(output_dir, f"{pct}%.json")
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(current_data, f, ensure_ascii=False, indent=2)
                    print(f"Saved {filename}")
                    break  # Success, exit retry loop

                except json.JSONDecodeError as e:
                    print(f"Error parsing JSON response: {e}")
                    print(f"Raw response: {content[:200]}...")
                    if attempt < MAX_RETRIES:
                        print("Retrying...")
                        continue
                    else:
                        print("Max retries reached. Aborting.")
                        sys.exit(1)

            except Exception as e:
                print(f"API Request Failed: {e}")
                if attempt < MAX_RETRIES:
                    print("Retrying...")
                    continue
                else:
                    sys.exit(1)

    # === Final Timeline Curation at 100% ===
    if current_data and current_data.get("analysis_progress") == 100:
        pending_events = current_data.get("pending_events", [])
        timeline = current_data.get("timeline", [])

        # Only run curation if there are pending events to process
        if pending_events:
            print("\n=== Running Final Timeline Curation ===")

            pending_json = json.dumps(pending_events, ensure_ascii=False)
            timeline_json = json.dumps(timeline, ensure_ascii=False)
            curation_prompt = TIMELINE_CURATION_PROMPT % (
                title,
                pending_json,
                timeline_json,
            )

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
                        # The curation prompt returns an array, but json_object mode wraps it
                        curated_result = json.loads(content)

                        # Handle both array and object responses
                        if isinstance(curated_result, list):
                            curated_timeline = curated_result
                        elif (
                            isinstance(curated_result, dict)
                            and "timeline" in curated_result
                        ):
                            curated_timeline = curated_result["timeline"]
                        else:
                            curated_timeline = timeline  # Fallback

                        # Update the data
                        current_data["timeline"] = curated_timeline
                        current_data["pending_events"] = []  # Clear pending

                        # Save final curated version
                        filename = os.path.join(output_dir, "100%.json")
                        with open(filename, "w", encoding="utf-8") as f:
                            json.dump(current_data, f, ensure_ascii=False, indent=2)
                        print(f"Saved curated timeline to {filename}")

                    except json.JSONDecodeError as e:
                        print(f"Warning: Failed to parse curation response: {e}")

            except Exception as e:
                print(f"Warning: Timeline curation failed: {e}")
        else:
            print("\nNo pending events to curate. Timeline is complete.")

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
