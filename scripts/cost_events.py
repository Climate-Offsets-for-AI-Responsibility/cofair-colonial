#!/usr/bin/env python3
"""Deterministic request-level cost events for meter and ledger runners."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "dashboard" / "data"
COST_EVENTS_FILE = DATA_DIR / "cost_events.json"
_FORBIDDEN_SAVE_KEYS = frozenset({"error", "api_key", "secret", "credential", "password", "token"})

_ALLOWED_BUILD_KWARGS = frozenset(
    {
        "date",
        "run_at",
        "source",
        "provider_id",
        "tier",
        "task_id",
        "turn",
        "request_kind",
        "api_model",
        "input_tokens",
        "output_tokens",
        "input_price_per_1m",
        "output_price_per_1m",
        "pricing_snapshot_date",
        "corpus_version",
        "chat_corpus_version",
        "run_id",
        "billable",
        "replicate",
    }
)


def now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CostEventInput:
    date: str
    run_at: str
    source: str
    provider_id: str
    tier: str
    task_id: str
    turn: int | None
    request_kind: str
    api_model: str
    input_tokens: int | None
    output_tokens: int | None
    input_price_per_1m: float | None
    output_price_per_1m: float | None
    pricing_snapshot_date: str | None
    corpus_version: str
    chat_corpus_version: str | None
    run_id: str
    billable: bool = True
    replicate: int = 1


def cost_event_id(
    *,
    date: str,
    source: str,
    provider_id: str,
    tier: str,
    task_id: str,
    turn: int | None,
    request_kind: str,
    run_id: str,
    replicate: int = 1,
) -> str:
    return ":".join(
        map(
            str,
            (
                date,
                source,
                provider_id,
                tier,
                task_id,
                turn or 0,
                request_kind,
                run_id,
                replicate,
            ),
        )
    )


def _sort_key(row: dict[str, Any]) -> tuple:
    turn = row.get("turn")
    return (
        row.get("date") or "",
        row.get("source") or "",
        row.get("provider_id") or "",
        row.get("tier") or "",
        row.get("task_id") or "",
        turn is None,
        turn or 0,
        row.get("event_id") or "",
    )


def build_cost_event(
    input_row: CostEventInput | None = None,
    /,
    **kwargs: Any,
) -> dict[str, Any]:
    if input_row is not None and kwargs:
        raise TypeError("pass either CostEventInput or keyword arguments, not both")
    if input_row is not None:
        fields = {
            "date": input_row.date,
            "run_at": input_row.run_at,
            "source": input_row.source,
            "provider_id": input_row.provider_id,
            "tier": input_row.tier,
            "task_id": input_row.task_id,
            "turn": input_row.turn,
            "request_kind": input_row.request_kind,
            "api_model": input_row.api_model,
            "input_tokens": input_row.input_tokens,
            "output_tokens": input_row.output_tokens,
            "input_price_per_1m": input_row.input_price_per_1m,
            "output_price_per_1m": input_row.output_price_per_1m,
            "pricing_snapshot_date": input_row.pricing_snapshot_date,
            "corpus_version": input_row.corpus_version,
            "chat_corpus_version": input_row.chat_corpus_version,
            "run_id": input_row.run_id,
            "billable": input_row.billable,
            "replicate": input_row.replicate,
        }
    else:
        unknown = set(kwargs) - _ALLOWED_BUILD_KWARGS
        if unknown:
            raise TypeError(f"unexpected keyword arguments: {sorted(unknown)}")
        fields = dict(kwargs)

    date = fields["date"]
    run_at = fields.get("run_at") or now_iso_z()
    source = fields["source"]
    provider_id = fields["provider_id"]
    tier = fields["tier"]
    task_id = fields["task_id"]
    turn = fields.get("turn")
    request_kind = fields["request_kind"]
    api_model = fields["api_model"]
    input_tokens = fields.get("input_tokens")
    output_tokens = fields.get("output_tokens")
    input_price_per_1m = fields.get("input_price_per_1m")
    output_price_per_1m = fields.get("output_price_per_1m")
    pricing_snapshot_date = fields.get("pricing_snapshot_date")
    corpus_version = fields["corpus_version"]
    chat_corpus_version = fields["chat_corpus_version"]
    run_id = fields["run_id"]
    billable = fields.get("billable", True)
    replicate = fields.get("replicate", 1)

    complete = (
        input_tokens is not None
        and output_tokens is not None
        and input_price_per_1m is not None
        and output_price_per_1m is not None
        and pricing_snapshot_date is not None
    )
    if not billable:
        if input_tokens is not None and output_tokens is not None and pricing_snapshot_date is not None:
            input_cost = 0.0
            output_cost = 0.0
            total = 0.0
            complete = True
        else:
            input_cost = output_cost = total = None
            complete = False
    elif complete:
        input_cost = input_tokens / 1_000_000 * input_price_per_1m
        output_cost = output_tokens / 1_000_000 * output_price_per_1m
        total = input_cost + output_cost
    else:
        input_cost = output_cost = total = None

    event_id = cost_event_id(
        date=date,
        source=source,
        provider_id=provider_id,
        tier=tier,
        task_id=task_id,
        turn=turn,
        request_kind=request_kind,
        run_id=run_id,
        replicate=replicate,
    )

    return {
        "event_id": event_id,
        "date": date,
        "run_at": run_at,
        "source": source,
        "provider_id": provider_id,
        "tier": tier,
        "task_id": task_id,
        "turn": turn,
        "request_kind": request_kind,
        "api_model": api_model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_price_per_1m": input_price_per_1m,
        "output_price_per_1m": output_price_per_1m,
        "input_cost_usd": input_cost,
        "output_cost_usd": output_cost,
        "estimated_cost_usd": total,
        "pricing_snapshot_date": pricing_snapshot_date,
        "corpus_version": corpus_version,
        "chat_corpus_version": chat_corpus_version,
        "run_id": run_id,
        "complete": complete,
    }


def merge_cost_events(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {row["event_id"]: row for row in existing}
    for row in incoming:
        merged[row["event_id"]] = row
    rows = list(merged.values())
    rows.sort(key=_sort_key)
    return rows


def load_cost_events(path: Path = COST_EVENTS_FILE) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    return list(payload.get("rows", []))


def save_cost_events(rows: list[dict], path: Path = COST_EVENTS_FILE) -> None:
    for row in rows:
        lowered = {str(key).lower() for key in row}
        forbidden = sorted(key for key in lowered if key in _FORBIDDEN_SAVE_KEYS)
        if forbidden:
            raise ValueError(f"forbidden keys in cost event: {forbidden}")
    sorted_rows = sorted(rows, key=_sort_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": now_iso_z(),
                "row_count": len(sorted_rows),
                "rows": sorted_rows,
            },
            indent=2,
        )
        + "\n"
    )
