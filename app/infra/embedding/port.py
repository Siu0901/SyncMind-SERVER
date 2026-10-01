from typing import Protocol


class EmbeddingPort(Protocol):
    async def embed_document(
        self,
        texts: list[str]
    ) -> list[list[float]]:
        ...


    async def embed_query(
        self,
        texts: str
    ) -> list[float]:
        ...


    async def close(self) -> None:
        ...
