from typing import Optional

import tiktoken
from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter

from app.core.config import get_settings
from app.domains.ingestion.schema import ParsedDocument, ChunkData


settings = get_settings()


class DocumentChunker:
    def __init__(
        self,
        chunk_size: int = settings.MAX_TOKENS,
        overlap_ratio: float = settings.OVERLAP_RATIO,
        min_tokens: int = settings.MIN_TOKENS,
        encoding_name: str = settings.ENCODING_NAME,
    ):
        self._chunk_size = chunk_size
        self._min_tokens = min_tokens
        self._encoding = tiktoken.get_encoding(encoding_name)
        self._markdown_parser = MarkdownNodeParser()

        self._splitter = SentenceSplitter.from_defaults(
            chunk_size=chunk_size,
            chunk_overlap=int(chunk_size * overlap_ratio),
            paragraph_separator="\n\n\n",
            tokenizer=self._tokenize,
        )


    def chunk(self, document: ParsedDocument) -> list[ChunkData]:
        chunks: list[ChunkData] = []

        carry: Optional[str] = None
        carry_section = None

        for section in document.sections:
            pieces = self._split(section.text)

            if carry is not None:
                pieces.insert(0, carry)
                carry = None

            pieces = self._merge_small(pieces)

            if (
                pieces and
                self._count(pieces[-1]) <
                self._min_tokens and
                pieces[-1].lstrip().startswith("#")
            ):
                carry = pieces.pop()
                carry_section = section

            if (
                pieces and
                chunks and
                self._count(pieces[0]) <
                self._min_tokens and
                not pieces[0].lstrip().startswith("#")
            ):
                candidate = f"{chunks[-1].content}\n\n{pieces[0]}"

                if self._count(candidate) <= self._chunk_size:
                    previous = chunks[-1]
                    chunks[-1] = ChunkData(
                        content=candidate,
                        token_count=self._count(candidate),
                        page_number=previous.page_number,
                        section=previous.section,
                        metadata=previous.metadata,
                    )
                    pieces.pop(0)

            for text in pieces:
                chunks.append(
                    ChunkData(
                        content=text,
                        token_count=self._count(text),
                        page_number=section.page_number,
                        section=section.section,
                        metadata=dict(section.metadata),
                    )
                )

        if carry is not None:
            chunks.append(
                ChunkData(
                    content=carry,
                    token_count=self._count(carry),
                    page_number=carry_section.page_number,
                    section=carry_section.section,
                    metadata=dict(carry_section.metadata),
                )
            )

        return chunks


    def _split(self, text: str) -> list[str]:
        if not text.strip():
            return []

        nodes = self._markdown_parser.get_nodes_from_documents(
            [Document(text=text)]
        )

        pieces: list[str] = []

        for node in nodes:
            content = node.get_content().strip()
            if not content:
                continue

            if self._count(content) <= self._chunk_size:
                pieces.append(content)
                continue

            pieces.extend(
                piece.strip()
                for piece in self._splitter.split_text(content)
                if piece.strip()
            )

        return pieces


    def _merge_small(self, pieces: list[str]) -> list[str]:
        merged: list[str] = []

        for piece in pieces:
            if (
                merged and
                self._count(merged[-1]) <
                self._min_tokens and
                merged[-1].lstrip().startswith("#")
            ):
                candidate = f"{merged[-1]}\n\n{piece}"

                if self._count(candidate) <= self._chunk_size:
                    merged[-1] = candidate
                    continue

            if (
                merged and
                self._count(piece) <
                self._min_tokens and
                not piece.lstrip().startswith("#")
            ):
                candidate = f"{merged[-1]}\n\n{piece}"

                if self._count(candidate) <= self._chunk_size:
                    merged[-1] = candidate
                    continue

            merged.append(piece)

        return merged


    def _tokenize(self, text: str) -> list[int]:
        return self._encoding.encode(text, disallowed_special=())


    def _count(self, text: str) -> int:
        return len(self._tokenize(text))