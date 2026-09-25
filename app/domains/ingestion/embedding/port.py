from typing import Protocol, runtime_checkable


class EmbeddingPort(Protocol):
    async def embed(
        self,
        texts: list[str]
    ) -> list[list[float]]:
        ...