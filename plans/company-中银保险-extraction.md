# 中银保险（中银保险有限公司）提取规则审计与补强

范围：`Data/pdfs(2022)/中银保险.pdf`、`Data/pdfs(2023)/中银保险.pdf`、
`Data/pdfs(2024)/中银保险.pdf`、`Data/pdfs(2025)/中银保险.pdf`，共 4 份报告（2022–2025）。
页码均为 PDF 物理页（封面为第 1 页）。实测环境：Windows，`.venv` Python 3.12；
未改共享层、未动其他公司文件、未执行 git 写操作。

## 0. 结论

- 新增 `config/extraction/companies/中银保险.yaml`（`rule_version: 1`、`extractor: zhongyin`、
  `tolerance: 2`、`page_match: prefer`、单条 `structure_versions.since: 2022`）。
- 新增 `services/company_extractors/zhongyin.py`，只重建三类表：经营指标表、流动性监测表、
  最低资本表；主表、实际资本表、近三年收益率表保持通用结果。所有重建都从 PDF 文本锚点出发，
  不写死年份和页码，重复执行幂等，只改 `table.rows` / `table.table_blocks`。
- 定位结果：2022 为 5/6（全册确无“近三年（综合）投资收益率”表，属披露缺失，不是漏检）；
  2023、2024、2025 均为 6/6。四份报告的公司级状态全部“成功”。
- 提取行数：2022 共 5 表 118 行；2023–2025 各 6 表 121 行。标准化（保留未映射）：
  2022 213 条/102 编码/18 个未映射编码；2023–2025 各 217 条/103 编码/18 个未映射编码。
  `--strict-metrics` 下：2022 177 条/84 编码/0 未映射，2023–2025 各 181 条/85 编码/0 未映射。
- 2024 关键值与 PDF 逐项一致：认可资产 13,133,269,291.93 元、实际资本 5,073,788,987.43 元、
  最低资本 1,179,908,448.50 元、保险业务收入 6,212,036,393.43 元、净利润 346,983,461.81 元、
  综合成本率 99.10%。

## 1. YAML 页码窗口（6 表 × 4 年）

| 表 | 2022 | 2023 | 2024 | 2025 | expected_pages | 其他 |
|---|---|---|---|---|---|---|
| SOLVENCY_MAIN | [15] | [14] | [15] | [14] | [14, 15] | anchor 偿付能力充足率 |
| NON_LIFE_OPERATING_METRICS | [17] | [16] | [17] | [16] | [16, 17] | anchor 主要经营指标 |
| ACTUAL_CAPITAL | [33] | [35] | [31] | [30] | [30, 31, 33, 35] | anchor 实际资本表 |
| MINIMUM_CAPITAL | [36, 37] | [38, 39] | [34, 35] | [33, 34] | [33, 34, 36, 38] | include_continuation: true, max_pages: 2 |
| THREE_YEAR_INVESTMENT_RETURN | 无此表 | [15] | [16] | [15] | [15, 16] | anchor 近三年 |
| LIQUIDITY_MONITORING | [16] | [15] | [16] | [15] | [15, 16] | anchor 流动性风险监测指标 |

说明：

- 四年版式一致，只是页码随正文长度平移：`expected_pages` 取四年实测页的并集，窗口按 min..max
  再向两侧扩 `tolerance: 2`。例如 MIN 的窗口是 31–40，2022 的 36–37、2023 的 38–39 都在窗口内。
- `page_match: prefer` 保留全文锚点回退。2022 的 THREE_YEAR 在窗口内无法命中，回退全文也没有，
  因此定位结果为空，属预期；不是把别的表硬凑成近三年表。
- `structure_versions` 只有一条 `since: 2022`：四年表头、列数、章节顺序相同，没有必须按年覆盖的
  结构差异。2022 与 2023+ 的唯一差异是 S05 少“量化风险最低资本（未考虑特征系数前）”一行
  （33 行 vs 34 行），由 extractor 按实际行数容纳，不需要拆版本。

## 2. aliases 依据

- `canonical: 中银保险` 与文件名主干一致，满足加载校验。
- `aliases: [中银保险有限公司]`：四年封面均为“中银保险有限公司”，`resolve_rule` 按封面公司名
  归一化后命中；2022 封面报告期为 2022Q4，与 `--years 2022` 一致。
- 没有再加“中银财险”等口语简称，避免与其它公司别名冲突；当前别名集合加载无警告。

## 3. 每表问题与处置

| 表 | 通用结果的问题 | 处置 | 处置后 |
|---|---|---|---|
| SOLVENCY_MAIN | 无；四年第 15/14 页，16 行，列头“指标名称/本季度数/上季度数/下季度预测数”。 | 不重建。 | 16 行 1 块；认可资产、实际资本、最低资本、核心/综合充足率均可用。 |
| NON_LIFE_OPERATING_METRICS | 页内区域裁剪只保留到“1.综合成本率”，仅 13 行；效益类、规模类、签单保费、渠道明细全丢。 | `_rebuild_operating`：从“财产保险公司主要经营指标”后的“指标名称”开始，按“标签行 + 两个数值”重建；删除 3 个分组标题，保留“基本每股收益 --/--”这类真实空值行；校验首行保险业务收入、末行其他渠道签单保费。 | 30 行（表头 + 29 数据行）1 块，四年一致。 |
| ACTUAL_CAPITAL | 16 行 3 列，标签被换行拆成带空格文本（如“差 额”“递 延所得税资产”）。 | 不重建；标准化器 `_normalize_label` 去空白后可匹配。 | 16 行 1 块；实际资本合计与主表一致。 |
| MINIMUM_CAPITAL | 跨页两块；混入【第N页表格分块】占位行；续页 3.1–3.4 在 PDF 网格里多一列，通用结果列数不一致。 | `_rebuild_minimum`：定位“S05-最低资本表”+“行次”，首页从表头后解析、续页整页解析；按“行次 + 项目 + 两个数值”重建三列；首块写表头，续块不写表头（STEP3 继承）；校验末行“最低资本”、含量化风险/附加资本。 | 2022 34 行（表头 + 33 数据）、2023–2025 35 行（表头 + 34 数据），2 块；无占位行，3.1–3.4 不再错列。 |
| THREE_YEAR_INVESTMENT_RETURN | 2022 无表；2023–2025 通用 2 行正常（近三年平均投资收益率/综合投资收益率）。 | 不重建。 | 2023–2025 均 2 行 1 块；2022 缺表。 |
| LIQUIDITY_MONITORING | 与“（二）监管指标”“（四）近三年”同页；`whole_page=True` 每页只取第一个 match，导致 LIQ 只拿到近三年 2 行；另外监管段 11 行含“（本季度）/（上季度）”两行回溯偏差率，四值压成两期间会互相覆盖。 | `_rebuild_liquidity`：在同一页分别截取“监管指标”到“（三）”、“监测指标”到“（四）/近三年”；监管 11 行解析后把 LCR 长标签收敛为 `LCR1未来3个月` 等 taxonomy 短标签，并只保留“（本季度）”回溯行；监测 10 行。输出两个 block，各自带表头。 | 22 行 = 2 表头 + 10 监管 + 10 监测；6 个 LCR 全部映射到 `LCR1_3M` 等，回溯率本季度=当季值、上季度=上季值，不再覆盖。 |

extractor 约束：`@register("zhongyin")`；`postprocess(tables, *, pdf_bytes)`；只改
`table.rows` / `table.table_blocks`；不改 `table_id`、单位、定位页；不写死年份/页码；
结构校验不通过时直接返回，保留通用结果；异常不在这里捕获，由 `apply_company_extractor_for_tables`
统一记录。`py_compile` 通过。

## 4. 2024 验证（YAML + extractor）

| 表 | 定位页 | 行数 | 块数 | 单位 | 状态 |
|---|---|---|---|---|---|
| SOLVENCY_MAIN | [15] | 16 | 1 | 元 | 成功 |
| NON_LIFE_OPERATING_METRICS | [17] | 30 | 1 | 元 | 成功（通用只有 13 行） |
| ACTUAL_CAPITAL | [31] | 16 | 1 | 元 | 成功 |
| THREE_YEAR_INVESTMENT_RETURN | [16] | 2 | 1 | 元（数值带 %） | 成功 |
| MINIMUM_CAPITAL | [34, 35] | 35 | 2 | 元 | 成功 |
| LIQUIDITY_MONITORING | [16] | 22 | 2 | 元（数值带 %/元） | 成功（通用只有 2 行） |

标准化：217 条 / 103 个编码 / 103 个有值 / 18 个未映射编码，状态“成功”；
`--strict-metrics`：181 条 / 85 个编码 / 0 未映射，状态“成功”。

2024 关键值（万元 / %）：

| 指标 | 数值 | 期间 |
|---|---:|---|
| 认可资产 | 1,313,326.929193 | 本季度末数 |
| 认可负债 | 805,948.030450 | 本季度末数 |
| 实际资本 | 507,378.898743 | 本季度末数 |
| 最低资本 | 117,990.844850 | 本季度末数 |
| 保险业务收入（累计） | 621,203.639343 | 本年累计数 |
| 净利润（累计） | 34,698.346181 | 本年累计数 |
| 综合成本率（累计） | 99.10 | 本年累计数 |
| 签单保费（累计） | 642,018.390830 | 本年累计数 |
| 核心偿付能力充足率 | 409.24 | 本季度末数 |
| 综合偿付能力充足率 | 430.02 | 本季度末数 |
| 近三年平均投资收益率 | 3.91 | 近三年平均 |
| 近三年平均综合投资收益率 | 3.60 | 近三年平均 |

## 5. 2025 终检

| 表 | 定位页 | 行数 | 块数 | 状态 |
|---|---|---|---|---|
| SOLVENCY_MAIN | [14] | 16 | 1 | 成功 |
| NON_LIFE_OPERATING_METRICS | [16] | 30 | 1 | 成功 |
| ACTUAL_CAPITAL | [30] | 16 | 1 | 成功 |
| THREE_YEAR_INVESTMENT_RETURN | [15] | 2 | 1 | 成功 |
| MINIMUM_CAPITAL | [33, 34] | 35 | 2 | 成功 |
| LIQUIDITY_MONITORING | [15] | 22 | 2 | 成功 |

标准化：217 条 / 103 个编码 / 103 个有值 / 18 个未映射，状态“成功”；
`--strict-metrics`：181 条 / 85 个编码 / 0 未映射。

2025 关键值（万元 / %）：认可资产 1,350,156.473403；实际资本 524,548.319408；
最低资本 125,338.833153；保险业务收入累计 647,430.757789；净利润累计 54,194.862566；
综合成本率 97.67；签单保费累计 670,612.491044；综合偿付能力充足率 418.50；
近三年平均投资收益率 3.67、综合 3.51。

## 6. 关键指标核对（万元 / %）

| 指标 | 2022 | 2023 | 2024 | 2025 | 来源页（22/23/24/25） |
|---|---:|---:|---:|---:|---|
| 认可资产 | 1,413,434.71 | 1,315,025.39 | 1,313,326.93 | 1,350,156.47 | 15/14/15/14 |
| 认可负债 | 932,699.84 | 823,998.68 | 805,948.03 | 825,608.15 | 15/14/15/14 |
| 实际资本 | 480,734.87 | 491,026.71 | 507,378.90 | 524,548.32 | 15/14/15/14 |
| 最低资本 | 142,274.39 | 117,486.03 | 117,990.84 | 125,338.83 | 36-37/38-39/34-35/33-34 |
| 保险业务收入（累计） | 579,990.61 | 605,529.10 | 621,203.64 | 647,430.76 | 17/16/17/16 |
| 净利润（累计） | 30,243.60 | 33,323.56 | 34,698.35 | 54,194.86 | 17/16/17/16 |
| 综合成本率（累计） | 98.94 | 96.69 | 99.10 | 97.67 | 17/16/17/16 |
| 签单保费（累计） | 601,755.94 | 627,406.56 | 642,018.39 | 670,612.49 | 17/16/17/16 |
| 核心偿付能力充足率 | 312.80 | 391.39 | 409.24 | 397.84 | 15/14/15/14 |
| 综合偿付能力充足率 | 337.89 | 417.94 | 430.02 | 418.50 | 15/14/15/14 |
| 近三年平均投资收益率 | —（无表） | 4.63 | 3.91 | 3.67 | —/15/16/15 |
| 近三年平均综合投资收益率 | —（无表） | 4.17 | 3.60 | 3.51 | —/15/16/15 |

补充核对：2022 综合成本率本季度为 `--`、累计 98.94；2023 本季度 `--`、累计 96.69；
2024 本季度 `--`、累计 99.10；2025 本季度 `--`、累计 97.67。四年最低资本与 S05 末行合计一致；
实际资本与 S02 合计一致。2022 综合成本率与费用率、赔付率均从 PDF 整页文本恢复后与表内数字核对，
未出现“通用 13 行截断”导致的缺失。

## 7. 2022 无近三年表的确认

- PDF 全文检索“近三年”在 2022 文件中 0 次命中；2022 第 16 页只有“（二）流动性风险监管指标”
  和“（三）流动性风险监测指标”两段，没有“（四）近三年（综合）投资收益率”。
- 2023–2025 同一位置均有“（四）”段，且 extractor/定位器均能取到 2 行，说明不是规则或页码问题，
  而是 2022 披露缺失。
- YAML 仍保留 `THREE_YEAR_INVESTMENT_RETURN` 表配置（供 2023+ 使用），但 2022 定位为空；
  没有用其它投资收益率行硬凑“近三年平均”。

## 8. 遗留问题与共享层观察

1. **同页多表只取第一个 match（共性缺陷，未修）**：
   `services/batch_workflow.py:316-319` 在 `whole_page=True` 时执行
   `tasks = [(page, page_matches[0]) for page, page_matches in ...]`，每页只让表配置顺序中的第一个
   match 做整页提取。2023–2025 的流动性表与近三年表同页，通用流程因此把近三年 2 行塞给
   LIQUIDITY_MONITORING；2024 无 extractor 时可复现 LIQ=2 行、OPER=13 行、MIN=36 行。
   复现命令（只读，不改文件）：

   ```bash
   PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe - <<'PY'
   from pathlib import Path
   from services.batch_processor import build_context_from_profile, locate_one_pdf_bytes
   from services.batch_workflow import extract_document_tables
   from services.report_profiles import load_profile_workbook
   root = Path(".").resolve()
   profile = load_profile_workbook((root/"config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx").read_bytes(), project_root=root)
   ctx = build_context_from_profile(profile, api_key="", base_url="", model="")
   pdf = (root/"Data/pdfs(2024)/中银保险.pdf").read_bytes()
   loc = locate_one_pdf_bytes("中银保险", pdf, ctx, mode="rule")
   for m in loc["matches"]:
       m.table_config.setdefault("_extraction_rule", {})["extractor"] = ""
   out = extract_document_tables(company="中银保险", pdf_bytes=pdf, matches=loc["matches"],
       method="rule", allow_llm_fallback=False, whole_page=True,
       api_key=ctx.api_key, base_url=ctx.base_url, model=ctx.model)
   print([(t.table_id, len(t.rows)) for t in out.tables])
   PY
   ```

   期望输出包含 `('LIQUIDITY_MONITORING', 2)`、`('NON_LIFE_OPERATING_METRICS', 13)`、
   `('MINIMUM_CAPITAL', 36)`；加回 `extractor: zhongyin` 后分别为 22、30、35。当前由 extractor
   按 PDF 文本重建兜底，未建议在本任务中修改共享层。

2. **指标库别名/覆盖缺口（数据层，非提取错误）**：
   - `LCR1（未来3个月基本情景下公司整体流动性覆盖率）` 归一化后比 taxonomy 别名
     `LCR1未来3个月` 多出“基本情景下公司整体流动性覆盖率”，coverage 不足，得分 0.68 < 0.72，
     原样进入 RAW；extractor 已把标签收敛为短标签，标准化可映射到 `LCR1_3M` 等。
   - 流动性监测的“二、百元保费经营活动净现金流”“三、特定业务现金流支出占比”“七、AA 级（含）
     以下境内固定收益类资产占比”“八、持股比例大于5%的上市股票投资占比”“九、应收款项占比”
     “十、持有关联方资产占比”在 `config/solvency_taxonomy.xlsx` 中没有对应编码，保留为 RAW
     是预期行为，不是漏提。
   - 非 strict 模式的 18 个未映射编码还包含 S05 明细（寿险费用风险/风险分散效应、损失吸收调整、
     递延所得税资产等）、S02 实际资本明细（D-SII/G-SII 附加资本、逆周期附加资本等）和
     本年度累计净现金流/上一会计年度净现金流等；关键指标均已映射。
3. **表级 unit 标注不精确**：`THREE_YEAR_INVESTMENT_RETURN` 与 `LIQUIDITY_MONITORING` 的通用
   `table.unit` 为“元”，但实际数值含 `%`；标准化按记录值后缀处理，2024/2025 收益率与 LCR 的
   单位均为 `%`，数值正确。按“extractor 只改 rows/table_blocks”的约束，本次未改 unit。
4. **2022 缺 THREE_YEAR 不是失败**：定位表数 5/6、提取 5 表、标准化状态“成功”；验收时不要把
   2022 的 5 表当成漏检。2023–2025 均 6/6。
5. **未发现其它共享层缺陷**：规则加载、页码窗口、`page_match: prefer` 回退、extractor 注册与
   异常兜底、STEP3 对两个 LIQ block 的展开均按预期工作；本任务没有改共享层文件。

## 9. 证据与复现

- 四轮 dump：`tmp/tables/中银保险_2022.txt`、`_2023.txt`、`_2024.txt`、`_2025.txt`
  （每表 pages/rows/blocks/unit 与逐行 rNN；2022 为 5 表，2023–2025 为 6 表）。
- 四轮回归：`tmp/daxing/中银保险/summary.csv`；日志 `/tmp/zhongyin_all.log`、
  `/tmp/zhongyin_strict_all.log`（本机临时目录，不入库）。
- 命令：

  ```bash
  PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe tmp/tools/dump_tables.py --company 中银保险 --years 2022,2023,2024,2025
  PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m tests.test_three_companies_extraction --company 中银保险 --years 2022,2023,2024,2025 --no-excel --out-dir tmp/daxing/中银保险
  PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m tests.test_three_companies_extraction --company 中银保险 --years 2022,2023,2024,2025 --no-excel --strict-metrics --out-dir tmp/daxing/中银保险
  PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m py_compile services/company_extractors/zhongyin.py
  ```

- 规则文件：`config/extraction/companies/中银保险.yaml`；专属提取器：
  `services/company_extractors/zhongyin.py`。
