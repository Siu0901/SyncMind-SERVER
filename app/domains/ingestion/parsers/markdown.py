from app.domains.ingestion.schema import (
    ParsedDocument,
    ParsedSection,
)

# 걍 일단 text랑 똑같이 ㄱㄱ
def markdown_parse(data: bytes) -> ParsedDocument:
    text = data.decode("utf-8").strip()

    return ParsedDocument(
        sections=[ParsedSection(text=text)]
    )