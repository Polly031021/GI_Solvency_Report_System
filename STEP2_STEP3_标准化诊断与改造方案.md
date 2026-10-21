# STEP2 → STEP3 标准化诊断与改造方案

> 结论先行：STEP3 不是“看表格长得是否板正”，而是按一组很强的隐含假设在读表：
> **第 1 行（或前 2 行）必须是表头；每个数值列必须能从表头解析出期间；标签必须命中指标字典；值必须紧跟在标签右侧；每个跨页块必须有独立表头。**
> 这五条里任意一条不满足，STEP3 往往不会报错，而是**静默跳过整列/整行**。所以 STEP2 预览很整齐，STEP3 仍然可能“什么都提不出来”。

---

## 0. 先给结论：四类断点

| 断点 | 典型现象 | STEP3 的实际行为 |
|---|---|---|
| **表头断** | 跨页第二页没有表头；表头前有标题行；多行表头只取到第一行 | 把标题/数据行当表头，data_start=1 吃掉第一行数据；或数值列找不到期间，整列 continue |
| **期间断** | 表头是“金额”“本期金额”“2024年12月31日”或空单元格 | _period_header() 返回空 → if not period: continue，整列静默丢弃 |
| **标签断** | 指标名在单元格内换行被拆成两行；标签带“（万元）”；二级指标在第二列 | 只取一个 label 单元格；拆行后标签截断，模糊匹配失败；第二列指标永远不看 |
| **值断** | 跨页列错位；空白/占位符被当 0；同一指标多表重复 | 值挂到错误期间；假 0 参与去重并顶掉真值；去重后只剩一条错误记录 |

**最关键的认知差异：**

- STEP2 页面展示用的是 to_frame()：有列名、有单位脚注，看起来是一张“表”。
- STEP3 实际消费的是 table.rows 和 table.table_blocks：**原始行列表**。
- 两者不是同一个视图。STEP2 展示整齐，不代表 rows 的第一行就是表头。

---

## 1. STEP2 现在到底做了什么

### 1.1 主链路

    PDF
     → STEP1 定位目标表页码（PageMatch）
     → 逐页提取：规则法 / 大模型文本 / 大模型视觉
     → 每页得到一个或多个帧（frames）
     → build_tables_from_page_results() 拼成 raw_tables
     → STEP3 standardize_to_target()

关键代码：

- 逐页入口：services/solvency_rule_table_extractor.py
- 跨页/逐页拼接（AI 路径）：services/solvency_hybrid_pipeline/page_extract.py:429
- 中间表组装（UI 主路径）：ui/step2_extraction.py:226
- 批量路径组装：services/batch_workflow.py:175

### 1.2 中间表是“行拼接”，不是“按列对齐”

ui/step2_extraction.py:259-266 的注释已经说明：不再用 pd.concat 按列名对齐，而是**以第一页/最丰富表头为统一表头，把各页行直接追加**。

具体规则在 ui/step2_extraction.py:328-381：

1. 在所有页的前 20 行里找“最像表头”的一行；
2. 选“表头丰富度”最高的一行作为**整张表唯一的表头**；
3. 跳过这一行本身；
4. 后续页中“与主表头完全相同”的行丢弃；
5. **其余所有行原样拼到 body**。

这带来两个直接后果：

- 第二页表头如果与第一页不完全相同（例如第一页“本季度/上季度”，第二页“本期金额/上期金额”），**第二页表头会变成一条数据行**；
- 第二页的数据列继续套用第一页表头，**期间口径整体错位**。

### 1.3 table_blocks 是唯一的“页内独立表头”载体

- 只有 whole_page=True 时，才在 ui/step2_extraction.py:304-308 / 536 保留 table_blocks。
- 每个 block 是 {page, rows}，即页内原始行。
- STEP3 在 services/solvency_step3_standardizer.py:400 的 _expand_table_blocks() 里再展开。

但展开逻辑有一个硬缺口：

    # solvency_step3_standardizer.py:419-447
    raw_headers = (table.table_config or {}).get("canonical_headers") or ()
    ...
    if canonical_headers and not _block_has_metric_header(block_rows):
        # 用 canonical_headers 补一行表头

也就是说：**block 没有表头时，只有 profile 配置了 canonical_headers 才能补。**

我检查了 config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx 的“目标表”页，6 张表的 canonical_headers **全部为空**。因此非寿险跨页第二页一旦没有表头，STEP3 展开后仍然是无表头块。

### 1.4 换行标签的合并条件很窄

services/solvency_table_extractor.py:317-341：

    def _is_wrapped_continuation(row, previous):
        if previous is None or _row_number_index(row) is not None:
            return False
        nonempty = [...]
        if not nonempty or any(_is_numeric_cell(cell) for cell in nonempty):
            return False
        ...

只有“**没有行号、没有任何数值、纯中文文本**”的行才会被合并到上一行标签。

但真实 PDF 里经常是：

    行A: | 1 | 核心一级资 |        |        |
    行B: |   | 本         | 100.00 | 90.00  |

行 B 有数值，不满足合并条件 → 行 B 被当成独立数据行，标签只剩“本”，行 A 的标签“核心一级资”也不完整。

而且：

- _merge_wrapped_table_rows() 只在规则提取/整页提取中调用；
- AI 逐页路径 page_extract._prepare_page_rows() **没有调用**这个合并；
- STEP3 主归一化路径 normalize_tables() 也**没有**标签换行合并；
- STEP3 的补漏匹配里有一段换行合并（solvency_step3_standardizer.py:781-850），但只对“未匹配的目标指标”生效，主路径已经匹配错/丢弃的不会回头修。

### 1.5 单位是“整表一个”

ui/step2_extraction.py:468-474 用 normalize_table_unit() 给整张表一个 table_unit。

STEP3 的 _source_unit() 在 services/solvency_normalizer.py:464 优先信任 table.unit。

如果第一页写“单位：万元”，第二页写“单位：元”，整表仍按万元处理，第二页数值会差 10000 倍。不是“提不出来”，而是“提出来是错的”。

---

## 2. STEP3 标准化的完整规则

入口：services/solvency_step3_standardizer.py:545 standardize_to_target()

### 2.1 表块展开

_expand_table_blocks()：

- 有 table_blocks → 每个 block 复制成一个独立 ExtractedTable；
- 设置 local.whole_page_block = True；
- 没有 block → 直接用原表。

注意：whole_page_block=True 会让后面的指标匹配 allow_all_codes=True，即**不再受目标表编码白名单限制**。这是双刃剑：能救回跨表指标，也会带来误匹配。

### 2.2 表头假设

services/solvency_normalizer.py:924-946：

    second_header = rows[1]
    is_multiheader = any(term in "".join(second_header)
                         for term in ("账面价值", "非认可", "认可价值"))
    header_rows = rows[:2] if is_multiheader else rows[:1]
    data_start = 2 if is_multiheader else 1

也就是说：

- **默认第 0 行是表头，数据从第 1 行开始；**
- 只有第二行出现“账面价值/非认可/认可价值”才认为是双行表头；
- 其他多行表头（例如“期末数”下再分“审计后/审计前”）不会被识别；
- 如果第 0 行是标题/章节名，整张表会按错误表头解析；
- 如果第 0 行本身就是数据，**第一行数据会被当表头吃掉**。

### 2.3 行号与标签列

services/solvency_normalizer.py:979-984：

    row_number = row[0] if re.fullmatch(r"数字格式", row[0].strip()) else ""
    label_index = 1 if row_number and max_width > 1 else 0

- 只认“第一列是纯数字”为行号；
- 只取 row[label_index] **一个单元格**作为标签；
- 如果表结构是“项目 | 子项 | 数值”，第二列的子项永远不会参与匹配；
- 如果行号与标签被合并成“1 核心一级资本”，不会识别为行号，但标签归一化会剥前导数字，通常还能救。

### 2.4 标签归一化与匹配

services/solvency_normalizer.py:403-418 的 _normalize_label()：

- 去掉公式后缀 (=1+2+3)；
- 去掉括号内的单位（万元）；
- 去掉前导行号（阿拉伯/中文/带括号）；
- 去掉“其中：”；
- 去掉空白和全角/半角标点；
- 对 LCR 做“三个月/未来3个月”统一。

匹配 resolve_metric_match()（solvency_normalizer.py:569）：

1. 先查精确锁 _EXACT_LABEL_LOCKS（solvency_normalizer.py:161）；
2. 遍历 taxonomy 的 指标名称 + 别名（按竖线拆分）；
3. 打分：
   - 完全相同：2.0
   - 一方包含另一方：0.5 + min(len)/max(len)
   - 否则：SequenceMatcher.ratio()
4. 阈值：
   - minimum_score=0.72
   - ambiguity_margin=0.08
   - 前两名都 ≥0.72 且分差 <0.08，且（best 不是精确 或 runner 也是精确）→ **判为歧义，直接丢弃**；
5. 表级白名单 TABLE_ALLOWED_CODES（solvency_normalizer.py:181）：
   - SOLVENCY_MAIN 只允许主表编码；
   - ACTUAL_CAPITAL 只允许实际资本编码；
   - MINIMUM_CAPITAL 只允许一级模块=最低资本；
   - OPERATING_METRICS / NON_LIFE_OPERATING_METRICS 只允许一级模块=经营指标；
   - 只有 whole_page_block=True 才放开全部编码。
6. 寿险/非寿险硬互斥（solvency_normalizer.py:644），避免“寿险业务保险风险最低资本”挂到非寿险编码。

### 2.5 数值解析

services/solvency_normalizer.py:1020-1045：

    numeric_value = parse_numeric(raw_value)
    if pd.isna(numeric_value):
        ...
        numeric_value = 0.0
        placeholder_zero = True

**任何无法解析的数值单元格（包括空字符串、“不适用”、“未披露”、文本）都会被写成 0.0**，并加备注“原始值空白或占位符，按0处理”。

这不是“提取失败”，而是“提取出一个假 0”。假 0 会参与后续去重，可能顶掉另一张表里的真实值。

### 2.6 期间解析

services/solvency_normalizer.py:1104-1133：

    period = _period_header(headers, header_index)
    ...
    if not period:
        continue
    period = _normalize_period_alias(period, metric_code=...)

_period_header()（solvency_normalizer.py:711）：

- 剥掉 (期末)/(期初)/(审计后)/(预测) 等括号；
- 先匹配“本年度/上年度/累计”；
- 再按 PERIOD_TERMS 从长到短匹配；
- 最后 **如果没有命中任何标准词，就把原表头文本原样返回**。

所以：

- 表头“金额” → period = “金额” → 不是空，会写入记录，但后续报表不认识；
- 表头“2024年12月31日” → period = 日期 → 写入记录，但 STEP4/5/7 不认；
- 表头是空单元格 → period = “” → **整列跳过**；
- 表头是“列2” → 返回空 → **整列跳过**。

### 2.7 单位换算

solvency_normalizer.py:464 _source_unit() 的优先级：

1. table.unit（STEP2 嗅探的整表单位）；
2. 行级 unit_records；
3. 标签内嵌单位；
4. 列级 unit_records / 表头内嵌单位；
5. 表级 unit_records；
6. 前 5 行里找“单位：X”。

STEP3 再用 taxonomy 的 标准单位 做换算，_UNIT_CONVERT_EXEMPT_CODES 里的 EPS、车均保费不换算。

### 2.8 补漏匹配

solvency_step3_standardizer.py:629-982：

- 只处理 target_codes - matched_codes 的“未填报”指标；
- 用 _clean_label_supp() 清洗标签：去掉数字、标点、空白、括号，但**不剥中文“万元”**；
- 只做**清洗后完全相等**的匹配，不做模糊匹配；
- 有一张 _PREMIUM_ALIAS_MAP 专门处理非车险 TOP1-TOP5 / 分渠道签单保费；
- 每个编码只匹配一次，匹配后 pop 掉。

因此：

- 主归一化已经匹配错的指标，补漏不会修；
- 标签“核心一级资本 万元”清洗成“核心一级资本万元”，如果目标名是“核心一级资本”，补漏匹配不上（主归一化反而可能通过模糊匹配救回）；
- 补漏只对“未填报”生效，不能解决“填了但填错期间/填了假 0”。

### 2.9 去重

solvency_step3_standardizer.py:1074-1143：

去重键：

    公司统一编码 + 报告期 + 指标编码 + 归一化期间 + 来源工作表

并优先保留：

    本季度末数 > 上季度末数 > 下季度末预测数 > 本年累计数 > 近三年平均

问题：

- 同一指标在不同表（来源工作表不同）**不会互相去重**，下游可能随机取一条；
- 假 0 与真值如果落在同一来源工作表、同一期间，先出现的假 0 可能保留；
- 两个真实口径（例如“期末数-审计后”和“期末数-审计前”）如果期间被归一成同一个，会互相覆盖。

### 2.10 目标表覆盖与汇总

solvency_step3_standardizer.py:1168-1195：

- 用 target_catalog 覆盖 一级模块/二级模块/指标名称/单位/数据类型；
- 生成 填报汇总，状态只有“已填报/未填报”，**不区分“未匹配/期间未识别/值为空/口径冲突”**。

---

## 3. 为什么“STEP2 表格很板正，STEP3 还是提不出来”

下面按发生概率排序。

### 原因 A：STEP3 把“第 0 行”当表头，但 block 第 0 行不是表头

这是最隐蔽、最常见的一类。

STEP2 展示时用的是 to_frame()，它会：

- 用 grid[0] 做列名；
- 只展示 grid[1:]；
- 可选追加单位脚注。

所以你在 STEP2 看到的是“有列名的漂亮表”。

但 STEP3 的 normalize_tables() 直接读 rows：

- 如果 rows[0] 是标题/章节名（例如“一、主要经营指标”“S02-实际资本明细表”），它会把标题当表头；
- 标题通常只占 1 个单元格，其他列的表头是空 → _period_header() 返回空 → **整列跳过**；
- 如果 rows[0] 是第一条数据（无表头块），它会把第一行数据当表头 → data_start=1 → **第一行数据被吃掉**。

_expand_table_blocks() 只负责“补 canonical 表头”，**不负责“在 block 里定位表头行”**。所以 block 前面有标题行时，补表头也救不了。

### 原因 B：跨页只保留一个全局表头，第二页表头变成数据行

ui/step2_extraction.py:328-381 选的是整张表唯一表头。

如果：

- 第 1 页表头：项目 | 本季度末数 | 上季度末数
- 第 2 页表头：项目 | 本季度数 | 上年度末数

则：

- 第 2 页表头与主表头不完全相同 → 被保留为数据行；
- 第 2 页数据继续用第 1 页表头 → “本季度数”被当“本季度末数”，“上年度末数”被当“上季度末数”；
- 去重时可能因为期间碰撞，把第 2 页真实值删掉。

如果 whole_page=False，table_blocks 为空，STEP3 连“按页展开”的机会都没有，只能吃这张错位合并表。

### 原因 C：跨页第二页没有表头，且 profile 没有 canonical_headers

这是非寿险当前最直接的缺口：

- table_blocks 保留了第二页的原始行；
- 第二页是续页，PDF 上本来就没有表头；
- _expand_table_blocks() 想补表头，但 table_config 的 canonical_headers 为空；
- TABLE_CANONICAL_HEADERS（services/solvency_table_boundaries.py:215）里其实有默认表头，但 _expand_table_blocks() **没有用它兜底**；
- 结果：第二页 block 无表头 → STEP3 把第一行数据当表头 → 第一行数据丢失/整块列期间为空。

### 原因 D：指标单元格内换行被拆成两行

典型结构：

    | 行次 | 项目       | 本季度末数 | 上季度末数 |
    | 1    | 核心一级资 |            |            |
    |      | 本         | 100.00     | 90.00      |

- STEP2 的 _is_wrapped_continuation() 要求“无行号、无数值”，第二行有数值 → 不合并；
- STEP3 主路径没有换行合并；
- 结果：第一行标签“核心一级资”模糊匹配可能失败或误匹配；第二行标签“本”直接跳过；
- 如果第一行没有数值，STEP3 还会把第一行当表头或空行。

如果 AI 提取把标签拆成两行、数值放在第二行，这个结构非常常见。

### 原因 E：多行表头只取第一行

STEP3 只认：

- 单行表头：rows[0]
- 双行表头：只有当第二行含“账面价值/非认可/认可价值”时，才把两行拼起来。

如果表头是：

    | 项目 | 期末数 |        | 期初数 |        |
    |      | 审计后 | 审计前 | 审计后 | 审计前 |

STEP3 只看到“期末数/期初数”，4 个数值列会变成同一个期间：

- 第 1 列和第 2 列都映射为“本季度末数”；
- 去重键相同 → 只保留一条 → 另外三个值消失。

### 原因 F：标签列不止一个

如果表是：

    | 项目     | 子项       | 本季度末数 |
    | 投资资产 | 固定收益类 | 100        |

STEP3 只取 row[label_index] 一个单元格，即“投资资产”，**永远不看“固定收益类”**。如果目标指标是“固定收益类投资资产”，就提不出来。

### 原因 G：标签带单位/别名/括号，归一化不彻底

主归一化能剥（万元），但：

- “核心一级资本 万元”（无括号）不会被 _INLINE_UNIT_PATTERN 剥掉；
- “核心一级资本（人民币万元）”可能只剥到部分；
- 补漏匹配 _clean_label_supp() 不剥中文“万元”，比主归一化更严格；
- 目标表指标名与源表标签只差一个“合计/金额/其中”时，可能进入模糊匹配，分差不够被判歧义。

### 原因 H：目标表编码白名单把正确标签挡掉

resolve_metric_match() 默认按 table.table_id 限制允许编码。

例如：

- 当前 block 的 table_id = ACTUAL_CAPITAL；
- 表里却出现“核心偿付能力充足率”；
- ACTUAL_CAPITAL 白名单里没有这个编码；
- 即使标签完全匹配，也直接“目标表范围内没有候选指标”。

只有 whole_page_block=True 才会放开全部编码。如果 table_blocks 为空（whole_page=False），这个限制就生效。

### 原因 I：空白/占位符被写成 0，假 0 顶掉真值

normalize_tables() 对所有数值型指标的不可解析值都写 0.0。

典型场景：

- 第一张表是“资本工具一览表”，发行金额被误挂到“附属一级资本”，数值很大；
- 或者某张表该行是空白，被写成 0；
- 同一指标在另一张表有真值，但去重键包含“来源工作表”，两条都保留；
- 下游按顺序取第一条 → 取到 0 或错误值。

### 原因 J：单位跨页混用

整张表只有一个 table.unit。

- 第 1 页“单位：万元”，第 2 页“单位：元”；
- STEP3 对所有 block 都用 table.unit；
- 第 2 页数值被多除/少除 10000。

### 原因 K：模糊匹配歧义被直接丢弃

例如：

- “保险风险最低资本”
- “非寿险业务保险风险最低资本合计”

两者 SequenceMatcher 分数接近，且都可能 ≥0.72，分差 <0.08，且 best 不是精确 → 判为歧义，metric=None，整行跳过。

诊断表里会写“前两候选分差小于0.08”，但用户通常只看“未填报”。

### 原因 L：目标模板/指标编码本身不在支持范围

read_target_template() 会校验目标表里的编码必须在 supported_metric_catalog() 内，否则直接抛错。

这不是“提取失败”，是“目标表配置失败”。如果用户上传了自定义目标表，编码拼写/新增编码不对，STEP3 会整体失败。

---

## 4. 解决方案

核心思路：**不要把 STEP2 的“展示表”直接喂给 STEP3；要引入一个显式的“表块契约（TableBlock IR）”，把表头、列角色、期间、标签、值、来源都变成结构化字段。**

### 4.1 第一层：定义表块契约

建议新增数据类（可放在 solvency_table_extractor.py 或新模块 table_block.py）：

    @dataclass
    class TableBlock:
        table_id: str
        table_name: str
        page: int
        bbox: tuple[float, float, float, float] | None
        rows: list[list[str]]                 # 原始网格，不改写
        header_rows: list[list[str]]          # 0/1/2 行表头
        header_source: str                    # native | inherited | canonical | manual | none
        header_confidence: float
        column_roles: list[ColumnRole]        # label / row_no / period / unit / note / ignore
        unit: str
        diagnostics: list[str]

ColumnRole 至少包含：

    @dataclass
    class ColumnRole:
        index: int
        role: str                # label | row_no | value | unit | note
        period: str              # 仅 value 列有
        period_source: str       # header | inherited | canonical | default | unknown
        raw_header: str

**原则：STEP3 只消费 TableBlock，不再直接猜 rows[0]。** 展示层继续用 to_frame()，两者解耦。

### 4.2 STEP2 改造

#### 4.2.1 逐页/逐块识别表头，不再选“全局唯一表头”

改造 build_tables_from_page_results()：

- 每一页、每一个 frame 独立识别表头；
- 识别函数不只找“关键词最多”，还要看：
  - 该行下方是否有连续数值行；
  - 该行是否包含“项目/指标名称/行次/期末/期初/本季度/本年累计”等；
  - 该行是否位于表格顶部 0-3 行；
- 每个 block 保存自己的 header_rows，不要合成一张全局表。

#### 4.2.2 表头继承（续页无表头）

对同一 table_id 的 block 序列：

    block[i] 无表头
      → 看 block[i-1] 是否有表头
      → 列数一致 / 数值列位置一致
      → 继承 block[i-1] 的 header_rows
      → header_source = "inherited"

列数不一致时，不要硬套；用列角色对齐（见 4.2.3）。

#### 4.2.3 用“列角色”对齐，而不是按位置拼接

当前 _merge_page_rows() 和 build_tables_from_page_results() 都是按列位置拼接。

建议改为：

1. 对每个 block 识别：
   - 标签列：含“项目/指标名称/行次”或大部分为非数值文本；
   - 数值列：≥60% 行为数值；
   - 行号列：首列大部分为纯数字且宽度短；
2. 用表头语义 + 数值列位置做匹配；
3. 跨页时按“标签列 + 数值列相对顺序”对齐；
4. 如果列数不同但数值列数量一致，按数值列顺序对齐，并记录 alignment_confidence。

#### 4.2.4 多行表头展开

不要只认“账面价值/非认可/认可价值”。

建议：

- 允许 header_rows 为 0/1/2 行；
- 对每一列，把多行表头从上到下拼成叶子路径，例如：
  - 期末数 / 审计后
  - 期末数 / 审计前
- 期间解析时先看叶子，再看父级；
- 去重时保留“审计后/审计前”限定，不要归一成同一个期间。

#### 4.2.5 换行标签合并要覆盖“带数值的第二行”

改造 _is_wrapped_continuation()：

- 允许第二行有数值，只要：
  - 第二行没有行号；
  - 第二行第一个非空单元格是中文标签片段；
  - 上一行在相同标签列有文本，且上一行数值列少于第二行（说明上一行是标签行）；
- 合并时：
  - 标签片段追加到上一行标签；
  - 数值按列索引并入上一行；
  - 记录 wrap_repairs，在 STEP2 展示“已合并 N 个换行标签片段”。

同时：

- 把合并函数复用到 AI 路径 page_extract._prepare_page_rows()；
- STEP3 主归一化路径也做一次兜底合并（不要只在补漏匹配里做）。

#### 4.2.6 展示分隔行不要进入 STEP3 的 rows

【第N页表格分块】、【整页第N张表】这类行只应出现在 to_frame() 展示层，不应进入 table.rows 或 table_blocks[].rows。

否则 STEP3 会把它们当数据行；虽然通常匹配不上，但会干扰“第一行是表头”的判断。

#### 4.2.7 单位按 block 保存

- TableBlock.unit 独立；
- 跨页单位不同时，STEP3 按 block 单位换算；
- 不要用一个 table.unit 覆盖所有页。

#### 4.2.8 STEP2 增加“STEP3 视图”预览

在 STEP2 结果页增加一个只读预览：

    目标表 | 页 | 表头来源 | 标签列 | 数值列期间 | 预计可匹配行数 | 警告

让用户在进 STEP3 前就能看到：

- 哪些 block 没有表头；
- 哪些列期间为空；
- 哪些标签会被 STEP3 当成未匹配。

这比 STEP3 失败后再查诊断表更直观。

### 4.3 STEP3 改造

#### 4.3.1 表头解析器（HeaderResolver）

不要再写死 rows[0]。

    resolve_header(block):
      1. block.header_rows（native）
      2. 若空，继承前一个同 table_id block（inherited）
      3. 若空，用 TABLE_CANONICAL_HEADERS[table_id]（canonical）
      4. 若仍空，用列数生成 列1..列N，并标记 header_source=none

normalize_tables() 从 block.header_rows 取表头，data_start = len(header_rows)。

#### 4.3.2 标签提取改为“标签区”而不是“一个单元格”

在 normalize_tables() 中：

- 先确定第一个数值列；
- 把该列之前的所有非空文本单元格按顺序拼接；
- 跳过行号、单位、备注；
- 例如：["投资资产", "固定收益类", "100"] → 标签“投资资产 固定收益类”，同时保留“投资资产”作为父标签；
- 匹配时先试完整标签，再试最后一个文本单元格。

这样二级指标、嵌套指标都能覆盖。

#### 4.3.3 期间解析：允许“未知期间”而不是静默丢弃

当前 if not period: continue 是“静默丢弃”的根源。

建议：

- 如果表头无法解析期间：
  - 先查 block 继承的期间；
  - 再查目标表默认期间配置；
  - 再查报告期（例如“本季度末数”）；
  - 仍无法确定 → 写入 期间口径="未知"，备注="期间未识别，待人工确认"，并进入诊断；
- **不要直接丢弃整列**。

同时给 _period_header() 增加：

- 日期型表头：2024年12月31日 → 期末数（需结合报告期判断是本期还是上期）；
- 本期金额/上期金额 → 明确映射；
- 年末/年初 → 期末数/期初数；
- 审计后/审计前 作为限定词保留。

#### 4.3.4 匹配策略：先保证“不错杀”，再谈“高精度”

- 表级白名单只在“标签完全匹配”时使用；模糊匹配时可以放开到全量，但要在诊断里标注；
- 对“精确命中但被白名单挡住”的情况，增加一条 fallback：
  - 如果标签归一化后与某个编码的 指标名称/别名 完全相等，但该编码不在当前 table_id 白名单；
  - 不要直接丢弃，写入诊断“跨表精确命中，建议人工确认”，或允许 cross_table_exact 开关；
- 对模糊歧义：
  - 不要直接丢；保留 best 候选，标记 ambiguous=True，让 STEP3 表格显示“待确认”，而不是“未填报”；
  - 人工确认后可以一键采纳。

#### 4.3.5 数值解析：不要把“不可解析”变成 0

建议区分：

| 原始值 | 处理 |
|---|---|
| 空字符串 | 缺失，默认不写记录；只有明确配置“空白=0”的表/指标才写 0 |
| - / -- / — | 按指标/表规则：资本明细可 0，其他缺失 |
| 不适用 / 未披露 / N/A | 缺失，不写 0 |
| 文本 | 缺失，写入诊断 |
| 明确 0 / 0.00 | 写 0 |

这样能避免假 0 顶掉真值，也能让“未填报”真实反映披露情况。

#### 4.3.6 去重改为“候选打分”，不要先到先得

对同一 (公司, 报告期, 指标, 期间) 的多个候选打分：

    + 表头来源 native/inherited/canonical
    + 期间来源 header/inherited/default
    + 数值是显式解析（不是占位符 0）
    + 来源表是目标表主表
    + 标签精确匹配（不是模糊）
    - 歧义
    - 假 0

保留最高分；其余写入“口径冲突”诊断，而不是静默删除。

#### 4.3.7 汇总状态细化

当前只有“已填报/未填报”。建议增加：

- 已填报
- 未匹配（标签没命中）
- 期间未识别
- 值为空
- 口径冲突
- 歧义待确认

这样用户能一眼看出“是没提取到，还是提取到了但期间不对”。

### 4.4 诊断与可观测性

STEP3 已有 normalization_diagnostics，但信息偏“匹配层”。建议补充：

1. **表块级诊断**：
   - table_id / page / block_id
   - header_source
   - header_rows
   - 列角色与期间
   - 数据行数 / 可解析数值行数
2. **行级诊断**：
   - 原始标签
   - 归一化标签
   - 最佳候选 / 得分 / 第二候选
   - 期间 / 期间来源
   - 值解析结果（数值/缺失/假0）
   - 丢弃原因
3. **汇总级诊断**：
   - 每个目标指标的“为什么没填上”
   - 跨页/跨表冲突列表

---

## 5. 建议的落地顺序

### P0：低风险修复（1-2 天）

目标：先止住“跨页无表头”和“静默丢列”。

1. _block_has_metric_header() 补充 期末数/期初数/期末/期初/本年累计 等词，避免把已有表头误判为无表头。
2. _expand_table_blocks() 在 canonical_headers 为空时，回退到 TABLE_CANONICAL_HEADERS[table_id]。
3. _expand_table_blocks() 增加“从前一个 block 继承表头”：
   - 同 table_id；
   - 列数相同或数值列数量相同；
   - 标记 header_source="inherited"。
4. normalize_tables() 不再无条件 if not period: continue：
   - 先尝试 block 继承期间 / 目标表默认期间；
   - 仍失败则写 期间口径="未知" 并进诊断。
5. 数值解析：不适用/未披露/文本 不再写 0；只有空白和显式 - 按现有资本规则处理。
6. 补漏匹配 _clean_label_supp() 增加中文单位剥离（万元/千元/亿元/元）。

### P1：契约层改造（3-5 天）

目标：让 STEP3 不再猜表头。

1. 引入 TableBlock / ColumnRole 数据类。
2. STEP2 每个 block 保存 header_rows/header_source/column_roles/unit。
3. STEP3 normalize_tables() 改为消费 TableBlock。
4. 跨页按列角色对齐，不再按位置拼接。
5. 换行标签合并覆盖“第二行带数值”的情况，并在 STEP3 做兜底。
6. STEP2 增加“STEP3 视图”预览。

### P2：复杂表头与智能化（5-8 天）

1. 多行表头展开（审计后/审计前、期末/期初、预测/实际）。
2. 日期型表头解析。
3. 列角色对齐失败时，用 PDF 坐标（x/y）做几何对齐。
4. 仅对“本地规则无法判定”的表头调用 LLM 期间语义兜底（现有 semantic_period_fallback.py 已有雏形）。
5. 去重候选打分与冲突诊断。

---

## 6. 具体修改点清单

| 文件 | 位置 | 改什么 |
|---|---|---|
| services/solvency_step3_standardizer.py | 383 _block_has_metric_header | 补 期末数/期初数/期末/期初/本年累计 等表头词 |
| services/solvency_step3_standardizer.py | 400 _expand_table_blocks | 用 TABLE_CANONICAL_HEADERS 兜底；增加前块表头继承；定位 block 内真实表头行 |
| services/solvency_normalizer.py | 924-946 | 不再假设 rows[0]；从 block header 取；支持 0/1/2 行表头 |
| services/solvency_normalizer.py | 979-984 | 标签改为“第一个数值列之前的文本区”，支持二级标签 |
| services/solvency_normalizer.py | 1104-1133 | 期间未知不丢列；增加继承/默认/日期规则 |
| services/solvency_normalizer.py | 1033-1045 | 不可解析值不再一律写 0；区分空白/占位符/文本 |
| services/solvency_normalizer.py | 1074-1090 | 假 0 的百分比防护保留，但不要用它替代缺失语义 |
| ui/step2_extraction.py | 328-381 | 不再选全局唯一表头；每页/每块独立表头；展示分隔行不进 rows |
| ui/step2_extraction.py | 304-308 / 536 | table_blocks 始终保留，不再依赖 whole_page |
| services/solvency_table_extractor.py | 317-398 | 换行合并支持“第二行带数值”；AI 路径复用 |
| services/solvency_hybrid_pipeline/page_extract.py | 429-488 | _merge_page_rows 改为按 block 保留表头，不做全局表头覆盖 |
| services/batch_workflow.py | 206-219 | 同样的问题：canonical header 只补整表，不补 block；改为 block 级 |
| services/solvency_table_boundaries.py | 215 | TABLE_CANONICAL_HEADERS 作为统一默认表头来源，供 STEP2/STEP3 共用 |

---

## 7. 验收指标

改造后建议用以下指标验收，而不是只看“是否能跑通”：

1. **目标指标填报率**：系统默认目标表中“已填报”数量 / 总数。
2. **跨页一致率**：同一指标在第 1 页和第 2 页都出现时，期间/数值是否一致。
3. **假 0 率**：备注含“按0处理”的记录数 / 总记录数。
4. **期间未识别率**：期间口径="未知" 的记录数。
5. **歧义率**：ambiguous=True 的记录数。
6. **单位待核对率**：备注含“未识别原始单位”的记录数。
7. **人工回退率**：STEP3 后仍需人工补录的指标数。

建议先在一批已知 Gold 样本（config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx 的“Gold样本”页）上跑基线，再对比改造后指标。

---

## 8. 最重要的建议

**先加“表块契约”和“STEP3 视图”，不要先调模糊匹配阈值。**

从代码看，绝大多数“表格很板正但提不出来”不是 SequenceMatcher 的问题，而是：

- 表头没被正确识别；
- 期间没被正确解析；
- 标签被拆行/只取了一个单元格；
- 跨页列被错位；
- 假 0 顶掉真值。

这些问题都属于**结构/契约问题**。把“rows[0] 是表头”这个隐含假设去掉，让每个 block 显式携带表头和列角色，能一次性解决大部分跨页、换行、缺表头问题；模糊匹配只需要在“标签确实存在但写法不同”时兜底。
