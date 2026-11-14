"""Explicit live retrieval evaluation; never invoked by pytest or app startup.

LEARNTRAIL_EVAL_TOKEN supplies an existing Java access token (not a CLI argument).
Run: uv run --locked python -m learntrail_agent.rag.evaluation --index --answers
Output is local/ignored by default. Qrels and corpus hashes are committed fixtures.
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from learntrail_agent.clients.java import ArticleUnavailable
from learntrail_agent.errors import AppError
from learntrail_agent.main import create_app
from learntrail_agent.rag.schemas import SearchRequest
from learntrail_agent.security.auth import authenticate_token
from learntrail_agent.settings import PROJECT_ROOT


def metrics(rows: list[dict], k: int) -> dict[str, float | int]:
    positives = [r for r in rows if r["relevant"]]
    negatives = [r for r in rows if not r["relevant"]]
    recall, reciprocal = [], []
    for row in positives:
        relevant = set(row["relevant"])
        ranked = row["ranked"][:k]
        recall.append(len(relevant & set(ranked)) / len(relevant))
        reciprocal.append(next((1 / rank for rank, slug in enumerate(ranked, 1) if slug in relevant), 0.0))
    return {"k":k, "positive_cases":len(positives), "negative_cases":len(negatives),
            "recall":sum(recall)/len(recall) if recall else 0.0,
            "mrr":sum(reciprocal)/len(reciprocal) if reciprocal else 0.0,
            "negative_nonempty_rate":sum(bool(r["ranked"][:k]) for r in negatives)/len(negatives) if negatives else 0.0,
            "forbidden_hits":sum(len(set(r["ranked"][:k]) & set(r.get("forbidden", []))) for r in rows)}


async def evaluate(dataset: dict, token: str, index: bool, answers: bool) -> dict:
    app = create_app()
    async with app.router.lifespan_context(app):
        state, s = app.state, app.state.settings
        user = await authenticate_token(token, state.jwt_public_key, state.http, s)
        corpus = []
        for expected in dataset["sources"]:
            try:
                source = await state.java_articles.get_published_article(expected["slug"], token)
            except ArticleUnavailable:
                if expected["available"]:
                    raise RuntimeError("Evaluation corpus changed: published source is unavailable") from None
                corpus.append({"slug":expected["slug"], "status":"unavailable"})
                continue
            if not expected["available"]:
                raise RuntimeError("Evaluation corpus changed: withdrawn fixture is published")
            doc, _, _ = state.article_indexer.prepare_source(source)
            if doc.content_hash != expected["content_hash"]:
                raise RuntimeError("Evaluation corpus hash changed; review qrels before rerunning")
            if index:
                result = await state.article_indexer.index_article(expected["slug"], token)
                corpus.append(result.model_dump())
            else:
                saved = await state.vector_repository.get_state(user.user_id, source.article.id)
                if saved is None or not saved.active or saved.content_hash != doc.content_hash:
                    raise RuntimeError("Evaluation source not indexed; explicitly pass --index")
                corpus.append({"slug":expected["slug"], "status":"existing"})
        slugs = [source["slug"] for source in dataset["sources"]]
        report = {"time":datetime.now(timezone.utc).isoformat(), "dataset_version":dataset["version"],
            "metric_definition":dataset["metrics"], "corpus":corpus,
            "settings":{key:getattr(s, key) for key in ("embedding_model", "embedding_dimensions",
                "embedding_model_revision", "rag_chunk_size", "rag_chunk_overlap", "rag_min_similarity",
                "rag_candidate_k", "rag_bm25_k1", "rag_bm25_b", "rag_bm25_min_score", "rag_rrf_k")},
            "strategies":{}, "answers":[]}
        for strategy in ("vector", "bm25", "hybrid", "hybrid_reranker"):
            if strategy == "hybrid_reranker" and not s.reranker_configured:
                report["strategies"][strategy] = {"status":"unverified", "reason":"RERANKER_NOT_CONFIGURED"}
                continue
            rows = []
            for case in dataset["cases"]:
                result = await state.retriever.search(user.user_id, SearchRequest(query=case["query"],
                    mode="hybrid" if strategy == "hybrid_reranker" else strategy,
                    top_k=5, article_slugs=slugs, rerank=strategy == "hybrid_reranker"), token)
                rows.append({"id":case["id"], "category":case["category"], "relevant":case["relevant"],
                    "forbidden":case.get("forbidden", []), "ranked":[h.slug for h in result.results],
                    "scores":[h.score for h in result.results], "duration_ms":result.duration_ms})
            report["strategies"][strategy] = {"status":"verified", "cases":rows,
                                               "metrics":[metrics(rows, k) for k in (1, 3, 5)]}
            print(json.dumps({"strategy":strategy, "metrics":report["strategies"][strategy]["metrics"]},
                             ensure_ascii=False), flush=True)
        if answers:
            session = await state.sessions.create(user.user_id, "真实 RAG 固定语料评测")
            for case in dataset["cases"]:
                try:
                    result = await state.rag_service.answer(user.user_id, session.id,
                        SearchRequest(query=case["query"], top_k=5, article_slugs=slugs), token)
                    row = {"id":case["id"], "expected_answer_available":case["answer_available"],
                        "status":result.status, "answer":result.answer, "citations":[c.slug for c in result.citations],
                        "usage":result.usage.model_dump() if result.usage else None, "duration_ms":result.duration_ms}
                except AppError as error:
                    row = {"id":case["id"], "status":"error", "code":error.code}
                report["answers"].append(row)
                print(json.dumps({k:v for k,v in row.items() if k not in {"answer"}}, ensure_ascii=False), flush=True)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT/"tests/fixtures/rag_retrieval_cases.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT.parent/".local/run/rag-retrieval-report.json")
    parser.add_argument("--index", action="store_true", help="Explicitly index fixture articles in the AI database")
    parser.add_argument("--answers", action="store_true", help="Explicitly call the live chat model for fixture questions")
    args = parser.parse_args()
    token = os.getenv("LEARNTRAIL_EVAL_TOKEN", "")
    if not token:
        parser.error("Set LEARNTRAIL_EVAL_TOKEN to an existing Java access token; never pass it on the command line")
    report = asyncio.run(evaluate(json.loads(args.dataset.read_text(encoding="utf-8")), token, args.index, args.answers))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.chmod(0o600)


if __name__ == "__main__":
    main()
