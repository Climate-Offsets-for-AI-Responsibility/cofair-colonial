#!/usr/bin/env python3
"""Emit incident envelope for non-scrape colonial pipelines."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from incident import build_incident_envelope, now_iso_z, send_slack_message, write_incident, format_slack_context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--status", required=True, choices=["success", "degraded", "failed", "escalated"])
    parser.add_argument("--error", default=None)
    parser.add_argument("--run-id", default=str(uuid.uuid4()))
    args = parser.parse_args()

    envelope = build_incident_envelope(
        pipeline=args.pipeline,
        status=args.status,
        run_id=args.run_id,
        started_at=now_iso_z(),
        error_message=args.error,
    )
    path = write_incident(envelope)
    print(f"Wrote {path}")

    if args.status in ("failed", "escalated", "degraded"):
        send_slack_message(
            format_slack_context(
                headline=f"Pipeline {args.status.upper()}: {args.pipeline}",
                run_id=args.run_id,
                pipeline=args.pipeline,
                extra_lines=[f"error: {args.error or 'none'}"],
            ),
            pipeline=args.pipeline,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
