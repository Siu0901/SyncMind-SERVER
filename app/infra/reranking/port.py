from typing import Protocol
from dataclasses import dataclass


@dataclass(frozen=True)
class RerankCandidate:
    chunk_id: int
    text: str


@dataclass(frozen=True)
class RerankResult:
    chunk_id: int
    score: float


class RerankerError(Exception):
    pass


class RerankerUnavailableError(RerankerError):
    pass


class RerankerRequestError(RerankerError):
    pass


class RerankerPort(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        limit: int,
    ) -> list[RerankResult]:
        ...