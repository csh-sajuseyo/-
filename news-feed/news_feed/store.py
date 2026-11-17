"""Event store: news_events.jsonl (append-only, never rewritten) + labels.jsonl (late AI labels)
+ summaries.jsonl (body summaries of important articles; the article text itself is never stored).

An item that could not be classified when it arrived (no key yet, API error, cap reached) is retried
later; the result is appended to labels.jsonl and merged in on read. Readers should use load_events().
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

LABEL_FIELDS = ("coins", "event_type", "direction", "importance", "summary_ko", "why", "crypto_impact")


def read_tail(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    rows: deque = deque(maxlen=n)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(line)
    out = []
    for line in rows:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_labels(data_dir: Path) -> dict[str, dict]:
    return {r["id"]: r for r in read_tail(data_dir / "labels.jsonl", 10**6) if r.get("id")}


def load_summaries(data_dir: Path) -> dict[str, dict]:
    return {r["id"]: r["body_summary"] for r in read_tail(data_dir / "summaries.jsonl", 10**6)
            if r.get("id") and r.get("body_summary")}


def load_events(data_dir: Path, n: int = 3000) -> list[dict]:
    rows = read_tail(data_dir / "news_events.jsonl", n)
    labels = load_labels(data_dir)
    sums = load_summaries(data_dir)
    if not labels and not sums:
        return rows
    out = []
    for r in rows:
        lab = labels.get(r.get("id"))
        if lab and not r.get("classified"):
            r = {**r, **{k: lab.get(k) for k in LABEL_FIELDS}, "classified": True,
                 "model": lab.get("model"), "labelled_at": lab.get("labelled_at")}
        if r.get("id") in sums:
            r = {**r, "body_summary": sums[r["id"]]}
        out.append(r)
    return out


def append_summaries(data_dir: Path, summaries: dict[str, dict]) -> None:
    if not summaries:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "summaries.jsonl").open("a", encoding="utf-8") as fh:
        for item_id, s in summaries.items():
            fh.write(json.dumps({"id": item_id, "body_summary": s}, ensure_ascii=False) + "\n")


def append_labels(data_dir: Path, labels: dict[str, dict], model: str, at: str) -> None:
    if not labels:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "labels.jsonl").open("a", encoding="utf-8") as fh:
        for item_id, lab in labels.items():
            fh.write(json.dumps({"id": item_id, "model": model, "labelled_at": at,
                                 **{k: lab.get(k) for k in LABEL_FIELDS}}, ensure_ascii=False) + "\n")
