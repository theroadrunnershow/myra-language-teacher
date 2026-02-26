#!/usr/bin/env python3
from __future__ import annotations
"""Collect [TIMING] metrics from a local log file or GCP Cloud Logging.

Usage
-----
  # Capture local logs while running the app
  python main.py 2>&1 | tee local.log
  python tools/collect_metrics.py --source local --input local.log --output local_metrics.json

  # Pull last 24 h of GCP Cloud Run logs
  python tools/collect_metrics.py --source gcp --project my-project --output gcp_metrics.json

  # Override environment label
  python tools/collect_metrics.py --source local --input local.log --output local_metrics.json --env laptop
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

# Matches the [TIMING] prefix anywhere in a log line
_TIMING_RE = re.compile(r"\[TIMING\]\s+(.+)")
# Handles both  key='quoted value'  and  key=unquoted
_KV_RE = re.compile(r"(\w+)='([^']*)'|(\w+)=([^\s']+)")

_NUMERIC_KEYS = {"duration_ms", "size_bytes", "text_len", "audio_bytes", "audio_duration_ms", "best_sim"}
_BOOL_KEYS = {"correct", "cold_start", "is_correct"}


def _parse_kv(kv_str: str) -> dict:
    result = {}
    for m in _KV_RE.finditer(kv_str):
        if m.group(1):  # quoted
            key, val = m.group(1), m.group(2)
        else:           # unquoted
            key, val = m.group(3), m.group(4)

        # Type coercion
        if key in _NUMERIC_KEYS:
            try:
                result[key] = float(val)
            except ValueError:
                result[key] = val
        elif key in _BOOL_KEYS:
            result[key] = val.lower() in ("true", "1", "yes")
        else:
            result[key] = val
    return result


def _parse_timing_line(line: str, timestamp: str | None = None) -> dict | None:
    m = _TIMING_RE.search(line)
    if not m:
        return None
    sample = _parse_kv(m.group(1))
    if not sample.get("step"):
        return None
    if timestamp:
        sample["timestamp"] = timestamp
    return sample


def _extract_timestamp_from_line(line: str) -> str | None:
    """Try to grab an ISO/logging timestamp from a local log line."""
    # Python logger default: '2024-01-15 12:00:00,123'
    m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", line)
    return m.group(1).replace(" ", "T") + "Z" if m else None


# ---------------------------------------------------------------------------
# Local log file
# ---------------------------------------------------------------------------

def parse_local_log(filepath: str) -> list[dict]:
    samples = []
    with open(filepath, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            ts = _extract_timestamp_from_line(line)
            sample = _parse_timing_line(line, timestamp=ts)
            if sample:
                samples.append(sample)
    return samples


# ---------------------------------------------------------------------------
# GCP Cloud Logging
# ---------------------------------------------------------------------------

def parse_gcp_logs(project: str, service: str, hours: int) -> list[dict]:
    since = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    log_filter = (
        f'resource.type="cloud_run_revision" '
        f'resource.labels.service_name="{service}" '
        f'(textPayload=~"\\[TIMING\\]" OR jsonPayload.message=~"\\[TIMING\\]") '
        f'timestamp>="{since}"'
    )

    cmd = [
        "gcloud", "logging", "read", log_filter,
        "--project", project,
        "--format", "json",
        "--limit", "2000",
    ]
    print(f"Running: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"gcloud error:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    try:
        entries = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        print(f"Failed to parse gcloud output as JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    samples = []
    for entry in entries:
        timestamp = entry.get("timestamp", "")
        # Cloud Run may log as textPayload or jsonPayload.message
        text = entry.get("textPayload") or entry.get("jsonPayload", {}).get("message", "")
        sample = _parse_timing_line(text, timestamp=timestamp)
        if sample:
            samples.append(sample)

    return samples


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect [TIMING] metrics into a JSON file for the dashboard."
    )
    parser.add_argument("--source", choices=["local", "gcp"], required=True,
                        help="Where to read logs from.")
    parser.add_argument("--input", metavar="FILE",
                        help="Local log file (required when --source local).")
    parser.add_argument("--project", metavar="PROJECT_ID",
                        help="GCP project ID (required when --source gcp).")
    parser.add_argument("--service", default="dino-app",
                        help="Cloud Run service name (default: dino-app).")
    parser.add_argument("--hours", type=int, default=24,
                        help="Hours of history to pull from GCP (default: 24).")
    parser.add_argument("--output", required=True, metavar="FILE",
                        help="Output JSON path.")
    parser.add_argument("--env", default=None,
                        help="Environment label (default: 'local' or 'gcp').")
    args = parser.parse_args()

    if args.source == "local":
        if not args.input:
            parser.error("--input is required when --source local")
        samples = parse_local_log(args.input)
        env_label = args.env or "local"
    else:
        if not args.project:
            parser.error("--project is required when --source gcp")
        samples = parse_gcp_logs(args.project, args.service, args.hours)
        env_label = args.env or "gcp"

    output = {
        "env": env_label,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(samples),
        "samples": samples,
    }

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2)

    print(f"✓ Collected {len(samples)} timing samples → {args.output}")


if __name__ == "__main__":
    main()
