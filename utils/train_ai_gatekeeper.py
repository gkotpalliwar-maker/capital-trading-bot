#!/usr/bin/env python3
"""Train the XGBoost gatekeeper from a labelled signal CSV."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "bot"
sys.path.insert(0, str(BOT_DIR))

from ai_gatekeeper import XGBoostGatekeeper


def main() -> int:
    parser = argparse.ArgumentParser(description="Train XGBoost AI gatekeeper")
    parser.add_argument("csv_path", help="CSV with historical signals and outcome/label columns")
    args = parser.parse_args()

    gatekeeper = XGBoostGatekeeper()
    meta = gatekeeper.train_model(args.csv_path)
    print("AI gatekeeper trained")
    print(f"Rows: {meta['rows']}")
    print(f"Test accuracy: {meta['test_accuracy']:.1%}")
    print(f"Test AUC: {meta['test_auc'] if meta['test_auc'] is not None else 'N/A'}")
    print(f"Model: {gatekeeper.model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
