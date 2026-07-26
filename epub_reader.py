"""
EPUB file reader for X-Ray Generator.

Handles extracting chapters, metadata, and text content from EPUB files.
"""

from __future__ import annotations

import os
import re
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
