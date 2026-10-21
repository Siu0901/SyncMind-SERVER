import hashlib
import json
from collections import defaultdict
from math import log2
from pathlib import Path


CASES_PATH = Path(__file__).with_name("cases_all_text.jsonl")
RESULTS_PATH = Path(__file__).with_name("results.jsonl")
REPORT_PATH = Path(__file__).with_name("evaluation.json")
METHODS = ("dense", "dbsf", "rrf", "rerank")
KS = (1, 5, 10)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def index_by_case_id(items: list[dict], name: str) -> dict[str, dict]:
    indexed = {item["case_id"]: item for item in items}
    if len(indexed) != len(items):
        raise ValueError(f"{name}: case_id가 중복되었습니다")
    return indexed


def ranked_hits(result: dict, method: str) -> list[dict]:
    if method == "rerank":
        rerank = result["methods"]["rerank"]
        if rerank["status"] != "ok":
            raise ValueError(
                f"{result['case_id']}: rerank 상태가 ok가 아닙니다: "
                f"{rerank['status']}"
            )
        return rerank["hits"]
    return result["methods"][method]


def score_case(hits: list[dict], gold_ids: set[int]) -> dict:
    ids = [hit["chunk_id"] for hit in hits]
    if len(ids) != len(set(ids)):
        raise ValueError("한 검색 결과에 같은 chunk_id가 중복되었습니다")

    gold_ranks = [rank for rank, chunk_id in enumerate(ids, 1) if chunk_id in gold_ids]
    scores = {"first_gold_rank": gold_ranks[0] if gold_ranks else None}

    for k in KS:
        found = sum(rank <= k for rank in gold_ranks)
        dcg = sum(1 / log2(rank + 1) for rank in gold_ranks if rank <= k)
        ideal = sum(1 / log2(rank + 1) for rank in range(1, min(k, len(gold_ids)) + 1))
        scores[
            f"at_{k}"] = {
            "hit": int(found > 0),
            "mrr": 1 / gold_ranks[0] if gold_ranks and gold_ranks[0] <= k else 0.0,
            "known_gold_recall": found / len(gold_ids),
            "precision_lower_bound": found / k,
            "known_gold_ndcg": dcg / ideal,
        }

    return scores


def aggregate(case_scores: list[dict]) -> dict:
    count = len(case_scores)
    return {
        f"at_{k}": {
            metric: round(sum(case[f"at_{k}"][metric] for case in case_scores) / count, 4)
            for metric in (
                "hit", "mrr", "known_gold_recall",
                "precision_lower_bound", "known_gold_ndcg",
            )
        }
        for k in KS
    }


def main() -> None:
    cases = index_by_case_id(read_jsonl(CASES_PATH), "cases")
    results = index_by_case_id(read_jsonl(RESULTS_PATH), "results")
    if not cases or cases.keys() != results.keys():
        raise ValueError("질문과 수집 결과의 case_id가 일치하지 않습니다")

    dataset_hash = hashlib.sha256(CASES_PATH.read_bytes()).hexdigest()
    run_configs = {
        json.dumps(result["run_config"], sort_keys=True)
        for result in results.values()
    }
    if len(run_configs) != 1:
        raise ValueError("결과 파일에 서로 다른 실행 설정이 섞여 있습니다")
    run_config = next(iter(results.values()))["run_config"]
    if run_config["dataset_sha256"] != dataset_hash:
        raise ValueError("수집 이후 질문 파일이 변경되었습니다")

    per_case = []
    by_method = {method: [] for method in METHODS}
    by_domain = defaultdict(lambda: {method: [] for method in METHODS})

    for case_id, case in cases.items():
        result = results[case_id]
        if result["query"] != case["question"].strip():
            raise ValueError(f"{case_id}: 수집 당시 질문과 현재 질문이 다릅니다")
        gold_ids = set(case["gold_chunk_ids"])
        if not gold_ids:
            raise ValueError(f"{case_id}: gold_chunk_ids가 비어 있습니다")

        method_scores = {}
        for method in METHODS:
            hits = ranked_hits(result, method)
            if len(hits) < max(KS):
                raise ValueError(f"{case_id}: {method} 결과가 {max(KS)}개 미만입니다")
            scored = score_case(hits, gold_ids)
            if method != "rerank":
                scored["known_gold_in_top_30"] = int(
                    any(hit["chunk_id"] in gold_ids for hit in hits[:30])
                )
            method_scores[method] = scored
            by_method[method].append(scored)
            by_domain[case["domain"]][method].append(scored)

        per_case.append({
            "case_id": case_id,
            "domain": case["domain"],
            "gold_chunk_ids": sorted(gold_ids),
            "methods": method_scores,
        })

    summary = {
        method: {
            "metrics": aggregate(scores),
            **({"known_gold_hit_at_30": round(
                sum(score["known_gold_in_top_30"] for score in scores) / len(scores), 4
            )} if method != "rerank" else {}),
        }
        for method, scores in by_method.items()
    }
    report = {
        "case_count": len(per_case),
        "label_scope": (
            "gold_chunk_ids는 확인된 근거만 포함합니다. hit/MRR/recall은 확인된 "
            "근거 기준이며, precision은 하한값, nDCG는 미확인 청크를 0으로 "
            "간주한 잠정값입니다. 전체 관련 청크 기준의 확정 지표가 아닙니다."
        ),
        "run_config": run_config,
        "summary": summary,
        "timing_observation": {
            "note": (
                "질문당 1회, dense→DBSF→RRF 고정 순서로 측정한 참고값입니다. "
                "정식 지연 시간 벤치마크가 아니며 rerank_wait는 제외했습니다."
            ),
            "stage_mean_ms": {
                stage: round(
                    sum(result["timing_ms"][stage] for result in results.values())
                    / len(results),
                    2,
                )
                for stage in ("embedding", "dense", "dbsf", "rrf", "rerank")
            },
        },
        "by_domain": {
            domain: {
                "case_count": len(scores["dense"]),
                "methods": {
                    method: aggregate(method_scores)
                    for method, method_scores in scores.items()
                },
            }
            for domain, scores in sorted(by_domain.items())
        },
        "cases": per_case,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"{len(per_case)}개 질문 평가 완료 → {REPORT_PATH}")
    print("확인된 gold_chunk_ids 기준 잠정 결과 (Hit@K / MRR@K)")
    for method, data in summary.items():
        parts = [
            f"@{k} {data['metrics'][f'at_{k}']['hit']:.3f} / "
            f"{data['metrics'][f'at_{k}']['mrr']:.3f}"
            for k in KS
        ]
        print(f"{method:7} " + " | ".join(parts))
    print("Precision 하한값·nDCG 잠정값과 도메인별/질문별 결과는 evaluation.json에 저장했습니다")


if __name__ == "__main__":
    main()