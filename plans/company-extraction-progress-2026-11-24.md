# 财险公司专属提取进度（2026-11-24）

- 分支：`feature/track-company-and-rule-config`
- 基线提交：`a54fe23`
- 范围：`财险公司列表.xlsx` 中 89 家财险公司
- 目标表：`SOLVENCY_MAIN`、`NON_LIFE_OPERATING_METRICS`、
  `ACTUAL_CAPITAL`、`MINIMUM_CAPITAL`、`LIQUIDITY_MONITORING`、
  `THREE_YEAR_INVESTMENT_RETURN`

## 1. 当前结论

2025 年已经完成一次 89 家全量契约验证：

| 结果 | 数量 |
| --- | ---: |
| 契约通过 | 519 |
| 原文明确未披露 | 3 |
| 样例缺失（没有 PDF） | 2 |
| 失败 | 0 |
| 未生成 | 0 |
| 合计检查记录 | 524 |

完整明细：

- `plans/all-company-contract-validation-2025.csv`
- `plans/all-company-contract-summary-2025.csv`

明确例外：

| 公司 | 表 | 状态 | 原因 |
| --- | --- | --- | --- |
| 前海财险 | `THREE_YEAR_INVESTMENT_RETURN` | 通过（未披露） | 原文没有独立的近三年投资收益率表 |
| 劳合社 | `ACTUAL_CAPITAL` | 通过（未披露） | 特殊主体仅披露整体层面充足率 |
| 劳合社 | `MINIMUM_CAPITAL` | 通过（未披露） | 特殊主体仅披露整体层面充足率 |
| 安心财险 | 全部目标表 | 样例缺失 | `Data/pdfs(2025)` 没有 PDF |
| 长安保险 | 全部目标表 | 样例缺失 | `Data/pdfs(2025)` 没有 PDF |

“契约通过”表示页码、表头、列数、最小行数、必要指标和跨页结构通过校验；
不等于所有金额都已经完成逐项人工黄金值核对。

## 2. 2022–2024 状态

2022–2024 已按异常公司分组完成定向验证，但尚未在所有修复完成后重新合并成
一次 89 家全量矩阵。三个定向快照均为 0 失败：

| 范围 | 记录数 | 通过 | 未披露 | 失败 | 样例缺失 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 中路、久隆、史带、长安 | 81 | 74 | 4 | 0 | 3 |
| 华海、国元、黄河、阳光信保 | 96 | 92 | 4 | 0 | 0 |
| 比亚迪、兴亚、浙商、珠峰 | 91 | 87 | 3 | 0 | 1 |

这三组公司互不重叠，合计覆盖 12 家公司、2022–2025 年。
其余在首轮 2022–2024 全量快照中失败的项目，已经完成以下共享层或公司规则修复：

- 原文没有近三年投资收益率时，按精确披露措辞识别并标记为“未披露”，
  不再只依据是否出现“近三年”三个字。
- 年度页码优先使用 YAML 的 `expected_pages`，超出 PDF 页数时按实际末页收敛。
- 实际资本、最低资本和流动性表在契约校验前补齐缺失列，但不补造数值。
- 2022 年中文数字年份（如“二零二五年”）可正确识别。
- 长江、恒邦、中煤、太平科技等公司的跨年页码和文本/OCR 重建已复核。

下一次跨年收敛仍应执行完整 2022–2024 矩阵，并以 0 失败、0 未生成作为门槛。

## 3. 架构现状

所有 89 家公司都可以取得公司级 document hooks：

```text
PDF
  → 公司别名与年度路由
  → 公司专属 / 兼容 locator
  → 公司专属 / 兼容 extractor
  → layout contract
  → STEP3 标准化
```

共享层不再把“有后处理模块”误当成“已完成专属迁移”：

- 67 家只有 `postprocess()` 的公司由自动适配器提供 locator/extractor，
  但仍按公司 YAML 的页码、表头、必要行和年度结构执行契约。
- 16 家已有成熟全文/页级解析器的公司走 `LEGACY_PAGE_ADAPTERS`。
- 22 家前十大/大型公司保留显式 YAML 契约和专属逻辑。
- 中煤、恒邦、太平科技新增纯扫描页 OCR 重建。

OCR 实现：

- `services/company_extractors/_ocr.py`
- 依赖：`rapidocr_onnxruntime`，本地离线运行
- 缓存：`gi_solvency_cache/ocr_v1/<pdf-sha1>/<page>_<scale>.json`
- 缓存只保存 OCR 词块与坐标，不保存业务结论
- 识别失败或结构不完整时仍判失败，不把噪声写成 0

## 4. 2025 复现命令

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:OMP_NUM_THREADS = "4"

& .\venv\Scripts\python.exe tools\large_company_contract_audit.py `
  --all-companies `
  --years 2025 `
  --workers 6 `
  --out-dir tmp\all89_2025_final
```

建议使用 `--workers 6`。扫描件 OCR 共用一个本地引擎，提高到 12 并发不会线性变快，
反而会因锁竞争变慢。相同 PDF 的 OCR 页在首次完成并写入磁盘缓存后会显著加速。

## 5. 后续门槛

1. 用当前代码重跑 2022–2024 全量矩阵，要求 0 失败、0 未生成。
2. 对关键财务指标建立 PDF 黄金值样本，而非只检查表结构。
3. 对 STEP4 勾稽失败和 STEP7 缺口逐条归因。
4. 对纯图片表保留“未生成/需人工确认”，不以 OCR 结果直接替代监管原文。
5. 每次修改公司 YAML 或 extractor 后，至少重跑该公司全部年份的定向矩阵。
