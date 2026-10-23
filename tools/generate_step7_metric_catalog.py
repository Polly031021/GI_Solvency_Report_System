"""Regenerate config/Step7_指标需求清单.xlsx from the STEP7 template.

Usage:
    python tools/generate_step7_metric_catalog.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_metric_catalog import (  # noqa: E402
    DEFAULT_STEP7_CATALOG_PATH,
    write_step7_metric_catalog,
)


def main() -> None:
    target = write_step7_metric_catalog()
    print(f"Wrote {target}")
    print(f"Default path: {DEFAULT_STEP7_CATALOG_PATH}")


if __name__ == "__main__":
    main()
