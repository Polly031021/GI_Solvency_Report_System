# 偿付能力报告处理与分析平台

把保险公司披露的**偿付能力季度/年度报告 PDF**，半自动化地转换成可跨公司、跨期比较的
标准数据表，并生成公司级对标报告与行业分析报告。

平台是一个 Streamlit 应用：登录后按 STEP0 → STEP8 的顺序推进，每一步的产物（页码、中间表、
标准窄表、集成数据）都保存在会话中，可随时回到上一步修正。

---

## 一、快速开始

```bash
pip install -r requirements.txt
streamlit run app.py
```

- 运行环境：Python 3.13（仓库内的 `__pycache__` 由 3.13 生成）、Streamlit 1.62。
- 登录安全码：项目组成员默认 `KPMG666`，普通用户默认 `KPMG1234`；
  可用环境变量 `SOLVENCY_PROJECT_ACCESS_CODE` / `SOLVENCY_USER_ACCESS_CODE` 覆盖。
- 普通用户通道只开放 **STEP6 自定义分析** 与 **STEP7 公司报告**；
  STEP0–STEP5 与 STEP8 为项目组成员功能。
- AI 功能（STEP1 大模型定位复核、STEP2 图片识别、STEP7 一键 AI 点评）共用登录页配置的
  Base URL / 模型 / API Key；**API Key 只存在于当前浏览器会话，不落盘、不出现在导出文件里**。
  不填 API Key 时，STEP1 规则法定位、STEP2 规则法提取（文字版报告零 token）、
  数据合并与本地图表分析仍然可用。

---

## 二、主要功能：STEP0 到 STEP8

| 步骤 | 功能 | 关键实现 |
| --- | --- | --- |
| STEP0 报告监控 | 读取本地财险公司清单，单家或并发检查披露页面是否已更新目标报告期 | `ui/step0_monitor.py`、`services/solvency_pdf_locator.py` |
| STEP1 页码定位 | 上传 PDF → 识别文字版/扫描版 → 规则法（离线）或大模型法定位目标表物理页码 → 给出置信度、人工核对与页面预览 | `ui/step1_locator.py`、`services/solvency_hybrid_pipeline.py`、`services/solvency_pdf_locator.py` |
| STEP2 表格提取 | 按确认页码逐页并发提取，默认“智能模式”：文字版页面优先规则法（离线零 token），规则未通过或扫描版页面自动转大模型；默认开启“整页全表提取”，一页多表时合并为同一张中间表展示（内部保留独立表块），不做目标表首尾边界裁切，避免指标被截断；提取结果可就地编辑、单表重跑，跨页按行拼接、表头归一、单位换算为万元 | `ui/step2_extraction.py`、`services/solvency_rule_table_extractor.py`、`services/solvency_table_region.py`、`services/solvency_ai_table_extractor.py`、`services/batch_extraction.py` |
| STEP3 标准化 | 单份：按目标指标清单生成标准窄表（自动补注册资本、派生指标）；标准化后按 STEP7 指标需求清单检查缺口，可导出缺失指标补录表并回填合并；批量：定位→核对页码→提取三阶段处理多家公司，同样执行 STEP7 缺口检查 | `ui/step3_standardization.py`、`ui/step7_metric_check.py`、`services/solvency_step3_standardizer.py`、`services/step3_metric_gap.py`、`services/batch_processor.py` |
| STEP4 勾稽检查 | 只读检查：数据规范、关键指标完整性、公式勾稽、跨表一致性；失败项显示原因、公式代入值与缺失/异常指标，并可下钻到 STEP3 对应记录；输出待处理事项与依据工作簿 | `ui/step4_validation.py`、`services/solvency_validator.py` |
| STEP5 数据集成 | 两种互斥接入方式：上传标准窄表，或上传外部宽表（CROSS，仅寿险）精确映射；确认后成为 STEP6–STEP8 的数据源 | `ui/step5_integration.py`、`services/solvency_dataset_adapter.py` |
| STEP6 自定义分析 | 任选指标/公司/报告期，生成簇状柱、折线、散点、横向条形等对比图，图例与配色可自定义 | `ui/step6_analysis.py`、`services/solvency_step6_analysis.py` |
| STEP7 公司报告 | 按 Excel 注释表（或内置导航）逐图表生成公司级对标报告：趋势图、资本结构组合图、气泡图、雷达图、承保效益五年年度散点图、行业五年保费结构、公司类别市场份额等，支持图片覆盖、AI 点评与打印导出 PDF；页面顶部可一键切换中英文展示。STEP7 所需指标统一维护在 `config/Step7_指标需求清单.xlsx` | `step7_solvency/`、`services/solvency_step7_charts/`、`services/solvency_step7_metric_catalog.py`、`services/report_i18n.py` |
| STEP8 行业分析 | 行业整体概览、同业分类分布趋势与排名、重大融资统计，支持整本报告打印 | `step8_solvency.py`、`dashboard_components.py` |

STEP7 默认自动读取 `templates/Step7-偿付能力公司报告展示模块.xlsx`，无需每次在侧边栏上传；上传文件仅覆盖当前会话，可点击“恢复默认配置”切回内置模板。侧边栏只保留一级/二级分类，二级分类即图表入口。

### STEP7 缺失指标补录脚本

汇总数据（至少包含 `标准数据` 页）可直接运行：

```bash
python tools/build_step7_fill_sheet.py "偿付能力行业集成数据_公司名称与分类规范化.xlsx"
```

- 默认每家公司取最后一个报告期，检查全部 STEP7 基础指标；
- 按 STEP7 指标目录的建议期间核对，例如 `ROE` 需要 `本年累计数`，只有 `本季度数` 会被列为期间缺口；
- 输出工作簿的 `标准数据` 页与源表列名、列顺序完全一致，只包含缺失指标行，`数值` 列留空；
- 填完数值后，选中数据行复制，粘贴到已有汇总表最后一行之后即可；
- 只补核心指标：加 `--scope core`；检查全部报告期：加 `--all-periods`；
- 可用 `--sheet` 指定源工作表，默认 `标准数据`；
- 若根目录 `Data/pdfs(YYYY)` 存在，输出会附 `年度公司数` 页，对照 PDF 文件数与汇总数据公司数；
- 输出另含 `期间口径核对` 页，逐项列出建议期间、实际期间分布、期间缺口公司和完全缺失公司；
- 输出文件默认在源文件旁：`*_STEP7缺失指标_可粘贴补录表_全部.xlsx`。

### 大文件读取性能

- 标准窄表按文件 SHA256 缓存到系统临时目录，同一文件首次解析后再次读取不再重新打开 Excel。
- STEP6 分析读取只加载公开的 `标准数据` 工作表，跳过隐藏的系统字段表。
- 公司名称识别按唯一公司/类型分组执行，不再对每一行重复解析。
- 安装 `python-calamine`（已列入 requirements）后，Excel 首次读取会优先使用 calamine 引擎；未安装时自动回退到 openpyxl。

### 三个贯穿全流程的设计

1. **报告 Profile（报告类型）隔离**：财险 `NON_LIFE_SOLVENCY` 与寿险 `LIFE_SOLVENCY` 使用
   各自的公司范围、目标表、定位关键词与指标字典，切换 Profile 会清空当前未保存的结果，
   避免两类数据混用。仓库当前只提供财险 Profile 工作簿。
2. **金标准对比**：若 `gold_standard/manifest.json` 存在，STEP1/STEP2 会自动匹配真实 PDF 样本
   并显示定位/提取的基准对比；文件不存在时该功能自动跳过。
3. **本地 Profile 与公司分类**：默认读取 `config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx`，
   文件修改后自动失效并可手动刷新；财险公司的同业分类来自根目录
   `财险公司列表.xlsx` 的“公司类别”，短名称与全称通过披露地址和别名关联。

---

## 三、代码结构

```
app.py                    应用入口（登录门禁、样式、各页面编排）≈140 行
ui/                       界面层：按工作流步骤拆分的页面模块
step7_solvency/           STEP7 公司报告包
step8_solvency.py         STEP8 行业分析报告
dashboard_components.py   报告通用组件（封面、注释编辑器、概览表、打印控件、色卡）
services/                 业务逻辑层（PDF 定位/提取/标准化/校验/集成/图表库）
config/                   Profile 工作簿、指标字典、勾稽规则、同业分类、公司网址
templates/                目标表与注释表模板
tests/                    回归测试
```

### ui/ 包（界面层）

| 模块 | 职责 |
| --- | --- |
| `ui/config.py` | 路径、Profile 默认值、工作流标签、登录用 AI 提供商预设 |
| `ui/auth.py` | 登录页、安全码校验、登出 |
| `ui/theme.py` | 全局 CSS（含打印样式与 STEP6–STEP8 侧边栏样式） |
| `ui/session.py` | 会话状态默认值、初始化、切换 Profile 时的清理、AI 配置校验 |
| `ui/cache.py` | 所有 `st.cache_data` 缓存读取（工作簿、字典、规则、PDF 渲染、导出） |
| `ui/helpers.py` | 通用小工具（列名清洗、页码解析、控件状态维护、期间关键词） |
| `ui/navigation.py` | 侧边栏导航（公司/行业报告导航、Excel 驱动导航、补充数据上传） |
| `ui/step0_monitor.py` … `ui/step6_analysis.py` | 各步骤页面 |
| `ui/profile_settings.py` | Profile 切换/上传/核对与指标字典路径解析 |
| `ui/report_pages.py` | STEP7、STEP8 报告入口封装 |

### step7_solvency/ 包（公司报告）

| 模块 | 职责 |
| --- | --- |
| `specs.py` | 图表规格常量：配色、面板宽度、构成堆叠口径、能力五边形打分基准 |
| `theme.py` | STEP7 专用 CSS（屏幕 + 打印） |
| `selection.py` | 公司/指标筛选、排序、配色映射、图表清单解析 |
| `rendering.py` | 单指标图表通用渲染（单位换算、图例、公司面板网格） |
| `plans.py` | 组合图调度中心：`render_combination_analysis` 按图表类型分派 |
| `plan_renderers.py` | 20 种组合图的具体渲染实现（`render_plan_*`） |
| `plan_inputs.py` | 组合图输入准备：指标编码解析、阶梯打分、能力五边形打分 |
| `fragments.py` | 气泡图/散点图的局部放大联动（Streamlit fragment） |
| `ai_analysis.py` | 一键 AI 点评（调用登录页配置的模型接口） |
| `underwriting.py` | 承保效益五年年度散点图与盈利/亏损公司数趋势 |
| `report.py` | 主流程编排；`__init__.py` 只导出 `show_step_7_solvency` |

### services/ 包（业务逻辑）

| 模块 | 职责 |
| --- | --- |
| `report_profiles.py` | Profile 工作簿的校验、加载与导出（含 v2 版式变体/字段字典/完整性规则） |
| `solvency_hybrid_pipeline/` | STEP1 定位管线包：`config` 配置、`llm` 调用、`locator_vision` 视觉定位、`locator_rule` 规则定位、`locator` 定位入口、`page_extract` 逐页提取与跨页合并、`extract` 提取入口 |
| `solvency_pdf_locator.py` | 文本层分析、目录换算、文档类型识别、报告元数据提取 |
| `solvency_table_boundaries.py` | 目标表起止边界与版式变体（含 S05 单行跨页等真实版式） |
| `solvency_ai_table_extractor/` | STEP2 逐页提取包：`models` 模型与文本工具、`units` 单位记录、`grid` 文本网格、`source_profile` 源PDF完整性、`llm` 模型调用、`extract` 主流程 |
| `solvency_rule_table_extractor.py` | STEP2 提取方式路由：文字版页面用 pdfplumber 线框/文字对齐规则法离线提取并做完整性门禁，扫描版页面或未通过校验时才调用大模型 |
| `solvency_table_region.py` | 页内表格区域定位：用目标表首尾项目在一页多表时圈定目标表的纵向范围，规则法按区域识别、图片模式按区域裁切，界面用红框标出 |
| `table_number_repair.py` | 数值格式修复：以同页原文为准校正金额分隔符（千分位被写成小数点、丢逗号、全角逗号等），并输出待人工核对清单 |
| `table_strategy_registry.py` / `table_strategy_handlers.py` | 各目标表的专用提取策略 |
| `solvency_normalizer.py` | 指标匹配、期间口径归一、单位换算、标准窄表 schema |
| `solvency_step3_standardizer.py` | 目标指标清单填报与派生指标计算 |
| `solvency_step7_metric_catalog.py` | STEP7 指标需求目录、图表-指标映射与维护用 Excel 生成 |
| `step3_metric_gap.py` | STEP3 标准化后的 STEP7 指标缺口检查、缺失补录表导出与回填合并 |
| `solvency_validator.py` | STEP4 勾稽规则引擎 |
| `solvency_dataset_adapter.py` | 集成层：外部宽表映射、派生指标补全、行业合计生成、工作簿读写 |
| `solvency_company_identity.py` | 公司名称归一化、历史名称别名、同业分类解析 |
| `solvency_navigation.py` | 图表导航定义与 KPMG 官方色卡 |
| `report_style.py` | 行业概览图表共享色板、字体、坐标轴与网格风格常量 |
| `solvency_step6_analysis.py` | STEP6 数据准备与 Altair 对比图构建 |
| `solvency_step7_charts/` | STEP7/STEP8 图表库包：`primitives` 原语、`trend` 趋势、`capital` 资本、`bubble` 气泡、`matrix` 矩阵散点、`radar` 雷达、`structure` 结构变化、`performance` 业绩模式 |
| `solvency_metric_registry.py` | 寿险/CROSS/行业派生指标的补充定义 |
| `registered_capital_extractor.py` | 从披露文本中提取注册资本 |
| `solvency_financing_analysis.py` | 重大融资信息（增资/发债）统计 |
| `solvency_report_notes.py` | 报告注释模板与注释工作簿读写 |
| `solvency_gold_standard.py` | 金标准用例匹配与评分 |
| `batch_processor.py` / `batch_extraction.py` | STEP3 批处理与逐页提取引擎 |
| `step7_excel_config.py` | STEP7 Excel 注释表（模块/图表类型/指标编码）解析 |
| `llm_config.py` / `llm_http.py` | 模型 ID 归一、采样参数、带重试的 HTTP 调用 |

---

## 四、数据模型与关键约定

- **标准窄表**（`STANDARD_COLUMNS`，30 列）：内部唯一真源，保留公司身份、期间口径、
  provenance（来源文件/工作表/页码）与计算逻辑；对外的 STEP3/STEP5 交换格式是它的
  精简版 `NARROW_TABLE_COLUMNS`（14 列）。
- **单位为万元**：金额类指标统一换算为万元；比率类统一为百分数（`%`），倍数为 `倍`。
- **无单位金额默认按元**：源表、表头、行标签都没有声明金额单位时，按“元”作为原始单位再换算为万元（不再默认万元）；备注列会写“未识别原始单位，按默认单位元换算”，并在 STEP3 警告中汇总条数，便于核对。
- **期间口径**：所有期间叫法（本期/上期/年末等）归一为 `本季度末数`、`本年累计数` 等标准口径。
- **公司名称归一**：去掉「（偿付能力）」「（合并）」等披露口径后缀，避免同一家公司被算作两家。
- **人工确认优先**：定位置信度低于阈值或证据冲突时会标记需人工核对，提取按人工确认的页码执行。
- **行业概览口径**：`行业概览 > 规模与发展` 使用上传数据中的全部公司，不受 STEP7 展示公司/公司类型筛选影响；年度值取每年 Q4 的 `本年累计数`，车险取 `SIGNED_PREMIUM_MOTOR`，非车险为总签单保费减车险；拆分缺失时以灰色“未拆分”段保留已有合计。图表内置 2017–2024 行业历史签单保费与车险占比，上传数据覆盖同年度固定值并保留其新增年度；显示范围默认全部，也可切换近 5 年或近 10 年，并保留车险、非车险与合计三条趋势线。聚合前会按记录自身单位换算为万元，并按公司历史年度与 Q4 单季值校正元/亿元误标为万元的量级异常；公司名称中的「（偿付能力）」等披露后缀会先归并，避免合计保费与车险拆分被拆成两行。
- **保费增速结构口径**：`经营与偿付能力联动分析 > 保费增速` 每家展示公司单独一张五年小图，并按 `同业分类`（界面中的公司类型）分组展示；分类缺失时回退到 `公司类型`。100% 堆叠柱按 `SIGNED_PREMIUM_MOTOR / SIGNED_PREMIUM` 计算车险占比，非车险为总签单保费减车险；折线为总保费 `(本年 - 上年) / 上年` 的同比增速，使用右轴。展示区间首年若缺少上一年度基数则不画折线点，车险拆分缺失或异常时以灰色“未拆分”段保留总保费。
- **经营表现口径**：`公司业绩展示` 按利润排名轨迹模式分组，每组默认每行 4 家公司（可在图表配置中用 `grid_cols` 调整为 5）；每张小图的横轴为签单保费排名、纵轴为净利润排名，数值越小排名越靠前，第 1 名位于右上角。
- **资本充足率组合图口径**：`综合充足率变化` 与 `核心充足率变化` 按年度展示，每年优先取 Q4（无 Q4 时取该年最后可得期），横轴统一显示年份，不再显示 `Q1–Q4` 标签。
- **行业市场份额口径**：`行业概览 > 市场份额` 使用最新一个覆盖达标的完整年度，按根目录 `财险公司列表.xlsx` 的 `公司类别` 聚合；饼图展示保费、净利润、承保利润的正值占比，柱状图展示三类指标的具体金额，承保利润缺失时按 `签单保费 ×（1 - 综合成本率）` 测算。
- **承保效益口径**：`行业概览 > 承保效益` 取最近 5 个有数据的年度（Q4/年末），每年按当年签单保费降序排名；每家公司一个点，横轴为签单保费排名、纵轴为综合成本率，纵轴以 100% 为中心对称展开并用虚线标示盈亏平衡。综合成本率低于 100% 计为盈利、高于 100% 计为亏损；图下展示当年盈利/亏损公司数，第三行第二张图汇总五年盈亏公司数趋势。年度累计签单保费缺失时回退到本季度数参与排名。

---

## 五、配置与数据文件

| 路径 | 说明 |
| --- | --- |
| `config/report_profiles/*_profile.xlsx` | 报告 Profile 工作簿（目标表、定位关键词、字段字典、完整性规则、公司来源） |
| `config/solvency_taxonomy.xlsx` | 财险指标字典（正式口径，财险强制使用） |
| `config/Step7_指标需求清单.xlsx` | STEP7 指标需求清单（指标、图表映射、派生依赖、单位与期间口径），由 `python tools/generate_step7_metric_catalog.py` 生成 |
| `config/solvency_validation_rules.xlsx` | STEP4 勾稽规则 |
| `config/solvency_peer_groups.json` | 公司 → 同业分类映射 |
| `config/non_life_solvency_company_urls.xlsx` | Profile 内嵌的公司网址来源 |
| `财险公司列表.xlsx` | STEP0 报告监控 + STEP5 公司类型映射的公司清单（列：`公司`/`业务类型`/`公司类别`/`是否上市`/`报告披露地址`） |
| `templates/*.xlsx` | STEP3 目标表与 STEP7/STEP8 注释表模板 |
| `picture/`（可选） | 报告封面/封底图片；缺失时自动使用 CSS 渐变兜底 |
| `gold_standard/manifest.json`（可选） | 金标准用例清单 |

### `财险公司列表.xlsx` 的两个分类维度

清单里有两类“分类”，语义完全不同，维护时不要混用：

- **`公司类别`（互斥）**：一家公司只能有一个值，当前为 `前十大`（人保财险、平安产险、
  太保财险、国寿财产、众安财产、英大财产、大地财产、中华联合、太平财险、阳光财产）、
  `大型公司`、`中型公司`、`小型公司`、`外资系`、`自保公司`、`农业保险`。
  它是 STEP5 写入标准窄表 `公司类型` 的唯一来源，也是 STEP6/STEP8 分组统计的口径，
  因此**严禁为同一家公司写两行**：`ui/helpers.finalize_company_frame` 会按公司名去重
  （保留最后一行），多写的那个类别会被静默丢弃。
- **`是否上市`（叠加标签）**：取值 `是`/`否`，与 `公司类别` 允许重合。当前 6 家上市公司
  （人保财险、平安产险、太保财险、太平财险、阳光财产、众安财产）同时属于 `前十大`。
  STEP0 会把它渲染成虚拟筛选项「上市公司」，与其他类别取**并集并按公司去重**，
  所以同时勾选「前十大 + 上市公司」仍然是 10 家，不会重复抓取同一家公司的披露页。
  该标签**不进入标准窄表**，因此不会影响 STEP4 勾稽、STEP6 自定义分析、STEP7 公司报告
  与 STEP8 行业分析的分组与合计。

---

## 六、测试与质量检查

```bash
cd tests
python test_step1_locator_modes.py    # STEP1 定位：文档类型、离线规则法、大模型复核、S05 单行跨页
python test_step2_extraction_modes.py # STEP2 提取：千分位修复、规则法离线提取、提取方式路由、中间表编辑
python test_step4_validation.py       # STEP4 勾稽：失败原因、STEP3 依据、跨表比对与页面下钻
python test_app_smoke.py              # 登录后首页渲染
python test_workflow_smoke.py         # 注入合成数据，逐页渲染 STEP0–STEP8（含全部内置图表）
python test_step7_metric_gap.py       # STEP7 指标目录、STEP3 缺失检查/补录合并、无单位默认按元
python test_underwriting_yearly.py    # 承保效益：五年年度散点图、100% 中轴与盈亏汇总
python test_step7_excel_modules.py    # Excel 配置驱动的 37 种 STEP7 图表类型
python test_industry_premium_logic.py # 行业保费结构：固定历史、上传覆盖、Q4 全年累计与 5/10/全部窗口
python test_industry_market_share_logic.py # 行业市场份额：最新完整年度、公司类别与图表结构
python -m pyflakes ..                 # 静态检查：未定义名字/未使用导入（当前零告警）
```

测试全部离线运行，不调用任何大模型接口。

---

## 七、已知限制与后续建议

1. `dashboard_components.render_print_control` 仍使用 `st.components.v1.html`，
   Streamlit 已提示该 API 将于 2026-06-01 后移除；升级 Streamlit 时需一并改造。
2. 仍有若干千行以上的业务模块可按同样方式继续拆分，按优先级排序：
   `services/solvency_step3_standardizer.py`（1268 行）、
   `services/table_strategy_handlers.py`（1192 行）、
   `services/report_profiles.py`（1186 行）、
   `services/solvency_dataset_adapter.py`（1156 行）。
3. 代码里保留了较多"★ 修复/★ 新增"式的历史注释，确认稳定后可以精简为结论性说明。
4. `config/non_life_solvency_page_features.json`、`config/solvency_page_features.json`
   与内置 JSON Profile 属于历史兼容路径，当前财险走工作簿配置，可按部署情况清理。
