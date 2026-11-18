# 改造计划：Track Company 增强 + 提取规则配置化（公司级跨年度）

- 版本：v3（2026-11 确认：物理页码 / 不绕过类型筛选 / 对比公司仅类型池 / 叠加图进 PDF）
- 历史：v2 公司级跨年度 + 容差 2 页；v1 草案（按公司×年度配置）
- 适用 Profile：`NON_LIFE_SOLVENCY`（财险）
- 文档位置：`plans/`（入库，`docs/` 在 `.gitignore` 中，不作为长期管理位置）
- 旧逻辑处理原则：**不删除**，保留为默认实现 + 配置兜底，靠 git 分支/commit 追溯

---

## 0. 需求原文与拆解

| 编号 | 需求 | 拆解后的可执行目标 |
| --- | --- | --- |
| R1-a | Track 公司放到最前方 | 选中追踪公司后，报告内所有“按公司逐个展示”的模块把它排到第 1 位（含 PDF 导出顺序） |
| R1-b | 逐个展示时固定，不随横向滚动条移动 | 公司卡片横向 strip 中追踪卡片 `position: sticky; left: 0`，滚动时钉在左侧 |
| R1-c | 能力五维图可与任意公司面积重叠对比 | 能力五边形模块新增“对比公司”选择器，渲染 Track + 对比公司的双层填充雷达 |
| R2 | 一家公司一份规则，跨年度复用，封面识别后路由 | 新增 YAML 规则库；用 `extract_report_metadata` 的公司名解析规则文件，页码用“典型位置 + 容差 ±2 页 + 锚点验证”，结构大改才加版本 |
| R3 | 表格定义写入配置文件，可手工维护 | `TABLE_*` 常量外移到 YAML；代码读配置，内置常量降级为兜底 |
| R4 | 未命中直接标缺失值并备注原因 | 定义缺失原因码，写入 STEP2 日志 / STEP3 匹配诊断 + 窄表“备注” / STEP7 缺口清单 |

补充约定（用户已确认）：
- 配置格式：**YAML**
- 封面识别：上传后**自动**识别公司名 + 年度，不做人工下拉
- 规则粒度：**一家公司一份配置，跨年度复用**；页码容差 ±2 页（已确认）；仅表结构大改时才追加 `structure_versions`
- 新年度上传：**零配置改动**，不要求提前补年度文件
- Track 公司：**不绕过公司类型筛选**，只受“展示公司”多选影响（可自动并入）
- 能力五边形对比公司：候选 = **当前公司类型筛选后的公司池**；叠加图进 PDF
- 旧规则：保留，不删
- 文档：写成文档纳入管理

---

## 1. 现状梳理（已读代码的事实基线）

### 1.1 Track Company

| 位置 | 现状 |
| --- | --- |
| `step7_solvency/report.py:296-305` | “特定追踪”下拉候选 = `["无", *selected_companies]`，只含**当前展示公司**；公司类型筛选会把它排除 |
| `step7_solvency/report.py:378-385` | `scoped` 按 `selected_companies` 排序，追踪公司没有置顶逻辑 |
| `step7_solvency/rendering.py:490-530` `render_horizontal_panel_strip` | 公司卡片横向 strip，>6 家时容器 key 为 `annual_company_grid_scroll_*`（可横向滚动） |
| `step7_solvency/rendering.py:518-588` `render_company_chart_grid` | 追踪公司只改卡片 tone（`tracked`）+ 文案提示“左右拖动下方滚动条” |
| `step7_solvency/theme.py:417-474` | strip 横向滚动 CSS；`[class*="st-key-annual_company_panel_tracked_"]` 只改背景和边框，**无 sticky** |
| 调用点 | `render_company_chart_grid` 共 7 处：`plan_renderers.py:988/1025/1105/1262/1528/1594`、`rendering.py:360` |
| 能力五边形 | `plan_renderers.py:1807` `render_plan_capability_radar`；按 `同业分类` 分组，每组 10 家分面 `services/solvency_step7_charts/radar.py:353 build_capability_radar_grid`；追踪公司仅 ★ 前缀 + 红色 |
| 雷达能力 | `radar.py:298 build_capability_radar_chart` 本身支持一次传入多家公司（同一坐标系叠加），但目前只画描边，不填充面积 |
| 其他追踪逻辑 | `fragments.py:42` bubble 图“定位追踪公司”缩放；`matrix.py:53`、`bubble.py:459`、`trend.py:574` 有局部追踪逻辑 |

关键结论：
- R1-a/R1-b 落在 `render_horizontal_panel_strip` + `render_company_chart_grid` + `theme.py`，**一处改动可覆盖 7 个调用点**。
- R1-c 的底层 API 已具备叠加能力，只差“填充图层 + 对比选择器 + 不依赖分类展示”。

### 1.2 表格提取（规则法）

| 位置 | 现状 |
| --- | --- |
| `services/solvency_table_extractor.py:12-25` | `TABLE_SIGNATURES`：每张目标表的**必含关键词**，硬编码 |
| `services/solvency_table_extractor.py:26-48` | `TABLE_EXCLUSIONS`：混表排除词，硬编码 |
| `services/solvency_table_extractor.py:50-60` | `TABLE_HEADERS`：表头词（质量打分用），硬编码 |
| `services/solvency_table_extractor.py:62-100` | `TABLE_START_MARKERS` / `TABLE_END_MARKERS`：起止边界，硬编码 |
| `services/solvency_table_extractor.py:1087-1125` `_candidate_quality` | 用上面四组常量打分，`score<0` 直接丢候选 |
| `services/solvency_table_extractor.py:1195-1200` | 候选必须包含全部 `TABLE_SIGNATURES`，否则丢弃 |
| `services/solvency_table_extractor.py:1132-1160` | `_PAGE_STRATEGIES`（线框/文字对齐）也硬编码 |
| `services/table_strategy_registry.py` | 已有 strategy_id 路由（`generic.grid_table.v1`、`life_solvency.*` 等），但只路由“处理函数”，不含表定义 |
| `services/solvency_pdf_locator.py:841` `locate_tables` | 页定位靠锚点词打分 + 续页评分，**不读页码**，无公司/年度概念 |
| `services/solvency_pdf_locator.py:911` `extract_report_metadata` | 已能识别封面 `公司` / `报告年度` / `报告季度` / `报告期`（含 fitz 兜底、零宽字符处理） |
| `config/solvency_page_features.json`、`config/non_life_solvency_page_features.json` | 已有 Profile v2 表定义 schema（title/content/header/continuation/stop/terminal…），但 **grep 确认代码中无任何引用**，是历史遗留 |
| `services/report_profiles.py` | Profile 工作簿已支持“目标表 / 定位关键词 / 版式变体 / 字段字典 / 完整性规则”等 sheet，`table_config` 已能带 `title_terms`、`header_terms`、`minimum_score` 等 |
| `services/batch_workflow.py:288` `extract_document_tables` | 批量提取以 `company` 字符串为 key，`match.table_config` 原样透传给 `extract_page_with_method` |
| `services/batch_workflow.py:79` `ensure_document_metadata` | 文档首次使用时才跑 `extract_report_metadata`，结果按需惰性加载 |
| `services/batch_processor.py:268` `locate_one_pdf_bytes` | 批量定位入口（单文件定位共用） |

关键结论：
- “表格定义写死在代码里”指 `solvency_table_extractor.py` 顶部五个常量字典，**而不只是 Profile 的 `title_terms`**。
- 现有定位是纯语义打分，没有“这家公司这份报告第 10-11 页”的先验知识可用。
- `metadata` 里的 `公司`/`报告年度` 已可直接作为路由键，无需新写封面识别。
- **table_id 不一致（已 grep 确认）**：内置常量用 `OPERATING_METRICS`，`NON_LIFE_SOLVENCY` Profile 用 `NON_LIFE_OPERATING_METRICS`；配置化时必须做别名映射，否则该表头打分经 Profile 路径不生效。

### 1.3 缺失值与备注

| 位置 | 现状 |
| --- | --- |
| `services/solvency_normalizer.py:21` `STANDARD_COLUMNS` | 已含 `备注`、`来源页码`、`来源类型`、`计算逻辑` |
| `services/solvency_step3_standardizer.py:282` `_attach_diagnostic_status` | 已有诊断状态：`未匹配 / 期间未识别 / 值为空 / 口径冲突 / 歧义待确认` |
| `services/solvency_step3_standardizer.py:1130` `standardize_to_target` | 返回 `summary + diagnostics + standard_data` |
| `services/step3_metric_gap.py` | STEP7 指标缺口清单，已有“缺失指标编码 / 缺失依赖 / 说明”列 |
| `ui/step2_extraction.py` 提取日志 | 列为 `目标表 / 物理页码 / 识别方式 / 状态 / 说明`，无“规则来源/未命中原因” |

关键结论：
- 不需要新建缺失值体系，只需给现有 `诊断状态` / `备注` / `说明` **补“为什么缺”**（规则未命中、页码不符、锚点未命中、必填行缺失等）。

---

## 2. 设计决策

### 2.1 配置分层（R3 + R2）

三层配置，优先级从低到高：

```
代码内置默认常量（保留，兜底）
  ↑
config/extraction/tables.yaml        表级定义（所有公司共用）
  ↑
config/extraction/companies/<公司>.yaml  公司级规则（跨年度复用 + 可选结构版本）
  ↑
（可选）Profile 工作簿“目标表”sheet 已有字段仍生效（不重复迁移）
```

- 表级 YAML：迁移现有 `TABLE_SIGNATURES / TABLE_EXCLUSIONS / TABLE_HEADERS / TABLE_START_MARKERS / TABLE_END_MARKERS`。
- 公司级 YAML：只写**与默认不同**的部分（页码、标题变体、列映射、必填行），避免复制整张表定义。
- 公司专属提取器（可选）：`company.extractor` 指向 `services/company_extractors/<name>.py`，
  在通用提取之后修补版式特例（合并单元格、跨章节指标、无表头续页）。
  目前只有平安启用（`extractor: pingan`）；其他公司不配则行为完全不变。
- 兼容：`solvency_table_extractor.py` 继续导出 `TABLE_HEADERS` 等名字（改为从配置模块读取），`batch_workflow.py`、`table_strategy_handlers.py` 无需改 import。

### 2.2 规则模型：公司级配置 + 容差窗口（R2）

**不做年度配置**。一家公司一个 YAML，跨年度复用；只有表结构大改时才追加一条“结构版本”。

路由键：

```
profile_id + canonical_company        （report_year 仅用于选结构版本）
```

- `canonical_company`：`metadata["公司"]` → 去后缀/别名归一（复用 `services/solvency_company_identity.py`、`normalize_insurers.py` 的别名表）。
- `report_year`：`metadata["报告年度"]`；为 `None` 时按文件名回退，再按 `报告期` 前 4 位。
- **年份缺失不阻塞提取**：直接用文件主体规则，记 `YEAR_UNKNOWN` 警告。
- 结构版本：`structure_versions` 取 `since <= report_year` 的最新一条覆盖文件主体；没有该节点则永远用主体。

**页码定位 = 典型位置 + 容差 + 锚点验证**：

```
expected_pages: [10, 11]   → 搜索窗口 [8, 13]（tolerance: 2，已确认）
                          → 窗口内先按 anchor_terms 命中
                          → 窗口内无命中 → 全文找锚点
                          → 全文也没有 → 标记缺失
```

| 场景 | 结果 |
| --- | --- |
| 锚点在窗口内、偏移 ≤ 容差 | 采用；偏移 ≠ 0 时记 `PAGE_DRIFT` 提示 |
| 锚点在窗口外但全文找到 | 采用；记 `PAGE_OUT_OF_TOLERANCE`，提示规则该更新 |
| 全文找不到锚点 | 该表标记缺失 `ANCHOR_NOT_FOUND` |
| 窗口内多页命中 | 取锚点分最高、且与 `expected_pages` 最近的一页 |

页匹配模式（逐表可选）：

| 模式 | 行为 |
| --- | --- |
| `anchor_only` | 完全沿用现有锚点打分（**未配置公司**的默认值，行为不变） |
| `prefer` | 窗口内优先、窗口外全文回退（**已配置公司**的默认值，推荐） |
| `strict` | 只在窗口内接受；窗口内无锚点即标记缺失，不全文回退（对页码极有把握的表才用） |

> 已配置公司默认 `prefer`，所以“今年 11 页、明年 9 页”仍在容差内，无需改配置；未配置公司走 `anchor_only`，零回归。

### 2.3 缺失原因码（R4）

| 码 | 触发条件 | 文案示例 |
| --- | --- | --- |
| `RULE_NOT_FOUND` | 该公司无配置文件 | “未找到该公司提取规则，按通用规则提取”（仅提示，不阻断） |
| `YEAR_UNKNOWN` | 封面未识别出年度 | “封面未识别报告年度，仅应用公司级主体规则” |
| `PAGE_DRIFT` | 锚点在容差窗口内，但偏离 `expected_pages` | “经营情况表定位到第 12 页（预期 10-11 页，容差 ±2）” |
| `PAGE_OUT_OF_TOLERANCE` | 锚点在窗口外、全文找到 | “经营情况表在第 16 页，超出容差 ±2 页，建议更新规则” |
| `ANCHOR_NOT_FOUND` | 全文无规则标题词 | “未找到表标题‘主要经营指标’，该表标记缺失” |
| `SIGNATURE_MISSING` | 页内有表但缺必含关键词 | “第 10 页表格缺少关键词‘保险业务收入’/‘本年累计’” |
| `REQUIRED_ITEM_MISSING` | 表已提取但必填行缺失 | “经营情况表缺少行‘综合退保率’（规则必填）” |
| `UNIT_UNKNOWN` | 单位未识别 | “未识别表单位，按默认万元处理” |

落点：
1. STEP1 页码面板：显示“命中规则：人保产险（rule v1，prefer）”，并给出“预期 10-11 页 → 实际 12 页”的偏差提示。
2. STEP2 日志：新增列 `规则来源`、`未命中原因`。
3. STEP3 匹配诊断：`状态` 增加上述码；标准窄表 `备注` 写同义中文原因。
4. STEP7 缺口清单：`说明` 列透出原因（`step3_metric_gap.py` 现有字段即可）。

### 2.4 Track Company（R1）

**候选池（已确认：不绕过类型筛选）**：`highlight_options = ["无", *company_scope_for_types(...) 返回的 all_companies]`——**受公司类型筛选限制，不受“展示公司”多选限制**。

- 若追踪公司被类型筛选排除：进入报告页时 `highlight_company` 自动回退为“无”，并提示“追踪公司不在当前公司类型范围内”。
- 若追踪公司在类型池内、但不在 `selected_companies` 内：自动并入展示。

选中后：
1. 若不在 `selected_companies` 内，**自动追加**（排在首位），并在图表设置区给一行提示“追踪公司已自动加入展示”。
2. `scoped` 的 `Categorical` 顺序把追踪公司放第 0 位。
3. `render_company_chart_grid` 内部再做一次兜底排序（防止其他调用点绕过 `report.py`）。

**sticky**：
- 只在 screen 媒体生效，作用在 `[class*="annual_company_grid_scroll_"]` 下的追踪卡片列；
- `@media print` 已 `overflow: visible`（`theme.py:578`），sticky 不参与排版，PDF 不受影响；
- 风险点：Streamlit DOM 结构（`stColumn`）变更会让 `:has()` 选择器失效，需要在 `theme.py` 里加注释并写测试脚本（用 AppTest 无法验样式，靠人工截图）。

**雷达叠加**：
- 模块内新增 `st.selectbox("对比公司", ["无", *当前类型筛选后的公司])`，**候选与 Track 同池**，仅含 `company_scope_for_types` 得到的公司；
- 对比公司可以不勾选在“展示公司”里（不影响叠加图渲染），但不能是类型筛选之外的公司；
- 选“无”：现有分组网格不变；
- 选了公司：在分组网格**之前**插入一张“Track vs 对比公司”双层雷达（`panel_width=560`，PDF 整页宽，已确认入库）；
- `radar.py` 新增 `filled=True` 参数：在 `poly_layer` 之上叠 `mark_area(opacity=0.18)`，公司色沿用 `company_color_map`；Track 强制 `#00338D`，对比公司用 `#ED2124`（与现有 ★ 红一致）或调色板第一色，实施时按对比度定；
- 若追踪公司被类型筛选排除（`highlight_company` 已回退“无”）或对比公司不在当前类型池：叠加图不渲染，只显示一行提示，不强行插入。

---

## 3. 配置文件草案

### 3.1 `config/extraction/tables.yaml`（表级）

```yaml
version: 1
description: 规则法提取的目标表定义（代码内置常量已迁移，缺失字段回退内置默认）
tables:
  SOLVENCY_MAIN:
    table_name: 偿付能力充足率指标
    strategy_id: life_solvency.solvency_main.v1
    required: true
    signature_terms: [认可资产, 综合偿付能力充足率]      # 原 TABLE_SIGNATURES
    exclude_terms: [监管指标名称, 流动性覆盖率, LCR1, LCR2, LCR3]  # 原 TABLE_EXCLUSIONS
    header_terms: [项目, 指标名称, 本季度数, 本季度末数, 上季度可比数, 上季度末数]
    start_markers: [指标名称, 认可资产]
    end_markers: [监管指标名称, 流动性覆盖率, LCR1]
  OPERATING_METRICS:
    table_name: 主要经营指标
    aliases: [NON_LIFE_OPERATING_METRICS]   # 见下方“table_id 对齐”
    signature_terms: [保险业务收入]
    header_terms: [指标名称, 本季度, 本年累计]
    start_markers: [主要经营指标, 效益类指标, 规模类指标, 品质类指标, 保险业务收入]
    end_markers: [流动性风险监测指标, 近三年综合投资收益率]
  # ACTUAL_CAPITAL / MINIMUM_CAPITAL / THREE_YEAR_INVESTMENT_RETURN 同理
extraction_strategies:                   # 原 _PAGE_STRATEGIES
  - name: 线框识别
    settings: {vertical_strategy: lines, horizontal_strategy: lines, snap_tolerance: 4, join_tolerance: 4, intersection_tolerance: 5}
  - name: 文字对齐识别
    settings: {vertical_strategy: text, horizontal_strategy: text, min_words_vertical: 2, min_words_horizontal: 1}
```

**table_id 对齐（批次 1 必做）**：代码内置常量用 `OPERATING_METRICS`，而 `NON_LIFE_SOLVENCY` Profile 的 table_id 是 `NON_LIFE_OPERATING_METRICS`（已 grep 确认）。现状下经 Profile 走批量提取时，`TABLE_HEADERS.get("NON_LIFE_OPERATING_METRICS")` 为空，表头打分实际不生效。配置化时用 `aliases` 建立双向映射，`solvency_table_extractor` 取定义前先解析别名，两个 id 都能命中同一份定义。

### 3.2 `config/extraction/companies/人保产险.yaml`（公司级，跨年度）

```yaml
company:
  canonical: 人保产险
  aliases: [中国人民财产保险股份有限公司, 人保财险]
  profile_id: NON_LIFE_SOLVENCY
  rule_version: 1
  notes: 2022 起版式稳定；经营情况表常年位于第 10-11 页，条目与单位不变

tables:                       # 跨年度通用：新年度上传无需改动
  SOLVENCY_MAIN:
    page_match: anchor_only
  NON_LIFE_OPERATING_METRICS:
    expected_pages: [10, 11]  # 典型位置
    tolerance: 2              # 搜索窗口 8–13 页（已确认）
    page_match: prefer
    anchor_terms: [主要经营指标]
    required_items: [保险业务收入, 净利润, 综合成本率]
    unit: 万元

# 只有表结构大改才启用；平时整段留空或删除
structure_versions:
  - since: 2025
    notes: 2025 起经营情况表并入“经营情况分析”章节
    tables:
      NON_LIFE_OPERATING_METRICS:
        expected_pages: [14, 15]
        anchor_terms: [经营情况分析]
```

字段语义：
- `expected_pages`：PDF 物理页码（从 1 开始，与 STEP1 面板一致），不是页眉印刷页码
- `tolerance`：默认 2 页（已确认），公司级生效，单表可覆盖
- `page_match`：`anchor_only` / `prefer` / `strict`，已配置公司默认 `prefer`
- `anchor_terms`：覆盖 `tables.yaml` 的 `start_markers`（标题变体）
- `required_items` / `unit`：长期断言，用于缺失判定与单位校验，不逐年维护
- `structure_versions[].since`：该版本生效年份；运行时取 `since <= 报告年度` 的最新一条
- `header_terms` / `exclude_terms` / `start_item` / `end_item`：按需覆盖同名默认
- `columns`（可选）：列名映射，供表头不标准的公司使用

### 3.3 校验规则（加载时即报错，不静默）

- `table_id` 必须存在于 `tables.yaml`
- `expected_pages` 必须是正整数；`tolerance` 非负整数（默认 2）
- `page_match` 枚举值合法
- `structure_versions[].since` 年份递增且不重复
- `canonical` 不重复；`aliases` 不与其他公司冲突
- 公司文件名 = `canonical`，防止拼写漂移

---

## 4. 分步实施

> 每一步结束都可独立验收、单独 commit；不同批次不交叉修改同一文件。

### 实施进度（2026-11-18）

| 批次 | 状态 | 提交 |
| --- | --- | --- |
| 0 准备 | ⚠️ 部分完成：分支 `feature/track-company-and-rule-config` + tag `pre-refactor-2026-11` 已建；未跑 3 家×3 年完整基线 | — |
| 1 表定义外置 | ✅ 完成：常量与改造前逐项一致（含别名） | `0865d90` |
| 2 规则库骨架 | ✅ 完成：3 家 × 6 张目标表 + 容差窗口 + 别名/结构版本路由 | `6409715` |
| 3 规则铺开 + 缺失原因 | ⚠️ 部分：3 家全部目标表已配；缺失原因透出（STEP2/STEP3/STEP7）未做 | `6409715` |
| 4 Track 置顶 + sticky | ✅ 完成 | `ab086ec` |
| 5 五边形叠加对比 | ✅ 完成 | `ab086ec` |
| 6 回归 + 文档 | ⚠️ 部分完成：新测试与 AppTest 通过；PDF 整本导出未跑 | — |

已完成验证：

- `tests/test_extraction_config.py`、`test_track_company_order.py`、`test_capability_radar_overlap.py` 通过。
- `test_new_workflow_behavior`、`test_step2_extraction_modes`、`test_step3_header_recovery`、`test_workflow_smoke`、`test_app_smoke` 通过。
- 真实 PDF 冒烟：人保产险 2022 定位成功（43 页），`NON_LIFE_OPERATING_METRICS` → 物理页 20-21；封面公司识别为“中国财产再保险股份有限公司”（文件名与封面不一致，属数据问题，路由以封面为准）。
- 规则端到端：人保/平安/太保 × 2022–2025 共 12 份 PDF，全部命中规则窗口（人保 18-23、平安 17-22、太保 10-15）；2025 样本为首次使用，未新增任何年度配置。
- 人保 2025 无规则时经营表被误扩到 20-23 页（混入“五、风险管理能力”），规则窗口将其修正为 20-21 页。
- 三家公司规则已覆盖 Profile 全部 6 张目标表，2025 年每张表均命中窗口（人保 15-20/18-23/33-38/39-44/17-22；平安 15-20/17-22/34-39/40-45/18-23；太保 8-13/10-15/19-24/23-26/11-15）。
- 新增 `keep_unmapped`（默认关闭）：体检脚本默认保留未匹配标准指标库的原始行（编码 `RAW_*`），2025 实测指标数 人保 90→111、平安 58→71、太保 51→58。
- 平安 2022 专属提取器（`services/company_extractors/pingan.py`）：修复偿付能力主表粘连列与换行标签，重建经营表效益/规模/品质类指标，并从公司分类表补签单保费；实测指标 66→83，认可资产/负债、量化风险最低资本、签单保费全部就位。
- 能力五边形叠加图已用 `vegalite_to_svg` 验证可转换（PDF 链路可用）。

已知未处理：

- `tests/test_step7_excel_modules.py` 的 `test_industry_premium_template_module` 既有失败（与本改造无关，行业保费模块未动）。
- 批次 3 的缺失原因透出（`RULE_NOT_FOUND` / `PAGE_OUT_OF_TOLERANCE` / `ANCHOR_NOT_FOUND` / `REQUIRED_ITEM_MISSING` 写入 STEP2 日志、STEP3 诊断与备注、STEP7 缺口清单）尚未实现；当前仅在 STEP1 面板与 `PageMatch.rule_note` 中显示窗口命中情况。
- 其余 86 家公司的 YAML 尚未铺开。
- 公司专属提取器目前只在批量路径（`batch_workflow`）生效；STEP2 单文件上传路径尚未接入。
- 专属提取器仅平安试点；太保续页（第 13/14 页）仍走通用规则，指标覆盖偏低（39-51）。

### 批次 0：准备（0.5 天）

- 从 `main` 拉分支 `feature/track-company-and-rule-config`
- 打 tag `pre-refactor-2026-11`
- 用 3 家（人保产险/平安产险/太保产险）× 3 年（2022–2024）跑一遍现状 STEP1→STEP3，导出页码/行数/缺失清单，存 `plans/baseline/`
- 产出：现状基线（后续回归对比用）

### 批次 1：表定义外置（R3，纯重构，不改行为）—— 1–1.5 天

改动：
- 新增 `services/extraction_config.py`
  - `load_table_config(path)` / `table_definitions()` / `page_strategies()`
  - 校验 + `lru_cache`（按 mtime + size）
  - 任何字段缺失回退 `solvency_table_extractor` 内置常量
- 新增 `config/extraction/tables.yaml`
- 改 `services/solvency_table_extractor.py`：五个常量字典改为“从配置构造的只读映射”，对外名字不变
- 改 `_PAGE_STRATEGIES` 改为从配置读取
- 新增 `tests/test_extraction_config.py`：YAML 缺字段/非法值/回退默认/常量一致性（与旧值逐项比对）

验收：
- 同一批 PDF（3 家 × 3 年）提取结果与基线**逐表逐行一致**
- `py_compile` 通过；`tests/test_step2_extraction_modes.py`、`test_new_workflow_behavior.py` 通过

风险：`TABLE_HEADERS` 被 `table_strategy_handlers.py`、`batch_workflow.py` 直接 import，重构时只改来源不改名字。

### 批次 2：规则库骨架 + 容差定位（R2 第一步，1 家公司试点）—— 1.5–2 天

改动：
- 新增 `services/extraction_rules.py`
  - `load_rule_catalog(config_dir)` → `{canonical: CompanyRule}`
  - `resolve_rule(profile_id, company, year)` → `ResolvedRule`（含 `rule_source`、`structure_version`、`page_windows`、`warnings`）
  - `apply_rule(profile_tables, resolved)` → 覆盖后的 table_config 列表（不修改原对象）
  - `normalize_company_name(metadata, aliases)`（复用 `solvency_company_identity`）
- 新增 `config/extraction/companies/人保产险.yaml`：**一份文件覆盖 2022–2024** 的 `NON_LIFE_OPERATING_METRICS`（`expected_pages: [10, 11]`、`tolerance: 2`）
- 改 `services/batch_processor.py: locate_one_pdf_bytes`：定位前解析规则，用规则覆盖 `profile.feature_config["tables"]`
- 改 `services/solvency_pdf_locator.py: locate_tables`：新增可选 `expected_pages` / `tolerance` / `page_match` 处理（默认 `anchor_only` 时逻辑完全不变）
- 改 `services/batch_workflow.py: ensure_document_metadata` 返回值带 `rule`（供 STEP1/STEP2 展示）
- 新增 `tests/test_company_rule_catalog.py`：别名归一、年度缺失、容差窗口计算、结构版本选择、三种 `page_match`、无规则回退

验收：
- 人保 2022/2023/2024 三份 PDF 使用**同一份配置**，`OPERATING_METRICS` 均落入 8–13 页窗口并命中锚点
- 手动不新增任何 2023/2024 配置，验证“新年度零配置”成立
- 其余 6 份 PDF 结果与基线一致（无规则不影响）

### 批次 3：规则铺开 + 缺失原因（R2 完成 + R4）—— 2–3 天（取决于公司数）

改动：
- 补 `config/extraction/companies/*.yaml`：先 3 家（人保产险/平安产险/太保产险）各一份，全部跨年度复用，验证后再逐家扩
- 新增 `services/extraction_trace.py`
  - `build_trace(resolved_rule, matches, logs)` → `list[ExtractionTrace]`
  - 原因码枚举（见 §2.3）
- 改 `services/batch_workflow.py: extract_document_tables`：outcome 增加 `rule` / `trace`
- 改 `ui/step1_locator.py`：页码面板显示命中规则 + `PAGE_DRIFT` / `PAGE_OUT_OF_TOLERANCE` 警告
- 改 `ui/step2_extraction.py`：日志表增加 `规则来源`、`未命中原因` 两列
- 改 `services/solvency_step3_standardizer.py`：把 trace 并入 `diagnostics`（`状态` = 原因码）与标准窄表 `备注`
- 改 `services/step3_metric_gap.py`：缺口清单说明列带原因
- 新增 `tests/test_missing_reason.py`

验收：
- 把某公司 `expected_pages` 故意改成 `[40, 41]`（`prefer`）→ 全文回退成功，且日志出现 `PAGE_OUT_OF_TOLERANCE`
- 把 `anchor_terms` 改成不存在的词 → STEP2 日志、STEP3 诊断、STEP7 缺口清单三处都显示 `ANCHOR_NOT_FOUND`，数值为缺失（不是 0）
- 恢复配置后结果回到基线

### 批次 4：Track 置顶 + sticky（R1-a/R1-b，独立可并行）—— 1 天

改动：
- `step7_solvency/report.py`
  - `highlight_options` 改为 `["无", *company_scope_for_types(...) 返回的公司]`（受类型筛选，不受展示多选）
  - 选中追踪公司后并入 `selected_companies` 并置顶
  - 类型筛选把追踪公司排除时，自动回退“无”并提示
  - 配置区提示“追踪公司已自动加入展示”
- `step7_solvency/rendering.py: render_company_chart_grid`
  - `companies` 排序时把 tracked 提到首位（兜底）
- `step7_solvency/theme.py`
  - 在 `[class*="annual_company_grid_scroll_"]` 下给含 `annual_company_panel_tracked_` 的列加 `position: sticky; left: 0; z-index: 3; background: #fff`
  - 只在 screen 媒体块内；`@media print` 不变
- 新增 `tests/test_track_company_order.py`（纯函数级：给定 frame/highlight，断言公司顺序）

验收：
- 选“无”时顺序 = 现状
- 选一家在类型池内、但未勾选展示的公司：出现在首位、卡片有追踪样式、横向滚动时钉在左侧
- 切换公司类型把追踪公司排除：追踪自动回退“无”，页面提示原因，不报错
- PDF 导出：追踪公司仍是第一张卡片，且不出现滚动条残留

风险：`:has()` 兼容性（Chromium 已支持）；Streamlit 升级导致 `stColumn` 结构变化。

### 批次 5：能力五边形叠加对比（R1-c）—— 1.5 天

改动：
- `services/solvency_step7_charts/radar.py`
  - `_radar_layers` 新增 `filled` / `fill_opacity` 参数，叠加 `mark_area`
  - `build_capability_radar_chart` 新增 `fill` 透传
- `step7_solvency/plan_renderers.py: render_plan_capability_radar`
  - 模块顶部新增“对比公司”选择器（`key` 含 `key_prefix`，避免多模块互相覆盖）
  - 选“无”：行为不变
  - 选了公司：渲染 Track vs 对比公司叠加图（含图例、分数标注可选）
  - 对比公司下拉候选 = `company_scope_for_types` 的公司池，与 Track 同池
- 新增 `tests/test_capability_radar_overlap.py`：数据结构、两层多边形点数、颜色/图例、空数据报错

验收：
- 叠加图两色区分明显、面积半透明可见网格
- 对比公司候选不包含类型筛选之外的公司
- 侧边栏切换语言后重渲染正常
- PDF 导出该模块为整页宽、无图例遮挡

风险：Vega `mark_area` 的 `order` 需显式给（否则自交多边形填充出错）；SVG 转换后半透明面积可能变实，需按 `agent.md §5.5` 的导出流程目视检查。

### 批次 6：收尾（1 天）

- 全量回归：
  - `py_compile`
  - `tests/test_step2_extraction_modes.py`、`test_step3_header_recovery.py`、`test_step4_validation.py`
  - `tests/test_step7_*` 全部
  - 3 家 × 3 年人工页码/行数对比基线
  - 10 家公司 18 模块 PDF 导出（`S7_EXPORT_DEBUG=1`）
- 更新 `agent.md`：新增 `services/extraction_config.py`、`extraction_rules.py`、`extraction_trace.py` 的说明；`config/extraction/` 目录职责；`plans/` 入库约定
- 文档同步本文件（标记各批次完成状态）

---

## 5. 分支与版本管理

| 项 | 约定 |
| --- | --- |
| 主分支 | `main` 保持可运行 |
| 工作分支 | `feature/track-company-and-rule-config`（批次 1–3）；`feature/track-company-ui`（批次 4–5） |
| 提交粒度 | 一个批次至少一个 commit，commit message 带批次号，如 `feat(extraction): 批次1 表定义外置到 YAML` |
| 旧代码 | 保留内置常量与 `anchor_only` 路径，不删；废弃项加 `# DEPRECATED:` 注释说明由谁替代 |
| 回滚 | 每批次可单独 revert；配置化失败可删 `config/extraction/` 回到内置常量 |
| 标签 | `pre-refactor-2026-11`、每批次验收后 `batch-N-ok` |

---

## 6. 回归测试清单（新增）

| 测试文件 | 覆盖点 |
| --- | --- |
| `tests/test_extraction_config.py` | YAML 加载/校验/缺字段回退/与旧常量逐项一致 |
| `tests/test_company_rule_catalog.py` | 别名归一、年度缺失、容差窗口、结构版本选择、三种 `page_match`、无规则回退 |
| `tests/test_missing_reason.py` | 各原因码生成与三处透出字段 |
| `tests/test_track_company_order.py` | 追踪公司置顶、自动并入展示、未选时不改变顺序 |
| `tests/test_capability_radar_overlap.py` | 叠加图层、配色、图例、空输入 |

人工验收（每批次必做）：
- STEP1 页码面板：规则来源、页码偏差警告
- STEP2 日志：规则列、未命中原因
- STEP7 报告页：追踪卡置顶 + 横向滚动钉住（截图）
- STEP7 能力五边形：叠加图 + PDF 导出目视

---

## 7. 已确认决策与遗留项

已确认（2026-11）：
1. `expected_pages` = **PDF 物理页码**（从 1 开始，与 STEP1 面板一致），不做印刷页码偏移。
2. Track 公司 **不绕过公司类型筛选**；被类型筛选排除时回退“无”并提示。
3. 能力五边形对比公司候选 = **当前公司类型筛选后的公司池**（与 Track 同池）。
4. 叠加雷达图 **进 PDF**（整页宽）。
5. 试点公司：**人保产险 / 平安产险 / 太保产险**，各一份配置跨 2022–2024 复用。
6. 页码容差：**±2 页**（已确认）。
7. 规则路由键用**封面公司名**（`metadata["公司"]`）；文件名与封面冲突时以封面为准，并保留 `report_identity_warning` 人工核查提示。

遗留待定：
- `config/solvency_page_features.json` 的处置：建议保留只读、不接入新链路（批次 1 不动它）。

---

## 8. 工作量汇总

| 批次 | 内容 | 预估 |
| --- | --- | --- |
| 0 | 准备 + 基线 | 0.5 天 |
| 1 | 表定义外置 YAML | 1–1.5 天 |
| 2 | 规则路由骨架（1 家试点、跨年度复用） | 1.5–2 天 |
| 3 | 规则铺开 + 缺失原因透出 | 2–3 天 |
| 4 | Track 置顶 + sticky | 1 天 |
| 5 | 五边形叠加对比 | 1.5 天 |
| 6 | 回归 + 文档 | 1 天 |
| 合计 | | 8–10 天（人工核对 PDF 时间另计） |

批次 1–3（提取）与批次 4–5（展示）互不依赖，可并行；建议先做批次 4/5 快速见效，再做提取改造。
