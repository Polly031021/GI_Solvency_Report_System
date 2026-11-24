# 后续工作计划（2026-11-20，基于 89 家覆盖后的最新状态）

- 基线分支：`feature/track-company-and-rule-config`
- 基线提交：`a54fe23`（22 家大型公司专属链路迁移）
- 前置文档：
  - `plans/extraction-coverage-89-and-shared-fixes-2026-11.md`（89 家交付与遗留）
  - `plans/company-extraction-playbook.md`（方法论）
  - `plans/company-layout-contract-migration.md`（公司专属全链路迁移手册）
  - `plans/company-migration-template.md`（单家公司迁移记录模板）
  - `plans/large-company-contract-validation.md`（22 家逐表逐年契约验收）
  - `plans/data-coverage-audit.md`（Data 盘点）
  - `plans/refactor-plan-track-and-rule-config.md`（早期计划，§9 已过时）

> **2026-11-24 更新：** 本文 §0、§1.4、§2.1 中“仅 22 家/扫描件未处理”的状态已被
> `plans/company-extraction-progress-2026-11-24.md` 取代。当前 89 家均已取得公司级
> document hooks；2025 全量契约结果为 519 通过、3 未披露、2 个公司无 PDF、0 失败、
> 0 未生成。扫描件已接入本地 RapidOCR 与磁盘页缓存。下一步重点是：
> 1）合并重跑 2022–2024 全量矩阵；
> 2）建立关键金额 PDF 黄金值样本；
> 3）完成 STEP4/STEP7 回归。

---

## 0. 当前基线（事实）

| 项 | 状态 |
| --- | --- |
| 公司 YAML | 89 份，与 `财险公司列表.xlsx` 一一对应 |
| 专属 extractor | 90 个模块（54 实修 + 35 占位 + `_main_table` helper） |
| 2024 全量 | 84 家有报告，82 成功，2 例纯扫描件 |
| 2022–2025 链路 | 2023–2025 基本 6/6 表；2022 普遍 5/6（无近三年表） |
| 期间口径 | 本季度数/上季度数 → 本季度末数/上季度末数 |
| 单文件/批量 | 共用 extractor 入口，日志显示“公司专属规则” |
| 提取架构迁移 | 大地已走公司专属定位 + 整份 PDF 提取；其余公司暂时保留通用链路 |
| 新工具 | `tools/new_year_check.py`、扫描件自动视觉、缺失指标双通道 |

**尚未完成的核心项**：其余公司专属全链路迁移、勾稽告警配置、扫描件联调、工程化。

---

## 1. P0：正确性收尾（优先做）

### 1.0 STEP1 页码自动确认（✅ 已完成 2026-11-20）

- 定位完成后 `compare_pages_with_rule()` 逐表对比 YAML `expected_pages±tolerance`
- 全部“通过/偏移”且无定位冲突 → 自动 `pages_confirmed=True`，不再要求人工点确认
- 未配置 / 未定位 / 超出窗口 → STEP1 显示对比表并提示人工确认
- 批量定位逐份自动确认，批量状态表“页码确认”列直接反映结果
- 测试：`tests/test_page_check_with_rule.py`

### 1.1 R4 缺失原因透出（✅ 已完成 2026-11-20）

实现：

- `services/extraction_trace.py`：`build_traces()` 生成表级状态与原因码，`build_diagnostics()` 转 STEP3 诊断行
- STEP2 单文件/批量：`extraction_traces` 存入 session / document；预览区新增“目标表提取状态与未命中原因”表
- STEP3：`standardize_to_target(..., extraction_traces=...)`，诊断表新增 `来源缺失` 行（目标表/页码/原因）
- 原因码：`RULE_NOT_FOUND` / `YEAR_UNKNOWN` / `ANCHOR_NOT_FOUND` / `SIGNATURE_MISSING` / `REQUIRED_ITEM_MISSING`（`PAGE_DRIFT` / `PAGE_OUT_OF_TOLERANCE` 仍由 `rule_note` 显示）
- 测试：`tests/test_extraction_trace.py`（7 项）

待补齐：STEP7 缺口清单暂未单独展示原因列，可复用 `build_diagnostics()` 的行结构。

### 1.2 勾稽告警治理（部分完成）

已完成：**容差改为 Excel 维护**。`config/solvency_validation_rules.xlsx` 的 12 条规则容差
统一改为 **0.1**（百分点/万元按原单位）；`COMBINED_RATIO` / `CORE_RATIO` 不再因舍入误报，
`tests/test_step4_validation.py` 同步为 0.1 口径。

待确认：

1. `SCHEMA_ALLOWED_PERIOD`：字典允许期间与披露列不匹配（预测列/累计列）
2. 构成类勾稽在“下季度预测”期间缺子项（主表预测列只披露汇总项）——属应缺，建议规则“适用期间”排除预测
3. `DUPLICATE_*` 跨表一致性：S02/S05 未提取到对应行（如 S02 实际资本合计），需先补提取再看差异

### 1.3 四家版式遗留（已确认忽略）

劳合社、利宝、法巴天星、黄河属于非传统财险主体（或特殊披露口径），
**数据不完整属正常，不再投入 extractor 修复**；保留在文档中仅供追溯。

### 1.4 公司专属全链路迁移（最高优先级）

目标：公司规则直接决定页码、表结构和取数行；公司专属提取成功时完全跳过
通用页级抽表。详细 SOP、验收标准和批次见
`plans/company-layout-contract-migration.md`。

已完成：

- 公司接口 `locate_company_tables()` / `extract_company_tables()`
- YAML `layout_contracts`
- 契约失败整表阻断
- 规则/extractor 指纹缓存失效
- 前十大 + 12 家大型公司共 22 家接入公司专属 hooks
- 22 家六表契约与逐表逐年合同审计工具
- STEP3 批量入口复用公司专属定位/提取
- 预期表集合对账，静默少表不再判成功
- 契约支持 `max_rows`、`forbidden_rows`、`unique_rows` 和年度覆盖

当前结论：

- 22 家文字层可提取目标表通过当前契约矩阵。
- 3 个公司/年份无 PDF 样例：大家 2022、申能 2022–2023。
- 诚泰 2022 有 4 张纯图片表尚未文本验收。
- 契约通过不等于数值核对完成；STEP4/STEP7 仍需继续回归。

第一批迁移：

1. 平安产险
2. 太保产险
3. 人保产险
4. 合众财险、长江财险、安心财险、中煤财险
5. 华泰财险、建信财险、中银保险、汇友相互、长安保险

每家公司必须通过：

- 专属页码定位
- 整份 PDF 表体重建
- 表头/期间列契约
- 关键值黄金样本
- 跨年回归
- 0、占位符、缺失区分

---

## 2. P1：覆盖与体验

### 2.1 扫描件视觉链路联调

对象：中煤 2024/2025、恒邦 2024、太平科技 2025（纯扫描）。

- 在会话配置 `llm_api_key/base_url/model`，用 `mode="hybrid"` 跑这 4 份
- 记录“扫描页 → 识别方式 → 表数 → 关键指标”
- 无凭据分支已有 `tests/test_scanned_auto_vision.py`，补一条真实接口的临时脚本（不入库）

验收：4 份至少能定位并提取主表；失败时说明具体页和原因。

### 2.2 提取体检工具正式化

`tmp/tools/check_all.py` → `tools/check_extraction.py`：

- 支持 `--years`、`--companies`、`--shard i/n`、`--workers`
- 输出 CSV：公司/年份/表数/行数/编码数/关键指标/状态
- 与 `summary.csv` 格式兼容，便于回归对比

验收：89 家 2024 分 4 片并行，单次 < 15 分钟，结果可复现。

### 2.3 主表重建去重

现状：`changjiang`/`rongtong`/`dongwu`/`zhongmei`/`byd` 五份近似拷贝。

- 在 YAML 增加 `postprocess: main_table_rebuild` 开关
- extractor 里只保留公司特有差异（比亚迪碎金额、东吴行次竖排）
- 回归 5 家关键值不变

验收：5 家 2022–2025 关键值与当前基线逐项一致。

### 2.4 行次消歧

只有在指标字典补充行次别名后才启用 `resolve_metric_match` 的行次信号；
当前维持“未映射 + 诊断可见”，不要靠猜测。

---

## 3. P2：工程化与回归

### 3.1 pytest/CI

- 把 35+ 脚本式测试包成 pytest（`def test_*` 已兼容，主要是路径与 fixture）
- CI 只跑快速集：`test_extraction_config`、`test_company_rule_catalog`、
  `test_company_extractor_integration`、`test_scanned_auto_vision`、`test_step3/4`
- 真实 PDF 体检不进 CI

### 3.2 全量跨年回归

- 89 家 × 2022–2025（按实际可得年份）
- 输出：每年成功/部分/失败清单、编码集合差异、0 值清单
- 对比基线：2024 年 82/84 成功、关键指标 0 差异

### 3.3 STEP7/8 回归

- 期间口径统一后，STEP7 的“本季度末数”会包含经营指标，需确认图表取数无异常
- Track 置顶/sticky、五边形叠加、PDF 导出（10 家 × 18 模块）
- `S7_EXPORT_DEBUG=1` 跑一次整本导出

### 3.4 文档维护

- 每接入/修改一家公司，更新 `plans/company-<公司>-extraction.md`
- 共享层改动同步 `extraction-coverage` 文档的“共享层修复”章节
- 本文件按周更新优先级与完成状态

---

## 4. 建议执行顺序（两周）

| 周 | 内容 |
| --- | --- |
| W1 | 公司专属全链路第一批（平安/太保/人保）→ 高异常公司迁移 |
| W2 | 第二批迁移 → 体检工具正式化 → 全量跨年回归 → STEP7/PDF 回归 |

并行建议：不同公司可并行迁移，但每家公司必须独立提交、独立黄金样本；
共享解析工具与公司 extractor 分开提交，避免一家公司的特殊逻辑污染其他公司。

---

## 5. 验收口径（统一）

- 目标表全部定位；表头列数 = 数据列数
- 关键指标与 PDF 逐项核对（认可资产/负债、实际资本、最低资本、保费、净利润、成本率）
- 跨年编码差异可归因（披露变化 / 应缺）
- 0 值逐条核对（源表 `-` 或空白）
- 期间口径只允许：本季度末数 / 上季度末数 / 下季度末预测数 / 本年累计数 / 近三年平均
- `keep_unmapped=False` 无回归
- 新测试通过 + 至少一份真实 PDF 端到端

---

## 6. 风险与依赖

| 项 | 风险 | 应对 |
| --- | --- | --- |
| 视觉联调 | 需要模型接口与额度 | 先跑 1–2 页验证链路，再全量 |
| 勾稽容差 | 放宽后可能掩盖真实错误 | 按规则类型分级，比率类单独放宽 |
| 全量回归 | 89×4 耗时 | 分片 + 夜间跑，结果落 CSV |
| 主表重建收敛 | 改动 5 家高风险 | 先加开关（默认关），逐家打开验证 |
| 通用提取混入 | 公司专属提取失败后静默回退通用结果 | 已迁移公司契约失败必须整表失败，禁止回退 |
| 文档漂移 | 多份计划并存 | 以本文件为唯一“后续计划”，旧计划只作历史 |
