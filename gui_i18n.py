#!/usr/bin/env python3
"""Lightweight internationalization (i18n) for the X-Ray Generator GUI.

Pattern & maintenance guide
---------------------------
* **English is the source of truth.** Every user-facing string is written in
  plain English directly in the code and wrapped in :func:`tr`. The English
  text doubles as the lookup key, so the source stays readable and grep-able::

      label = QLabel(tr("Start Analysis"))

* **Values are interpolated with ``str.format``**, never f-strings, so the
  translation key stays stable regardless of the runtime value::

      self.count.setText(tr("{n} books").format(n=len(rows)))

* **Translations live in one place** — the ``_TRANSLATIONS`` registry below,
  shaped as ``{lang_code: {english_source: translated}}``. Adding a language is
  a single new dict; adding a string is a single new key. A missing key falls
  back to the English source, so the UI never breaks on an untranslated string.

* **Language selection** (highest priority first): an explicit
  :func:`set_language` call, the ``XRAY_GUI_LANG`` environment variable, then
  the host's system locale. Chinese variants collapse to ``zh`` (Simplified) or
  ``zh_TW`` (Traditional).

To add a new UI string:
    1. Write it in English inside ``tr("…")`` at the call site.
    2. Add the same English text as a key to each language dict below.

To add a new language:
    1. Add ``("code", "Native Name")`` to :data:`AVAILABLE_LANGUAGES`.
    2. Add a ``"code": { … }`` dict to :data:`_TRANSLATIONS`.
"""

from __future__ import annotations

import locale
import os

# Languages offered in the UI: (code, native display name).
AVAILABLE_LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"),
    ("zh", "简体中文"),
    ("zh_TW", "繁體中文"),
]

_DEFAULT_LANG = "zh"


# =============================================================================
# Translation registry: {lang_code: {english_source: translated_text}}
# English is the source language, so it has no dict (keys fall through to self).
# =============================================================================

_TRANSLATIONS: dict[str, dict[str, str]] = {
    # ---------------------------------------------------------------- 简体中文
    "zh": {
        # App / window / tabs
        "X-Ray Generator": "X-Ray 生成器",
        "Configuration": "配置",
        "Books": "书籍",
        "Progress": "进度",
        "Sync": "同步",
        "Results": "结果",
        "Ready": "就绪",
        # Menu
        "&File": "文件(&F)",
        "Load settings from .env": "从 .env 加载设置",
        "Save settings to .env": "保存设置到 .env",
        "Quit": "退出",
        "Create Desktop Shortcut": "创建桌面快捷方式",
        "X-Ray": "X-Ray",
        # Config tab
        "Provider & Model": "提供商与模型",
        "Refresh": "刷新",
        "Provider:": "提供商：",
        "Model:": "模型：",
        "Provider": "提供商",
        "Model": "模型",
        "OpenAI-compatible Endpoint": "OpenAI 兼容端点",
        "Cloud Provider API Keys": "云提供商 API 密钥",
        "Custom Headers": "自定义请求头",
        "One per line: Header-Name: value   (or a JSON object)":
            "每行一个：Header-Name: value（或一个 JSON 对象）",
        "Show": "显示",
        "Library & Advanced": "书库与高级",
        "Browse…": "浏览…",
        "Calibre Library:": "Calibre 书库：",
        "X-Ray Output Folder:": "X-Ray 输出文件夹：",
        "Default: <app folder>/xray": "默认：<应用文件夹>/xray",
        "Select X-Ray Output Folder": "选择 X-Ray 输出文件夹",
        "Temperature:": "温度：",
        "Language:": "语言：",
        "Apply Settings": "应用设置",
        "Load .env": "加载 .env",
        "Save .env": "保存 .env",
        "Setup Wizard": "设置向导",
        "Choose your primary provider, model, and API key.": "选择你的首选提供商、模型和 API 密钥。",
        "Set your Calibre library and output settings.": "设置 Calibre 书库与输出选项。",
        "Optional: configure KOReader device and WebDAV sync.": "可选：配置 KOReader 设备与 WebDAV 同步。",
        "Review & Apply": "检查并应用",
        "Review your choices, then click Finish to apply.": "检查你的选择，然后点击“完成”应用。",
        "Provider: {value}": "提供商：{value}",
        "Model: {value}": "模型：{value}",
        "Calibre Library: {value}": "Calibre 书库：{value}",
        "X-Ray Output Folder: {value}": "X-Ray 输出文件夹：{value}",
        "Device IP[:port]: {value}": "设备 IP[:端口]：{value}",
        "Server URL: {value}": "服务器 URL：{value}",
        "(not set)": "（未设置）",
        "(default)": "（默认）",
        "OpenAI-compatible endpoints may work without an API key.": "OpenAI 兼容端点在某些情况下可不填 API 密钥。",
        "Setup wizard completed.": "设置向导已完成。",
        "Run setup wizard?": "运行设置向导？",
        "Would you like a guided setup for provider, library, and sync settings?": "是否使用引导流程配置提供商、书库和同步设置？",
        "Desktop shortcut creation is only supported on Windows.": "桌面快捷方式创建仅支持 Windows。",
        "Desktop folder not found: {path}": "未找到桌面文件夹：{path}",
        "Failed to create desktop shortcut: {error}": "创建桌面快捷方式失败：{error}",
        "Desktop shortcut created: {path}": "已创建桌面快捷方式：{path}",
        "Not supported": "不支持",
        # Provider labels
        "OpenAI-compatible": "OpenAI 兼容",
        "Anthropic Claude": "Anthropic Claude",
        "Groq": "Groq",
        "Google Gemini": "Google Gemini",
        "DeepSeek": "DeepSeek",
        # Key field labels
        "Base URL": "基础 URL",
        "API Key": "API 密钥",
        "API Key:": "API 密钥：",
        "Enter API key\u2026": "输入 API 密钥\u2026",
        "Models Endpoint": "模型端点",
        "Claude API Key": "Claude API 密钥",
        "Groq API Key": "Groq API 密钥",
        "Gemini API Key": "Gemini API 密钥",
        "DeepSeek API Key": "DeepSeek API 密钥",
        # Books tab
        "Scan Library": "扫描书库",
        "Add EPUB…": "添加 EPUB…",
        "Cleanup Ghost Folders": "清理残留文件夹",
        "Filter by title or author…": "按书名或作者筛选…",
        "Title": "书名",
        "Author": "作者",
        "Added": "添加日期",
        "Status": "状态",
        "Select All": "全选",
        "Start Analysis": "开始分析",
        "Generate X-Ray": "生成 X-Ray",
        "Stop": "停止",
        "{n} books": "{n} 本书",
        # Progress tab
        "Overall Progress": "总体进度",
        "Idle": "空闲",
        "Batch:": "批次：",
        "Current Book": "当前书籍",
        "Book:": "书籍：",
        "Chunk:": "分块：",
        "Operation:": "操作：",
        "Stats": "统计",
        "Characters: {n}": "人物：{n}",
        "Locations: {n}": "地点：{n}",
        "Events: {n}": "事件：{n}",
        "Log": "日志",
        "Clear": "清空",
        "Save Log…": "保存日志…",
        "Auto-scroll": "自动滚动",
        "Progress:": "进度：",
        # Retry / fallback chain
        "Retry / Fallback Chain": "重试 / 回退链",
        "Models are tried top-to-bottom. Each is retried up to its "
        "retry count with the given cooldown between requests. A "
        "content-moderation refusal skips straight to the next row.":
            "模型按从上到下的顺序尝试。每个模型最多按其重试次数重试，请求之间使用指定的冷却时间。"
            "内容审核拒绝将直接跳到下一行。",
        "Retries": "重试次数",
        "Cooldown (s)": "冷却时间（秒）",
        "Input ($/M tok)": "输入（$/百万令牌）",
        "Output ($/M tok)": "输出（$/百万令牌）",
        "Add current provider/model": "添加当前提供商/模型",
        "Add row": "添加行",
        "Remove": "移除",
        "↑": "↑",
        "↓": "↓",
        "Max full-chain cycles:": "完整链最大循环次数：",
        "Wait between cycles (s):": "循环之间等待（秒）：",
        "Raise error (skip this book)": "抛出错误（跳过此书）",
        "Skip request (empty result)": "跳过请求（空结果）",
        "Exit the program": "退出程序",
        "When chain is exhausted:": "当链耗尽时：",
        "Honor server Retry-After header on rate limits": "在速率限制时遵循服务器的 Retry-After 头",
        # Concurrency / chunk-size limits
        "Concurrency & Chunk-Size Limits": "并发与分块大小限制",
        "Per-provider limits. Max Workers = parallel chunk requests. "
        "Max Chunk Size = characters sent per request. Set either to 0 "
        "(auto) to use the built-in default (Groq auto-derives chunk "
        "size from its token-per-minute budget).":
            "按提供商设置的限制。最大并发数 = 并行分块请求数。最大分块大小 = 每次请求发送的字符数。"
            "将任一项设为 0（自动）即使用内置默认值（Groq 会根据其每分钟令牌预算自动推算分块大小）。",
        "Max Workers": "最大并发数",
        "Max Chunk Size (chars)": "最大分块大小（字符）",
        "Consolidation Batch Size:": "合并批次大小：",
        "Number of entities (characters + locations + summary) merged into one consolidation request. Higher = fewer requests but larger payloads. 0 (auto) uses the built-in default.": "每次合并请求中合并的实体数（人物 + 地点 + 摘要）。数值越大，请求次数越少但数据量越大。0（自动）使用内置默认值。",
        "auto": "自动",
        # Sync tab
        "KOReader Device": "KOReader 设备",
        "Test Connection": "测试连接",
        "Choose Path": "选择路径",
        "Device IP[:port]:": "设备 IP[:端口]：",
        "Push results automatically after each book": "每本书完成后自动推送结果",
        "Push Selected Book Now": "立即推送所选书籍",
        "Push “{title}” Now": "立即推送“{title}”",
        "Push {n} Selected Books Now": "立即推送所选 {n} 本书",
        "On KOReader: X-Ray menu → Cloud Sync → Receive from PC":
            "在 KOReader 中：X-Ray 菜单 → 云同步 → 从电脑接收",
        "Status:": "状态：",
        "Enter a device IP first.": "请先输入设备 IP。",
        # WebDAV sync
        "WebDAV Cloud Sync": "WebDAV 云同步",
        "Server URL:": "服务器 URL：",
        "Username:": "用户名：",
        "Password:": "密码：",
        "Upload to WebDAV automatically after each book": "每本书完成后自动上传到 WebDAV",
        "Point KOReader and this app at the same WebDAV folder so "
        "X-Ray data syncs both ways.":
            "将 KOReader 和本应用指向同一个 WebDAV 文件夹，即可双向同步 X-Ray 数据。",
        "Not configured.": "未配置。",
        "Testing\u2026": "正在测试…",
        "Enter a WebDAV server URL first.": "请先输入 WebDAV 服务器 URL。",
        "Please test connection before browsing.": "请先测试连接，再浏览文件夹。",
        "Browse WebDAV Folders": "浏览 WebDAV 文件夹",
        "Select a folder to use as the WebDAV base URL for X-Ray sync.": "选择一个文件夹作为 X-Ray 同步的 WebDAV 基础 URL。",
        "Selected:": "已选择：",
        "Loading\u2026": "加载中…",
        "Error: {msg}": "错误：{msg}",
        "Select This Folder": "选择此文件夹",
        "Enter a WebDAV server URL and credentials before browsing.": "请先输入 WebDAV 服务器 URL 和凭据，再进行浏览。",
        "Open Local Folder": "打开本地文件夹",
        "Delete Local X-Ray": "删除本地 X-Ray",
        "No local X-Ray data found for the selected book(s).": "所选书籍没有本地 X-Ray 数据。",
        "Delete local X-Ray data for {n} book(s)?": "删除 {n} 本书的本地 X-Ray 数据？",
        "Deleted X-Ray data for {n} book(s).": "已删除 {n} 本书的 X-Ray 数据。",
        "Upload Selected to WebDAV": "上传所选到 WebDAV",
        "Download Selected from WebDAV": "从 WebDAV 下载所选",
        "Auto-refresh WebDAV status": "自动刷新 WebDAV 状态",
        "Refresh WebDAV Status": "刷新 WebDAV 状态",
        "WebDAV": "WebDAV",
        "Not uploaded": "未上传",
        "On server": "服务器上",
        "Synced": "已同步",
        "Differs": "有差异",
        "Error": "错误",
        "Checking\u2026": "检查中…",
        "No WebDAV server": "无 WebDAV 服务器",
        "Configure a WebDAV server on the Sync tab.": "请在“同步”标签页中配置 WebDAV 服务器。",
        "WebDAV is busy\u2026": "WebDAV 正忙…",
        "Uploading {n} book(s) to WebDAV\u2026": "正在上传 {n} 本书到 WebDAV…",
        "Downloading {n} book(s) from WebDAV\u2026": "正在从 WebDAV 下载 {n} 本书…",
        "[{book}] uploaded to WebDAV.": "[{book}] 已上传到 WebDAV。",
        "[{book}] downloaded from WebDAV.": "[{book}] 已从 WebDAV 下载。",
        "[{book}] WebDAV upload failed: {error}": "[{book}] WebDAV 上传失败：{error}",
        "[{book}] WebDAV download failed: {error}": "[{book}] WebDAV 下载失败：{error}",
        # Results tab
        "Open xray_data.json…": "打开 xray_data.json…",
        "Load Selected Book's Result": "加载所选书籍的结果",
        "No result loaded.": "未加载结果。",
        "Entity": "实体",
        "Detail": "详情",
        "Characters": "人物",
        "Locations": "地点",
        "Timeline": "时间线",
        "Themes": "主题",
        "Summary / Author": "摘要 / 作者",
        "{title} — {author}  ({prog}%)": "{title} — {author}  ({prog}%)",
        "Unknown": "未知",
        # status_text()
        "Complete": "已完成",
        "Pending": "待处理",
        "Partial {progress}%": "部分完成 {progress}%",
        # test_device()
        "Port {host}:{port} is open.": "端口 {host}:{port} 已开放。",
        "Connection refused. Enable 'Receive from PC' on KOReader.":
            "连接被拒绝。请在 KOReader 上启用“从电脑接收”。",
        # Dialogs & status messages
        "No .env": "无 .env",
        "No .env file found at:\n{path}": "未在以下位置找到 .env 文件：\n{path}",
        "Load .env failed": "加载 .env 失败",
        "Save .env failed": "保存 .env 失败",
        "Loaded settings from .env": "已从 .env 加载设置",
        "Saved settings to {path}": "已将设置保存到 {path}",
        "Settings applied.": "设置已应用。",
        "Select Calibre Library": "选择 Calibre 书库",
        "Fetching models for {api}…": "正在获取 {api} 的模型…",
        "Loaded {n} models.": "已加载 {n} 个模型。",
        "Model fetch failed": "获取模型失败",
        "Model fetch failed.": "获取模型失败。",
        "Scanning Calibre library…": "正在扫描 Calibre 书库…",
        "Invalid library": "无效书库",
        "Set a valid Calibre library path on the Configuration tab.":
            "请在“配置”标签页中设置有效的 Calibre 书库路径。",
        "Set a valid library path.": "请设置有效的书库路径。",
        "Found {n} books.": "找到 {n} 本书。",
        "Scan failed": "扫描失败",
        "Scan failed.": "扫描失败。",
        "Add EPUB files": "添加 EPUB 文件",
        "(added file)": "（已添加文件）",
        "Cleanup ghost folders": "清理残留文件夹",
        "Remove book folders on disk that are not registered in Calibre?\n"
        "This deletes files. Continue?":
            "删除磁盘上未在 Calibre 中登记的书籍文件夹？\n此操作会删除文件。是否继续？",
        "Cleanup done": "清理完成",
        "Removed {n} ghost folder(s).": "已删除 {n} 个残留文件夹。",
        "No books selected": "未选择书籍",
        "Select one or more books in the table first.": "请先在表格中选择一本或多本书。",
        "No model": "未选择模型",
        "Choose a model on the Configuration tab.": "请在“配置”标签页中选择模型。",
        "Missing API key": "缺少 API 密钥",
        "Set the API key for this provider.": "请为该提供商设置 API 密钥。",
        "=== Starting batch: {n} book(s) | {api} / {model} ===\n":
            "=== 开始批处理：{n} 本书 | {api} / {model} ===\n",
        "Stop requested — stopping as soon as possible…\n":
            "已请求停止，正在尽快停止…\n",
        "Book {index} of {total}": "第 {index} / {total} 本",
        "Batch complete": "批处理完成",
        "Batch complete.": "批处理完成。",
        "=== Batch complete ===\n": "=== 批处理完成 ===\n",
        "Save Log": "保存日志",
        "Log files (*.log *.txt)": "日志文件 (*.log *.txt)",
        "Save failed": "保存失败",
        "Log saved to {path}": "日志已保存到 {path}",
        "No selection": "未选择",
        "Select a book in the Books tab.": "请在“书籍”标签页中选择一本书。",
        "No device": "无设备",
        "Enter a KOReader device IP.": "请输入 KOReader 设备 IP。",
        "Open xray_data.json": "打开 xray_data.json",
        "JSON files (*.json)": "JSON 文件 (*.json)",
        "No result": "无结果",
        "No xray_data.json for that book yet.": "该书尚无 xray_data.json。",
        "Load failed": "加载失败",
        "Analysis running": "分析进行中",
        "Analysis is still running. Stop and quit?": "分析仍在进行中。停止并退出？",
        "⚠ No API key set for “{provider}”. Fill it in above and click Apply Settings.":
            "⚠ 未为“{provider}”设置 API 密钥。请在上方填写并点击“应用设置”。",
        "Language changed. Restart the app to apply.": "语言已更改。重启应用后生效。",
        # Worker / log lines
        "ERROR: Could not create AI client. Check the API key/base URL.":
            "错误：无法创建 AI 客户端。请检查 API 密钥/基础 URL。",
        "=== Stopped by user ===": "=== 已被用户停止 ===",
        "✓ done": "✓ 完成",
        "✗ failed: {message}": "✗ 失败：{message}",
        "[{book}] pushed to device: {result}": "[{book}] 已推送到设备：{result}",
        "ok": "成功",
        "failed": "失败",
        "[{book}] no xray_data.json to push.": "[{book}] 没有可推送的 xray_data.json。",
        # New buttons (Books tab)
        "Add Model": "添加模型",
        "Scan": "扫描",
        "Refresh All": "刷新全部",
        "Refresh Selected": "刷新所选",
        "Delete Selected from WebDAV": "从 WebDAV 删除所选",
        # Auto-detect Calibre
        "Auto-detect Calibre library locations": "自动检测 Calibre 书库位置",
        "No Calibre Library Found": "未找到 Calibre 书库",
        "Could not find a Calibre library in common locations.\nUse Browse\u2026 to select it manually.": "在常见位置未找到 Calibre 书库。\n请使用\u201c浏览\u2026\u201d手动选择。",
        "Multiple Calibre libraries found. Select one:": "找到多个 Calibre 书库，请选择一个：",
        "Calibre library auto-detected: {path}": "已自动检测到 Calibre 书库：{path}",
        # WebDAV delete
        "Delete the remote X-Ray folder for {preview} from WebDAV?\nThis cannot be undone.": "从 WebDAV 删除 {preview} 的远程 X-Ray 文件夹？\n此操作无法撤销。",
        "Deleting {n} book(s) from WebDAV\u2026": "正在从 WebDAV 删除 {n} 本书\u2026",
        "[{book}] deleted from WebDAV.": "[{book}] 已从 WebDAV 删除。",
        "[{book}] WebDAV delete failed: {error}": "[{book}] WebDAV 删除失败：{error}",
        " and {n} more": " 以及另外 {n} 本",
        # Cost summary dialog
        "Batch Complete \u2013 Token Usage & Cost": "批处理完成 \u2013 令牌用量与费用",
        "Processing complete. Token usage summary:": "处理完成。令牌用量摘要：",
        "Prompt Tokens": "输入令牌",
        "Completion Tokens": "输出令牌",
        "Total Chars": "总字符数",
        "Est. Cost (USD)": "预估费用（USD）",
        "Fetching prices from LiteLLM catalog\u2026": "正在从 LiteLLM 目录获取价格\u2026",
        "Could not fetch prices (offline?). Token counts are still accurate.": "无法获取价格（离线？）。令牌计数仍然准确。",
        'Prices sourced from <a href="{url}">LiteLLM community catalog</a>{date}': '价格来源：<a href="{url}">LiteLLM 社区目录</a>{date}',
        ", updated {date}": "，更新于 {date}",
        "Total estimated cost: ${cost}": "预估总费用：${cost}",
        "N/A": "不可用",
        "unknown": "未知",
        "Cancel": "取消",
        # Chain tooltip
        "Model chain:": "模型链：",
        "No models configured in the chain.": "链中没有配置模型。",
        "  {i}. {provider} / {model}  (\u00d7{retries}, {cooldown:.0f}s cooldown)": "  {i}. {provider} / {model}  (\u00d7{retries}，冷却 {cooldown:.0f}s)",
        "Max cycles: {n}": "最大循环次数：{n}",
        "Wait between cycles: {n:.0f}s": "循环间等待：{n:.0f}s",
    },
    # ---------------------------------------------------------------- 繁體中文
    "zh_TW": {
        "X-Ray Generator": "X-Ray 產生器",
        "Configuration": "設定",
        "Books": "書籍",
        "Progress": "進度",
        "Sync": "同步",
        "Results": "結果",
        "Ready": "就緒",
        "&File": "檔案(&F)",
        "Load settings from .env": "從 .env 載入設定",
        "Save settings to .env": "儲存設定至 .env",
        "Quit": "結束",
        "Create Desktop Shortcut": "建立桌面捷徑",
        "X-Ray": "X-Ray",
        "Provider & Model": "供應商與模型",
        "Refresh": "重新整理",
        "Provider:": "供應商：",
        "Model:": "模型：",
        "Provider": "供應商",
        "Model": "模型",
        "OpenAI-compatible Endpoint": "OpenAI 相容端點",
        "Cloud Provider API Keys": "雲端供應商 API 金鑰",
        "Custom Headers": "自訂請求標頭",
        "One per line: Header-Name: value   (or a JSON object)":
            "每行一個：Header-Name: value（或一個 JSON 物件）",
        "Show": "顯示",
        "Library & Advanced": "書庫與進階",
        "Browse…": "瀏覽…",
        "Calibre Library:": "Calibre 書庫：",
        "X-Ray Output Folder:": "X-Ray 輸出資料夾：",
        "Default: <app folder>/xray": "預設：<應用程式資料夾>/xray",
        "Select X-Ray Output Folder": "選擇 X-Ray 輸出資料夾",
        "Temperature:": "溫度：",
        "Language:": "語言：",
        "Apply Settings": "套用設定",
        "Load .env": "載入 .env",
        "Save .env": "儲存 .env",
        "Setup Wizard": "設定精靈",
        "Choose your primary provider, model, and API key.": "選擇你的主要供應商、模型與 API 金鑰。",
        "Set your Calibre library and output settings.": "設定 Calibre 書庫與輸出選項。",
        "Optional: configure KOReader device and WebDAV sync.": "選用：設定 KOReader 裝置與 WebDAV 同步。",
        "Review & Apply": "檢查並套用",
        "Review your choices, then click Finish to apply.": "檢查你的選擇，然後按下「完成」以套用。",
        "Provider: {value}": "供應商：{value}",
        "Model: {value}": "模型：{value}",
        "Calibre Library: {value}": "Calibre 書庫：{value}",
        "X-Ray Output Folder: {value}": "X-Ray 輸出資料夾：{value}",
        "Device IP[:port]: {value}": "裝置 IP[:連接埠]：{value}",
        "Server URL: {value}": "伺服器 URL：{value}",
        "(not set)": "（未設定）",
        "(default)": "（預設）",
        "OpenAI-compatible endpoints may work without an API key.": "OpenAI 相容端點在某些情況下可不填 API 金鑰。",
        "Setup wizard completed.": "設定精靈已完成。",
        "Run setup wizard?": "執行設定精靈？",
        "Would you like a guided setup for provider, library, and sync settings?": "是否使用引導流程設定供應商、書庫與同步選項？",
        "Desktop shortcut creation is only supported on Windows.": "桌面捷徑建立僅支援 Windows。",
        "Desktop folder not found: {path}": "找不到桌面資料夾：{path}",
        "Failed to create desktop shortcut: {error}": "建立桌面捷徑失敗：{error}",
        "Desktop shortcut created: {path}": "已建立桌面捷徑：{path}",
        "Not supported": "不支援",
        "OpenAI-compatible": "OpenAI 相容",
        "Anthropic Claude": "Anthropic Claude",
        "Groq": "Groq",
        "Google Gemini": "Google Gemini",
        "DeepSeek": "DeepSeek",
        "Base URL": "基礎 URL",
        "API Key": "API 金鑰",
        "API Key:": "API 金鑰：",
        "Enter API key\u2026": "輸入 API 金鑰\u2026",
        "Models Endpoint": "模型端點",
        "Claude API Key": "Claude API 金鑰",
        "Groq API Key": "Groq API 金鑰",
        "Gemini API Key": "Gemini API 金鑰",
        "DeepSeek API Key": "DeepSeek API 金鑰",
        "Scan Library": "掃描書庫",
        "Add EPUB…": "新增 EPUB…",
        "Cleanup Ghost Folders": "清理殘留資料夾",
        "Filter by title or author…": "依書名或作者篩選…",
        "Title": "書名",
        "Author": "作者",
        "Added": "新增日期",
        "Status": "狀態",
        "Select All": "全選",
        "Start Analysis": "開始分析",
        "Generate X-Ray": "產生 X-Ray",
        "Stop": "停止",
        "{n} books": "{n} 本書",
        "Overall Progress": "整體進度",
        "Idle": "閒置",
        "Batch:": "批次：",
        "Current Book": "目前書籍",
        "Book:": "書籍：",
        "Chunk:": "區塊：",
        "Operation:": "操作：",
        "Stats": "統計",
        "Characters: {n}": "人物：{n}",
        "Locations: {n}": "地點：{n}",
        "Events: {n}": "事件：{n}",
        "Log": "日誌",
        "Clear": "清除",
        "Save Log…": "儲存日誌…",
        "Auto-scroll": "自動捲動",
        "Progress:": "進度：",
        # Retry / fallback chain
        "Retry / Fallback Chain": "重試 / 回退鏈",
        "Models are tried top-to-bottom. Each is retried up to its "
        "retry count with the given cooldown between requests. A "
        "content-moderation refusal skips straight to the next row.":
            "模型依由上而下的順序嘗試。每個模型最多依其重試次數重試，請求之間使用指定的冷卻時間。"
            "內容審核拒絕將直接跳至下一列。",
        "Retries": "重試次數",
        "Cooldown (s)": "冷卻時間（秒）",
        "Input ($/M tok)": "輸入（$/百萬 Token）",
        "Output ($/M tok)": "輸出（$/百萬 Token）",
        "Add current provider/model": "新增目前供應商/模型",
        "Add row": "新增列",
        "Remove": "移除",
        "↑": "↑",
        "↓": "↓",
        "Max full-chain cycles:": "完整鏈最大循環次數：",
        "Wait between cycles (s):": "循環之間等待（秒）：",
        "Raise error (skip this book)": "擲出錯誤（跳過此書）",
        "Skip request (empty result)": "跳過請求（空結果）",
        "Exit the program": "結束程式",
        "When chain is exhausted:": "當鏈耗盡時：",
        "Honor server Retry-After header on rate limits": "在速率限制時遵循伺服器的 Retry-After 標頭",
        # Concurrency / chunk-size limits
        "Concurrency & Chunk-Size Limits": "並行與區塊大小限制",
        "Per-provider limits. Max Workers = parallel chunk requests. "
        "Max Chunk Size = characters sent per request. Set either to 0 "
        "(auto) to use the built-in default (Groq auto-derives chunk "
        "size from its token-per-minute budget).":
            "依供應商設定的限制。最大並行數 = 並行區塊請求數。最大區塊大小 = 每次請求傳送的字元數。"
            "將任一項設為 0（自動）即使用內建預設值（Groq 會依其每分鐘權杖預算自動推算區塊大小）。",
        "Max Workers": "最大並行數",
        "Max Chunk Size (chars)": "最大區塊大小（字元）",
        "Consolidation Batch Size:": "合併批次大小：",
        "Number of entities (characters + locations + summary) merged into one consolidation request. Higher = fewer requests but larger payloads. 0 (auto) uses the built-in default.": "每次合併請求中合併的實體數（人物 + 地點 + 摘要）。數值越大，請求次數越少但資料量越大。0（自動）使用內建預設值。",
        "auto": "自動",
        "KOReader Device": "KOReader 裝置",
        "Test Connection": "測試連線",
        "Choose Path": "選擇路徑",
        "Device IP[:port]:": "裝置 IP[:連接埠]：",
        "Push results automatically after each book": "每本書完成後自動推送結果",
        "Push Selected Book Now": "立即推送所選書籍",
        "Push “{title}” Now": "立即推送「{title}」",
        "Push {n} Selected Books Now": "立即推送所選 {n} 本書",
        "On KOReader: X-Ray menu → Cloud Sync → Receive from PC":
            "在 KOReader 中：X-Ray 選單 → 雲端同步 → 從電腦接收",
        "Status:": "狀態：",
        "Enter a device IP first.": "請先輸入裝置 IP。",
        # WebDAV sync
        "WebDAV Cloud Sync": "WebDAV 雲端同步",
        "Server URL:": "伺服器 URL：",
        "Username:": "使用者名稱：",
        "Password:": "密碼：",
        "Upload to WebDAV automatically after each book": "每本書完成後自動上傳至 WebDAV",
        "Point KOReader and this app at the same WebDAV folder so "
        "X-Ray data syncs both ways.":
            "將 KOReader 與本應用程式指向同一個 WebDAV 資料夾，即可雙向同步 X-Ray 資料。",
        "Not configured.": "未設定。",
        "Testing\u2026": "正在測試…",
        "Enter a WebDAV server URL first.": "請先輸入 WebDAV 伺服器 URL。",
        "Please test connection before browsing.": "請先測試連線，再瀏覽資料夾。",
        "Browse WebDAV Folders": "瀏覽 WebDAV 資料夾",
        "Select a folder to use as the WebDAV base URL for X-Ray sync.": "選擇一個資料夾作為 X-Ray 同步的 WebDAV 基礎 URL。",
        "Selected:": "已選擇：",
        "Loading\u2026": "載入中…",
        "Error: {msg}": "錯誤：{msg}",
        "Select This Folder": "選擇此資料夾",
        "Enter a WebDAV server URL and credentials before browsing.": "請先輸入 WebDAV 伺服器 URL 和憑證，再進行瀏覽。",
        "Open Local Folder": "開啟本機資料夾",
        "Delete Local X-Ray": "刪除本機 X-Ray",
        "No local X-Ray data found for the selected book(s).": "所選書籍沒有本機 X-Ray 資料。",
        "Delete local X-Ray data for {n} book(s)?": "刪除 {n} 本書的本機 X-Ray 資料？",
        "Deleted X-Ray data for {n} book(s).": "已刪除 {n} 本書的 X-Ray 資料。",
        "Upload Selected to WebDAV": "上傳所選至 WebDAV",
        "Download Selected from WebDAV": "從 WebDAV 下載所選",
        "Auto-refresh WebDAV status": "自動重新整理 WebDAV 狀態",
        "Refresh WebDAV Status": "重新整理 WebDAV 狀態",
        "WebDAV": "WebDAV",
        "Not uploaded": "未上傳",
        "On server": "伺服器上",
        "Synced": "已同步",
        "Differs": "有差異",
        "Error": "錯誤",
        "Checking\u2026": "檢查中…",
        "No WebDAV server": "無 WebDAV 伺服器",
        "Configure a WebDAV server on the Sync tab.": "請在「同步」分頁中設定 WebDAV 伺服器。",
        "WebDAV is busy\u2026": "WebDAV 忙碌中…",
        "Uploading {n} book(s) to WebDAV\u2026": "正在上傳 {n} 本書至 WebDAV…",
        "Downloading {n} book(s) from WebDAV\u2026": "正在從 WebDAV 下載 {n} 本書…",
        "[{book}] uploaded to WebDAV.": "[{book}] 已上傳至 WebDAV。",
        "[{book}] downloaded from WebDAV.": "[{book}] 已從 WebDAV 下載。",
        "[{book}] WebDAV upload failed: {error}": "[{book}] WebDAV 上傳失敗：{error}",
        "[{book}] WebDAV download failed: {error}": "[{book}] WebDAV 下載失敗：{error}",
        "Open xray_data.json…": "開啟 xray_data.json…",
        "Load Selected Book's Result": "載入所選書籍的結果",
        "No result loaded.": "未載入結果。",
        "Entity": "實體",
        "Detail": "詳情",
        "Characters": "人物",
        "Locations": "地點",
        "Timeline": "時間線",
        "Themes": "主題",
        "Summary / Author": "摘要 / 作者",
        "{title} — {author}  ({prog}%)": "{title} — {author}  ({prog}%)",
        "Unknown": "未知",
        "Complete": "已完成",
        "Pending": "待處理",
        "Partial {progress}%": "部分完成 {progress}%",
        "Port {host}:{port} is open.": "連接埠 {host}:{port} 已開放。",
        "Connection refused. Enable 'Receive from PC' on KOReader.":
            "連線遭拒。請在 KOReader 上啟用「從電腦接收」。",
        "No .env": "無 .env",
        "No .env file found at:\n{path}": "未在以下位置找到 .env 檔案：\n{path}",
        "Load .env failed": "載入 .env 失敗",
        "Save .env failed": "儲存 .env 失敗",
        "Loaded settings from .env": "已從 .env 載入設定",
        "Saved settings to {path}": "已將設定儲存至 {path}",
        "Settings applied.": "設定已套用。",
        "Select Calibre Library": "選擇 Calibre 書庫",
        "Fetching models for {api}…": "正在取得 {api} 的模型…",
        "Loaded {n} models.": "已載入 {n} 個模型。",
        "Model fetch failed": "取得模型失敗",
        "Model fetch failed.": "取得模型失敗。",
        "Scanning Calibre library…": "正在掃描 Calibre 書庫…",
        "Invalid library": "無效書庫",
        "Set a valid Calibre library path on the Configuration tab.":
            "請在「設定」分頁中設定有效的 Calibre 書庫路徑。",
        "Set a valid library path.": "請設定有效的書庫路徑。",
        "Found {n} books.": "找到 {n} 本書。",
        "Scan failed": "掃描失敗",
        "Scan failed.": "掃描失敗。",
        "Add EPUB files": "新增 EPUB 檔案",
        "(added file)": "（已新增檔案）",
        "Cleanup ghost folders": "清理殘留資料夾",
        "Remove book folders on disk that are not registered in Calibre?\n"
        "This deletes files. Continue?":
            "刪除磁碟上未在 Calibre 中登記的書籍資料夾？\n此操作會刪除檔案。是否繼續？",
        "Cleanup done": "清理完成",
        "Removed {n} ghost folder(s).": "已刪除 {n} 個殘留資料夾。",
        "No books selected": "未選擇書籍",
        "Select one or more books in the table first.": "請先在表格中選擇一本或多本書。",
        "No model": "未選擇模型",
        "Choose a model on the Configuration tab.": "請在「設定」分頁中選擇模型。",
        "Missing API key": "缺少 API 金鑰",
        "Set the API key for this provider.": "請為此供應商設定 API 金鑰。",
        "=== Starting batch: {n} book(s) | {api} / {model} ===\n":
            "=== 開始批次處理：{n} 本書 | {api} / {model} ===\n",
        "Stop requested — stopping as soon as possible…\n":
            "已要求停止，正在盡快停止…\n",
        "Book {index} of {total}": "第 {index} / {total} 本",
        "Batch complete": "批次處理完成",
        "Batch complete.": "批次處理完成。",
        "=== Batch complete ===\n": "=== 批次處理完成 ===\n",
        "Save Log": "儲存日誌",
        "Log files (*.log *.txt)": "日誌檔 (*.log *.txt)",
        "Save failed": "儲存失敗",
        "Log saved to {path}": "日誌已儲存至 {path}",
        "No selection": "未選擇",
        "Select a book in the Books tab.": "請在「書籍」分頁中選擇一本書。",
        "No device": "無裝置",
        "Enter a KOReader device IP.": "請輸入 KOReader 裝置 IP。",
        "Open xray_data.json": "開啟 xray_data.json",
        "JSON files (*.json)": "JSON 檔 (*.json)",
        "No result": "無結果",
        "No xray_data.json for that book yet.": "該書尚無 xray_data.json。",
        "Load failed": "載入失敗",
        "Analysis running": "分析進行中",
        "Analysis is still running. Stop and quit?": "分析仍在進行中。停止並結束？",
        "⚠ No API key set for “{provider}”. Fill it in above and click Apply Settings.":
            "⚠ 未為「{provider}」設定 API 金鑰。請在上方填寫並點擊「套用設定」。",
        "Language changed. Restart the app to apply.": "語言已變更。重新啟動應用程式後生效。",
        # Worker / log lines
        "ERROR: Could not create AI client. Check the API key/base URL.":
            "錯誤：無法建立 AI 用戶端。請檢查 API 金鑰/基礎 URL。",
        "=== Stopped by user ===": "=== 已被使用者停止 ===",
        "✓ done": "✓ 完成",
        "✗ failed: {message}": "✗ 失敗：{message}",
        "[{book}] pushed to device: {result}": "[{book}] 已推送到裝置：{result}",
        "ok": "成功",
        "failed": "失敗",
        "[{book}] no xray_data.json to push.": "[{book}] 沒有可推送的 xray_data.json。",
        # New buttons (Books tab)
        "Add Model": "新增模型",
        "Scan": "掃描",
        "Refresh All": "重新整理全部",
        "Refresh Selected": "重新整理所選",
        "Delete Selected from WebDAV": "從 WebDAV 刪除所選",
        # Auto-detect Calibre
        "Auto-detect Calibre library locations": "自動偵測 Calibre 書庫位置",
        "No Calibre Library Found": "未找到 Calibre 書庫",
        "Could not find a Calibre library in common locations.\nUse Browse\u2026 to select it manually.": "在常見位置未找到 Calibre 書庫。\n請使用「瀏覽\u2026」手動選取。",
        "Multiple Calibre libraries found. Select one:": "找到多個 Calibre 書庫，請選擇其中一個：",
        "Calibre library auto-detected: {path}": "已自動偵測到 Calibre 書庫：{path}",
        # WebDAV delete
        "Delete the remote X-Ray folder for {preview} from WebDAV?\nThis cannot be undone.": "從 WebDAV 刪除 {preview} 的遠端 X-Ray 資料夾？\n此操作無法復原。",
        "Deleting {n} book(s) from WebDAV\u2026": "正在從 WebDAV 刪除 {n} 本書\u2026",
        "[{book}] deleted from WebDAV.": "[{book}] 已從 WebDAV 刪除。",
        "[{book}] WebDAV delete failed: {error}": "[{book}] WebDAV 刪除失敗：{error}",
        " and {n} more": " 以及另外 {n} 本",
        # Cost summary dialog
        "Batch Complete \u2013 Token Usage & Cost": "批次處理完成 \u2013 Token 用量與費用",
        "Processing complete. Token usage summary:": "處理完成。Token 用量摘要：",
        "Prompt Tokens": "輸入 Token",
        "Completion Tokens": "輸出 Token",
        "Total Chars": "總字元數",
        "Est. Cost (USD)": "預估費用（USD）",
        "Fetching prices from LiteLLM catalog\u2026": "正在從 LiteLLM 目錄取得價格\u2026",
        "Could not fetch prices (offline?). Token counts are still accurate.": "無法取得價格（離線？）。Token 計數仍然準確。",
        'Prices sourced from <a href="{url}">LiteLLM community catalog</a>{date}': '價格來源：<a href="{url}">LiteLLM 社群目錄</a>{date}',
        ", updated {date}": "，更新於 {date}",
        "Total estimated cost: ${cost}": "預估總費用：${cost}",
        "N/A": "不可用",
        "unknown": "未知",
        "Cancel": "取消",
        # Chain tooltip
        "Model chain:": "模型鏈：",
        "No models configured in the chain.": "鏈中未設定模型。",
        "  {i}. {provider} / {model}  (\u00d7{retries}, {cooldown:.0f}s cooldown)": "  {i}. {provider} / {model}  (\u00d7{retries}，冷卻 {cooldown:.0f}s)",
        "Max cycles: {n}": "最大循環次數：{n}",
        "Wait between cycles: {n:.0f}s": "循環間等待：{n:.0f}s",
    },
}


# =============================================================================
# Public API
# =============================================================================

_current_lang: str = _DEFAULT_LANG


def _normalize(code: str) -> str:
    """Map an arbitrary locale/language code to a supported language code."""
    if not code:
        return _DEFAULT_LANG
    code = code.replace("-", "_")
    lower = code.lower()
    if lower.startswith("zh"):
        if any(tag in lower for tag in ("tw", "hk", "mo", "hant")):
            return "zh_TW"
        return "zh"
    root = lower.split("_", 1)[0]
    for supported, _name in AVAILABLE_LANGUAGES:
        if supported.lower() == lower or supported.lower() == root:
            return supported
    return _DEFAULT_LANG


def detect_language() -> str:
    """Detect the preferred language from env var, then the system locale."""
    env = os.environ.get("XRAY_GUI_LANG")
    if env:
        return _normalize(env)
    try:
        sys_locale = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
    except (ValueError, IndexError):
        sys_locale = ""
    return _normalize(sys_locale)


def set_language(code: str | None) -> str:
    """Set the active UI language. Returns the normalized code that was applied."""
    global _current_lang
    _current_lang = _normalize(code) if code else detect_language()
    return _current_lang


def get_language() -> str:
    """Return the currently active language code."""
    return _current_lang


def tr(text: str) -> str:
    """Translate ``text`` into the active language, falling back to the source."""
    if _current_lang == "en":
        return text
    return _TRANSLATIONS.get(_current_lang, {}).get(text, text)
