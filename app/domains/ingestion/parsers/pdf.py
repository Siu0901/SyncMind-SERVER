import pymupdf
import pymupdf4llm

import logging

from typing import Optional

from app.core.config import get_settings
from app.domains.ingestion.exceptions import (
    CorruptedPdfError,
    EncryptedPdfError,
    EmptyPdfError,
    PdfTooLargeError,
    NoTextLayerError,
    PdfParseError,
)
from app.domains.ingestion.schema import (
    ParsedDocument,
    ParsedSection,
)


logger = logging.getLogger(__name__)

settings = get_settings()


def _is_garbled(text: str) -> bool:
    sample = text[:3000]

    if "(cid:" in sample:
        return True

    if sample.isascii():
        return False

    printable = sum(c.isprintable() and not c.isspace() for c in sample)
    if printable < 100:
        return False

    hangul = sum(settings.HANGUL_START <= ord(c) <= settings.HANGUL_END for c in sample)
    return hangul / printable < 0.03


def _section_of(chunk: dict) -> Optional[str]:
    items = chunk.get("toc_items") or []
    return " > ".join(item[1] for item in items) if items else None


class PDFParser:
    @staticmethod
    def parse(data: bytes) -> ParsedDocument:
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise CorruptedPdfError() from exc

        try:
            if document.needs_pass:
                raise EncryptedPdfError()

            page_count = document.page_count

            if page_count == 0:
                raise EmptyPdfError()

            if page_count > settings.MAX_PAGES:
                raise PdfTooLargeError(page_count, settings.MAX_PAGES)

            try:
                chunks = pymupdf4llm.to_markdown(
                    document,
                    page_chunks=True,
                    ocr_language="kor+eng",
                    ocr_dpi=300,
                    graphics_limit=settings.GRAPHICS_LIMIT,
                    ignore_graphics=True,
                    image_size_limit=0.05,
                    show_progress=False,
                )
            except Exception as exc:
                raise PdfParseError(type(exc).__name__) from exc

            sections: list[ParsedSection] = []
            skipped = 0

            for index, chunk in enumerate(chunks, start=1):
                text = chunk["text"].strip()

                if not text or _is_garbled(text):
                    skipped += 1
                    continue

                sections.append(
                    ParsedSection(
                        text=text,
                        page_number=chunk["metadata"].get("page", index),
                        section=_section_of(chunk),
                    )
                )

            if not sections:
                raise NoTextLayerError(page_count=page_count, skipped_pages=skipped)

            if skipped:
                logger.warning(
                    "PDF partially skipped | pages=%s parsed=%s skipped=%s",
                    page_count, len(sections), skipped,
                )

            return ParsedDocument(sections=sections)

        finally:
            document.close()