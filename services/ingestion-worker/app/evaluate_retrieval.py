from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg


def main() -> int:
    dataset_path = Path(os.environ.get("EVALUATION_DATASET", "/app/evaluation/odoo_lexical_v1.json"))
    output_path = Path(os.environ.get("EVALUATION_OUTPUT", "/results/odoo-lexical-v1-results.json"))
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    top_k = int(dataset["top_k"])
    results = []
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT scope_id FROM knowledge.scopes WHERE scope_key=%s", ("product:odoo:documentation:19.0",))
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Odoo documentation scope is missing")
            scope_id = row[0]
            for case in dataset["cases"]:
                started = time.perf_counter()
                cur.execute(
                    """SELECT passage_id,rank,source_path,line_start,line_end,source_revision,citation_uri
                       FROM knowledge.search_passages(%s,%s,%s)""",
                    (case["question"], scope_id, top_k),
                )
                hits = [
                    {"passage_id":str(r[0]),"rank":r[1],"source_path":r[2],"line_start":r[3],
                     "line_end":r[4],"source_revision":r[5],"citation_uri":r[6]}
                    for r in cur.fetchall()
                ]
                latency_ms = (time.perf_counter() - started) * 1000
                expected = set(case["expected_paths"])
                first_rank = next((i + 1 for i, hit in enumerate(hits) if hit["source_path"] in expected), None)
                citation_valid = all(
                    h["source_revision"] == dataset["source_revision"]
                    and h["line_start"] >= 1 and h["line_end"] >= h["line_start"]
                    and h["citation_uri"].startswith("https://github.com/odoo/documentation/blob/")
                    for h in hits
                )
                results.append({
                    "id":case["id"],"question":case["question"],"expected_paths":case["expected_paths"],
                    "relevant_rank":first_rank,"hit":first_rank is not None,"citation_valid":citation_valid,
                    "latency_ms":round(latency_ms,3),"results":hits,
                })
    hits = sum(1 for r in results if r["hit"])
    reciprocal_rank = sum(1 / r["relevant_rank"] for r in results if r["relevant_rank"]) / len(results)
    citation_cases = sum(1 for r in results if r["citation_valid"])
    latencies = sorted(r["latency_ms"] for r in results)
    p50 = latencies[(len(latencies)-1)//2]
    p95 = latencies[max(0, int(len(latencies)*0.95)-1)]
    report = {
        "dataset":dataset["name"],"evaluated_at":datetime.now(timezone.utc).isoformat(),
        "source_revision":dataset["source_revision"],"top_k":top_k,"case_count":len(results),
        "recall_at_k":hits / len(results),"mean_reciprocal_rank":reciprocal_rank,
        "citation_integrity_rate":citation_cases / len(results),
        "latency_ms":{"p50":p50,"p95":p95,"max":max(latencies)},"cases":results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = [
        f"# {report['dataset']} results", "",
        f"- Source revision: `{report['source_revision']}`",
        f"- Recall@{top_k}: {report['recall_at_k']:.1%}",
        f"- Mean reciprocal rank: {report['mean_reciprocal_rank']:.3f}",
        f"- Citation integrity: {report['citation_integrity_rate']:.1%}",
        f"- Query latency: p50 {p50:.1f} ms; p95 {p95:.1f} ms; max {max(latencies):.1f} ms", "",
        "| Case | Hit | Relevant rank | Latency ms | Top result |", "|---|---:|---:|---:|---|",
    ]
    for item in results:
        top_path = item["results"][0]["source_path"] if item["results"] else ""
        markdown.append(f"| {item['id']} | {'yes' if item['hit'] else 'no'} | {item['relevant_rank'] or '-'} | {item['latency_ms']:.1f} | `{top_path}` |")
    output_path.with_suffix(".md").write_text("\n".join(markdown)+"\n", encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("dataset","case_count","recall_at_k","mean_reciprocal_rank","citation_integrity_rate","latency_ms")}))
    return 0 if report["recall_at_k"] >= 0.90 and report["citation_integrity_rate"] == 1.0 else 2


if __name__ == "__main__":
    sys.exit(main())
