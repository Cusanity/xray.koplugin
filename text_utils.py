"""
Text utilities, constants, and name normalization for X-Ray Generator.

Stateless helpers used across multiple modules.
"""

from __future__ import annotations

import re

try:
    import opencc

    _T2S_CONVERTER = opencc.OpenCC("t2s")
except ImportError:
    print(
        "Error: opencc is required. Install with: pip install opencc-python-reimplemented"
    )
    import sys

    sys.exit(1)

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

    potential_name = name
    for prefix in NAME_PREFIXES:
        if potential_name.startswith(prefix) and len(potential_name) > len(prefix):
            test_name = potential_name[len(prefix) :].strip()
            if test_name:
                potential_name = test_name
                break

    for suffix in NAME_SUFFIXES:
        if potential_name.endswith(suffix) and len(potential_name) > len(suffix):
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


def t2s_convert(text: str) -> str:
    """Convert Traditional Chinese to Simplified Chinese."""
    return _T2S_CONVERTER.convert(text)
