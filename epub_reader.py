"""
EPUB file reader for X-Ray Generator.

Handles extracting chapters, metadata, and text content from EPUB files.
"""

from __future__ import annotations

import html as _html_mod
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from text_utils import (
    XML_NS_CONTAINER,
    XML_NS_NCX,
    XML_NS_OPF,
    html_to_text,
    sanitize_filename,
    strip_html_tags,
)


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
            creator_elem = metadata.find(
                ".//{http://purl.org/dc/elements/1.1/}creator"
            )
        if creator_elem is not None and creator_elem.text:
            author = creator_elem.text.strip()

        for meta in metadata.findall(".//{http://www.idpf.org/2007/opf}meta"):
            if meta.get("name") == "calibre:timestamp":
                added_date = meta.get("content", added_date)
                break

    return title, author, added_date


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
                        t = metadata.find(
                            ".//{http://purl.org/dc/elements/1.1/}title"
                        )
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
    ) -> tuple[list[tuple[str, str, int]] | None, str, str]:
        """Extract chapters as (title, text, spine_idx) tuples in reading order."""
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
            return None, "Unknown Title", "Unknown Author"

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
            ".//{http://www.idpf.org/2007/opf}manifest/"
            "{http://www.idpf.org/2007/opf}item"
        ):
            manifest[item.attrib["id"]] = item.attrib["href"]
        return manifest

    def _parse_spine(self, opf_root: ET.Element) -> list[str]:
        spine = []
        for itemref in opf_root.findall(
            ".//{http://www.idpf.org/2007/opf}spine/"
            "{http://www.idpf.org/2007/opf}itemref"
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
                    text_elem = nav_point.find(
                        "ncx:navLabel/ncx:text", XML_NS_NCX
                    )
                    content_elem = nav_point.find("ncx:content", XML_NS_NCX)
                    if text_elem is not None and content_elem is not None:
                        nav_title = (
                            text_elem.text.strip() if text_elem.text else None
                        )
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
    ) -> list[tuple[str, str, int]]:
        """Return (title, text, spine_idx) triples.

        spine_idx is the 0-based position of this item in the full EPUB spine
        list. CREngine numbers DocFragments as DocFragment[spine_idx+1] (1-based),
        so this value maps directly to a CREngine xpointer fragment index.
        """
        chapters = []
        chapter_index = 0

        for spine_idx, item_id in enumerate(spine):
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
                    chapters.append((chapter_title, text, spine_idx))
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
                rf"<{h_level}[^>]*>(.*?)</{h_level}>",
                html,
                re.DOTALL | re.IGNORECASE,
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


# ---------------------------------------------------------------------------
# EPUB TOC rebuild
# ---------------------------------------------------------------------------

def _is_toc_page(html_text: str) -> bool:
    """Return True if html_text looks like an embedded table of contents."""
    links = re.findall(r'<a\s+href="(?!(?:https?:|mailto:|#))[^"]*"', html_text, re.I)
    if len(links) < 4:
        return False
    if re.search(r'[\u76ee][\u5f55\u6b21\u9304]', html_text):
        return True
    if re.search(
        r'<h[1-6][^>]*>\s*(?:table\s+of\s+contents|contents)\s*</h[1-6]',
        html_text, re.I,
    ):
        return True
    text_only = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html_text)).strip()
    return len(links) >= 8 and len(links) / max(len(text_only), 1) * 600 > 1


def _extract_toc_entries(html_text: str) -> list[tuple[int, str, str]]:
    """Return (depth, title, href) for internal links in an HTML TOC page."""
    entries: list[tuple[int, str, str]] = []
    depth = 0
    token_re = re.compile(
        r'<(/?blockquote)[^>]*>|<a\s+href="([^"]*)"[^>]*>(.*?)</a>',
        re.S | re.I,
    )
    for m in token_re.finditer(html_text):
        if m.group(1):
            depth = max(0, depth - 1) if m.group(1).startswith('/') else depth + 1
        elif m.group(2) is not None:
            href = m.group(2)
            if href.startswith(('http://', 'https://', 'mailto:', '#')):
                continue
            label = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', m.group(3))).strip()
            if label:
                entries.append((depth, label, href))
    return entries


def _get_spine_order_epub(tmpdir: str) -> dict[str, int]:
    """Return {filename: spine_index} from the OPF in the extracted EPUB dir."""
    import glob
    opf_files = glob.glob(os.path.join(tmpdir, '**', 'content.opf'), recursive=True)
    if not opf_files:
        opf_files = glob.glob(os.path.join(tmpdir, '**', '*.opf'), recursive=True)
    if not opf_files:
        return {}
    with open(opf_files[0], encoding='utf-8') as f:
        opf_text = f.read()
    manifest: dict[str, str] = {}
    for m in re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf_text):
        manifest[m.group(1)] = m.group(2)
    for m in re.finditer(r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', opf_text):
        manifest[m.group(2)] = m.group(1)
    spine_ids = re.findall(r'<itemref\s+idref="([^"]+)"', opf_text)
    return {
        href.split('/')[-1].split('#')[0]: idx
        for idx, idref in enumerate(spine_ids)
        if (href := manifest.get(idref, ''))
    }


def _entries_to_tree_epub(entries, start, min_depth):
    """Convert flat (depth, title, href) list to a nested tree."""
    children: list = []
    i = start
    while i < len(entries):
        depth, label, href = entries[i]
        if depth < min_depth:
            break
        if depth == min_depth:
            grandchildren, i = _entries_to_tree_epub(entries, i + 1, min_depth + 1)
            children.append((label, href, grandchildren))
        else:
            i += 1
    return children, i


def _render_navpoints_epub(tree: list, counter: list[int], indent: int) -> str:
    xml = ''
    pad = '  ' * indent
    for label, href, children in tree:
        counter[0] += 1
        po = counter[0]
        xml += (
            f'{pad}<navPoint class="chapter" id="np_{po}" playOrder="{po}">\n'
            f'{pad}  <navLabel><text>{_html_mod.escape(label)}</text></navLabel>\n'
            f'{pad}  <content src="{_html_mod.escape(href)}"/>\n'
        )
        if children:
            xml += _render_navpoints_epub(children, counter, indent + 1)
        xml += f'{pad}</navPoint>\n'
    return xml


def rebuild_toc_ncx(epub_path: str) -> bool:
    """Rebuild toc.ncx from embedded HTML TOC pages in an EPUB.

    Detects HTML pages that look like tables of contents (via 目录/目次 heading
    or link density), extracts their chapter links and nesting hierarchy, and
    rewrites toc.ncx with a proper multi-level navMap. Skips EPUBs whose NCX
    already has nested navigation entries.

    Returns True if toc.ncx was rewritten with richer content.
    """
    if not os.path.exists(epub_path) or not epub_path.lower().endswith('.epub'):
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(epub_path) as zin:
            zin.extractall(tmpdir)

        import glob
        ncx_files = glob.glob(os.path.join(tmpdir, '**', 'toc.ncx'), recursive=True)
        if not ncx_files:
            return False
        ncx_path = ncx_files[0]
        with open(ncx_path, encoding='utf-8') as f:
            ncx_text = f.read()

        top_level = re.findall(
            r'<navPoint[^>]*>\s*<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*'
            r'<content\s+src="([^"]+)"',
            ncx_text, re.S,
        )
        if not top_level:
            return False
        if len(re.findall(r'<navPoint\b', ncx_text)) > len(top_level) + 1:
            return False

        spine_order = _get_spine_order_epub(tmpdir)
        top_labels = {label for label, _ in top_level}

        html_files = sorted(
            glob.glob(os.path.join(tmpdir, '**', '*.html'), recursive=True)
            + glob.glob(os.path.join(tmpdir, '**', '*.xhtml'), recursive=True)
            + glob.glob(os.path.join(tmpdir, '**', '*.htm'), recursive=True)
        )

        toc_pages: list[tuple[int, list]] = []
        for html_path in html_files:
            try:
                with open(html_path, encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue
            if not _is_toc_page(content):
                continue
            entries = _extract_toc_entries(content)
            if not entries:
                continue
            entry_labels = {e[1] for e in entries}
            if len(entry_labels & top_labels) / max(len(entry_labels), 1) >= 0.5:
                continue
            fname = os.path.basename(html_path)
            toc_pages.append((spine_order.get(fname, 999999), entries))

        if not toc_pages:
            return False
        toc_pages.sort(key=lambda x: x[0])

        top_positions = sorted(
            [
                (spine_order.get(src.split('/')[-1].split('#')[0], 999999), label, src)
                for label, src in top_level
            ],
            key=lambda x: x[0],
        )

        book_toc_map: dict[int, list] = {i: [] for i in range(len(top_positions))}
        for toc_idx, entries in toc_pages:
            book_idx = 0
            for i, (book_start, _, _) in enumerate(top_positions):
                if toc_idx >= book_start:
                    book_idx = i
            book_toc_map[book_idx].append(entries)

        if not any(v for v in book_toc_map.values()):
            return False

        uid_m = re.search(r'<meta\s+content="([^"]+)"\s+name="dtb:uid"', ncx_text) or \
                re.search(r'<meta\s+name="dtb:uid"\s+content="([^"]+)"', ncx_text)
        uid = uid_m.group(1) if uid_m else 'epub-uid'
        lang_m = re.search(r'xml:lang="([^"]+)"', ncx_text)
        lang = lang_m.group(1) if lang_m else 'zh'
        title_m = re.search(r'<docTitle>\s*<text>(.*?)</text>', ncx_text, re.S)
        title = title_m.group(1).strip() if title_m else 'Untitled'

        counter = [0]
        navmap_xml = ''
        max_depth = 1
        for book_idx, (_, book_label, book_src) in enumerate(top_positions):
            counter[0] += 1
            po = counter[0]
            children_xml = ''
            for entries in book_toc_map[book_idx]:
                tree, _ = _entries_to_tree_epub(entries, 0, 0)
                children_xml += _render_navpoints_epub(tree, counter, indent=3)
                for depth, _, _ in entries:
                    max_depth = max(max_depth, depth + 2)
            navmap_xml += (
                f'    <navPoint class="chapter" id="np_{po}" playOrder="{po}">\n'
                f'      <navLabel><text>{_html_mod.escape(book_label)}</text></navLabel>\n'
                f'      <content src="{_html_mod.escape(book_src)}"/>\n'
                f'{children_xml}'
                f'    </navPoint>\n'
            )

        new_ncx = (
            "<?xml version='1.0' encoding='utf-8'?>\n"
            f'<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"'
            f' xml:lang="{lang}">\n'
            '  <head>\n'
            f'    <meta content="{uid}" name="dtb:uid"/>\n'
            f'    <meta content="{max_depth}" name="dtb:depth"/>\n'
            '    <meta content="epub-patcher" name="dtb:generator"/>\n'
            '    <meta content="0" name="dtb:totalPageCount"/>\n'
            '    <meta content="0" name="dtb:maxPageNumber"/>\n'
            '  </head>\n'
            '  <docTitle>\n'
            f'    <text>{_html_mod.escape(title)}</text>\n'
            '  </docTitle>\n'
            '  <navMap>\n'
            f'{navmap_xml}'
            '  </navMap>\n'
            '</ncx>'
        )
        with open(ncx_path, 'w', encoding='utf-8') as f:
            f.write(new_ncx)

        output_tmp = epub_path + '.toc_tmp'
        try:
            mimetype_path = os.path.join(tmpdir, 'mimetype')
            with zipfile.ZipFile(output_tmp, 'w') as zout:
                if os.path.exists(mimetype_path):
                    zout.write(mimetype_path, 'mimetype', compress_type=zipfile.ZIP_STORED)
                for root, _dirs, files in os.walk(tmpdir):
                    for fname in sorted(files):
                        fpath = os.path.join(root, fname)
                        arcname = os.path.relpath(fpath, tmpdir).replace(os.sep, '/')
                        if arcname == 'mimetype':
                            continue
                        zout.write(fpath, arcname, compress_type=zipfile.ZIP_DEFLATED)
            shutil.move(output_tmp, epub_path)
            return True
        except Exception:
            if os.path.exists(output_tmp):
                os.remove(output_tmp)
            raise
