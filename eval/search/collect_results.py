import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from qdrant_client import AsyncQdrantClient, models

from app.core.config import get_settings
from app.core.qdrant import close_qdrant, get_qdrant, init_qdrant
from app.infra.embedding.factory import close_embedding, init_embedding
from app.infra.embedding.port import EmbeddingPort
from app.infra.reranking.factory import reranker_lifespan
from app.infra.reranking.port import (
    RerankCandidate,
    RerankerPort,
    RerankerRequestError,
    RerankerUnavailableError,
)


CASES_PATH = Path(__file__).with_name("cases_all_text.jsonl")
RESULTS_PATH = Path(__file__).with_name("results.jsonl")
WORKSPACE_ID = 1
CANDIDATE_LIMIT = 30
FINAL_LIMIT = 10
MAX_CASES: int | None = None
RERANK_MIN_INTERVAL_SECONDS = 6.0

settings = get_settings()


def load_cases() -> list[dict]:
    cases = [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = [case["case_id"] for case in cases]
    if not cases or len(case_ids) != len(set(case_ids)):
        raise RuntimeError("평가 질문이 비었거나 case_id가 중복되었습니다")
    return cases


def run_config() -> dict:
    return {
        "dataset_sha256": hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(),
        "workspace_id": WORKSPACE_ID,
        "collection": settings.QDRANT_COLLECTION,
        "embedding_model": settings.OPENAI_EMBED_MODEL,
        "embedding_dimension": settings.EMBEDDING_DIMENSION,
        "candidate_limit": CANDIDATE_LIMIT,
        "final_limit": FINAL_LIMIT,
        "rerank_enabled": settings.RERANK_ENABLED,
        "rerank_provider": settings.RERANK_PROVIDER,
        "rerank_model": settings.RERANK_MODEL,
        "rerank_timeout_seconds": settings.RERANK_TIMEOUT_SECONDS,
        "rerank_max_retries": settings.RERANK_MAX_RETRIES,
        "rerank_min_interval_seconds": RERANK_MIN_INTERVAL_SECONDS,
    }


def load_existing(config: dict) -> dict[str, dict]:
    if not RESULTS_PATH.exists():
        return {}

    results = {}
    for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        result = json.loads(line)
        if result["run_config"] != config:
            raise RuntimeError(
                "기존 results.jsonl의 평가 설정이 다릅니다. "
                "기존 결과를 별도로 보관한 뒤 새로 실행하세요"
            )
        case_id = result["case_id"]
        if case_id in results:
            raise RuntimeError(f"results.jsonl에 중복 case_id가 있습니다: {case_id}")
        results[case_id] = result
    return results


def save_results(results: dict[str, dict]) -> None:
    temporary_path = RESULTS_PATH.with_suffix(".jsonl.tmp")
    with temporary_path.open("w", encoding="utf-8") as stream:
        for result in results.values():
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
    temporary_path.replace(RESULTS_PATH)


def workspace_filter() -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="workspace_id",
                match=models.MatchValue(value=WORKSPACE_ID),
            )
        ]
    )


def to_hits(points: list) -> list[dict]:
    hits = []
    for point in points:
        payload = point.payload or {}
        hits.append(
            {
                "chunk_id": payload["chunk_id"],
                "document_id": payload["document_id"],
                "document_version_id": payload["document_version_id"],
                "title": payload["title"],
                "content": payload["content"],
                "page_number": payload.get("page_number"),
                "section": payload.get("section"),
                "score": float(point.score),
            }
        )
    return hits


async def search_qdrant(
    client: AsyncQdrantClient,
    query: str,
    dense_vector: list[float],
    fusion: models.Fusion | None,
) -> tuple[list[dict], float]:
    query_filter = workspace_filter()
    started = perf_counter()

    if fusion is None:
        response = await client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=dense_vector,
            using="dense",
            query_filter=query_filter,
            limit=CANDIDATE_LIMIT,
            with_payload=True,
        )
    else:
        response = await client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            prefetch=[
                models.Prefetch(
                    query=dense_vector,
                    using="dense",
                    filter=query_filter,
                    limit=CANDIDATE_LIMIT,
                ),
                models.Prefetch(
                    query=models.Document(text=query, model="qdrant/bm25"),
                    using="bm25",
                    filter=query_filter,
                    limit=CANDIDATE_LIMIT,
                ),
            ],
            query=models.FusionQuery(fusion=fusion),
            limit=CANDIDATE_LIMIT,
            with_payload=True,
        )

    hits = to_hits(response.points)
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    return hits, elapsed_ms


def format_rerank_candidate(hit: dict) -> str:
    values = [f"title: {hit['title']}"]
    if hit["section"]:
        values.append(f"section: {hit['section']}")
    if hit["page_number"] is not None:
        values.append(f"page: {hit['page_number']}")
    values.append(f"content: {hit['content']}")
    return "\n".join(values)


def ranked_hits(hits: list[dict]) -> list[dict]:
    return [
        {"rank": rank, "chunk_id": hit["chunk_id"], "score": hit["score"]}
        for rank, hit in enumerate(hits, start=1)
    ]


async def collect_case(
    case: dict,
    client: AsyncQdrantClient,
    embedding: EmbeddingPort,
    reranker: RerankerPort | None,
    config: dict,
    last_rerank_started: float | None,
) -> tuple[dict, float | None]:
    query = case["question"].strip()
    started = perf_counter()
    dense_vector = await embedding.embed_query(query)
    embedding_ms = round((perf_counter() - started) * 1000, 2)

    dense, dense_ms = await search_qdrant(client, query, dense_vector, None)
    dbsf, dbsf_ms = await search_qdrant(client, query, dense_vector, models.Fusion.DBSF)
    rrf, rrf_ms = await search_qdrant(client, query, dense_vector, models.Fusion.RRF)

    candidate_pool = {}
    for hit in dense + dbsf + rrf:
        candidate_pool[str(hit["chunk_id"])] = {
            key: value for key, value in hit.items() if key != "score"
        }

    rerank_result = {"status": "disabled", "hits": [], "error": None}
    rerank_ms = None
    rerank_wait_ms = 0.0

    if reranker is not None and rrf:
        if last_rerank_started is not None:
            wait_seconds = max(
                0.0,
                RERANK_MIN_INTERVAL_SECONDS - (perf_counter() - last_rerank_started),
            )
            if wait_seconds:
                await asyncio.sleep(wait_seconds)
                rerank_wait_ms = round(wait_seconds * 1000, 2)

        last_rerank_started = perf_counter()
        try:
            reranked = await reranker.rerank(
                query=query,
                candidates=[
                    RerankCandidate(
                        chunk_id=hit["chunk_id"],
                        text=format_rerank_candidate(hit),
                    )
                    for hit in rrf
                ],
                limit=FINAL_LIMIT,
            )
        except (RerankerUnavailableError, RerankerRequestError) as exc:
            rerank_result = {
                "status": "failed",
                "hits": [],
                "error": type(exc).__name__,
            }
        else:
            rrf_by_id = {hit["chunk_id"]: hit for hit in rrf}
            rerank_result = {
                "status": "ok" if reranked else "empty",
                "hits": [
                    {
                        "rank": rank,
                        "chunk_id": hit.chunk_id,
                        "retrieval_score": rrf_by_id[hit.chunk_id]["score"],
                        "rerank_score": hit.score,
                    }
                    for rank, hit in enumerate(reranked, start=1)
                ],
                "error": None,
            }
        rerank_ms = round((perf_counter() - last_rerank_started) * 1000, 2)
    elif reranker is not None:
        rerank_result["status"] = "no_candidates"

    return (
        {
            "case_id": case["case_id"],
            "query": query,
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_config": config,
            "timing_ms": {
                "embedding": embedding_ms,
                "dense": dense_ms,
                "dbsf": dbsf_ms,
                "rrf": rrf_ms,
                "rerank": rerank_ms,
                "rerank_wait": rerank_wait_ms,
            },
            "candidates": candidate_pool,
            "methods": {
                "dense": ranked_hits(dense),
                "dbsf": ranked_hits(dbsf),
                "rrf": ranked_hits(rrf),
                "rerank": rerank_result,
            },
        },
        last_rerank_started,
    )


async def main() -> None:
    cases = load_cases()
    config = run_config()
    results = load_existing(config)
    selected = cases[:MAX_CASES] if MAX_CASES is not None else cases
    print(f"평가 질문 {len(selected)}/{len(cases)}개 수집")

    await init_qdrant()
    try:
        embedding = init_embedding()
        async with reranker_lifespan() as reranker:
            last_rerank_started = None
            for index, case in enumerate(selected, start=1):
                existing = results.get(case["case_id"])
                if existing and existing["methods"]["rerank"]["status"] in {
                    "ok", "disabled", "no_candidates"
                }:
                    print(f"[{index}/{len(selected)}] {case['case_id']}: 이미 수집됨")
                    continue

                result, last_rerank_started = await collect_case(
                    case=case,
                    client=get_qdrant(),
                    embedding=embedding,
                    reranker=reranker,
                    config=config,
                    last_rerank_started=last_rerank_started,
                )
                results[case["case_id"]] = result
                save_results(results)
                timing = result["timing_ms"]
                status = result["methods"]["rerank"]["status"]
                print(
                    f"[{index}/{len(selected)}] {case['case_id']}: "
                    f"embedding {timing['embedding']} ms, "
                    f"dense {timing['dense']} ms, "
                    f"DBSF {timing['dbsf']} ms, "
                    f"RRF {timing['rrf']} ms, "
                    f"rerank {status} ({timing['rerank']} ms)"
                )
    finally:
        await close_embedding()
        await close_qdrant()


if __name__ == "__main__":
    asyncio.run(main())
