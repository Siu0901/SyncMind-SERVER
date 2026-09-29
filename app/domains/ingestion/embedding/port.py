from typing import Protocol, runtime_checkable


class EmbeddingPort(Protocol):
    async def embed_document(
        self,
        texts: list[str]
    ) -> list[list[float]]:
        ...


    async def embed_query(
        self,
        texts: list[str]
    ) -> list[float]:
        ...