# agent.md — 偿付能力报告平台开发指南

给在这个仓库里干活的 AI agent 看的速查手册。**先读这里，再动手。**

最后更新：2026-11（含 PDF 服务端导出重构、STEP7 增速口径统一）

---

## 1. 这是什么

把保险公司披露的**偿付能力季度/年度报告 PDF**半自动转成跨公司、跨期可比的标准化窄表，
再生成公司级对标报告（STEP7）与行业分析报告（STEP8）。

- 技术栈：Python 3.12 + Streamlit 单页应用（`st.tabs` 驱动 STEP0→STEP8），中文/英文双语切换。
- 规模：约 145 个 Python 文件 / 7 万行；29 个脚本式回归测试。
- 数据不随仓库分发（`Data/`、`*.xlsx` 原始数据均被 `.gitignore` 排除），只保留
  `财险公司列表.xlsx` 这一份公司主数据。

---

## 2. 环境与运行

### 2.1 解释器

- 项目自带虚拟环境 `venv/Scripts/python.exe`（Python 3.12，Streamlit 1.66）。
  系统里另有 3.12/3.13，依赖不同，**跑测试和导出优先用 `venv`**。
- 启动：
  ```bash
  ./venv/Scripts/python.exe -m streamlit run app.py
  ```
- 依赖清单 `requirements.txt`（含 `playwright`、`vl-convert-python`、`kaleido`、`python-calamine`）。

### 2.2 常见安装坑

| 现象 | 处理 |
| --- | --- |
| `[Errno 13] Permission denied ... pip-unpack-*.whl.metadata` | `TMP=$PWD/tmp/pip-tmp TEMP=$PWD/tmp/pip-tmp ... pip install --no-cache-dir <pkg>` |
| Playwright 报 `EPERM ... mkdtemp ... playwright-artifacts-*` | 浏览器已装到 `%LOCALAPPDATA%\ms-playwright`；`services/report_pdf/renderer.py:_ensure_writable_temp()` 已把 TEMP 指向 `tmp/playwright` |
| Kaleido 报 `Couldn't close or kill browser subprocess` | 并发转换冲突，`services/report_pdf/svg.py:plotly_to_svg` 已加锁+重试 |

### 2.3 测试

**没有装 pytest**，测试是普通脚本：

```bash
./venv/Scripts/python.exe -m tests.test_industry_market_share_logic
./venv/Scripts/python.exe -m tests.test_step7_excel_modules
```

---

## 3. 目录地图

```
app.py                     入口：登录门禁、全局样式、身份条、按 st.tabs 路由各 STEP
ui/                        界面层，一个 STEP 一个模块
  config.py                平台常量：工作流标签、Profile 默认值、AI 预设、角色
  navigation.py            侧边栏导航（一级/二级分类、Excel 驱动 STEP7 导航、导出按钮）
  auth.py / session.py     登录与会话状态
  cache.py                 Streamlit 缓存包装（Profile、STEP7 模块、可视化数据源）
  profile_settings.py      Profile 切换与标题
  theme.py                 全局 CSS
  step0_monitor.py         STEP0 报告监控
  step1_locator.py         STEP1 页码定位
  step2_extraction.py      STEP2 表格提取
  step3_standardization.py STEP3 标准化 + STEP7 缺口检查
  step4_validation.py      STEP4 勾稽检查
  step5_integration.py     STEP5 数据集成
  step6_analysis.py        STEP6 自定义分析
  step7_metric_check.py    STEP7 指标需求清单检查 / 补录
  report_pages.py          STEP7/STEP8 报告页的工作区包装
  batch_workspace.py       批量工作区 UI

step7_solvency/            STEP7 公司报告（拆得很细，按需读）
  plans.py / specs.py      报告模块计划与规格
  plan_renderers.py        按计划逐个渲染模块（图表、卡片、诊断区）
  report.py                整本报告编排：封面 → 模块循环 → 封底、导出元数据
  rendering.py             Altair 统一出口 render_altair_chart
  theme.py                 打印/PDF 专用 CSS
  fragments.py / summaries.py / insight_cards.py / underwriting.py
  ai_analysis.py           一键 AI 点评
  selection.py             图表/公司/期间筛选
  plan_inputs.py           计划输入数据准备

step8_solvency.py          STEP8 行业分析报告
dashboard_components.py    通用组件：封面/封底、KPI 卡片、概览表、色卡、
                           以及 render_print_control（薄封装，实际委托 services.report_pdf.export）

services/                  业务逻辑层（见第 4 节）
config/                    Profile 工作簿、指标字典、勾稽规则、同业分类、公司披露地址
  report_profiles/NON_LIFE_SOLVENCY_profile.xlsx
  Step7_指标需求清单.xlsx     STEP7 指标需求的唯一真源
  solvency_validation_rules.xlsx / solvency_taxonomy.xlsx
  solvency_peer_groups.json / non_life_solvency_company_urls.xlsx
  extraction/tables.yaml   规则法提取的表级定义（表头/签名/起止边界/页内策略）
  extraction/companies/    公司级规则（页码窗口、锚点、extractor），一家公司一份，跨年度复用
templates/                 STEP7 展示模块配置、STEP8 注释/刻度模板、标准目标表
tests/                     35 个脚本式回归测试
plans/                     **入库**的改造计划与实战手册（docs/ 不入库）
  refactor-plan-track-and-rule-config.md   改造计划与进度
  company-extraction-playbook.md           公司专属提取实战手册
tools/                     离线小工具（STEP7 缺失指标补录表、缺口报告、指标目录生成）
docs/                      设计文档（pdf_export_design.md）— **不入库，本地参考**
tmp/                       临时脚本/预览图/样例 PDF，产物不提交
Data/、gi_solvency_cache/、venv/、*.xlsx  全部在 .gitignore 中
```

### services/ 分层速查

| 分组 | 文件 | 职责 |
| --- | --- | --- |
| 入口/编排 | `batch_extraction.py`、`batch_processor.py`、`batch_workflow.py`、`workflow_settings.py` | 批量流水线、设置持久化 |
| PDF 定位 | `solvency_pdf_locator.py`、`semantic_period_fallback.py` | 文字版/扫描版判断、目标页物理页码定位 |
| 混合流水线 | `solvency_hybrid_pipeline/`（`locator_rule`、`locator_vision`、`page_extract`、`extract`） | 规则法优先，失败转大模型 |
| 表格提取 | `solvency_rule_table_extractor.py`、`solvency_table_extractor.py`、`solvency_table_region.py`、`solvency_table_boundaries.py`、`table_number_repair.py`、`table_strategy_*.py` | 规则法表格识别、边界、数字修复、策略注册 |
| 提取配置/规则 | `extraction_config.py`、`extraction_rules.py`、`company_extractors/` | 表级 YAML、公司级页码窗口与别名、公司专属后处理（89 家：54 实修 + 35 占位） |
| AI 提取 | `solvency_ai_table_extractor/`（`grid`、`llm`、`units`、`source_profile`） | 图片表格识别、单位换算 |
| 标准化 | `solvency_step3_standardizer.py`、`solvency_normalizer.py`、`solvency_disclosure_normalizer.py`、`step3_metric_gap.py`、`solvency_metric_registry.py` | 生成标准窄表、派生指标、缺口检查 |
| 校验 | `solvency_validator.py` | 勾稽规则（公式/跨表/完整性） |
| 集成与数据 | `solvency_dataset_adapter.py`、`solvency_company_identity.py`、`normalize_insurers.py` | 唯一数据读取入口（含公式回填+磁盘缓存）、公司身份归一 |
| STEP6 | `solvency_step6_analysis.py` | 自定义对比图 |
| STEP7 图表 | `solvency_step7_charts/*`（14 个模块）、`solvency_step7_chart_plans.py`、`solvency_step7_metric_catalog.py`、`solvency_step7_recompute.py`、`step7_excel_config.py` | 每个图的取数与构图、计划、指标目录、重算 |
| 报告通用 | `solvency_navigation.py`、`solvency_report_notes.py`、`report_i18n.py`、`report_style.py`、`report_profiles.py`、`solvency_financing_analysis.py`、`registered_capital_extractor.py` | 导航配置、备注模板、双语、配色、Profile |
| PDF 导出 | `report_pdf/`（`capture`→`svg`→`html_builder`→`renderer`→`export`） | 服务端 PDF 生成全链路 |
| LLM 接入 | `llm_config.py`、`llm_http.py` | 统一的 Base URL / Key / 调用 |

---

## 4. 数据流与关键约定

### 4.1 工作流

```
PDF → STEP0 监控更新 → STEP1 页码定位 → STEP2 表格提取
   → STEP3 标准化（窄表）→ STEP4 勾稽检查 → STEP5 数据集成
   → STEP6 自定义分析 / STEP7 公司报告 / STEP8 行业分析
```

- 每一步产物存 `st.session_state`（`standard_data`、`integrated_data`、`step6_analysis_source` 等）。
- STEP6–STEP8 的数据源统一走 `prepare_visualization_source()`：优先 `integrated_data`，
  否则回退 `standard_data`。
- 只有 **STEP6/STEP7** 对普通用户开放；STEP0–STEP5、STEP8 需"项目组成员"身份。

### 4.2 统一窄表

列固定为 `公司/同业分类/报告期/一级模块/二级模块/指标编码/指标名称/期间口径/数值/单位/来源类型/...`。

- 指标一律用**编码**（`NET_PROFIT`、`SIGNED_PREMIUM`…），显示名走 `services/report_i18n.py`。
- **期间口径已统一（2026-11）**：`本季度数/本季度/本期` → `本季度末数`；
  `上季度数/上季度/上期` → `上季度末数`。不再区分存量/经营指标，
  见 `solvency_normalizer._normalize_period_alias()`。
  勾稽检查 `PERIOD_GROUP_ALIASES` 已将“本季度末数”归入 `当前期末` 组，校验逻辑无需改动。
- **财险口径（2026-11-20）**：`INSURANCE_RISK_CAPITAL`（寿险业务保险风险最低资本合计）
  对财险公司恒为 0；报告写“-”或整行不披露时，STEP3 补 0（备注“恒为0”），
  见 `solvency_step3_standardizer._fill_insurance_risk_zero_default()`。
- 期间口径：年度数据只认 `本年累计数/年度累计数/全年累计数`
  （见 `solvency_step7_charts/industry_premium.py:ANNUAL_SCOPE_ALIASES`）。
- **同比口径（2026-11 已定稿）**：`Q4` 优先；**部分年度（如 2026Q3）与上一整年不可比，
  增速折线一律不绘制该点**（柱/点仍展示累计金额）。
  涉及 `industry_premium._growth_rows`、`premium_by_line._attach_growth`。
  → 早期版本曾按用户要求"部分年度对比上年全年"画虚线，现已回退，**不要恢复**。

### 4.3 数据读取（唯一入口）

```python
services.solvency_dataset_adapter.read_standard_workbook(bytes, filename, include_internal=True)
```

- 磁盘缓存：`tempfile.gettempdir()/gi_solvency_cache`，key 含 `STANDARD_WORKBOOK_CACHE_VERSION`。
  **改了解析逻辑必须 bump 版本号**，否则用户看到旧缓存。
- **Excel 公式无缓存值**：上传文件里的 `=34962895605.86/10000` 用 `data_only=True` 读到 None。
  `_resolve_formula_values()` 用 zipfile + iterparse 解析 sheet XML，再用 `ast` 白名单求值回填。
  **不要换成 openpyxl 遍历**（6 万行要 30 秒）。

### 4.4 STEP7 渲染约定

- 图表由 `step7_solvency/plan_renderers.py` 按 `plans.py` 的计划逐个渲染，
  每个模块用 `st.container(key=f"s7_report_module_{index}")` 包住 —— **导出靠这个 key 抓模块**。
- 新增/修改图表：优先改 `services/solvency_step7_charts/*`，再在 renderer 里接。
- 图表尺寸常量集中在 `step7_solvency/theme.py` 与各 chart 模块顶部；改样式先找常量。
- 反向坐标（排名类）：`performance.py` 用 `axis.values=[...]` 显式给整数刻度，
  否则 Vega 对恒定排名序列会**抑制全部刻度**，PDF 里变成空白图。

### 4.5 公司级提取规则与专属 extractor（2026-11）

三层结构，行为与配置分离：

```
config/extraction/tables.yaml              表级定义（所有公司共用，代码内置默认兜底）
config/extraction/companies/<公司>.yaml    公司级规则（页码窗口、锚点、extractor），当前 89 家
services/company_extractors/<name>.py      公司级后处理：54 家实修 + 35 家占位（pass-through）
services/company_extractors/_main_table.py 主表重建 helper（金额跨行/行次竖排/表头缺列）
```

- 路由键：封面 `公司`（别名归一）+ `报告年度`（仅用于选 `structure_versions`）。
- 页码：`expected_pages` + `tolerance`（默认 2）→ 窗口；窗口内按 `anchor_terms` 命中，
  窗口外回退全文；`prefer` / `strict` / `anchor_only` 三种模式。
- **页码自动确认（2026-11-20）**：定位完成后 `compare_pages_with_rule()` 逐表对比
  `expected_pages`；全部“通过/偏移”且无定位冲突时自动 `pages_confirmed=True`，
  否则 STEP1 显示对比表并提示人工确认。批量定位同样逐份自动确认。
  对比函数在 `services/extraction_rules.py`，测试 `tests/test_page_check_with_rule.py`。
- extractor 名称经 `_extraction_rule.extractor` 传入；单文件与批量共用
  `apply_company_extractor_for_tables()`：
  - 批量：`batch_workflow.extract_document_tables`
  - 单文件：`ui/step2_extraction.build_tables_from_page_results`（含单表重提取）
- **必须同步 `table.rows` 与 `table.table_blocks`**：STEP3 `_expand_table_blocks`
  优先读 blocks，只改 rows 不生效。
- 调试：`tests/test_three_companies_extraction.py`（`--company` 指定多家、`--years` 多年，写 Excel 到 tmp/），
  `tests/test_company_extractor_integration.py`（extractor 入口/幂等/blocks 同步）。
- **规则加载容错**：`load_company_rules(strict=False)` 跳过坏 YAML/重复 canonical/冲突别名，
  并记入 `get_load_warnings()`；`resolve_rule` 遇到配置错误会自动以 tolerant 方式重载，
  单份 YAML 写错不会再让全目录降级成“0 表”。
- **主表被拆碎时用重建 helper**：`_main_table.rebuild_main_rows(doc, pages)` 按词坐标重建五列主表；
  参考 `changjiang` / `rongtong` / `dongwu` / `zhongmei` / `byd` 五个 extractor。
  新增公司时先跑通用规则，只有关键指标对不上再写 extractor。
- **扫描件**：整本判为 SCANNED 时 `batch_processor._auto_vision_for_scanned` 自动改走 hybrid 视觉，
  需要会话里配置 `llm_api_key/base_url/model`；未配置时在说明里提示，不再静默 0 表。
- **新增一家公司的步骤**：写 YAML → 跑 `tests/test_three_companies_extraction --company ...` →
  必要时写 extractor（`@register` + 同步 rows/table_blocks）→ 在
  `tests/test_three_companies_extraction.COMPANY_FILES` 注册 → 写 `plans/company-<公司>-extraction.md`。
- 全量覆盖记录与遗留清单见 `plans/extraction-coverage-89-and-shared-fixes-2026-11.md`。
- 详细方法论与踩坑记录见 `plans/company-extraction-playbook.md`。

---

## 5. PDF 导出管线（服务端生成，已废弃浏览器打印）

入口：侧边栏/报告页 "导出 PDF 报告" → `dashboard_components.render_print_control()`
→ `services/report_pdf/export.py:render_export_control()`。

### 5.1 链路

1. **`capture.install()`**（在 `app.py` 顶部调用）包装 `st.altair_chart / plotly_chart / markdown /
   caption / metric / image / columns`，把渲染内容记成 `ModuleSnapshot`。
   `st.dataframe / st.table` **故意不捕获**（只导图表+文字）。
2. 报告侧配合调用：
   - `capture.start_document()` — `report.py` 开头重置整本文档；
   - `capture.begin_module(title, section)` / `capture.end_module()` — 包住每个模块；
   - `capture.layout_card()` — 把"标题 + 图"绑成一张卡片（公司网格里的每个小面板）；
   - `capture.suspend()` — 让交互式诊断区不进入 PDF（见 `plan_renderers.py` 的"数据诊断"折叠区）；
   - `capture.set_note()` / `request_export()` / `store_result()` / `result()` — 备注、导出触发与结果回传。
3. `capture.to_content_items()`：Altair/Plotly → SVG（`ThreadPoolExecutor` 并行 + 缓存）；
   同一 `st.columns` 组折成 `kind="columns"` 块（饼图"左图右栏"靠它）。
4. `html_builder.build_report_html(meta, modules, measured=...)`：封面 + 每模块固定 1600×900 页 + 封底。
   - `_units()` 只把"短标题"（≤90 字、不含卡片/图例 class）绑成图标题；
   - `_blocks_for()` 把内容切成不可分割的排版块（图行/卡片/表格），分页按**实测高度**装页；
   - 普通图两两一行；公司卡片行按公司数量自动决定列数（见 5.2）；
   - **落单图**（一行里只剩一张）用 `.half-cell` 保持半页宽 + 限高 330px，并把它自己的小标题一起包进去；
     连续两张落单图（中间只隔一行短说明）会合并成一行 `figures-row half-pair`；
   - 只有**模块内仅一张图**或显式 `usermeta={"s7_full_width": True}` 的图，才撑满整页宽。
5. `renderer.measure_and_render(probe_html, build_report)`：**一次 Chromium 会话**内完成
   "测量块高 → 用实测高度生成 HTML → 渲染 PDF"，再用 PyMuPDF 盖页码。
   容量：首页 `FIRST_PAGE_HEIGHT=720`、续页 `NEXT_PAGE_HEIGHT=780`（等于 `.module-body` 的 720/790px）。
   **不要**再给每块追加固定间隔（如 +6px）：`flow-root` 的实测高度已含块自身 margin，
   再追加会累积成假溢出，把「说明 + 图」拆成两页（曾让投资收益率模块多出一页空说明页）。
   以前 measure 和 render 各起一个浏览器，每次多 5–10 秒且偶发 `unclean kill browser`。

### 5.2 图表尺寸与卡片列数

**转换尺寸**（`capture.py` 的 `plans` 分支，请求值 ≠ 最终显示尺寸）：

| 类型 | 请求尺寸 | 说明 |
| --- | --- | --- |
| 公司卡片图 | 480×240 | 卡片内显示宽约 470px，按显示尺寸出图才能保持字号 |
| 普通单面板 | 760×270 | 两图一行，行高约 360–420px，一页可放两行 |
| 面板 ≥2 | 780×330 | —— |
| 面板 ≥5 | 1100×460 | —— |
| 整行宽图 | 1500×400 | 折线趋势图，`usermeta` 标记；Vega 会另加约 70px 轴/标题区 |
| Plotly | 用图自身尺寸 | 缩小换算会重排图例导致与轴标签重叠 |

**CSS 侧**（`html_builder._CSS`）：

- `.figure svg`：`width:100%; max-height:500px`（普通图）。
- `.figure.fill svg`：**`height:500px; width:auto`** —— 整行宽图统一高度，宽度随各自比例自适应。
  曾经用 `width:100%; height:auto`，导致比例不同的三张折线图高度差 14%，看起来"某张变小了"。
- `.company-card .figure svg`：`max-height:250px`；卡片行高约 287–310px。
- `.half-cell`：半页宽（50%）+ 图 `max-height:330px`，用于落单图。

**公司卡片列数**：`_card_columns(count)` 按"目标卡片宽 480px + 空位扣分"自动选 2–3 列
（1 家 1 列、2 家 2 列、3–12 家 3 列…），不再按 10 家的特例写死。

**`svg.py` 的约束**：顶层 `width/height` 对 facet/concat 无效，**必须写到叶子 spec**（`_size_spec()` 递归），
并按面板数重设 `columns`；面板最小 250×190。

### 5.3 快速迭代（只改排版时用这个）

```bash
python tools/fast_layout.py --refresh  # 首次：跑一遍 Streamlit + 转图，约 100s，结果缓存在 tmp/pdf_diagnose/modules.pkl
python tools/fast_layout.py            # 之后：只重"测量+排版+渲染"，约 8–10s
```

改排版时**不要**用 `tmp/apptest_export_10.py` 全量跑（那要 75–150s）。
确认排版后，再用生产路径（点"导出 PDF 报告"或 `apptest_export_10.py`）做最终校验。

### 5.4 调试开关

`S7_EXPORT_DEBUG=1` 打印 `[s7-export] modules/items/convert/html/pdf` 耗时和 `[s7-module]` 结构。

### 5.4.1 大样本导出（几十家公司）

选 40+ 家公司时，图表数量会到 300–400 张、成稿 80+ 页。三条经验：

- **导出前必须等页面渲染完**。整本报告渲染 2–3 分钟，此间 `_s7_pdf_modules` 是空的；
  旧代码会静默生成只有封面封底的 PDF。现在 `export.py` 会明确提示"报告还在渲染"，
  并校验成稿页数（≤2 页直接报错，不交付空报告）。
- **不要用页数上限压内容**。`_paginate` 的 `max_pages` 只做病态输入防御（现为 400），
  早期设 20 时会把超限的十几页合并进一页，导致内容被裁。
- **转图是主要耗时**，`capture` 的线程池按 CPU 自适应（4–12）。实测 48 张 Altair：
  6 线程 1.0s / 12 线程 0.3s；Plotly/Kaleido 有锁，不随线程数提升。

参考量级：41 家公司 → 377 张图 → 渲染 160s + 转图 200s + 排版渲染 14s ≈ 6 分钟，
成稿 86 页 / 3.4MB，无溢出。

### 5.5 已知坑（改这块最容易踩）

- Altair 自带的 `properties(width=...)` 若不覆盖，小面板（235×200）在 PDF 里极小 → 由 `_size_spec()` 兜住。
- **测量块高时 `.s7-block` 必须 `display: flow-root`**：否则内部 `.module-note` 的 margin-top、
  `.figure` 的 margin-bottom 会折叠到块外，`getBoundingClientRect().height` 少算 8–14px，
  分页会多塞一块，浏览器把溢出部分裁掉（表现为页面底部文字被切一半）。
- **英文版说明文字更长**，同一模块常比中文多占 100–200px，容易出现"说明一页、图一页"；
  落单图合并条件因此只按高度（`est`）判断，不要加字符数限制，否则中英文排版会分叉。
- **CSS 类名必须和代码生成的类名一致**。曾经代码输出 `half-solo`、样式表写的是 `.half-cell`，
  落单图拿不到 50% 宽和 330 限高，回退到 `max-height:500px`（526px 高），被顶到新页，
  后续所有页码整体后移一页。**改类名时先用 grep 对一遍 CSS。**
- `_render_unit(..., fill=True)` 会把图变成整页宽。只允许用于"模块仅一张图"或 `full_width` 标记的图；
  给所有独占行的图都加会把你不想变大的图（如第 5 张险种图）撑到 750px 高、独占一页。
- SVG 根节点缺 `viewBox` → CSS 缩放不等比，`_ensure_viewbox()` 补上。
- `st.columns` 只有 **2 列**才折成 `kind="columns"` 保持左右排版；3 列以上视为公司网格，
  转为卡片行（`layout_card` 分组），**不要在 2 列块内部再套一层 `.half-cell`**，否则图只有 1/4 宽。
- 新增图的样式 class 必须同步进 `html_builder._CSS`，否则 PDF 里只剩无格式文字。
- 切换报告语言会让 Streamlit **重跑整本报告（实测约 30–50 秒）**，这期间点击导出会被丢弃；
  `export.py` 用 `_s7_language_switched_at` 在面板上提示等待，不要当成导出 bug 排查。
- 导出耗时（10 家公司 / 18 模块）：转换 40–60s + 测量/排版/渲染 10–15s ≈ **75s**；
  若出现 280s+ 通常是 Playwright 收尾异常重试，不必改逻辑。
- 旧的浏览器端打印实现（`dashboard_components.py` 里 400 行 JS：`s7-print-sheet`、
  `waitForModules`、`normaliseCompanyStrips`）已在重构中删除，
  `render_print_control` 现在是 40 行的委托封装。**不要从 git 历史里把它捡回来。**

---

## 6. 修改规范

- 改动前先读文件；同一文件不重复读，一次看全。
- 不改无关代码，不顺手重构；新依赖必须写进 `requirements.txt` 并说明原因。
- 中文字符串一律走 `t("中文", "English")` 或 `services/report_i18n.py`，不在渲染代码里硬编码英文。
- 取数逻辑改动后，先用三家（人保/平安/太保）小数据验证，再跑对应 `tests/test_industry_*.py`。
- 临时脚本放 `tmp/`；PDF/PNG 产物不提交。
- `docs/` 只是本地设计参考，**不纳入版本库**；关键结论请写进本文件或 README。

---

## 6.5 启动性能（别把重库放回模块级）

冷启动（重启服务后首次渲染）约 14–15 秒，其中约 12.6 秒是 `import`。已做的关键处理：

| 依赖 | 启动成本 | 处理 |
| --- | --- | --- |
| `openai` | 约 2.7s | `services/batch_extraction.py` 改为函数内导入 |
| `pdfplumber` | 约 0.7s | 仍为模块级（改动涉及多处缩进，收益小，未动） |
| `fitz`(PyMuPDF) | 约 0.2s | `ui/cache.py` 改为函数内导入 |
| `ui/session.py` | 级联 5.6s | 不再导入 `solvency_rule_table_extractor`，只复制 `MODE_RULE`/`DEFAULT_METHOD` 常量 |

**约定**：新增依赖时先想清楚是否"启动即需要"。`openai`/`pdfplumber`/`playwright`/`vl_convert`/
`kaleido` 这类只在具体操作里用的库，一律在函数内 import；否则每次重启服务都要多等数秒。

排查手法：

```bash
./venv/Scripts/python.exe -X importtime -c "import ui.session" 2>&1 | sort -t'|' -k2 -rn | head
```

## 7. 快速验证清单

```bash
# 语法
./venv/Scripts/python.exe -m py_compile services/report_pdf/*.py step7_solvency/*.py

# 提取配置与公司规则（89 家）
./venv/Scripts/python.exe -m tests.test_extraction_config
./venv/Scripts/python.exe -m tests.test_company_rule_catalog
./venv/Scripts/python.exe -m tests.test_company_extractor_integration

# 扫描件自动转视觉（未配模型接口时应给出提示而不是 0 表）
./venv/Scripts/python.exe -m tests.test_scanned_auto_vision

# 新年度页码体检（拿到 2026 等新 PDF 先跑，按“建议 expected_pages”回填 YAML）
./venv/Scripts/python.exe tools/new_year_check.py --pdf-dir "Data/pdfs(2026)" --year 2026

# 逐年体检（--company 可指定多家；不带默认三家；写 Excel 到 tmp/three_companies_extraction/）
./venv/Scripts/python.exe -m tests.test_three_companies_extraction --years 2025

# STEP7 交互与雷达
./venv/Scripts/python.exe -m tests.test_track_company_order
./venv/Scripts/python.exe -m tests.test_capability_radar_overlap

# 依赖 test_workflow_smoke 的测试（Windows 用分号）
PYTHONPATH="tests;." ./venv/Scripts/python.exe -m tests.test_step7_filter_dropdown

# 图表取数
./venv/Scripts/python.exe -m tests.test_industry_market_share_logic
./venv/Scripts/python.exe -m tests.test_step7_excel_modules
./venv/Scripts/python.exe -m tests.test_premium_growth_structure

# 导出（用前三大数据，不要全量 89 家）
S7_EXPORT_DEBUG=1 ./venv/Scripts/python.exe tmp/apptest_export_check.py
# 产物 tmp/export_check.pdf，可用 PyMuPDF 渲染成 tmp/check_pageN.png 人工看
```

---

## 8. 目前进度

> 后续计划与优先级以 `plans/next-steps-2026-11.md` 为准；89 家覆盖记录见
> `plans/extraction-coverage-89-and-shared-fixes-2026-11.md`。

### 已完成

- STEP0–STEP8 全流程可跑通，财险 Profile（`NON_LIFE_SOLVENCY`）为默认。
- STEP1/STEP2 双模式：规则法（离线、零 token）+ 大模型法，自动回退。
- STEP2 整页全表提取，一页多表合并为同一中间表。
- STEP3 标准化 + STEP7 指标缺口检查 + 补录表生成/回填闭环（`tools/build_step7_fill_sheet.py`）。
- STEP4 勾稽检查，失败项可下钻。
- STEP7 图表 14 个模块、中英文切换、图片覆盖、AI 点评、按图表选择导出范围。
- **PDF 导出改为服务端生成**：矢量图、1600×900 横版、自动分页、封面封底，摆脱浏览器打印。
  导出按钮在选中单个一级/二级模块时也可用（`ui/navigation.py`）。
- **R4 缺失原因透出（2026-11-20）**：新增 `services/extraction_trace.py`；
  STEP2 预览区显示“目标表提取状态与未命中原因”，STEP3 诊断新增 `来源缺失` 行；
  原因码覆盖 `RULE_NOT_FOUND` / `YEAR_UNKNOWN` / `ANCHOR_NOT_FOUND` /
  `SIGNATURE_MISSING` / `REQUIRED_ITEM_MISSING`。测试 `tests/test_extraction_trace.py`。
- 勾稽容差改由 Excel 维护：`config/solvency_validation_rules.xlsx` 12 条规则容差统一为 0.1。
- 劳合社/利宝/法巴天星/黄河为非传统财险主体，版式遗留已确认忽略。
- 同比口径统一为"部分年度不参与增速"。
- 提取表定义外置到 `config/extraction/tables.yaml`，内置常量保留兜底；Profile 的
  `NON_LIFE_OPERATING_METRICS` 与内置 `OPERATING_METRICS` 通过 aliases 共用定义。
- **89 家公司各一份跨年度 YAML**，覆盖 6 张目标表（页码窗口 ±2、锚点、按年 structure_versions）；
  `company_extractors/` 54 家实修 + 35 家占位，2024 全量 84 家有报告：
  82 成功，2 例纯扫描件（中煤 2024、恒邦 2024）按“不可提取”登记。
- 主表重建 helper：`_main_table.rebuild_main_rows` + 长江/融通/东吴/中煤 2022/比亚迪 extractor，
  修复金额跨行、行次竖排、表头缺列导致的关键值错列。
- 扫描件整本自动转视觉（`_auto_vision_for_scanned`），未配置模型时给出明确提示。
- 缺失指标双通道：批量 Excel 增加 `缺失指标清单`/`公司覆盖汇总`，STEP3 下载区显示缺口摘要。
- `tools/new_year_check.py`：新年度页码体检，输出“建议 expected_pages”，异常退出码 2。
- `plans/data-coverage-audit.md`：列表 89 家 vs Data 文件逐年盘点、9 家年份不全、33 组名称差异。
- 期间口径统一：本季度数/上季度数 → 本季度末数/上季度末数。
- STEP2 单文件与批量提取共用同一套 extractor 入口，日志显示“公司专属规则”。
- `keep_unmapped`（默认关闭）：体检脚本可保留未匹配标准指标的原始行（`RAW_*`）。
- STEP7：Track 公司候选改为类型池、自动并入并置顶、横向滚动 sticky；
  能力五边形支持“追踪 vs 对比公司”双层填充雷达。

### 未完成 / 待跟进

| 项 | 说明 |
| --- | --- |
| 模块级备注位置 | `ModuleSnapshot.note` 仍固定追加在模块最后一页底部，未做智能避让 |
| 雷达图/小面板网格 | SVG 转换后轴标签、网格偶有丢失，需按图类型单独处理 |
| 卡片网格尾行 | 卡片数不能被列数整除时，尾行会单独占一页下半部（如 10 家公司的 3+3+3+1），暂接受 |
| 图内微标签 | 每页多图的模块里，柱内百分比仍有 3.6–4.5pt，属"一页装多图"的固有代价 |
| 远程图片 | 导出 HTML 里 http 图片依赖网络，离线环境不显示 |
| STEP8 导出 | 仍走旧的打印控件路径，未接入 `services/report_pdf` 新管线 |
| 寿险 Profile | 仅有财险工作簿，`LIFE_SOLVENCY` 相关代码路径未验证 |
| 金标准对比 | `gold_standard/manifest.json` 不在仓库内，该功能默认跳过（本地可自建） |
| 自动化测试 | 仍是脚本式，未接入 pytest/CI |
| 少数公司版式遗留 | 劳合社、利宝、法巴天星、黄河属非传统财险/特殊披露，**已确认忽略**，不补 extractor |
| 扫描件联调 | 中煤 2024/2025、恒邦 2024、太平科技 2025 需配置模型接口后实测视觉链路 |
| 勾稽剩余项 | 容差已改为 Excel 维护（0.1）；`SCHEMA_ALLOWED_PERIOD`（字典允许期间）、预测期间构成类缺子项、`DUPLICATE_*` 跨表差异待处理 |

### 近期主要变更（供追溯）

- `app.py`：初始化 `pdf_capture.install()`。
- `dashboard_components.py`：删除浏览器打印 JS（-450 行），`render_print_control` 委托服务端导出。
- `step7_solvency/report.py`：`start_document` / `begin_module` / `end_module` 埋点，发布 `_s7_pdf_meta`；
  记录语言切换时间 `_s7_language_switched_at`，供导出面板提示"重渲染中"。
- `step7_solvency/plan_renderers.py`：诊断区用 `capture.suspend()` 排除；公司网格面板包 `layout_card()`；
  `st.dataframe` 仍不进 PDF。
- `services/solvency_step7_charts/{industry_premium,premium_by_line,performance}.py`：统一同比口径、排名轴显式刻度。
- `services/solvency_step7_charts/trend.py`：多公司折线图图例改**顶部水平**（原右侧竖排会压住数据线）、
  最低点数值标签上移避开年份标签、`usermeta` 标记整页宽。
- `services/report_pdf/{capture,html_builder,models,renderer,export}.py`：
  - `ContentItem` 增加 `panels / aspect / layout_group / layout_index / source / full_width`；
  - 落单图半页宽 + 连续落单图合并、`_card_columns()` 自适应列数、卡片禁跨页拆分；
  - `measure_and_render()` 单会话完成测量+渲染；导出面板按语言重渲染提示；
  - 修 `localize_plotly()` 把空轴标题 `None` 渲染成字符串 "None" 的 bug。
- 规模参考：10 家公司、18 个模块约 **39–40 页**（旧版 41–50 页）。
- `requirements.txt`：新增 `playwright`、`vl-convert-python`。
- **提取规则体系（2026-11）**：新增 `services/extraction_config.py`、`extraction_rules.py`、
  `company_extractors/pingan.py`；`locate_tables`
  支持 `expected_pages/tolerance/page_match`，`PageMatch` 增加 `rule_note`。
- **提取覆盖扩到 89 家（2026-11）**：新增 86 份 YAML（3 份原有）、40+ extractor、86 份 `plans/company-*.md`；
  共享层修复：整页多表帧隔离、区域失败回退整页、百分比误删、表头空格丢列、
  “百元保费”挂靠闸门、`(单位:万元)` 注解、续页白名单、公司签名覆盖、边界重扫、
  MOTOR/EPS 换算豁免、坏 YAML 容错、主表重建、PyMuPDF 2.x 崩溃规避。
  提交：`b15cacd`、`98f9501`、`027361c`、`d49490b`、`33d0468`、`b32fd66`。
  全量记录见 `plans/extraction-coverage-89-and-shared-fixes-2026-11.md`。
- **期间口径**：`solvency_normalizer` 统一本季度末/上季度末；
  `test_new_workflow_behavior` 的期间断言同步更新。
- **STEP7**：`report.py` Track 候选改为类型池并置顶；`rendering.py` 统一排序；
  `theme.py` 追踪卡片 sticky；`radar.py` 支持面积填充；`plan_renderers.py` 新增叠加对比选择器。
