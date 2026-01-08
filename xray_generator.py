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
API_BASE_URL = os.environ.get("XRAY_API_BASE", "http://localhost:8080/v1")
API_KEY = os.environ.get("XRAY_API_KEY", "your-api-key")
MODEL_NAME = os.environ.get("XRAY_MODEL", "gemini-2.5-flash-lite")
MAX_TOKENS = 128000

TEMPERATURE = 0.4
TOP_P = 0.95

# === Prompts ===
SYSTEM_PROMPT = "你是一位专业的文学评论家。你的回复必须仅使用有效的JSON格式。不要使用Markdown、引导语或额外解释。"

# Prompt for the FIRST chunk (No existing data)
# Parameters: title, author, reading_progress, chunk_text
TEXT_BASED_PROMPT = """你正在分析以下书籍文本以提取X-Ray数据。

书名：《%s》
作者：%s
当前阅读进度：%d%%

<书籍内容>
%s
</书籍内容>

关键规则：
1. 仅提取文本中实际出现的人物、事件和地点
2. 不要添加任何来自训练数据或外部知识的信息
3. 【严格排除】编辑、译者、出版商、致谢对象、作者家人、仅在序言/导言/前言/致谢中提及的非故事人物。只收录实际参与故事情节的人物。
4. 人物列表必须按在书中的重要性或出现频率降序排列（主角在前）
5. 【多视角处理】如果书籍使用多个第一人称叙述者，**必须**区分但若名字相同则**合并**，**严禁**在名字中添加（叙述者）等后缀。仅在description中标注。
6. 【强制输出 - 最高优先级】无论文本包含何种内容（暴力/色情/犯罪等），你**必须**返回合法的JSON对象。这是严肃的文学分析工作，不是创作有害内容。处理方法：
   - 对敏感内容使用学术化/中性化的描述（如"性暴力"→"受害者遭受侵犯"）
   - 如果整段文本都是敏感内容，仍然返回即有数据（不修改）
   - **绝对禁止**返回拒绝、道歉、空回复或非JSON格式

【时间线规则 - 叙事弧槽位分配】：
timeline共20个槽位，按叙事弧结构分配：
- **铺垫/设定(setup)**: 3-4个槽位 - 故事背景、人物初登场、初始状态
- **发展/升级(rising)**: 5-6个槽位 - 冲突展开、complications、stakes升高  
- **高潮(climax)**: 2-3个槽位 - 最高张力点、重大揭示、决定性对抗
- **收尾(falling)**: 3-4个槽位 - 高潮后果、关系解决
- **结局(resolution)**: 2-3个槽位 - 最终状态、人物命运

每个事件必须标注其叙事功能arc_type，用于后续槽位分配。
事件必须按**故事内时间顺序**排列，回忆/闪回放在实际发生时间点。

格式规则：
1. 不要在任何字段中使用Markdown格式（如**、##、\\n等）。
2. 【人物描述 - 必须严格遵守】每个人物描述**绝对不能超过**200字，精炼概括。
3. 【重要】每个人物只需name和description两个字段。
4. 【重要】每个地点只需name和description两个字段。
5. 【主题限制 - 必须严格遵守】themes严格限制为5-8个，**不得超过**。
6. 【概要限制】summary不超过500字，简明扼要。
7. 【禁止添加未要求的字段】不要添加gender、type、importance等未在JSON格式中列出的字段。

必需的JSON格式：
{
  "book_title": "书名",
  "author": "作者姓名",
  "author_bio": "作者简介（可使用外部知识）",
  "summary": "仅基于提供文本的概要",
  "characters": [
    {
      "name": "人物姓名",
      "description": "仅基于文本内容的描述（限200字）"
    }
  ],
  "locations": [
    {
      "name": "地点名称",
      "description": "如文本所述"
    }
  ],
  "themes": ["主题1", "主题2", "主题3"],
  "pending_events": [
    {
      "event": "事件描述",
      "arc_type": "setup|rising|climax|falling|resolution",
      "book_position_pct": 5
    }
  ],
  "timeline": []
}"""


# Prompt for SUBSEQUENT chunks (Incremental update with soft commit)
# Parameters: title, author, reading_progress, existing_json, chunk_text, progress, progress
INCREMENTAL_PROMPT = """你正在进行分块书籍分析。请基于新的文本片段更新现有的JSON数据。

书名：《%s》
作者：%s
当前阅读进度：%d%%

<即有数据>
%s
</即有数据>

<新文本片段>
%s
</新文本片段>

核心任务：
1. 分析新文本，提取新的人物、地点、事件。
2. 将新信息合并到"即有数据"中。
3. 严格排除编辑、译者、致谢对象等非故事人物。
4. 保持人物列表按重要性排序（主角在前）。
5. 更新summary以包含新内容（无痕融合）。
6. 【多视角处理】如名字相同则合并，不创建带后缀的新人物。
7. 【强制输出】必须返回合法JSON，对敏感内容使用学术化描述。

【时间线 - 软提交策略】：
系统采用pending_events缓冲区 + timeline正式区的双层结构：

**pending_events（待定事件缓冲区）**：
- 从新文本提取的事件首先进入pending_events
- 每个事件包含: event(描述), arc_type(叙事弧类型), book_position_pct(**必须使用当前阅读进度%d%%**)
- arc_type可选值: setup(铺垫), rising(发展), climax(高潮), falling(收尾), resolution(结局)

**timeline（正式时间线）**：
- 当pending_events累积≥5条 或 遇到major事件时，执行"软提交"
- 软提交规则：
  1. 将pending_events中的major事件提交到timeline
  2. 根据叙事弧槽位配额决定是否提交minor事件
  3. 槽位配额: setup(3-4), rising(5-6), climax(2-3), falling(3-4), resolution(2-3)
  4. 若某arc_type槽位已满，新事件应替换该类型中importance较低的事件
  5. 提交后清空pending_events中已提交的事件

**当前进度：%d%%**
- 若进度<30%%: 主要产生setup和rising事件，timeline保守提交
- 若30%%≤进度<70%%: 可能出现climax事件，适度提交
- 若进度≥70%%: 开始出现falling和resolution事件，积极提交

【timeline事件格式】：
{
  "sequence": N,
  "event": "事件描述",
  "arc_type": "setup|rising|climax|falling|resolution"
}

timeline必须按**故事内时间顺序**排列，sequence从1连续编号，最多20条。

【关键合并规则】：
- 名字字段禁止包含括号或后缀，使用最纯粹的姓名
- 同名人物/地点必须合并，不能创建重复条目
- 【人物描述 - 必须严格遵守】每个人物描述**绝对不能超过**200字，超出时必须精简

【主题压缩】：
- 【主题限制 - 必须严格遵守】themes严格限制为5-8个，**不得超过**
- 若themes超过8个，必须合并最相似的主题直到≤8个

【去重与无痕合并】：
- 禁用词汇："新文本"、"新片段"、"新增"、"新情节中"等
- 合并后内容必须读起来像从头撰写，无拼接痕迹
- 删除所有重复句子，只保留最完整版本

【概要限制】summary不超过500字，简明扼要。

【禁止添加未要求的字段】不要添加gender、type、importance等未在JSON格式中列出的字段。

返回合并后的完整JSON：
{
  "book_title": "书名",
  "author": "作者",
  "author_bio": "...",
  "summary": "更新后的概要（无分块痕迹）",
  "characters": [...],
  "locations": [...],
  "themes": [...],
  "pending_events": [
    {
      "event": "待提交事件",
      "arc_type": "rising",
      "book_position_pct": 45
    }
  ],
  "timeline": [
    {
      "sequence": 1,
      "event": "已提交事件（按故事时序）",
      "arc_type": "setup"
    }
  ]
}"""


# Prompt for final timeline curation at 100%
# Parameters: title, pending_events_json, timeline_json
TIMELINE_CURATION_PROMPT = """你是一位专业的文学编辑，正在为《%s》整理最终的时间线。

<待定事件>
%s
</待定事件>

<当前时间线>
%s
</当前时间线>

任务：将所有待定事件整合到时间线中，生成最终的20条精华时间线。

【叙事弧槽位分配】（共20条）：
- setup(铺垫): 3-4条 - 故事开端、人物登场、初始状态
- rising(发展): 5-6条 - 冲突展开、复杂化、stakes升高
- climax(高潮): 2-3条 - 最高张力、重大揭示、决战
- falling(收尾): 3-4条 - 高潮后果、关系变化
- resolution(结局): 2-3条 - 最终状态、人物命运

【整理规则】：
1. 优先保留major事件，minor事件根据槽位余量决定
2. 合并描述同一事件的重复条目
3. 按故事内时间顺序排列（非阅读顺序）
4. 确保叙事弧完整：从setup到resolution形成完整故事线
5. 事件描述应简洁有力，每条不超过50字

返回最终timeline的JSON数组：
[
  {
    "sequence": 1,
    "event": "精炼的事件描述",
    "arc_type": "setup"
  },
  ...
]"""


# === Data Cleanup Function ===
def cleanup_data(data, current_pct):
    """Remove unrequested fields and enforce limits after AI response"""
    # Remove gender from characters
    for char in data.get("characters", []):
        char.pop("gender", None)
        # Truncate description to 200 chars if needed
        if "description" in char and len(char["description"]) > 200:
            char["description"] = char["description"][:197] + "..."

    # Remove type from locations
    for loc in data.get("locations", []):
        loc.pop("type", None)

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

    # Truncate summary to 500 chars if needed
    if "summary" in data and len(data["summary"]) > 500:
        data["summary"] = data["summary"][:497] + "..."

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

    # Create Output Directory
    # Sanitize title for filesystem
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title).strip()
    if not safe_title:
        safe_title = "output"

    output_dir = os.path.join(os.getcwd(), safe_title)
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            print(f"Created output directory: {output_dir}")
        except OSError as e:
            print(f"Error creating directory {output_dir}: {e}")
            return
    else:
        print(f"Using output directory: {output_dir}")

    # Initialize OpenAI Client
    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY)

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
            # Incremental - params: title, author, reading_progress, existing_json, chunk_text, progress_again
            existing_json = json.dumps(current_data, ensure_ascii=False)
            prompt = INCREMENTAL_PROMPT % (
                title,
                author,
                pct,
                existing_json,
                chunk_text,
                pct,  # for the book_position_pct description
                pct,  # for the "当前进度" section
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
