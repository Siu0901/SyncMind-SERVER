from app.domains.ingestion.schema import (
    ParsedDocument,
    ParsedSection,
)


class TextParser:
    @staticmethod
    def parse(data: bytes) -> ParsedDocument:
        text = data.decode("utf-8").strip()

        return ParsedDocument(
            sections=[ParsedSection(text=text)]
        )