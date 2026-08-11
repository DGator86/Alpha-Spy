#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from alpha_spy.config import load_config

CONFIRMATION = "APPROVE ALPHA-SPY PRODUCTION"
EXPECTED_STATUS = "ELIGIBLE_FOR_MANUAL_LIVE_REVIEW"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a human approval bound to a passed Alpha-SPY paper-validation report."
    )
    parser.add_argument("evidence", type=Path, help="Passed promotion/validation JSON report")
    parser.add_argument("--config", default="/etc/alpha-spy/config.yaml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--confirm", required=True, help=f"Must equal: {CONFIRMATION}")
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise SystemExit("Refusing approval: confirmation phrase did not match exactly")
    if not args.evidence.is_file():
        raise SystemExit(f"Evidence file does not exist: {args.evidence}")

    config = load_config(args.config)
    # Approval is constructed while broker submission is disabled, never while a
    # production order path is already armed.
    if config.trading.submit_orders:
        raise SystemExit("Disable trading.submit_orders before creating a production approval artifact")

    evidence = args.evidence.resolve()
    evidence_bytes = evidence.read_bytes()
    try:
        report = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SystemExit(f"Evidence must be a JSON validation report: {exc}") from exc
    if report.get("status") != EXPECTED_STATUS:
        raise SystemExit(f"Refusing approval: validation status is not {EXPECTED_STATUS}")
    if report.get("automatic_live_enable") is not False:
        raise SystemExit("Refusing approval: validation report live-enable semantics are invalid")
    if str(report.get("model_version") or "") != config.prediction.model_version:
        raise SystemExit("Refusing approval: validation model_version differs from runtime")
    if str(report.get("config_fingerprint") or "") != config.production_fingerprint():
        raise SystemExit("Refusing approval: validation config fingerprint differs from runtime")
    if report.get("failed_gates"):
        raise SystemExit("Refusing approval: validation report still contains failed gates")

    digest = hashlib.sha256(evidence_bytes).hexdigest()
    output = args.output or config.paths.production_approval
    approval = {
        "approved": True,
        "approved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "model_version": config.prediction.model_version,
        "config_fingerprint": config.production_fingerprint(),
        "validation_id": report.get("validation_id"),
        "validation_status": report.get("status"),
        "evidence_path": str(evidence),
        "evidence_sha256": digest,
        "note": (
            "Human approval is bound to one passed paper-validation report. "
            "Any model/config/evidence change invalidates this artifact."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(approval, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
