"""AI+ shared ledger: every Claude call of every project, one line each, in C:\\CodexProjects\\_ai\\ledger.jsonl.

- This project only appends its own lines (project "news-feed"); a lock file makes each append atomic
  even with other projects writing at the same time. The folder and the file are created if missing.
- Monthly limit: C:\\CodexProjects\\_ai\\limits.json, key "news-feed" (default $15). The file is created
  only if it does not exist; an existing file is never rewritten (other projects own their keys).
- Month = calendar month in KST.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .budget import KST

PROJECT = "news-feed"
DEFAULT_MONTHLY_USD = 15.0


def _usage_get(usage: Any, k: str) -> int:
    v = usage.get(k) if isinstance(usage, dict) else getattr(usage, k, 0)
    return int(v or 0)


class SharedLedger:
    def __init__(self, folder: Path, project: str = PROJECT) -> None:
        self.folder = Path(folder)
        self.path = self.folder / "ledger.jsonl"
        self.limits_path = self.folder / "limits.json"
        self.project = project
        self._cache: tuple[float, int, str, float] | None = None  # (mtime, size, month, usd)

    # ------------------------------------------------------------------ writing
    def _lock(self) -> int | None:
        lock = self.folder / "ledger.jsonl.lock"
        for _ in range(200):  # ~10 s
            try:
                return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:  # a lock older than 60 s was left by a crashed writer
                    if time.time() - lock.stat().st_mtime > 60:
                        lock.unlink()
                        continue
                except FileNotFoundError:
                    continue
                time.sleep(0.05)
        return None

    def append(self, feature: str, model: str, usage: Any, usd: float, batch: bool = False,
               now: datetime | None = None) -> dict:
        now = now or datetime.now(KST)
        cache_w = _usage_get(usage, "cache_creation_input_tokens")
        cache_r = _usage_get(usage, "cache_read_input_tokens")
        row = {"ts": now.astimezone(KST).isoformat(timespec="seconds"), "project": self.project,
               "feature": feature, "model": model,
               "in_tok": _usage_get(usage, "input_tokens") + cache_w + cache_r,
               "out_tok": _usage_get(usage, "output_tokens"),
               "usd": round(usd, 8), "batch": bool(batch)}
        if cache_w or cache_r:
            row["cache_write_tok"], row["cache_read_tok"] = cache_w, cache_r
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        self.folder.mkdir(parents=True, exist_ok=True)
        fd_lock = self._lock()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0))
            try:
                os.write(fd, line)  # one write of one whole line
            finally:
                os.close(fd)
        finally:
            if fd_lock is not None:
                os.close(fd_lock)
                try:
                    (self.folder / "ledger.jsonl.lock").unlink()
                except FileNotFoundError:
                    pass
        self._cache = None
        return row

    # ------------------------------------------------------------------ reading
    def monthly_limit(self) -> float:
        try:
            data = json.loads(self.limits_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.limits_path.with_name("limits.json.tmp")
            body = json.dumps({self.project: {"monthly_usd": DEFAULT_MONTHLY_USD}}, indent=2)
            tmp.write_text(body, encoding="utf-8")
            try:
                os.link(tmp, self.limits_path)  # create only if still missing (never overwrite)
            except (FileExistsError, OSError):
                pass
            finally:
                tmp.unlink(missing_ok=True)
            return DEFAULT_MONTHLY_USD
        except (json.JSONDecodeError, OSError):
            return DEFAULT_MONTHLY_USD
        v = data.get(self.project) if isinstance(data, dict) else None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            for k in ("monthly_usd", "monthly", "usd", "limit_usd", "month_usd"):
                if isinstance(v.get(k), (int, float)):
                    return float(v[k])
        return DEFAULT_MONTHLY_USD

    def month_spent(self, now: datetime | None = None) -> float:
        month = (now or datetime.now(KST)).astimezone(KST).strftime("%Y-%m")
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return 0.0
        if self._cache and self._cache[:3] == (st.st_mtime, st.st_size, month):
            return self._cache[3]
        total = 0.0
        with self.path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if f'"{self.project}"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("project") == self.project and str(r.get("ts", "")).startswith(month):
                    total += float(r.get("usd") or 0)
        self._cache = (st.st_mtime, st.st_size, month, total)
        return total

    def allows(self, worst_usd: float, now: datetime | None = None) -> bool:
        return self.month_spent(now) + worst_usd <= self.monthly_limit()

    def status(self, now: datetime | None = None) -> dict:
        return {"month_spent_usd": round(self.month_spent(now), 4), "monthly_limit_usd": self.monthly_limit()}


class MonthlyLimitReached(Exception):
    pass
