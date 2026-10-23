# -*- coding: utf-8 -*-
"""Normalize insurer short names and peer classifications in the solvency workbook."""
import collections
import os
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

SRC = r"C:\Users\CaoRui\Desktop\AI Tool\Data\偿付能力行业集成数据_补充车险签单保费.xlsx"
OUT = r"C:\Users\CaoRui\Desktop\GI_Solvency_Report_System\偿付能力行业集成数据_公司名称与分类规范化.xlsx"

# original short name -> (standard short name, full legal name, capital, institution type,
#                         peer category, business feature, note)
MASTER = {
    "久隆保险": ("久隆财险", "久隆财产保险有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "建信财险": ("建信财险", "建信财产保险有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "铁路自保": ("铁路自保", "中国铁路财产保险自保有限公司", "中资", "自保公司", "自保公司", "专业自保", ""),
    "京东安联": ("京东安联", "京东安联财产保险有限公司", "外资", "财产保险公司", "外资系", "互联网", "中外合资"),
    "安华农业": ("安华农险", "安华农业保险股份有限公司", "中资", "农业保险公司", "农业保险", "农业保险", ""),
    "国元农业": ("国元农险", "国元农业保险股份有限公司", "中资", "农业保险公司", "农业保险", "农业保险", ""),
    "浙商财险": ("浙商财险", "浙商财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "阳光信保": ("阳光信保", "阳光信用保证保险股份有限公司", "中资", "信用保证保险公司", "中型公司", "信用保证", ""),
    "国任保险": ("国任财险", "国任财产保险股份有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "国泰产险": ("国泰财险", "国泰财产保险有限责任公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "华农保险": ("华农财险", "华农财产保险股份有限公司", "中资", "财产保险公司", "农业保险", "农业保险", "农险特色"),
    "融盛保险": ("融盛财险", "融盛财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "诚泰财险": ("诚泰财险", "诚泰财产保险股份有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "泰山财险": ("泰山财险", "泰山财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "中路财险": ("中路财险", "中路财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "中意财险": ("中意财险", "中意财产保险有限公司", "外资", "财产保险公司", "外资系", "综合", "中外合资"),
    "华安财险": ("华安财险", "华安财产保险股份有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "永安财险": ("永安财险", "永安财产保险股份有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "华海财险": ("华海财险", "华海财产保险股份有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "中华联合": ("中华财险", "中华联合财产保险股份有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "东京海上": ("东京海上日动", "东京海上日动火灾保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "都邦保险": ("都邦财险", "都邦财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "中银保险": ("中银保险", "中银保险有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "恒邦财险": ("恒邦财险", "恒邦财产保险股份有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "黄河财险": ("黄河财险", "黄河财产保险股份有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "紫金财产": ("紫金财险", "紫金财产保险股份有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "锦泰保险": ("锦泰财险", "锦泰财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "国寿财险": ("国寿财险", "中国人寿财产保险股份有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "汇友相互": ("汇友相互", "汇友财产相互保险社", "中资", "相互保险组织", "中型公司", "相互保险", ""),
    "中远海自保": ("中远海自保", "中远海运财产保险自保有限公司", "中资", "自保公司", "自保公司", "专业自保", ""),
    "瑞再企商": ("瑞再企商", "瑞再企商保险有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "北部湾财产": ("北部湾财险", "北部湾财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "太平科技": ("太平科技", "太平科技保险股份有限公司", "中资", "财产保险公司", "中型公司", "科技保险", ""),
    "广东能源自保": ("广东能源自保", "广东能源财产保险自保有限公司", "中资", "自保公司", "自保公司", "专业自保", ""),
    "鑫安汽车": ("鑫安汽车", "鑫安汽车保险股份有限公司", "中资", "财产保险公司", "小型公司", "汽车", ""),
    "东海航运": ("东海航运", "东海航运保险股份有限公司", "中资", "财产保险公司", "中型公司", "航运", ""),
    "鼎和财险": ("鼎和财险", "鼎和财产保险股份有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "中煤财险": ("中煤财险", "中煤财产保险股份有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "美亚保险": ("美亚财险", "美亚财产保险有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "三井住友": ("三井住友海上", "三井住友海上火灾保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "凯本财险": ("凯本财险", "凯本财产保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "利宝保险": ("利宝保险", "利宝保险有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "日本财产": ("日本财险", "日本财产保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "英大财产": ("英大财险", "英大泰和财产保险股份有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "富德产险": ("富德财险", "富德财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "现代财险": ("现代财险", "现代财产保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "永诚保险": ("永诚财险", "永诚财产保险股份有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "泰康在线": ("泰康在线", "泰康在线财产保险股份有限公司", "中资", "财产保险公司", "大型公司", "互联网", ""),
    "阳光产险": ("阳光财险", "阳光财产保险股份有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "华泰财险": ("华泰财险", "华泰财产保险有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "太平财险": ("太平财险", "太平财产保险有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "众诚保险": ("众诚保险", "众诚汽车保险股份有限公司", "中资", "财产保险公司", "中型公司", "汽车", ""),
    "珠峰财险": ("珠峰财险", "珠峰财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "平安产险": ("平安产险", "中国平安财产保险股份有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "亚太财险": ("亚太财险", "亚太财产保险有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "爱和谊": ("爱和谊日生同和", "爱和谊日生同和财产保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "苏黎世": ("苏黎世财险", "苏黎世财产保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "人保产险": ("人保财险", "中国人民财产保险股份有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "渤海财险": ("渤海财险", "渤海财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "兴亚财险": ("日本兴亚", "日本兴亚财产保险（中国）有限责任公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "中原农业": ("中原农险", "中原农业保险股份有限公司", "中资", "农业保险公司", "农业保险", "农业保险", ""),
    "大地财产": ("大地财险", "中国大地财产保险股份有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "中石油专属": ("中石油自保", "中石油专属财产保险股份有限公司", "中资", "自保公司", "自保公司", "专业自保", ""),
    "三星产险": ("三星财险", "三星财产保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "阳光农业": ("阳光农业相互", "阳光农业相互保险公司", "中资", "相互保险组织", "农业保险", "农业保险", ""),
    "安信农业": ("安信农险", "安信农业保险股份有限公司", "中资", "农业保险公司", "农业保险", "农业保险", ""),
    "安诚财险": ("安诚财险", "安诚财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "众安产险": ("众安保险", "众安在线财产保险股份有限公司", "中资", "财产保险公司", "前十大", "互联网", ""),
    "长江财险": ("长江财险", "长江财产保险股份有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "安盛天平": ("安盛天平", "安盛天平财产保险有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "史带财险": ("史带财险", "史带财产保险股份有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "燕赵财险": ("燕赵财险", "燕赵财产保险股份有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "太保产险": ("太保产险", "中国太平洋财产保险股份有限公司", "中资", "财产保险公司", "前十大", "综合", ""),
    "众惠相互": ("众惠相互", "众惠财产相互保险社", "中资", "相互保险组织", "小型公司", "相互保险", ""),
    "海峡金桥": ("海峡金桥", "海峡金桥财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "富邦财险": ("富邦财险", "富邦财产保险有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "前海财险": ("前海财险", "新疆前海联合财产保险股份有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "安盟财险": ("安盟财险", "安盟财产保险有限公司", "外资", "财产保险公司", "农业保险", "农业保险", ""),
    "合众财险": ("合众财险", "合众财产保险股份有限公司", "中资", "财产保险公司", "中型公司", "综合", ""),
    "中国渔业互助": ("中国渔业互保", "中国渔业互保协会", "中资", "行业互助组织", "其他", "行业互助", "非保险公司"),
    "大家财险": ("大家财险", "大家财产保险有限责任公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "比亚迪保险": ("比亚迪财险", "比亚迪财产保险有限公司", "中资", "财产保险公司", "小型公司", "汽车", ""),
    "申能财险": ("申能财险", "申能财产保险有限公司", "中资", "财产保险公司", "大型公司", "综合", ""),
    "安心财险": ("安心财险", "安心财产保险有限责任公司", "中资", "财产保险公司", "小型公司", "互联网", ""),
    "长安保险": ("长安责任保险", "长安责任保险股份有限公司", "中资", "财产保险公司", "小型公司", "责任险", ""),
    "劳合社": ("劳合社", "劳合社保险（中国）有限公司", "外资", "财产保险公司", "外资系", "综合", ""),
    "融通财险": ("融通财险", "中国融通财产保险有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "东吴财险": ("东吴财险", "东吴财产保险股份有限公司", "中资", "财产保险公司", "小型公司", "综合", ""),
    "法巴天星": ("法巴天星", "法巴天星财产保险股份有限公司", "外资", "财产保险公司", "外资系", "综合", "全称以监管批复为准"),
}

# name variants that only exist in the 标准数据 sheet
ALIASES = {
    "人保产险（偿付能力）": "人保产险",
    "众安产险（偿付能力）": "众安产险",
    "太保财险": "太保产险",
    "太保财险（偿付能力）": "太保产险",
    "太平产险": "太平财险",
    "太平产险（偿付能力）": "太平财险",
    "平安产险（偿付能力）": "平安产险",
}

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
BODY_FONT = Font(name="微软雅黑", size=10)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
CHANGE_FILL = PatternFill("solid", fgColor="FCE4D6")


def canon(name):
    return ALIASES.get(name, name)


def style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(1, c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"


def autosize(ws, minw=8, maxw=36):
    for col in range(1, ws.max_column + 1):
        width = minw
        for row in range(1, min(ws.max_row, 400) + 1):
            v = ws.cell(row, col).value
            if v is None:
                continue
            ln = sum(2 if ord(ch) > 127 else 1 for ch in str(v))
            width = max(width, ln + 2)
        ws.column_dimensions[get_column_letter(col)].width = min(width, maxw)


def main():
    wb = openpyxl.load_workbook(SRC)

    # ---------- collect audit info ----------
    stats = collections.defaultdict(lambda: {
        "rows": 0, "cats": collections.Counter(), "names": collections.Counter()})
    ws_sys = wb["_系统字段"]
    sys_header = [c.value for c in ws_sys[1]]
    ix = {h: i for i, h in enumerate(sys_header)}
    for r in ws_sys.iter_rows(min_row=2, values_only=True):
        name = r[ix["公司"]]
        if name is None:
            continue
        key = canon(name)
        d = stats[key]
        d["rows"] += 1
        d["names"][name] += 1
        d["cats"][r[ix["同业分类"]]] += 1

    ws_std = wb["标准数据"]
    std_header = [c.value for c in ws_std[1]]
    six = {h: i for i, h in enumerate(std_header)}
    std_rows = 0
    for r in ws_std.iter_rows(min_row=2, values_only=True):
        if r[six["公司"]] is None:
            continue
        std_rows += 1
        stats[canon(r[six["公司"]])]["std_rows"] = \
            stats[canon(r[six["公司"]])].get("std_rows", 0) + 1

    missing = [k for k in stats if k not in MASTER]
    if missing:
        raise SystemExit("UNMAPPED: %r" % missing)
    extra = [k for k in MASTER if k not in stats]
    if extra:
        raise SystemExit("MASTER NOT IN DATA: %r" % extra)

    # ---------- rewrite _系统字段 ----------
    new_cols = ["公司全称", "机构类型", "业务特色", "名称核对"]
    # insert before 计算逻辑? append at end is safer
    start = len(sys_header) + 1
    for i, h in enumerate(new_cols):
        c = ws_sys.cell(1, start + i, h)
    for row in range(2, ws_sys.max_row + 1):
        raw = ws_sys.cell(row, ix["公司"] + 1).value
        if raw is None:
            continue
        m = MASTER[canon(raw)]
        ws_sys.cell(row, ix["公司"] + 1).value = m[0]
        ws_sys.cell(row, ix["标准公司名称"] + 1).value = m[0]
        ws_sys.cell(row, ix["公司类型"] + 1).value = m[2]
        ws_sys.cell(row, ix["同业分类"] + 1).value = m[4]
        ws_sys.cell(row, start).value = m[1]
        ws_sys.cell(row, start + 1).value = m[3]
        ws_sys.cell(row, start + 2).value = m[5]
        ws_sys.cell(row, start + 3).value = "建议复核" if m[6] else "已核"
    ws_sys.auto_filter.ref = f"A1:{get_column_letter(ws_sys.max_column)}{ws_sys.max_row}"
    style_header(ws_sys, ws_sys.max_column)
    for c in range(1, ws_sys.max_column + 1):
        ws_sys.cell(1, c).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # ---------- rewrite 标准数据 ----------
    start2 = len(std_header) + 1
    for i, h in enumerate(new_cols):
        ws_std.cell(1, start2 + i, h)
    for row in range(2, ws_std.max_row + 1):
        raw = ws_std.cell(row, six["公司"] + 1).value
        if raw is None:
            continue
        m = MASTER[canon(raw)]
        ws_std.cell(row, six["公司"] + 1).value = m[0]
        ws_std.cell(row, six["同业分类"] + 1).value = m[4]
        ws_std.cell(row, start2).value = m[1]
        ws_std.cell(row, start2 + 1).value = m[3]
        ws_std.cell(row, start2 + 2).value = m[5]
        ws_std.cell(row, start2 + 3).value = "建议复核" if m[6] else "已核"
    ws_std.auto_filter.ref = f"A1:{get_column_letter(ws_std.max_column)}{ws_std.max_row}"
    style_header(ws_std, ws_std.max_column)

    # ---------- 公司标准台账 ----------
    if "公司标准台账" in wb.sheetnames:
        del wb["公司标准台账"]
    ws = wb.create_sheet("公司标准台账", 0)
    header = ["序号", "标准简称", "公司全称", "公司统一编码", "资本属性", "机构类型",
              "同业分类", "业务特色", "原名称/历史写法", "标准数据行数", "系统字段行数",
              "名称是否变更", "分类是否变更", "备注"]
    ws.append(header)
    code_map = {}
    for r in ws_sys.iter_rows(min_row=2, values_only=True):
        if r[ix["公司"]]:
            code_map[r[ix["公司"]]] = r[ix["公司统一编码"]]
    for i, raw in enumerate(sorted(MASTER, key=lambda k: MASTER[k][0]), 1):
        m = MASTER[raw]
        d = stats[raw]
        old_cats = "、".join(str(k) for k, _ in d["cats"].most_common())
        name_changed = "是" if any(n != m[0] for n in d["names"]) else "否"
        cat_changed = "是" if set(d["cats"]) - {m[4]} or m[4] not in d["cats"] else "否"
        ws.append([i, m[0], m[1], code_map.get(m[0], ""), m[2], m[3], m[4], m[5],
                   "、".join(f"{n}({c})" for n, c in d["names"].most_common()),
                   d.get("std_rows", 0), d["rows"], name_changed, cat_changed,
                   (m[6] + "；" if m[6] else "") + f"原分类：{old_cats}"])
    style_header(ws, len(header))
    for row in range(2, ws.max_row + 1):
        for c in range(1, len(header) + 1):
            cell = ws.cell(row, c)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=(c in (3, 9, 14)))
        if ws.cell(row, 12).value == "是" or ws.cell(row, 13).value == "是":
            for c in range(1, len(header) + 1):
                if ws.cell(row, c).fill.fgColor.rgb in (None, "00000000"):
                    ws.cell(row, c).fill = CHANGE_FILL
        if ws.cell(row, 14).value and "建议复核" in str(ws.cell(row, 14).value):
            ws.cell(row, 14).fill = WARN_FILL
    autosize(ws)
    ws.column_dimensions["I"].width = 34
    ws.column_dimensions["N"].width = 40

    # ---------- 规范化说明 ----------
    if "规范化说明" in wb.sheetnames:
        del wb["规范化说明"]
    ws2 = wb.create_sheet("规范化说明")
    ws2["A1"] = "保险公司名称与分类规范化说明"
    ws2["A1"].font = Font(name="微软雅黑", size=16, bold=True, color="1F4E79")
    ws2.merge_cells("A1:D1")
    ws2.row_dimensions[1].height = 32

    total_rows = sum(v["rows"] for v in stats.values())
    info = [
        ("数据源", os.path.basename(SRC)),
        ("标准化范围", f"{len(MASTER)} 家公司；_系统字段 {total_rows:,} 行；标准数据 {std_rows:,} 行"),
        ("", ""),
        ("一、发现的问题", ""),
        ("1. 名称不统一", "同一家公司存在多种简称：人保产险/人保产险（偿付能力）、太保产险/太保财险、"
                        "太平财险/太平产险、平安产险/平安产险（偿付能力）；“产险/财险/财产/保险”后缀混用，"
                        "如大地财产、紫金财产、英大财产、北部湾财产、阳光产险、国寿财险。"),
        ("2. 分类口径混用", "“同业分类”同时混入规模（前十大/大型/中型/小型）、资本属性（外资系）、"
                          "业务类型（农业保险）、机构类型（自保公司）和兜底（其他），且同一公司跨报告期"
                          "被分到不同类别：华农（农业保险/中型公司）、泰山（中型/大型）、众安（前十大/大型）、"
                          "安盟（农业保险/其他）、中石油专属（自保公司/其他）等。"),
        ("3. 兜底值过多", "“其他”1,047 行并非真实分类，而是导入时的默认值；公司类型 66,739 行全部为“未分类”。"),
        ("4. 名称变体", "标准数据中另有 7 个带“（偿付能力）”或“太保财险/太平产险”的写法，实际对应 5 家公司。"),
        ("", ""),
        ("二、规范化规则", ""),
        ("标准简称", "统一采用监管/公司官网常用简称：财产险公司统一为“XX财险”，农业险为“XX农险”，"
                  "自保为“XX自保”，相互制保留“XX相互”；人保财险、平安产险、太保产险、国寿财险、"
                  "中华财险、大地财险、阳光财险、太平财险、众安保险、英大财险等按官方品牌保留。"),
        ("同业分类", "每家公司只保留一个值，取值：前十大 / 大型公司 / 中型公司 / 小型公司 / 外资系 / "
                  "农业保险 / 自保公司 / 其他。冲突时按优先级：自保公司 > 农业保险 > 外资系 > 前十大 > "
                  "大型公司 > 中型公司 > 小型公司 > 其他。"),
        ("资本属性", "中资 / 外资（含中外合资）。“外资系”公司同时保留规模以外的资本属性，便于交叉分析。"),
        ("机构类型", "按法人机构性质划分：财产保险公司 / 农业保险公司 / 自保公司 / 相互保险组织 / "
                  "信用保证保险公司 / 行业互助组织。"),
        ("业务特色", "综合 / 农业保险 / 专业自保 / 相互保险 / 信用保证 / 互联网 / 汽车 / 航运 / 科技保险 / 责任险 / 行业互助。"),
        ("原始字段", "原始公司名称、原“同业分类”未删除；新增列全部追加在原有列之后，台账中保留原分类分布，便于回溯。"),
        ("", ""),
        ("三、规范化结果", ""),
    ]
    r = 2
    for k, v in info:
        ws2.cell(r, 1, k).font = Font(name="微软雅黑", size=10, bold=(k.endswith("）") or k.startswith(("一", "二", "三"))))
        ws2.cell(r, 2, v).font = BODY_FONT
        ws2.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        ws2.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
        ws2.row_dimensions[r].height = max(18, 15 * (len(v) // 45 + 1)) if v else 8
        r += 1

    cat_counter = collections.Counter(m[4] for m in MASTER.values())
    cap_counter = collections.Counter(m[2] for m in MASTER.values())
    inst_counter = collections.Counter(m[3] for m in MASTER.values())
    for title, ctr in [("同业分类（公司数）", cat_counter), ("资本属性（公司数）", cap_counter),
                       ("机构类型（公司数）", inst_counter)]:
        ws2.cell(r, 1, title).font = Font(name="微软雅黑", size=10, bold=True)
        r += 1
        for k, v in ctr.most_common():
            ws2.cell(r, 2, k).font = BODY_FONT
            ws2.cell(r, 3, v).font = BODY_FONT
            r += 1
        r += 1

    ws2.cell(r, 1, "四、需人工确认").font = Font(name="微软雅黑", size=10, bold=True)
    r += 1
    ws2.cell(r, 2, "法巴天星：2025 年新设外资财险公司，全称按“法巴天星财产保险股份有限公司”填写，"
                   "建议以监管批复/营业执照为准；台账中已标注“建议复核”。").alignment = Alignment(wrap_text=True)
    ws2.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
    ws2.row_dimensions[r].height = 34
    ws2.column_dimensions["A"].width = 24
    for col in "BCDE":
        ws2.column_dimensions[col].width = 26

    for name in ("公司标准台账", "规范化说明"):
        wb[name].sheet_view.showGridLines = False if name == "规范化说明" else True

    wb.active = 0
    wb.save(OUT)
    print("saved", OUT)
    print("companies", len(MASTER), "sys rows", total_rows, "std rows", std_rows)
    print("categories", dict(cat_counter))
    print("capital", dict(cap_counter))
    print("institution", dict(inst_counter))


if __name__ == "__main__":
    main()
