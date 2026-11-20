# 后续工作计划（2026-11-20，基于 89 家覆盖后的最新状态）

- 基线分支：`feature/track-company-and-rule-config`
- 基线提交：`8c2255c`（89 家覆盖 + 共享层修复）
- 前置文档：
  - `plans/extraction-coverage-89-and-shared-fixes-2026-11.md`（89 家交付与遗留）
  - `plans/company-extraction-playbook.md`（方法论）
  - `plans/data-coverage-audit.md`（Data 盘点）
  - `plans/refactor-plan-track-and-rule-config.md`（早期计划，§9 已过时）

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
| 新工具 | `tools/new_year_check.py`、扫描件自动视觉、缺失指标双通道 |

**尚未完成的核心项**：R4 缺失原因透出、勾稽告警配置、4 家版式遗留、扫描件联调、工程化。

---

## 1. P0：正确性收尾（优先做）

### 1.1 R4 缺失原因透出（唯一未交付的原始需求）

目标：任何“该表该指标没提取到”都能在三个位置看到原因，不再只有空值。

新建 `services/extraction_trace.py`：

```
build_trace(resolved_rule, matches, logs) -> list[ExtractionTrace]
```

原因码（沿用旧计划 §2.3）：

| 码 | 触发 |
| --- | --- |
| `RULE_NOT_FOUND` | 公司无 YAML |
| `YEAR_UNKNOWN` | 封面未识别年度 |
| `PAGE_DRIFT` | 窗口内偏移 |
| `PAGE_OUT_OF_TOLERANCE` | 窗口外全文回退 |
| `ANCHOR_NOT_FOUND` | 全文无锚点 |
| `SIGNATURE_MISSING` | 页内有表但缺核心词 |
| `REQUIRED_ITEM_MISSING` | 表已提取但必填行缺失 |
| `UNIT_UNKNOWN` | 单位未识别 |

落点：

1. STEP2 日志：新增列 `规则来源`、`未命中原因`（单文件 `ui/step2_extraction.py` + 批量 `outcome.logs`）
2. STEP3：`diagnostics` 增加原因列，标准窄表 `备注` 写中文原因（`solvency_step3_standardizer`）
3. STEP7：缺口清单 `说明` 列带原因（`step3_metric_gap.py`）

验收：

- 故意把某公司 `expected_pages` 改错 → STEP2 日志、STEP3 诊断、STEP7 缺口三处可见
- `strict` 模式缺锚点 → 该表 `pages=[]`，原因可见，数值为缺失不为 0
- 恢复配置后结果回到基线

预估：2–3 天。

### 1.2 勾稽告警治理（需业务拍板）

现状（平安/人保 2022–2025 均存在，属既有）：

- `SCHEMA_ALLOWED_PERIOD`：偿付能力主表“下季度末预测数”、经营表“本年累计数”不在部分指标字典允许期间
- `COMBINED_RATIO` / `CORE_RATIO`：主表万元整数 → 比率舍入差 0.01–0.05 百分点，超过 0.0001 容差
- 跨表核对、`DUPLICATE_*`：主表/S02/S05 同指标取值差异

待确认方案：

1. 更新 `config/solvency_taxonomy.xlsx` 允许期间（补预测/累计）
2. 比率勾稽容差 0.0001 → 0.1 百分点（或按规则单独配置）
3. 跨表核对指定优先来源（主表 > S02 > S05）

验收：跑 `validate_standard_data_detailed`，失败项归零或只剩“源表精度”类并已归类。

预估：0.5–1 天（确认后）。

### 1.3 四家版式遗留

| 公司 | 问题 | 处理 |
| --- | --- | --- |
| 劳合社 | 主表无认可资产/实际资本绝对值 | 先确认其他章节是否披露；无则标“未披露”，不补 0 |
| 利宝保险 | 经营表大额数字折行（752.03 万 vs 75 亿） | 复用 `byd` 的按列拼碎金额 |
| 法巴天星 2025 | S02 表头与数据错位（行次被当数值） | 写 extractor 重排 S02 |
| 黄河财险 | 近三年表原文缺失；经营表标签裂“保险/业务收入” | 标签合并 extractor；缺失记原文 |

验收：每家在 `tests/test_three_companies_extraction` 中跑该年，关键指标与 PDF 一致；跨年差异可归因。

预估：1–2 天。

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
| W1 | R4 缺失原因透出 → 四家版式遗留 → 勾稽方案确认（业务） |
| W2 | 扫描件联调 → 体检工具正式化 → 全量跨年回归 → STEP7/PDF 回归 |

并行建议：R4 与四家版式互不冲突，可两人并行；勾稽配置改动需单独分支（配置变更影响全量）。

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
| 文档漂移 | 多份计划并存 | 以本文件为唯一“后续计划”，旧计划只作历史 |
