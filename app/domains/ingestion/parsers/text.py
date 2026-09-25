from app.domains.ingestion.schema import (
    ParsedDocument,
    ParsedSection,
)


def text_parse(data: bytes) -> ParsedDocument:
    text = data.decode("utf-8").strip()

    return ParsedDocument(
        sections=[ParsedSection(text=text)]
    )