# 提取规则全网覆盖与共享层修复记录（2026-11）

本文记录 2026-11 这一轮“从 3 家扩到 89 家”的完整改动：做了什么、为什么改、
改在哪些文件、怎么验证、还剩什么问题。配套方法论见
`plans/company-extraction-playbook.md`，单公司细节见各 `plans/company-<公司>-extraction.md`。

> **2026-11-22 更新：** 89 家公司已登记规则，但不代表 2025 数据全部成功或已逐值验真。
> 当前全量 PDF 审计快照为 64 家表提取成功、20 家提取不完整、3 家失败（文本层不可用的扫描件）、
> 2 家样例缺失。该快照生成于本次平安、众安流动性表修复之前；两家公司修复后已通过重点回归，
> 但其余公司仍以该快照为最近一次全量盘点。重点指标仍需以逐公司 PDF 核对为准；状态与未完成项见 §9。

> **2026-11-23 更新：** 前十大 10 家 + “大型公司”12 家已完成公司专属
> locator/extractor 和六表契约接入。逐表逐年合同矩阵见
> `plans/large-company-contract-validation.md`。22 家文字层可提取表通过当前契约；
> 诚泰 2022 的 4 张纯图片表和 3 个缺失样例年份单独保留为例外。

> **2026-11-24 更新：** 89 家公司均已取得公司级 document hooks。
> 67 家通过公司 YAML + 兼容文档适配器接入，16 家成熟全文解析器通过
> `LEGACY_PAGE_ADAPTERS` 接入；中煤、恒邦、太平科技接入本地 OCR。
> 2025 全量契约快照为 519 通过、3 原文未披露、2 个公司无 PDF、0 失败、0 未生成。
> 最新结果以 `plans/company-extraction-progress-2026-11-24.md`、
> `plans/all-company-contract-validation-2025.csv` 为准；本文 §1、§6 的历史遗留表
> 仅保留改动前状态。

---

## 1. 范围与结果

| 批次 | 公司数 | 说明 | 提交 |
| --- | --- | --- | --- |
| 前十大 | 10 | 人保/平安/太保/国寿/众安/英大/大地/中华联合/太平/阳光 | `b15cacd` |
| 大型 | 12 | 紫金/永安/泰康在线/大家/永诚/诚泰/申能/中银/鼎和/华泰/华安/国任 | `98f9501` |
| 中型+外资 | 20 | 中型 13 + 外资 7（富邦、北部湾、泰山、众诚、安诚、海峡、阳光信保、太平科技、渔业互助、珠峰、亚太、东海、渤海、京东安联、爱和谊、瑞再、安盛、三井、苏黎世、三星） | `027361c` |
| 其余 | 47 | 中型 11 + 农业 6 + 外资 11 + 小型 15 + 自保 4 | `33d0468` |
| 占位 extractor | 35 | 通用规则已够用的公司，登记占位模块 | `b32fd66` |

结果：

- `config/extraction/companies/` 共 **89 份 YAML**，与 `财险公司列表.xlsx` 一一对应，无遗漏、无多余。
- `services/company_extractors/` 共 **91 个模块**：**54 家实修** + **35 家占位**（占位模块 pass-through，不改数据）。
- 2024 年全量体检（84 家有 2024 报告）：**82 成功**，2 失败均为纯扫描件（中煤 2024、恒邦 2024）。
- 与既有基线逐项比对：**核心指标 0 差异**（修复项除外，见 §3 的 5 个主表重建公司）。
- 2022–2025 四年链路：2023–2025 基本 6/6 表；2022 普遍 5/6（报告本身无“近三年投资收益率”表）。

---

## 2. 目录与关键文件

```
config/extraction/tables.yaml                 6 张表定义（全局）
config/extraction/companies/<公司>.yaml       89 份公司规则
services/extraction_rules.py                  加载/校验/路由；tolerant 加载
services/extraction_config.py                 规则 → 表配置（含 signature_terms 覆盖）
services/company_extractors/                  54 实修 + 35 占位 + _main_table helper
services/solvency_hybrid_pipeline/
  page_extract.py                             线框识别、表头/数据合并、行次列剥离
  locator_rule.py                             规则定位 + fitz 2.x find_tables 规避
services/batch_processor.py                   单文件/批量入口；扫描件自动转视觉
services/batch_workflow.py                    批量提取编排（帧隔离、缺失表标记）
services/solvency_normalizer.py               标签归一、期间口径、指标匹配
services/solvency_step3_standardizer.py       标准化、去重、缺口清单、Excel 导出
tools/new_year_check.py                       新年度页码体检
tests/test_three_companies_extraction.py      89 家注册表 + 逐年体检
tests/test_scanned_auto_vision.py             扫描件兜底测试
```

---

## 3. 共享层修复（按发现顺序）

### 3.1 整页模式“每页只取第一个 match”

- 症状：一页多表时 `tasks = [(page, page_matches[0]) ...]`，同一页第二张表永远拿不到配置；
  多表共用 `page_frames[page]`，不同表的行会串。
- 修复：`services/batch_workflow.py` 按 `(page, table_id)` 去重、每表各提一次；
  帧改存 `frames_by_target[(table_id, page)]`；`_assemble_tables` 只用本表帧。
- 附带：空 rows 不再静默 `continue`，写入 `missing_tables` 并标记“缺失”，状态可显示“部分成功”。

### 3.2 区域法失败不回退整页

- 症状：`solvency_rule_table_extractor._rule_attempt` 区域候选为空直接失败，
  续页没有表头的表整页提取也失败。
- 修复：抽出 `_rule_whole_page_outcome()`；区域失败时回退整页，仍失败才保留原原因。

### 3.3 百分比指标被“|值|>1000 丢弃”误删

- 症状：华泰 2023 回溯偏差率 -8647.49%、+491.34% 被当成异常值丢弃。
- 修复：丢弃条件收紧为“源单位是金额单位且 |value|>1000”。

### 3.4 表头空格导致整列期间丢失

- 症状：人保表头 PDF 排成“下一季 度预测数”，`_period_header` 子串匹配失败，整列丢。
- 修复：`_period_header` 匹配前 `re.sub(r"\s+", "", current)`；词表补“预测下季度数/预测下季度末数/预测下季度/预测下季度末/下季度末数 → 下季度末预测数”。

### 3.5 别名包含关系导致“百元保费”行顶掉真值

- 症状：“百元保费经营活动净现金流”(13 字) 含别名“经营活动净现金流”(8 字)，
  旧评分 `min/max+0.5` 让它以 ~1.1 通过阈值；去重阶段又因两位小数得 22.2 分，
  压过真实值 249159（22.1），大地/众安净现金流长期错。
- 修复：`resolve_metric_match` 增加覆盖率闸门：`coverage = len(alias)/len(normalized)`，
  `coverage >= 0.8` 才挂靠；模糊分支同样拒绝“实质性修饰前缀”（`_has_substantive_prefix`）。
  只放行白名单同义后缀（指标值/指标）。

### 3.6 `(单位:万元)` 注解导致标签匹配失败

- 症状：众安行标签为“经营活动净现金流(单位:万元)”，归一化后多出“单位万元”。
- 修复：`solvency_normalizer._normalize_label` 增加 `_UNIT_ANNOTATION_PATTERN`，
  只用于标签匹配，不参与元/万元换算判定。

### 3.7 YAML 里 include_continuation / max_pages 被白名单丢弃

- 症状：续页开关写在公司 YAML 里永远不生效（太保 2025 经营表续页空转）。
- 修复：`extraction_rules._DIRECT_FIELDS` 放行 `include_continuation`、`max_pages`、
  `continuation_min_structure_hits`、`continuation_min_numeric_lines`。

### 3.8 公司 signature_terms 无法覆盖全局

- 症状：`extraction_config` 只取 `tables.yaml` 的 `signature_terms`，紫金/国任等公司配了也不生效。
- 修复：新增 `build_table_configs(table_configs, *, maps=None)`，公司显式值优先、显式 `[]` 清除；
  `_RULE_TO_CONFIG_FIELDS` 放行 `signature_terms`；`apply_rule_to_tables` 调用。
  另补 `batch_workflow._table_terms` / `_signature_terms_for_match`，让帧筛选也用公司签名。

### 3.9 边界扩展全文档重扫

- 症状：`solvency_pdf_locator._expand_to_item_boundaries` 忽略 YAML start/end，
  华安最低资本表被扩到很远的页。
- 修复：start/end 同时存在时只用这组词，occurrences 限定选页 ±1；保留续页保护（人保 [20,21] 不被截成 [20]）。
  华安 MINIMUM_CAPITAL 页码修正为 2022 [40,41]、2023 [39,40]、2024 [39,40]、2025 [37,38]。

### 3.10 单位换算豁免把比率当金额

- 症状：`_UNIT_CONVERT_EXEMPT_CODES={"EPS","MOTOR_AVG_PREMIUM"}` 无条件豁免，
  富邦 2024 车均保费原始值 0.12（单位元）被直接标 0.12 元，正确应为 1,200 元。
- 修复：`solvency_normalizer` 与 `solvency_step3_standardizer` 的豁免集合改为空 frozenset，
  一律走正常换算；验证人保 2024 车均保费 2549.45/2512 元、EPS 0.242/1.236 元。

### 3.11 单份坏 YAML 拖垮全目录

- 症状：凯本财险 aliases 含 canonical 自身 → `ExtractionConfigError`，`load_company_rules`
  整体失败，所有公司降级“未命中规则/0 表”。
- 修复：`load_company_rules(directory=None, *, force=False, strict=True)`；strict=False 跳过坏文件/
  重复 canonical/冲突别名并记 `get_load_warnings()`；`resolve_rule` 捕获异常后以 tolerant 重载。

### 3.12 主表被拆碎（金额跨行/行次竖排/表头缺列）

- 症状：长江 2024 主表金额被拆成“29,1|77.35”，标准化把本季度值记到上季度并 /10⁴；
  融通 2025 标签与金额粘连；东吴“行次”竖排导致列右移；中煤 2022 预测列被当认可资产；
  比亚迪 2024 金额拆成整数段 + 小数段两行。
- 修复：新增 `services/company_extractors/_main_table.py::rebuild_main_rows(doc, pages, ...)`，
  按 `get_text("words")` 的 y 分组重建五列；配套 5 个 extractor：
  `changjiang`、`rongtong`、`dongwu`、`zhongmei`、`byd`（比亚迪按列拼回碎金额）。
- 验证：长江 2024 405,736.41/155,496.83/29,177.35；融通 2025 473,408.74/307,611.11/30,222.05；
  东吴 2025 225,354.02/195,243.46/16,853.45；中煤 2022 380,511.03/67,921.03/39,498.12；
  比亚迪 2024 488,275.50/169,888.15（认可负债修正）/318,387.35/27,123.80（万元）。

### 3.13 PyMuPDF 2.x `find_tables()` 原生崩溃

- 症状：众安 2023 p6 调 `Page.find_tables()` 触发 access violation（EXIT=-1073741819），Python 捕不到。
- 修复：`locator_rule.py` 按 `fitz.VersionBind` 主版本判断，`_FIND_TABLES_ENABLED = _FITZ_MAJOR < 2`，
  2.x 跳过该布尔信号。

---

## 4. 新能力

1. **扫描件整本自动转视觉**：`batch_processor._auto_vision_for_scanned`，
   文档被判为 SCANNED 时自动用 hybrid 重跑定位；未配置模型接口时在“说明”里给出提示，
   不再静默 0 表。测试 `tests/test_scanned_auto_vision.py`（3 用例）。
2. **缺失指标双通道提示**：批量 Excel 增加 `缺失指标清单`、`公司覆盖汇总` 两个 sheet；
   STEP3 下载按钮上方显示缺口摘要（`ui/step3_standardization.py`）。
3. **新年度页码体检**：`tools/new_year_check.py`，逐表比对 `expected_pages±tolerance`，
   输出“建议 expected_pages”，有异常时退出码 2；用法见 README。
4. **数据盘点**：`plans/data-coverage-audit.md`——列表 89 家 vs Data 文件逐年覆盖、9 家年份不全、
   33 组名称差异、扫描件清单。

---

## 5. 验收方法与证据

```bash
# 单公司多年
python -m tests.test_three_companies_extraction --company 人保产险 --years 2022,2023,2024,2025 --out-dir tmp/check

# 全量 2024（89 家，建议分片跑）
python tmp/tools/check_all.py --years 2024 --csv tmp/batch4/all89_2024.csv

# 新年度页码
python tools/new_year_check.py --pdf-dir "Data/pdfs(2026)" --year 2026

# 单测
python -m tests.test_company_extractor_integration
python -m tests.test_extraction_config
python -m tests.test_batch_workflow
python -m tests.test_step1_locator_modes
python -m tests.test_step2_extraction_modes
python -m tests.test_scanned_auto_vision
```

验收口径：目标表全部定位；表头列数 = 数据列数；认可资产/负债、实际资本、最低资本、
保费、净利润、成本率与 PDF 逐项核对；跨年差异可归因；0 值不误填；`keep_unmapped=False` 无回归。

---

## 6. 当前遗留

| 公司/项 | 情况 | 建议 |
| --- | --- | --- |
| 劳合社 | 主表只披露核心/综合充足率，无认可资产/实际资本绝对值 | 人工确认绝对值是否在其他章节；确认前不补 0 |
| 利宝保险 | 经营表大额数字折行拆格（752.03 万 vs 75 亿） | 需要 extractor 按列拼碎金额（可复用 byd 思路） |
| 法巴天星 2025 | S02 表头与数据错位（行次被当数值），主表值正确 | 加 extractor 重排 S02 |
| 黄河财险 | 近三年收益率表原文缺失；经营表标签裂成“保险/业务收入” | 补 extractor 或记字典缺口 |
| 前海财险 2024 | 近三年收益率表缺失 | 原文缺失，无需改 |
| 长江 2024 / 劳合社 2024 | 原文没有实际资本明细表（S02） | 原文缺失 |
| 兴亚财险 | 最低资本原文未披露 | 原文缺失 |
| 中煤 2024/2025、恒邦 2024、太平科技 2025 | 纯扫描件，0 表 | 配大模型接口后自动走视觉 |
| 行次消歧（2.4/3.8/4.3、1.2.4 与 3.4） | 指标字典无行次别名，靠猜测有害 | 维持未映射，诊断可见 |
| 5 家无 2024 报告 | 东吴/法巴天星/融通（仅 2025）、长安（仅 2022）、安心（2022–23） | 无需处理 |

---

## 7. 提交记录

| 提交 | 内容 |
| --- | --- |
| `b15cacd` | 前十大：9 份 YAML + extractor + 4 处共享层修复 + new_year_check |
| `98f9501` | 大型 12 家 + 人保流动性清洗 + 测试注册表 |
| `d49490b` | 共享层缺陷修复 + 缺失指标提示（Excel/STEP3） |
| `027361c` | 中型/外资 20 家 |
| `33d0468` | 其余 47 家 + 扫描件视觉兜底 + 规则容错 + 数据盘点 |
| `b32fd66` | 35 家占位 extractor，89 家全覆盖 |

均未推送远程，等待统一上传。

---

## 8. 后续优化建议

1. 把 `tmp/tools/check_all.py` 正式化为 `tools/check_extraction.py`（支持分片、并行）。
2. 主表重建逻辑目前有 5 份公司级拷贝，可收敛为“YAML 开关 + 通用后处理”。
3. 行次消歧若未来字典补行次别名，再在 `resolve_metric_match` 中启用。
4. 扫描件视觉链路需要一次真实接口联调（当前只有无凭据分支被自动化覆盖）。
5. 劳合社等披露口径特殊的公司，建议在 STEP3 增加“未披露”状态而不是空值。

---

## 9. 2025 全公司审计与重点指标状态（2026-11-22）

审计命令：

```bash
python tools/company_extraction_audit.py --all-companies --years 2025 --workers 2 --out-dir tmp/all_companies_2025_final
```

本轮修复前的全量快照统计：89 家配置公司中，64 家表提取成功、20 家提取不完整、3 家规则提取失败、
2 家没有 2025 样例。平安产险、众安产险在修复后重新通过单公司重点回归；其余 87 家未因本次改动重新跑全量审计，
因此不把修复后的全量成功数推算为已审计结果。失败/缺失不应被改写成零；“表提取成功”也只代表目标表生成，不等同于勾稽全部通过或逐值人工验真。

三个规则提取失败的 PDF 为中煤财险、恒邦财险、太平科技的扫描件，缺少可用文本层；安心财险、长安保险在本地没有 2025 样例。20 家不完整名单为：

三井住友、东京海上、中国渔业互助、亚太财险、京东安联、众安产险、众惠相互、众诚保险、利宝保险、前海财险、北部湾财产、安华农业、安诚财险、富德产险、平安产险、浙商财险、海峡金桥、爱和谊、长江财险、黄河财险。

其中修复前 19 家的主要失败表为流动性风险监测指标；浙商财险另有实际资本表失败。黄河财险还存在近三年投资收益率与流动性监测表未通过。
平安产险、众安产险的流动性表现已按 PDF 文本层重建整表行：平安取 10 条监测行，众安取 10 条监测行；
平安来源页 18 的规模保费同比增速为 6.5%/6.9%，众安来源页 14 为 6.93%/5.64%。平安同页前置的监管指标不会并入监测表，
众安行内脚注序号不会被当作数值。修复不改变空白证据政策，也不把横线转成零。

### 重点指标数据语义

- `特定类别保险合同损失吸收效应`、`规模保费同比增速`均须先确认原文指标行存在，再解释单元格。
- 只有原 PDF 线框单元格坐标内经文字层检查确认无文字时，空白才按用户口径记 0；提取值空白但没有原文空白证据时，保留缺失诊断，避免把漏提数字伪装成 0。
- 原文 `-`、`--` 等符号保留为原始披露，不擅自等同空白或零。
- 前十大 2025 重点指标回归测试：修复后 **10/10 通过**。空白证据、前十大文档契约、公司 extractor 集成和 workflow 回归共 **32/32 通过**；相关模块编译及 `git diff --check` 通过。测试会核对两个重点指标的本期/上期值和 PDF 页码；此结果不代表其余 87 家已重新完成全量审计。

审计明细在本地忽略目录 `tmp/all_companies_2025_final/`，不会随代码提交；重新运行命令可生成当前版本结果。
