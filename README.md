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
  不填 API Key 时，规则法定位、数据合并与本地图表分析仍然可用。

---

## 二、主要功能：STEP0 到 STEP8

| 步骤 | 功能 | 关键实现 |
| --- | --- | --- |
| STEP0 报告监控 | 读取本地财险公司清单，单家或并发检查披露页面是否已更新目标报告期 | `ui/step0_monitor.py`、`services/solvency_pdf_locator.py` |
| STEP1 页码定位 | 上传 PDF → 识别文字版/扫描版 → 规则法（离线）或大模型法定位目标表物理页码 → 给出置信度、人工核对与页面预览 | `ui/step1_locator.py`、`services/solvency_hybrid_pipeline.py`、`services/solvency_pdf_locator.py` |
| STEP2 表格提取 | 按确认页码逐页并发提取，失败页自动图片扫描/网格纠错，跨页按行拼接、表头归一、单位换算为万元 | `ui/step2_extraction.py`、`services/solvency_ai_table_extractor.py`、`services/batch_extraction.py` |
| STEP3 标准化 | 单份：按目标指标清单生成标准窄表（自动补注册资本、派生指标）；批量：定位→核对页码→提取三阶段处理多家公司 | `ui/step3_standardization.py`、`services/solvency_step3_standardizer.py`、`services/batch_processor.py` |
| STEP4 勾稽检查 | 只读检查：数据规范、关键指标完整性、公式勾稽、跨表一致性；输出待处理事项工作簿 | `ui/step4_validation.py`、`services/solvency_validator.py` |
| STEP5 数据集成 | 两种互斥接入方式：上传标准窄表，或上传外部宽表（CROSS，仅寿险）精确映射；确认后成为 STEP6–STEP8 的数据源 | `ui/step5_integration.py`、`services/solvency_dataset_adapter.py` |
| STEP6 自定义分析 | 任选指标/公司/报告期，生成簇状柱、折线、散点、横向条形等对比图，图例与配色可自定义 | `ui/step6_analysis.py`、`services/solvency_step6_analysis.py` |
| STEP7 公司报告 | 按 Excel 注释表（或内置导航）逐图表生成公司级对标报告：趋势图、资本结构组合图、气泡图、雷达图、承保效益散点图等，支持图片覆盖、AI 点评与打印导出 PDF | `step7_solvency/`、`services/solvency_step7_charts.py` |
| STEP8 行业分析 | 行业整体概览、同业分类分布趋势与排名、重大融资统计，支持整本报告打印 | `step8_solvency.py`、`dashboard_components.py` |

### 两个贯穿全流程的设计

1. **报告 Profile（报告类型）隔离**：财险 `NON_LIFE_SOLVENCY` 与寿险 `LIFE_SOLVENCY` 使用
   各自的公司范围、目标表、定位关键词与指标字典，切换 Profile 会清空当前未保存的结果，
   避免两类数据混用。仓库当前只提供财险 Profile 工作簿。
2. **金标准对比**：若 `gold_standard/manifest.json` 存在，STEP1/STEP2 会自动匹配真实 PDF 样本
   并显示定位/提取的基准对比；文件不存在时该功能自动跳过。

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
| `plan_renderers.py` | 18 种组合图的具体渲染实现（`render_plan_*`） |
| `plan_inputs.py` | 组合图输入准备：指标编码解析、阶梯打分、能力五边形打分 |
| `fragments.py` | 气泡图/散点图的局部放大联动（Streamlit fragment） |
| `ai_analysis.py` | 一键 AI 点评（调用登录页配置的模型接口） |
| `underwriting.py` | 承保效益散点图 |
| `report.py` | 主流程编排；`__init__.py` 只导出 `show_step_7_solvency` |

### services/ 包（业务逻辑）

| 模块 | 职责 |
| --- | --- |
| `report_profiles.py` | Profile 工作簿的校验、加载与导出（含 v2 版式变体/字段字典/完整性规则） |
| `solvency_hybrid_pipeline/` | STEP1 定位管线包：`config` 配置、`llm` 调用、`locator_vision` 视觉定位、`locator_rule` 规则定位、`locator` 定位入口、`page_extract` 逐页提取与跨页合并、`extract` 提取入口 |
| `solvency_pdf_locator.py` | 文本层分析、目录换算、文档类型识别、报告元数据提取 |
| `solvency_table_boundaries.py` | 目标表起止边界与版式变体（含 S05 单行跨页等真实版式） |
| `solvency_ai_table_extractor/` | STEP2 逐页提取包：`models` 模型与文本工具、`units` 单位记录、`grid` 文本网格、`source_profile` 源PDF完整性、`llm` 模型调用、`extract` 主流程 |
| `table_strategy_registry.py` / `table_strategy_handlers.py` | 各目标表的专用提取策略 |
| `solvency_normalizer.py` | 指标匹配、期间口径归一、单位换算、标准窄表 schema |
| `solvency_step3_standardizer.py` | 目标指标清单填报与派生指标计算 |
| `solvency_validator.py` | STEP4 勾稽规则引擎 |
| `solvency_dataset_adapter.py` | 集成层：外部宽表映射、派生指标补全、行业合计生成、工作簿读写 |
| `solvency_company_identity.py` | 公司名称归一化、历史名称别名、同业分类解析 |
| `solvency_navigation.py` | 图表导航定义与 KPMG 官方色卡 |
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
- **期间口径**：所有期间叫法（本期/上期/年末等）归一为 `本季度末数`、`本年累计数` 等标准口径。
- **公司名称归一**：去掉「（偿付能力）」「（合并）」等披露口径后缀，避免同一家公司被算作两家。
- **人工确认优先**：定位置信度低于阈值或证据冲突时会标记需人工核对，提取按人工确认的页码执行。

---

## 五、配置与数据文件

| 路径 | 说明 |
| --- | --- |
| `config/report_profiles/*_profile.xlsx` | 报告 Profile 工作簿（目标表、定位关键词、字段字典、完整性规则、公司来源） |
| `config/solvency_taxonomy.xlsx` | 财险指标字典（正式口径，财险强制使用） |
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
python test_app_smoke.py              # 登录后首页渲染
python test_workflow_smoke.py         # 注入合成数据，逐页渲染 STEP0–STEP8（含全部内置图表）
python test_step7_excel_modules.py    # Excel 配置驱动的 29 种 STEP7 图表类型
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
