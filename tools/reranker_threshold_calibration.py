#!/usr/bin/env python3
"""Reranker 阈值标定实验。

数据：
- 正例（positive）：cognihub event_sources 表里 linked_by ∈ {codex-coldstart, llm-manual} 的 (event, source) 对
- 负例（negative）：对每个正例，给同一 source 配 3 个**没**关联到的 event，作为反例

跑：
- 对每对计算 reranker score
- 按 score 排序、画 PR 曲线
- 输出推荐阈值（F1 最高 / Precision >= 0.9 时的最低 recall 等）

执行：
    /Users/zhang.longqiang/PycharmProjects/PythonProject/.venv/bin/python \
        tools/reranker_threshold_calibration.py \
        --db /tmp/cognihub.db.snapshot \
        --reranker http://127.0.0.1:5071 \
        --sample 200
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx


def fetch_pairs(db_path: str, sample_size: int):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 取 events
    events = list(conn.execute("SELECT id, title, description, topics FROM events WHERE is_active = 1"))
    event_map = {e["id"]: dict(e) for e in events}

    # 正例：从 event_sources 取 linked_by codex-coldstart 或 llm-manual
    positive_links = list(conn.execute(
        """SELECT es.event_id, es.source_id, es.linked_by, es.relevance_score
           FROM event_sources es
           JOIN events e ON e.id = es.event_id
           WHERE es.linked_by IN ('codex-coldstart', 'llm-manual') AND e.is_active = 1
           ORDER BY RANDOM()
           LIMIT ?""",
        (sample_size,),
    ))

    # 反例：给每个正例 source 找它**没关联**到的 event，随机抽几个
    pair_results = []  # list of dict
    for link in positive_links:
        event_id = link["event_id"]
        source_id = link["source_id"]
        if event_id not in event_map:
            continue
        # 取 source
        src_row = conn.execute(
            "SELECT id, title, content FROM source_items WHERE id=?", (source_id,)
        ).fetchone()
        if not src_row:
            continue

        # 正例
        pair_results.append({
            "label": 1,
            "event_id": event_id,
            "source_id": source_id,
            "linked_by": link["linked_by"],
            "ground_score": link["relevance_score"],
            "event_title": event_map[event_id]["title"],
            "source_title": src_row["title"],
            "source_content": src_row["content"],
            "event_description": event_map[event_id]["description"],
            "event_topics": event_map[event_id]["topics"],
        })

        # 反例：从该 source 没关联到的 event 里抽 3 个
        linked_event_ids = {
            row[0] for row in conn.execute(
                "SELECT event_id FROM event_sources WHERE source_id=?", (source_id,)
            )
        }
        unlinked_eids = [eid for eid in event_map if eid not in linked_event_ids]
        random.shuffle(unlinked_eids)
        for neg_eid in unlinked_eids[:3]:
            pair_results.append({
                "label": 0,
                "event_id": neg_eid,
                "source_id": source_id,
                "linked_by": "negative-sample",
                "ground_score": None,
                "event_title": event_map[neg_eid]["title"],
                "source_title": src_row["title"],
                "source_content": src_row["content"],
                "event_description": event_map[neg_eid]["description"],
                "event_topics": event_map[neg_eid]["topics"],
            })
    conn.close()
    return pair_results


def build_event_query(title: str, description: str | None, topics_json: str | None) -> str:
    parts = [(title or "").strip()]
    if description:
        d = description.strip()
        if d:
            parts.append(d)
    if topics_json:
        try:
            ts = json.loads(topics_json)
            if isinstance(ts, list):
                ts_str = ", ".join(str(t) for t in ts if t)
                if ts_str:
                    parts.append(f"主题：{ts_str}")
        except Exception:
            pass
    return "\n".join(parts)


def build_source_doc(title: str | None, content: str | None, max_chars: int = 1500) -> str:
    t = (title or "").strip()
    c = (content or "").strip()
    if c and len(c) > max_chars:
        c = c[:max_chars] + "...(已截断)"
    if t and c:
        return f"{t}\n\n{c}"
    return t or c


def score_all(pairs: list[dict], reranker_url: str, batch_size: int = 8) -> None:
    """对每个 pair 计算 reranker score，写回 pair["rerank_score"]。

    实测发现 reranker 是 (query, doc) 不对称的：
    我们采用 query=event_query, doc=source_doc 的方向（reranker 标准用法）。
    但当前 link_v2 用的是相反方向 query=source / docs=events。
    本实验同时测两个方向，给出对比。
    """
    # group by query 减少调用：按 event 聚合
    by_event: dict[int, list[int]] = defaultdict(list)
    for i, p in enumerate(pairs):
        by_event[p["event_id"]].append(i)

    print(f"scoring {len(pairs)} pairs across {len(by_event)} events...", flush=True)
    t0 = time.time()
    with httpx.Client(timeout=1800.0, trust_env=False) as client:
        # === 方向 A: query=event, docs=sources ===
        done = 0
        for event_id, indices in by_event.items():
            event_query = build_event_query(
                pairs[indices[0]]["event_title"],
                pairs[indices[0]]["event_description"],
                pairs[indices[0]]["event_topics"],
            )
            docs = [
                build_source_doc(pairs[i]["source_title"], pairs[i]["source_content"])
                for i in indices
            ]
            # 切 batch
            for s in range(0, len(docs), batch_size):
                batch_docs = docs[s : s + batch_size]
                batch_idx = indices[s : s + batch_size]
                r = client.post(
                    f"{reranker_url}/rerank",
                    json={"query": event_query, "docs": batch_docs, "batch_size": batch_size},
                )
                r.raise_for_status()
                scores = r.json()["scores"]
                for j, sc in zip(batch_idx, scores):
                    pairs[j]["score_event_query"] = float(sc)
                done += len(batch_idx)
            print(f"  A direction: {done}/{len(pairs)}  elapsed={time.time()-t0:.0f}s", flush=True)

        # === 方向 B: query=source, docs=events （v2 当前实现的方向） ===
        # 按 source 聚合
        by_source: dict[int, list[int]] = defaultdict(list)
        for i, p in enumerate(pairs):
            by_source[p["source_id"]].append(i)
        done = 0
        for source_id, indices in by_source.items():
            source_doc = build_source_doc(
                pairs[indices[0]]["source_title"], pairs[indices[0]]["source_content"]
            )
            docs = [
                build_event_query(
                    pairs[i]["event_title"],
                    pairs[i]["event_description"],
                    pairs[i]["event_topics"],
                )
                for i in indices
            ]
            for s in range(0, len(docs), batch_size):
                batch_docs = docs[s : s + batch_size]
                batch_idx = indices[s : s + batch_size]
                r = client.post(
                    f"{reranker_url}/rerank",
                    json={"query": source_doc, "docs": batch_docs, "batch_size": batch_size},
                )
                r.raise_for_status()
                scores = r.json()["scores"]
                for j, sc in zip(batch_idx, scores):
                    pairs[j]["score_source_query"] = float(sc)
                done += len(batch_idx)
            print(f"  B direction: {done}/{len(pairs)}  elapsed={time.time()-t0:.0f}s", flush=True)


def evaluate_threshold(pairs: list[dict], score_field: str, thresholds: list[float]) -> list[dict]:
    """对每个候选阈值算 precision/recall/F1。"""
    rows = []
    pos_count = sum(1 for p in pairs if p["label"] == 1)
    for thr in thresholds:
        tp = sum(1 for p in pairs if p["label"] == 1 and p[score_field] >= thr)
        fp = sum(1 for p in pairs if p["label"] == 0 and p[score_field] >= thr)
        fn = sum(1 for p in pairs if p["label"] == 1 and p[score_field] < thr)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        rows.append({
            "threshold": thr,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        })
    return rows


def print_table(direction_name: str, rows: list[dict], pos_count: int):
    print(f"\n=== {direction_name} ===")
    print(f"  positives in test set: {pos_count}")
    print(f"  {'thr':>6} {'precision':>10} {'recall':>8} {'F1':>8} {'TP':>5} {'FP':>5} {'FN':>5}")
    for r in rows:
        print(
            f"  {r['threshold']:>6.2f} {r['precision']:>10.3f} "
            f"{r['recall']:>8.3f} {r['f1']:>8.3f} "
            f"{r['tp']:>5} {r['fp']:>5} {r['fn']:>5}"
        )
    # 推荐
    best_f1 = max(rows, key=lambda r: r["f1"])
    p_high = [r for r in rows if r["precision"] >= 0.9]
    print(f"  best F1 @ thr={best_f1['threshold']:.2f}: P={best_f1['precision']:.3f} R={best_f1['recall']:.3f} F1={best_f1['f1']:.3f}")
    if p_high:
        chosen = max(p_high, key=lambda r: r["recall"])
        print(f"  P>=0.9   @ thr={chosen['threshold']:.2f}: P={chosen['precision']:.3f} R={chosen['recall']:.3f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/tmp/cognihub.db.snapshot")
    parser.add_argument("--reranker", default="http://100.109.140.6:5071")
    parser.add_argument("--sample", type=int, default=30, help="正例数量")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="/tmp/reranker_calibration.json")
    args = parser.parse_args()

    random.seed(args.seed)

    print(f"[1] fetching pairs from {args.db} (sample={args.sample})")
    pairs = fetch_pairs(args.db, args.sample)
    pos = sum(1 for p in pairs if p["label"] == 1)
    neg = sum(1 for p in pairs if p["label"] == 0)
    print(f"    got {len(pairs)} pairs ({pos} positive + {neg} negative)")

    print(f"[2] scoring via {args.reranker}")
    score_all(pairs, args.reranker, batch_size=4)

    print(f"[3] evaluation")
    thresholds = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    rows_a = evaluate_threshold(pairs, "score_event_query", thresholds)
    rows_b = evaluate_threshold(pairs, "score_source_query", thresholds)
    print_table("Direction A: query=event_title+desc, doc=source_title+content", rows_a, pos)
    print_table("Direction B: query=source_title+content, doc=event_title+desc (v2 当前实现)", rows_b, pos)

    # 落盘
    Path(args.out).write_text(json.dumps({
        "pairs": pairs,
        "thresholds_A": rows_a,
        "thresholds_B": rows_b,
        "positive_count": pos,
        "negative_count": neg,
    }, ensure_ascii=False, indent=2))
    print(f"\nfull data saved to {args.out}")


if __name__ == "__main__":
    main()
