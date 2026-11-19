# 诚泰财险跨年度偿付能力报告提取规则

## 1. 公司范围与交付物

| 项目 | 内容 |
| --- | --- |
| canonical | `诚泰财险` |
| aliases | `诚泰财产`、`诚泰财产保险股份有限公司` |
| PDF | `Data/pdfs(2022..2025)/诚泰财险.pdf`，四年齐全 |
| profile | `NON_LIFE_SOLVENCY` |
| 规则文件 | `config/extraction/companies/诚泰财险.yaml` |
| 公司提取器 | `services/company_extractors/chengtai.py`（`@register("chengtai")`） |
| 设计年 / 验证年 / 终检年 | 2022、2023 / 2024 / 2025 |

封面公司名为“诚泰财产保险股份有限公司”，公司清单写作“诚泰财产”，文件名是“诚泰财险”。canonical 取文件名主干，别名不重复 canonical。

## 2. 年度页码与结构版本

YAML 中 2023/2024/2025 各有一条 `structure_versions`，且**每条都写全六表配置**。原因是 `resolve_rule` 只把命中的最新版本 `tables` 覆盖到主体表上，不跨版本累积；若 2024/2025 只补页码，会丢失 anchors、required_items、续页设置。

| 表 | 2022 | 2023 | 2024 | 2025 |
| --- | --- | --- | --- | --- |
| SOLVENCY_MAIN | p14 | p15 | p13 | p11 |
| NON_LIFE_OPERATING_METRICS | p17 | p17-18 | p21-22 | p17 |
| ACTUAL_CAPITAL | 图片（p33-34） | p32 | p36-37 | p32-33 |
| MINIMUM_CAPITAL | 图片（p36-38） | p35 | p43-44 | p39-40 |
| THREE_YEAR_INVESTMENT_RETURN | 无此章节 | p17 | p20 | p16 |
| LIQUIDITY_MONITORING | 图片（p15-16） | p17 | p18-19 | p15-16 |

- 页码均为 PDF 物理页，`tolerance` 默认 2；流动性监测表用 `tolerance: 0`，因为同页/邻近页有“流动性风险监管指标（LCR）”标题，容差会误定位。
- `SOLVENCY_MAIN`、`THREE_YEAR` 不启用续页；`OPERATING`、`ACTUAL`、`MINIMUM`、`LIQUIDITY` 启用 `include_continuation`（2025 经营表为单页，显式关闭）。
- 2022 年实际资本、最低资本、流动性监测、认可资产/负债明细均为图片页，文本层只有标题；规则法无法提取。主体规则只保留 2022 可文本提取的 `SOLVENCY_MAIN` 与 `NON_LIFE_OPERATING_METRICS`，没有为了凑满六表把 p14 主表重复指给其他 table_id。2022 全文也没有“近三年（综合）投资收益率”章节。

## 3. 逐表结构与处理

### 3.1 SOLVENCY_MAIN

- 表头：`行次 / 项目 / 本季度数 / 上季度可比数 / 基本情景下的下季度预测数`（2022 为 4 列，无行次）。
- 共享层按整页识别时能取到 17-22 行，但行次列会占位，且“综合偿付能力充足率（核心资本÷最低资本）”的公式后缀会让标签相似度不足而无法映射。
- 处理：`_build_main` 只保留 `指标名称 + 3 个数值列`，删除行次，剥掉公式括注，从“认可资产”截到“综合偿付能力充足率”。

### 3.2 NON_LIFE_OPERATING_METRICS

- 表头：`指标名称 / 本季度数 / 本年度累计数`。
- 2023 p17→p18、2024 p21→p22 跨页，续页无表头；2025 单页完整；2022 为 `序号 / 指标名称 / 本季度数（10-12月）/ 本年度累计数（1-12月）`。
- 共享层整页识别只选首个匹配帧，2023/2024/2025 都只取到“综合成本率”为止的 13 行，签单保费等规模指标丢失。
- 处理：`_build_operating` 按 pdfplumber lines 取主表，再向后 1-2 页找“无表头、首列以数字编号或（十二）”的续页拼接；2022 去掉序号列。续页标签固定取第 1 列（数值列更长，不能取最长单元格）。
- 2022 单位混排：`签单保费（万元）`、`综合成本率（%）`、其余多为元。共享层 `_block_unit` 会扫描表块前 5 行，把“保险业务收入（元）”当成整块单位，压过后续行标签的“万元”。`_operating_unit_records` 把行内单位拆成行级 `UnitRecord`、从标签删除括注，并让无单位行继承最近单位；`_build_operating` 返回 `unit_records`，postprocess 同步写回 `table.unit_records`。

### 3.3 ACTUAL_CAPITAL

- 表头：`行次 / 项目 / 期末数 / 期初数`；2023 在 p32，2024 在 p36-37，2025 在 p32-33，合计行常落在下一页。
- 共享层线框识别会把“项目 + 两个金额”合并进第 1 列、后两列留空（见第 6 节 D1）。2023 因此只解析出 30 条 0 值明细。
- 处理：`_build_capital` 取“核心一级资本”到“实际资本合计”区间，跨页续拼，跳过空值与“-”行；数值去掉千分位中的空格（如 `7 ,198,679,634.86`）。

### 3.4 MINIMUM_CAPITAL

- 表头同上；主表结束于“4 最低资本”，p36-37（2023）、p44（2024）、p40（2025）之后是“保费和准备金风险最低资本”等子表（表头为 `行次 / 类型 / 保费风险 / 准备金风险`）。
- 共享层默认终止词会命中“最低资本合计”类子表行，或页内区域从“最低资本”到“最低资本”直接裁空（2023 实测）。
- 处理：`_build_capital` 从“量化风险最低资本”取到“最低资本”，只保留主表行，子表不并入。

### 3.5 THREE_YEAR_INVESTMENT_RETURN

- 2023 p17、2024 p20、2025 p16 各 2 行：`近三年平均投资收益率`、`近三年平均综合投资收益率`；2022 无此章节。
- 处理：`_build_three_year` 用全文正则直接扫两行，避免同页经营表/准备金表被整页识别混入。

### 3.6 LIQUIDITY_MONITORING

- 表头：`监测指标 / 项目 / YYYY年1-12月数值（审计后）/ YYYY年1-9月数值`；10 组：经营活动净现金流、百元保费、特定业务、规模保费增速、现金及流动性管理工具占比、季均融资杠杆、AA 级占比、持股>5%、应收款项占比、关联方资产占比。
- 共享层在 2024 会把该表定位到 p14-15 的 LCR 覆盖率明细；2023 会把表头期间推断成“本季度末数-审计后 / 下季度末预测数”。
- 处理：YAML 用 `content_terms: [指标值, 经营活动现金流入本年累计数]` + `header_terms: [监测指标]` + `tolerance: 0` 锁定页码；`_build_liquidity` 只取 `项目=指标值` 的分组行，组名去“一、”前缀，百元保费统一改“百元保费现金净流量”，表头改为 `指标名称 / 本季度数 / 上季度数`（标准化后落“本季度末数/上季度末数”，这是现有口径词表的限制）。
- 2024 p19 的线框表从“AA 级期末账面价值”开始，漏掉表外上方的分组标题与指标值；`_liquidity_text_groups` 从页文本补回缺失的“AA 级（含）以下境内固定收益类资产占比 0.89%/0.78%”，并按去前缀、去空白的组名去重，2023/2025 不会重复追加。

## 4. 提取器实现要点

`services/company_extractors/chengtai.py`：

- `postprocess(tables, *, pdf_bytes)`：按 table_id 用重建结果替换行，同步 `rows`、`table_blocks`、`source_pages`、`page`、`unit`、`unit_records`、`note`、`strategy`；缺失表用 `FlexibleTable` 追加。
- `_rebuild(pdf_bytes)`：pdfplumber `vertical_strategy=lines, horizontal_strategy=lines` 读全 PDF，返回六表候选。
- `_build_main` / `_build_operating` / `_build_capital` / `_build_three_year` / `_build_liquidity`：分别重建五类表；实际资本与最低资本共用 `_build_capital(start, stop)`。
- `_report_year`：封面 `YYYY 年第 N 季度`。2022 年时删除重建未命中的 ACTUAL/MINIMUM/LIQUIDITY/THREE_YEAR，避免通用规则把 p14 主表重复指给这些 table_id。
- 幂等：同一 PDF 重复调用得到相同 rows；不写死年份/页码，页码来自 YAML 定位后的实际页。

## 5. 验证结果

命令：

```bash
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m tests.test_three_companies_extraction \
  --company 诚泰财险 --years 2022,2023,2024,2025 --method rule --no-excel \
  --out-dir tmp/chengtai_test
```

| 年度 | 定位 | 提取表/行 | 标准化记录/编码/未映射 | 状态 |
| --- | --- | --- | --- | --- |
| 2022 | 6/6（其中 4 张为图片页，提取器丢弃） | 2 / 46 | 113 / 49 / 4 | 部分成功（图片页） |
| 2023 | 6/6 | 6 / 92 | 166 / 74 / 7 | 成功 |
| 2024 | 6/6 | 6 / 127 | 225 / 106 / 16 | 成功 |
| 2025 | 6/6 | 6 / 127 | 225 / 106 / 16 | 成功 |

期间分布无 `(指标编码, 期间)` 重复：2022 49/16/32/16；2023 74/46/28/16/2；2024 101/77/29/16/2；2025 101/77/29/16/2（顺序：本季度末数/上季度末数/本年累计数/下季度末预测数/近三年平均）。

### 关键指标抽查（万元，百分比原值）

| 指标 | 2022 | 2023 | 2024 | 2025 |
| --- | --- | --- | --- | --- |
| 认可资产 | 989,895.9922 | 950,233.8624 | 1,053,519.9145 | 920,362.7654 |
| 认可负债 | 217,088.3678 | 223,930.5723 | 230,613.7107 | 211,297.8390 |
| 实际资本 | 772,807.6244 | 726,303.2901 | 822,906.2037 | 709,064.9265 |
| 最低资本 | 240,987.6847 | 202,879.1089 | 259,467.7761 | 207,574.3068 |
| 核心/综合充足率 | 318% / 321% | 355% / 358% | 315% / 317% | 339% / 342% |
| 保险业务收入（本季/累计） | 53,922.1222 / 193,581.2957 | 42,605.2532 / 203,146.6706 | 50,654.2755 / 202,816.4944 | 43,790.5305 / 220,089.4823 |
| 净利润（本季/累计） | 3,197.7943 / 3,321.6176 | -17,801.12 / -24,843.883 | -21,628.7282 / -30,990.8978 | -19,826.2232 / -17,886.4295 |
| 签单保费（本季/累计） | 56,662 / 203,448 | 44,847.6622 / 213,673.0459 | 53,297.9483 / 213,485.3386 | 46,214.1235 / 231,369.3904 |
| 综合成本率（累计） | 111.29 | 110.75 | 114.27 | 105.39 |
| 近三年平均投资收益率/综合 | 无此章节 | 4.01 / 3.21 | 2.04 / 1.87 | 0.65 / 0.65 |

2022 签单保费曾因表块单位被压成“元”而缩小 10000 倍，已在 `_operating_unit_records` 修复；上表为修复后数值。

### 未映射说明

未映射以流动性明细的 `RAW_*` 为主（AA 级占比、应收款项占比、关联方资产占比、持股>5%、特定业务现金流、百元保费），另有寿险 0 值子项、D-SII/G-SII 附加资本、损失吸收调整等；不涉及 `Step7_指标需求清单.xlsx` 的 36 个指标。2022 的 4 个未映射是非车险前五大险种子项（企业财产/健康/意外/责任）。

### 回归

- `python -m tests.test_extraction_config`：OK（YAML 校验通过）。
- `python -m tests.test_company_extractor_integration`：OK。
- `python -m tests.test_company_rule_catalog`：未能在本会话完成。用例写 `C:\Users\Rhys\AppData\Local\Temp\dsh-dSSFNR\...\测试公司.yaml` 时报 `PermissionError: [Errno 13] Permission denied`，属会话沙箱对系统临时目录的限制，不是 YAML 内容错误。

## 6. 共享层缺陷记录（未修改）

### D1 四列表“行次+项目+金额”被合并进第 1 列

- 位置：`services/solvency_table_extractor.py:1172`（`extract_page_all_tables` 的线框识别）。
- 现象：2023 p32 实际资本表、2024 p36 等页面，共享层输出 `核心一级资本7 ,198,679,634.867 ,525,279,704.20` 形式，后两列空；同一页 pdfplumber 原生 lines 结果是正常 4 列。
- 影响：实际资本/最低资本明细大面积 0 值或漏行，必须由公司提取器重建。
- 复现：

```bash
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe - <<'EOF'
import pdfplumber
from pathlib import Path
from services.solvency_table_extractor import extract_page_all_tables
p = Path('Data/pdfs(2023)/诚泰财险.pdf')
with pdfplumber.open(p) as pdf:
    page = pdf.pages[31]
    print('pdfplumber:')
    for t in page.extract_tables({'vertical_strategy':'lines','horizontal_strategy':'lines'}):
        print(t[:3])
print('shared:')
for t in extract_page_all_tables(p.read_bytes(), 32):
    print(t.rows[:3])
EOF
```

### D2 表块单位扫描压过行内单位

- 位置：`services/solvency_step3_standardizer.py:916`（`_block_unit` → `_unit_from_block_rows`），配合 `services/solvency_normalizer.py:511`（`_source_unit` 的 block_unit 优先级）。
- 现象：2022 经营表前 5 行有“保险业务收入（元）”，整块单位被定为“元”；后续“签单保费（万元）”的行级单位不再生效，数值缩小 10000 倍。
- 影响：混合单位表必须由公司提取器写行级 `unit_records` 规避。
- 复现：用通用规则提取 2022 经营表并查看 `签单保费` 记录（约 5.6662 万元，源表 56,662 万元）；本仓库已用 `chengtai` 提取器规避。

## 7. 维护建议

1. 新年度先看 `SOLVENCY_MAIN` 页，再按本文件页序核对经营表/实际资本/最低资本/监测表位置；页码变化只改对应 `structure_versions`。
2. 若某年经营表不再跨页，关闭该版本 `include_continuation`；若新增子表，确认 `_build_capital` 的 start/stop 词仍唯一。
3. 流动性监测表若改表头年份文字，`_liquidity_text_groups` 不依赖年份，只需保留“指标值”行。
4. 2022 类图片页只能走 OCR/人工补录，不应在 YAML 里硬指其他文本表。
