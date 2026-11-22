"""Inspect priority metrics while preserving the source's blank-versus-zero state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


METRICS = {
    "CONTRACT_LOSS_ABSORPTION_EFFECT": "特定类别保险合同损失吸收效应",
    "PREMIUM_GROWTH": "规模保费同比增速",
}


def audit_priority_metrics(folder: Path) -> pd.DataFrame:
    records = []
    for path in sorted(folder.glob("*/*/tables.json")):
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        data = pd.read_csv(path.with_name("standard.csv"))
        for code, label in METRICS.items():
            disclosed = []
            for table in snapshot["tables"]:
                if table["table_id"] != ("MINIMUM_CAPITAL" if code == "CONTRACT_LOSS_ABSORPTION_EFFECT" else "LIQUIDITY_MONITORING"):
                    continue
                for block in table["blocks"]:
                    for row in block["rows"]:
                        if row and label in row[0]:
                            disclosed.append((block["page"], row[1:]))
            for period, offset in (("本季度末数", 0), ("上季度末数", 1)):
                values = data[data["指标编码"].eq(code) & data["期间口径"].eq(period)]
                original = [(page, cells[offset]) for page, cells in disclosed if len(cells) > offset]
                raw = original[-1][1] if original else None
                status = ("原文空白按0" if raw == "" and not values.empty
                          else "已提取" if not values.empty
                          else "原文空白" if raw == ""
                          else "未找到披露行" if raw is None else "提取缺失")
                records.append({"公司": path.parent.parent.name, "年份": path.parent.name,
                                "指标编码": code, "重点指标": label, "期间口径": period,
                                "原始披露值": raw, "原文页码": original[-1][0] if original else "",
                                "数值": values.iloc[-1]["数值"] if not values.empty else None,
                                "来源页码": values.iloc[-1]["来源页码"] if not values.empty else "",
                                "状态": status})
    return pd.DataFrame(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", required=True)
    args = parser.parse_args()
    folder = Path(args.audit_dir)
    result = audit_priority_metrics(folder)
    result.to_csv(folder / "priority_metrics_audit.csv", index=False, encoding="utf-8-sig")
    print(result.groupby(["重点指标", "状态"]).size().to_string())
    return int(result.empty or result["状态"].eq("提取缺失").any())


if __name__ == "__main__":
    raise SystemExit(main())
