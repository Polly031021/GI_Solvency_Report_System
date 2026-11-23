# 大型公司逐表逐年契约验收

- 更新时间：2026-11-23
- 范围：项目 `财险公司列表.xlsx` 中“前十大”10 家 + “大型公司”12 家，共 22 家
- 数据年份：2022–2025，按实际存在的 PDF 执行
- 验证工具：`tools/large_company_contract_audit.py`

## 1. 验证标准

每家公司、每个年份、每张目标表分别记录：

- 实际物理页码
- 数据行数
- 标准化后的表头
- 契约状态
- 页码窗口状态
- 必要行、禁止行、重复行校验结果

公司专属结果只有同时满足以下条件才算通过：

1. 公司 locator 返回目标表页码。
2. 公司 extractor 返回目标表。
3. 表头与 YAML `layout_contracts` 完全一致。
4. 数据列数一致。
5. 行数位于 `min_rows` 与 `max_rows` 之间。
6. `required_rows` 全部存在。
7. `forbidden_rows` 不存在。
8. 启用 `unique_rows` 时，指标行标签不重复。

原文明确不披露近三年投资收益率的年份，以“通过（未披露）”记录，不伪造表格。

## 2. 执行命令

```powershell
$py = "C:\Users\CaoRui\Desktop\GI_Solvency_Report_System\venv\Scripts\python.exe"
& $py tools/large_company_contract_audit.py `
  --workers 4 `
  --out-dir "C:\Users\CaoRui\Desktop\GI_Solvency_Report_System\tmp\large22_contract_matrix_final2"
```

产物：

- `contract_matrix.csv`：公司 × 年份 × 表级明细
- `contract_summary.csv`：公司 × 年份汇总
- 仓库内快照：
  `plans/large-company-contract-validation.csv`
  `plans/large-company-contract-summary.csv`

## 3. 当前结论

- 22 家公司全部具备公司专属 `locate_company_tables()` 和 `extract_company_tables()`。
- 22 家公司全部具备六张目标表的 YAML 版式契约。
- 所有具备有效文字层的目标表均通过当前契约校验。
- 矩阵共 513 行：487 张表通过契约，19 张原文明确未披露，
  4 张纯图片表未生成，3 个公司/年份无 PDF 样例。
- 前十大公司原先存在的缺契约、重复指标、流动性串表、跨页断行、文本表重建等问题已修复。
- 12 家大型公司已完成文档级 locator/extractor 接入，并保留各自原有 PDF 重建后处理。

## 4. 明确例外

以下情况不是“提取成功”的替代，必须在报告中保留异常状态：

| 公司 | 年份 | 情况 | 处理 |
| --- | --- | --- | --- |
| 大家财险 | 2022 | 数据目录没有对应 PDF | 记录“样例缺失” |
| 申能财险 | 2022 | 公司尚未开展业务，无报告 | 记录“样例缺失” |
| 申能财险 | 2023 | 数据目录没有对应 PDF | 记录“样例缺失” |
| 诚泰财险 | 2022 | 实际资本、最低资本、流动性等 4 张表为纯图片页，无文字层 | 仅验收 2 张文本表；其余标记未生成，等待 OCR/视觉链路 |

## 5. 后续门槛

契约通过不代表所有数值必然正确。后续仍需：

- 对关键指标进行 PDF 原值黄金样本核对。
- 对 STEP4 勾稽失败项逐条归因。
- 对扫描件和纯图片表接入视觉模型或人工确认。
- 完成 STEP7 报告展示和导出回归。
