"""파란 설명서 시안 6쪽 PPTX — calc_result.json 만 읽는다 (숫자를 새로 만들지 않음).

지면 구조: UBS Global Wealth Report 2025 (가로 A4 · 3 단 · 상단 목차선 · 얇은 표 괘선 · 큰 인용문).
강조색만 빨강 → 파랑. 사진 · 로고 · 원문은 쓰지 않는다.

    <python-pptx 가 있는 파이썬> reports/deneb_polaris_report_20261017/blue_manual/build.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_TICK_LABEL_POSITION
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Cm, Emu, Pt

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / "calc_result.json").read_text(encoding="utf-8"))
RUNS = D["runs"]
OUT = HERE / "Deneb_Polaris_설명서_파랑_시안6쪽.pptx"

# ── 색 (파랑 중심 · 차가운 회색 바탕 · 구분되는 비교색) ─────────────────────────
def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


INK, MUTED, RULE, RULE_D = "1A1E25", "5C6573", "C9D0DA", "1A1E25"
ACCENT = "0B3D91"  # 짙은 파랑 — 목차 밑줄 · 인용 막대 · Deneb
NAVY = "0A2A5C"  # 판넬
PANEL = "EDF1F7"  # 원본의 베이지 자리
WHITE = "FFFFFF"
C = {
    "Deneb": "0B3D91",
    "QQQ": "E07A1F",
    "QLD": "3D9BE0",
    "TQQQ": "7A4CB0",
    "SPY": "B0862A",
    "QQQ60": "F2B27A",
    "QLD60": "9FCBEF",
    "TQQQ60": "BBA3DB",
    "VOL": "5F7FC0",
    "Polaris": "1E9A84",
    "IEF": "3D9BE0",
    "GLD": "C9A227",
    "SAV3": "8FA9C6",
    "SAV4": "4C6A8C",
    "PAID": "1A1E25",
}
BODY_FONT = "Noto Sans KR"
HEAD_FONT = "Malgun Gothic Semilight"

W, H = 29.7, 21.0
X1, X2, X3 = 1.55, 10.68, 19.80
CW = 8.35
SPAN2 = X3 + CW - X2  # 17.47
FULL = X3 + CW - X1  # 26.6
NAV = ["개요", "Deneb", "Polaris", "비교", "적금", "자료"]
FOOT = "Deneb · Polaris 투자 전략 설명서 — 시안"


# ── 숫자 표기 ───────────────────────────────────────────────────────────────
def man(v: float) -> str:
    return f"{v / 1e4:,.0f}"


def eok(v: float, unit: str = " 원") -> str:
    neg = v < 0
    v = abs(v)
    e = int(v // 1e8)
    m = round((v - e * 1e8) / 1e4)
    if m == 10000:
        e, m = e + 1, 0
    s = (f"{e}억 {m:,}만" if m else f"{e}억") if e else f"{m:,}만"
    return ("−" if neg else "") + s + unit


def pct(v: float, d: int = 1) -> str:
    return f"{v * 100:.{d}f}%".replace("-", "−")


def months(days: int | None) -> str:
    return "—" if days is None else f"{round(days / 30.44)}개월"


def run(name: str, m: int = 100_000) -> dict:
    return RUNS[name][str(m)]


def sav(r: int, m: int, kind: str = "pretax") -> float:
    return D["savings"][f"r{r}"][str(m)][kind]


AM = [100_000, 200_000, 300_000, 500_000, 1_000_000]
AM_LABEL = {a: f"월 {a // 10000}만 원" for a in AM}


# ── 글자 · 도형 도우미 ──────────────────────────────────────────────────────
def _font(r, name: str, size: float, color: str, bold: bool = False) -> None:
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = rgb(color)
    rpr = r._r.get_or_add_rPr()
    rpr.set("lang", "ko-KR")
    rpr.set("altLang", "en-US")
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rpr, qn(tag))
        el.set("typeface", name)


def _ppr(p, spacing: float, after: float, align=PP_ALIGN.LEFT) -> None:
    p.alignment = align
    p.line_spacing = spacing
    p.space_after = Pt(after)
    p._p.get_or_add_pPr().set("eaLnBrk", "0")


def text(sl, x, y, w, h, paras, size=10.5, color=INK, font=BODY_FONT, bold=False, spacing=1.32,
         after=5.0, anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT, name=""):
    """paras: [str | [(run, {bold, color, size, font})]]"""
    tb = sl.shapes.add_textbox(Cm(x), Cm(y), Cm(w), Cm(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    if isinstance(paras, str):
        paras = [paras]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        _ppr(p, spacing, after, align)
        runs = [(para, {})] if isinstance(para, str) else para
        for t, o in runs:
            r = p.add_run()
            r.text = t
            _font(r, o.get("font", font), o.get("size", size), o.get("color", color), o.get("bold", bold))
    return tb


def line(sl, x1, y1, x2, y2, color=RULE, pt=0.5, dash=None):
    ln = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Cm(x1), Cm(y1), Cm(x2), Cm(y2))
    ln.line.color.rgb = rgb(color)
    ln.line.width = Pt(pt)
    if dash:
        ln.line.dash_style = dash
    st = ln._element.find(qn("p:style"))  # 기본 테마 그림자 · 효과 빼기 (가는 선이 번져 보임)
    if st is not None:
        ln._element.remove(st)
    return ln


def box(sl, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE, line_color=None, name=""):
    s = sl.shapes.add_shape(shape, Cm(x), Cm(y), Cm(w), Cm(h))
    s.fill.solid()
    s.fill.fore_color.rgb = rgb(fill)
    if line_color:
        s.line.color.rgb = rgb(line_color)
        s.line.width = Pt(0.75)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    if name:
        s.name = name
    return s


def frame(sl, active: int, page: int, on_dark_left: float = 0.0) -> None:
    """원본처럼: 왼쪽 위 원형 표지 · 오른쪽 위 목차선(현재 장 밑줄) · 아래 쪽 표시."""
    circ = sl.shapes.add_shape(MSO_SHAPE.OVAL, Cm(X1), Cm(0.75), Cm(1.25), Cm(1.25))
    circ.fill.background()
    circ.line.color.rgb = rgb(WHITE if on_dark_left else MUTED)
    circ.line.width = Pt(0.6)
    circ.shadow.inherit = False
    circ.name = "표지 원"
    tf = circ.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = "D·P"
    _font(r, BODY_FONT, 7, WHITE if on_dark_left else MUTED)
    n = len(NAV)
    seg = SPAN2 / n
    for i, t in enumerate(NAV):
        x = X2 + i * seg
        text(sl, x, 1.02, seg, 0.5, t, size=9, color=INK if i == active else MUTED, spacing=1.0, after=0,
             name=f"목차 {t}")
    line(sl, X2, 1.72, X2 + SPAN2, 1.72, RULE, 0.5)
    ax = X2 + active * seg
    line(sl, ax, 1.72, ax + 1.25 + 0.12 * len(NAV[active]), 1.72, ACCENT, 2.0)
    text(sl, X1, 19.95, 12, 0.4, FOOT, size=8, color=WHITE if on_dark_left else MUTED, spacing=1.0, after=0,
         name="바닥 제목")
    text(sl, X3 + CW - 2, 19.95, 2, 0.4, str(page), size=8, color=MUTED, align=PP_ALIGN.RIGHT, spacing=1.0,
         after=0, name="쪽 번호")


def title(sl, x, y, w, t, size=27, color=INK, h=2.4):
    return text(sl, x, y, w, h, t, size=size, color=color, font=HEAD_FONT, spacing=1.08, after=0, name="제목")


def quote(sl, x, y, w, h, t, size=14.5, color=INK, bar=ACCENT):
    """원본의 큰 인용문 — 왼쪽 가는 막대 + 큰 가는 글씨."""
    box(sl, x, y + 0.08, 0.09, h - 0.16, bar, name="인용 막대")
    return text(sl, x + 0.5, y, w - 0.5, h, t, size=size, color=color, font=HEAD_FONT, spacing=1.22, after=0,
                name="인용")


def chart_head(sl, x, y, w, head, sub="", color=INK):
    line(sl, x, y, x + w, y, RULE_D if color == INK else WHITE, 0.6)
    text(sl, x, y + 0.18, w, 0.5, head, size=9.5, bold=True, color=color, spacing=1.0, after=0, name="그림 제목")
    if sub:
        text(sl, x, y + 0.68, w, 0.5, sub, size=8, color=MUTED if color == INK else "C9D6EA", spacing=1.1, after=0,
             name="그림 설명")


def para(head: str, body: str, hc: str = INK) -> list:
    return [(head + "  ", {"bold": True, "color": hc}), (body, {})]


# ── 표 ─────────────────────────────────────────────────────────────────────
def _border(tcPr, tag: str, color: str | None, pt: float) -> None:
    ln = etree.SubElement(tcPr, qn(tag), w=str(int(Pt(pt))) if color else "0")
    if color:
        sf = etree.SubElement(ln, qn("a:solidFill"))
        etree.SubElement(sf, qn("a:srgbClr"), val=color)
    else:
        etree.SubElement(ln, qn("a:noFill"))


def table(sl, x, y, widths, rows, size=9, head_rows=1, row_h=0.62, bold_first_col=False, fills=None,
          head_color=MUTED, name="표"):
    """제목 · 문자 · 숫자 모두 왼쪽 정렬, 가로 괘선만, 열 너비 고정."""
    nr, nc = len(rows), len(widths)
    gf = sl.shapes.add_table(nr, nc, Cm(x), Cm(y), Cm(sum(widths)), Cm(row_h * nr))
    gf.name = name
    tbl = gf.table
    tblPr = tbl._tbl.tblPr
    for a in ("firstRow", "bandRow", "firstCol", "lastCol", "bandCol", "lastRow"):
        tblPr.set(a, "0")
    sid = tblPr.find(qn("a:tableStyleId"))
    if sid is not None:
        sid.text = "{5940675A-B579-460E-94D1-54222C63F5DA}"  # 스타일 없음 · 표 눈금 없음
    for j, wd in enumerate(widths):
        tbl.columns[j].width = Cm(wd)
    for i in range(nr):
        tbl.rows[i].height = Cm(row_h)
        for j in range(nc):
            cell = tbl.cell(i, j)
            val = rows[i][j]
            if val is None:
                val = ""
            cell.margin_left = Cm(0.08)
            cell.margin_right = Cm(0.12)
            cell.margin_top = Cm(0.07)
            cell.margin_bottom = Cm(0.07)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE if i >= head_rows else MSO_ANCHOR.BOTTOM
            tf = cell.text_frame
            tf.word_wrap = True
            runs = [(val, {})] if isinstance(val, str) else val
            p = tf.paragraphs[0]
            _ppr(p, 1.12, 0)
            for t, o in runs:
                r = p.add_run()
                r.text = t
                is_head = i < head_rows
                _font(r, BODY_FONT, o.get("size", size - 0.5 if is_head else size),
                      o.get("color", head_color if is_head else INK),
                      o.get("bold", is_head or (bold_first_col and j == 0)))
            end = p._p.get_or_add_endParaRPr()
            end.set("sz", str(int((size - 0.5 if i < head_rows else size) * 100)))
            tcPr = cell._tc.get_or_add_tcPr()
            for ch in list(tcPr):
                tcPr.remove(ch)
            top = (RULE_D, 0.75) if i == 0 else (None, 0)
            bot = (RULE_D, 0.75) if i == head_rows - 1 else (None, 0) if i < head_rows else (RULE, 0.5)
            _border(tcPr, "a:lnL", None, 0)
            _border(tcPr, "a:lnR", None, 0)
            _border(tcPr, "a:lnT", *top)
            _border(tcPr, "a:lnB", *bot)
            f = fills.get((i, j)) if fills else None
            if f:
                sf = etree.SubElement(tcPr, qn("a:solidFill"))
                etree.SubElement(sf, qn("a:srgbClr"), val=f)
            else:
                etree.SubElement(tcPr, qn("a:noFill"))
    return tbl


# ── 차트 공통 ───────────────────────────────────────────────────────────────
def _ea_fonts(chart) -> None:
    """차트 글자에 한글 글꼴(ea)도 같이."""
    for el in list(chart._chartSpace.iter(qn("a:latin"))):
        parent = el.getparent()
        if parent.find(qn("a:ea")) is None:
            ea = etree.Element(qn("a:ea"), typeface=el.get("typeface"))
            el.addnext(ea)


def _style(chart, size=8) -> None:
    chart.font.name = BODY_FONT
    chart.font.size = Pt(size)
    chart.font.color.rgb = rgb(MUTED)


def _axes(chart, num_fmt: str | None = None, grid=True, cat_line=True, val_visible=True):
    va = chart.value_axis
    va.has_major_gridlines = grid
    if grid:
        va.major_gridlines.format.line.color.rgb = rgb("E3E7ED")
        va.major_gridlines.format.line.width = Pt(0.5)
    va.format.line.fill.background()
    va.tick_labels.font.size = Pt(8)
    va.tick_labels.font.color.rgb = rgb(MUTED)
    if num_fmt:
        va.tick_labels.number_format = num_fmt
        va.tick_labels.number_format_is_linked = False
    va.visible = val_visible
    ca = chart.category_axis
    ca.tick_labels.font.size = Pt(8)
    ca.tick_labels.font.color.rgb = rgb(INK)
    ca.format.line.color.rgb = rgb(INK if cat_line else RULE)
    ca.format.line.width = Pt(0.75)
    ca.has_major_gridlines = False
    ca.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    return va, ca


def _labels(series_or_plot, fmt: str, pos=None, size=8, color=INK, bold=False):
    dl = series_or_plot.data_labels
    dl.show_value = True
    dl.number_format = fmt
    dl.number_format_is_linked = False
    dl.font.size = Pt(size)
    dl.font.color.rgb = rgb(color)
    dl.font.bold = bold
    if pos is not None:
        dl.position = pos
    return dl


def _gap(plot, gap=60, overlap=None):
    plot.gap_width = gap
    if overlap is not None:
        plot.overlap = overlap


def add_chart(sl, kind, x, y, w, h, cats, series, name="차트"):
    cd = CategoryChartData()
    cd.categories = cats
    for s, vals in series:
        cd.add_series(s, vals)
    gf = sl.shapes.add_chart(kind, Cm(x), Cm(y), Cm(w), Cm(h), cd)
    gf.name = name
    ch = gf.chart
    _style(ch)
    ch.has_title = False
    return ch


def tick_skip(chart, n: int) -> None:
    cat = chart._chartSpace.find(".//" + qn("c:catAx"))
    for tag in ("c:tickLblSkip", "c:tickMarkSkip"):
        el = cat.find(qn(tag))
        if el is None:
            el = etree.SubElement(cat, qn(tag))
        el.set("val", str(n))
    # 스키마 순서: noMultiLvlLbl 뒤에 와야 함 → 마지막으로 옮김 뒤 noMultiLvlLbl 을 앞에
    nm = cat.find(qn("c:noMultiLvlLbl"))
    if nm is not None:
        cat.remove(nm)
        skip = cat.find(qn("c:tickLblSkip"))
        skip.addprevious(nm)


def fin(chart) -> None:
    _ea_fonts(chart)


# ═════════════════════════════════════════════════════════════════════════
prs = Presentation()
prs.slide_width, prs.slide_height = Cm(W), Cm(H)
BLANK = prs.slide_layouts[6]


def new_slide():
    return prs.slides.add_slide(BLANK)


# ── 1. Deneb 의 구성 ──────────────────────────────────────────────────────────
def page_deneb_structure(no: int) -> None:
    sl = new_slide()
    frame(sl, 1, no)
    title(sl, X1, 2.75, CW, "Deneb의 구성")
    quote(sl, X1, 4.35, CW, 2.75,
          "매달 넣는 돈을 TQQQ와 SCHD에 나눠 삽니다. TQQQ는 최대 60%까지만 담고, 시장이 크게 흔들리면 그 몫을 줄입니다.")
    text(sl, X1, 7.55, CW, 11.8, [
        para("TQQQ", "나스닥100 지수의 하루 움직임을 3배로 따라가는 미국 ETF입니다. 지수가 하루 1% 오르면 약 3% 오르고, "
             "1% 내리면 약 3% 내립니다. Deneb에서 수익을 키우는 쪽입니다."),
        para("SCHD", "배당을 꾸준히 늘려 온 미국 기업 약 100곳에 나눠 투자하는 ETF입니다. 성장주와 다르게 움직이는 날이 많아 "
             "계좌의 흔들림을 덜어 줍니다. 그래도 주식이라 원금을 지켜 주지는 않습니다. 2018년 −6%, 2022년 −3%처럼 "
             "함께 내린 해가 있습니다."),
        para("매달 맞춤", "매달 첫 거래일에 새 돈을 넣으면서 두 ETF의 비율을 목표에 다시 맞춥니다. 목표보다 많아진 쪽은 일부 팔고, "
             "모자란 쪽을 삽니다. 이 비율은 새 돈뿐 아니라 계좌 전체(하락 방어로 따로 보관한 현금은 빼고)에 적용됩니다."),
        para("계좌 전체로 보면", "TQQQ가 60%이면 계좌는 나스닥100에 약 1.8배(60% × 3배) 노출됩니다. "
             "‘3배 상품을 산다’와 ‘계좌가 3배로 움직인다’는 같은 말이 아닙니다."),
    ], size=10, after=7)

    # 오른쪽 위: 변동성별 배분 (월 10만 원)
    chart_head(sl, X2, 2.75, SPAN2, "시장 흔들림에 따른 월 10만 원의 목표 배분",
               "나스닥100(QQQ) 최근 20거래일 변동성(연율) 기준 · 하락 방어가 작동하지 않는 평상시 · 단위 만 원")
    cats = ["잔잔함 · 변동성 15%", "보통 · 20%", "출렁임 · 30%", "급락 · 40%", "폭락 · 60%"]
    share = [0.6 * min(1.0, 0.2 / v) for v in (0.15, 0.20, 0.30, 0.40, 0.60)]
    tq = [round(10 * s, 2) for s in share]
    sc = [round(10 - t, 2) for t in tq]
    ch = add_chart(sl, XL_CHART_TYPE.BAR_STACKED, X2, 4.05, SPAN2 - 5.6, 6.2, cats[::-1],
                   [("TQQQ", tq[::-1]), ("SCHD", sc[::-1])], name="변동성별 배분")
    plot = ch.plots[0]
    _gap(plot, 45, 100)
    for s, col, fc in [(plot.series[0], C["Deneb"], WHITE), (plot.series[1], "AFC3E3", INK)]:
        s.format.fill.solid()
        s.format.fill.fore_color.rgb = rgb(col)
        _labels(s, '0"만 원"', XL_LABEL_POSITION.CENTER, 8.5, fc, True)
    va, ca = _axes(ch, '0"만"', grid=False, cat_line=False, val_visible=False)
    va.maximum_scale, va.minimum_scale = 10, 0
    ca.format.line.fill.background()
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.TOP
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(8)
    fin(ch)
    text(sl, X3 + CW - 5.3, 4.25, 5.3, 6.0, [
        [("목표 TQQQ 비중", {"bold": True})],
        "60% × (20% ÷ 지금 변동성)",
        "단, 60%를 넘지 않습니다.",
        [("예: 변동성 30%", {"bold": True})],
        "60% × 20 ÷ 30 = 40%",
        "→ 10만 원 중 TQQQ 4만, SCHD 6만",
    ], size=9, after=3, spacing=1.25)

    # 아래 가운데: 일간 3배
    y2 = 11.0
    line(sl, X2, y2, X2 + CW, y2, RULE_D, 0.6)
    text(sl, X2, y2 + 0.25, CW, 0.6, "일간 3배와 장기 3배", size=12.5, font=HEAD_FONT, spacing=1.0, after=0)
    text(sl, X2, y2 + 1.05, CW, 2.6,
         "TQQQ는 ‘하루’ 수익률의 3배를 맞춥니다. 아래처럼 이틀 동안 지수가 1% 내렸을 때 TQQQ는 3%가 아니라 9% 내렸습니다. "
         "오르내림이 반복될수록 이런 차이가 쌓여, 긴 기간의 수익률은 지수의 정확히 3배가 되지 않습니다.",
         size=9.5, after=0)
    table(sl, X2, y2 + 3.75, [2.75, 1.25, 1.5, 1.5, 1.35], [
        ["100에서 시작", "출발", "1일 +10%", "2일 −10%", "결과"],
        ["나스닥100 지수", "100", "110", "99", "−1%"],
        ["TQQQ (일간 3배)", "100", "130", "91", [("−9%", {"bold": True})]],
    ], size=9, row_h=0.6)
    text(sl, X2, y2 + 6.0, CW, 1.4,
         "꾸준히 한 방향으로 오르는 시기에는 반대로 3배보다 더 오를 수도 있습니다. 같은 식이 TQQQ 단독 결과의 큰 낙폭(−82%)에도 그대로 작용했습니다.",
         size=8.5, color=MUTED, after=0)

    # 아래 오른쪽: 하락 방어
    line(sl, X3, y2, X3 + CW, y2, RULE_D, 0.6)
    text(sl, X3, y2 + 0.25, CW, 0.6, "하락 방어와 복귀", size=12.5, font=HEAD_FONT, spacing=1.0, after=0)
    text(sl, X3, y2 + 1.05, CW, 7.2, [
        para("1단계", "TQQQ 가격이 자기 최고점보다 15% 내리면 가진 TQQQ의 절반을 팝니다."),
        para("2단계", "30% 내리면 남은 TQQQ를 모두 팝니다."),
        para("보관", "판 돈은 현금으로 따로 두고, 매달 비율 계산에서 뺍니다. 그동안 새 돈의 TQQQ 몫도 1단계면 절반, 2단계면 0으로 줄어 나머지는 SCHD로 갑니다."),
        para("복귀", "나스닥100 추세 신호(20·60·120일 가격 비교 중 2개 이상 상승)가 꺼졌다가 다시 켜진 날, 보관한 현금으로 TQQQ를 다시 삽니다."),
        para("대가", "2012~2026년 계산에서 49번 팔고 34번 되샀는데, 되산 가격이 판 평균값보다 비쌌던 경우가 절반(17번)이었습니다."),
    ], size=9.5, after=5)
    sl.notes_slide.notes_text_frame.text = (
        "출처: src/auto_trading/ops/deneb_guard.py(브레이크 −15% 절반 · −30% 전부, T 기준), ops/mf_paper.py(LEV_MAX 0.6, VOL_REF 0.20), "
        "scripts/run_brake_184.py simulate. 변동성 = QQQ 20거래일 일간 수익률 표준편차 × √252. "
        "브레이크 횟수: blue_manual/calc_result.json brake_10 (월 10만 원 실행, 2012-01~2026-08). "
        "SCHD 연도 수익률: data/research/div_mix_180/SCHD.csv 수정 종가. 배당 단계(세후 배당 월 10·30·50·100만 원마다 TQQQ 목표 10%p 낮추자고 묻는 승인형 규칙)는 "
        "이 쪽과 과거 계산에 넣지 않았다."
    )


# ── 2. 나스닥 1·2·3배와 Deneb ────────────────────────────────────────────────
P2 = [
    ("QQQ 단독 적립", "QQQ\n단독", "QQQ"),
    ("QLD 단독 적립", "QLD\n단독", "QLD"),
    ("TQQQ 단독 적립", "TQQQ\n단독", "TQQQ"),
    ("S&P500 단독 적립(SPY)", "S&P500\n단독", "SPY"),
    ("QQQ 60 + SCHD 40 고정", "QQQ 60\nSCHD 40", "QQQ60"),
    ("QLD 60 + SCHD 40 고정", "QLD 60\nSCHD 40", "QLD60"),
    ("TQQQ 60 + SCHD 40 고정", "TQQQ 60\nSCHD 40", "TQQQ60"),
    ("변동성 조절만", "변동성\n조절만", "VOL"),
    ("Deneb", "Deneb", "Deneb"),
]


def color_points(series, keys) -> None:
    for i, k in enumerate(keys):
        pt = series.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(C[k])
    ser = series._element
    for el in ser.findall(qn("c:invertIfNegative")):
        ser.remove(el)
    dpts = ser.findall(qn("c:dPt"))
    dpts[0].addprevious(etree.Element(qn("c:invertIfNegative"), val="0"))
    for dpt in dpts:
        if dpt.find(qn("c:invertIfNegative")) is None:
            dpt.find(qn("c:idx")).addnext(etree.Element(qn("c:invertIfNegative"), val="0"))


def page_leverage(no: int) -> None:
    sl = new_slide()
    frame(sl, 3, no)
    title(sl, X1, 2.75, CW, "나스닥 1·2·3배와\nDeneb", h=2.4)
    text(sl, X1, 5.25, CW, 3.0,
         "같은 돈을 매달 넣었을 때, 배율과 섞는 방식에 따라 끝에 남은 돈과 중간에 견뎌야 했던 하락이 어떻게 달랐는지 봅니다.",
         size=10.5, after=0)
    text(sl, X2, 2.8, CW, 5.4, [
        para("비교 조건", "2012년 1월부터 2026년 8월까지 매달 첫 거래일에 10만 원씩 176번, 모두 1,760만 원을 넣었습니다. "
             "실제 ETF 가격(배당 재투자)을 썼고 사고팔 때마다 0.15% 비용, 세금 전, 환율 고정입니다."),
        para("고정 섞기", "‘60 + 40 고정’은 매달 첫 거래일에 비율을 60 : 40으로 되돌립니다. 하락 방어는 없습니다."),
        para("변동성 조절만", "Deneb에서 하락 방어(매도 · 보관 · 복귀)만 뺀 것입니다."),
    ], size=9.5, after=5)
    text(sl, X3, 2.8, CW, 5.4, [
        para("연구 결과와 선택", "1986~2026년 합성 자료 연구에서 3배 60 + 배당주 40 고정은 최대 −98%까지 내렸습니다. "
             "변동성 조절은 낙폭을 −79%로 줄였지만, 미리 정한 기준(2배에서도 5%p 이상 감소)을 못 넘어 연구 판정은 ‘기각’이었습니다. "
             "그래도 사용자가 수익과 낙폭을 보고 이 방식을 골랐고, 이후 하락 방어를 더했습니다."),
        para("60%", "최적값으로 증명된 숫자가 아니라 앞선 비교 연구에서 이어받은 상한입니다. 이 합성 연구 숫자는 아래 실제 ETF 결과와 기간 · 자료가 달라 섞어 비교하지 않습니다."),
    ], size=9.5, after=5)

    names = [p[0] for p in P2]
    cats = [p[1] for p in P2]
    keys = [p[2] for p in P2]
    yc = 8.55
    chart_head(sl, X1, yc, 13.0, "14년 8개월 적립 후 최종 평가액 · 월 10만 원", "2012년 1월 ~ 2026년 8월 31일 · 176회 · 원금 1,760만 원 · 단위 만 원 · 세전")
    ch = add_chart(sl, XL_CHART_TYPE.COLUMN_CLUSTERED, X1, yc + 1.25, 13.0, 5.1, cats,
                   [("최종 평가액", [run(n)["final"] / 1e4 for n in names])], name="최종 평가액")
    _gap(ch.plots[0], 45)
    color_points(ch.plots[0].series[0], keys)
    _labels(ch.plots[0].series[0], '#,##0', XL_LABEL_POSITION.OUTSIDE_END, 8, INK)
    va, ca = _axes(ch, '#,##0', grid=True)
    va.maximum_scale = 60000
    va.major_unit = 20000
    ch.has_legend = False
    fin(ch)

    xr = X1 + 13.6
    wr = FULL - 13.6
    chart_head(sl, xr, yc, wr, "최대 낙폭", "납입 효과를 뺀 단위 가격의 일간 고점 대비 최대 하락 · 2012-01 ~ 2026-08")
    ch2 = add_chart(sl, XL_CHART_TYPE.COLUMN_CLUSTERED, xr, yc + 1.25, wr, 5.1, cats,
                    [("최대 낙폭", [run(n)["mdd"] for n in names])], name="최대 낙폭")
    _gap(ch2.plots[0], 45)
    color_points(ch2.plots[0].series[0], keys)
    _labels(ch2.plots[0].series[0], '0%;"−"0%', XL_LABEL_POSITION.OUTSIDE_END, 8, INK)
    va2, ca2 = _axes(ch2, '0%;"−"0%', grid=True)
    va2.minimum_scale, va2.maximum_scale, va2.major_unit = -1.0, 0, 0.25
    ca2.tick_label_position = XL_TICK_LABEL_POSITION.HIGH
    ch2.has_legend = False
    fin(ch2)

    yt = 15.15
    hdr = ["월 10만 원 · 14년 8개월"] + [n if "SCHD" in n else n.replace("\n", " ") for n in cats]
    rows = [hdr,
            ["최종 평가액"] + [eok(run(n)["final"], "") for n in names],
            ["투자이익"] + [eok(run(n)["profit"], "") for n in names],
            ["연복리"] + [pct(run(n)["cagr"]) for n in names],
            ["최대 낙폭"] + [pct(run(n)["mdd"]) for n in names],
            ["회복 기간"] + [months(run(n)["recovery_days"]) for n in names]]
    w0 = 2.6
    wd = (FULL - w0) / 9
    fills = {(i, 9): PANEL for i in range(6)}
    table(sl, X1, yt, [w0] + [wd] * 9, rows, size=8.5, row_h=0.6, bold_first_col=True, fills=fills)
    text(sl, X1, 19.3, FULL, 0.5,
         "모든 값은 2012년 1월부터 2026년 8월까지 14년 8개월 동안 매달 10만 원을 넣은 결과. 투자이익 = 최종 평가액 − 원금 1,760만 원. 회복 기간 = 최대 낙폭이 시작된 고점에서 그 고점을 다시 넘은 날까지. 금액 단위 원(억 · 만 표기).",
         size=7.5, color=MUTED, after=0)
    sl.notes_slide.notes_text_frame.text = (
        "자료: QQQ · SCHD · SPY data/research/div_mix_180, TQQQ · QLD data/research/lev_div_181 (실제 ETF 수정 종가). "
        "계산: blue_manual/calc.py — full_report/calculation.py 와 같은 run_brake_184.simulate · 비용 0.15% · 세금 0, 월 20만 원 실행이 기존 원장과 일치. "
        "합성 연구 숫자: docs/research/181 · 182 (1986-02~2026-08, 합성 레버리지 + French 고배당 30%), 182 §7 사용자 결정 'm3가 맘에 드는데 수익과 최대 낙폭 보니까'. "
        "주의: 2012~2026은 나스닥100이 매우 강했던 기간이라 레버리지 결과가 크게 나왔다."
    )


# ── 3. 다섯 납입액 ────────────────────────────────────────────────────────────
def page_amounts(no: int) -> None:
    sl = new_slide()
    frame(sl, 3, no)
    title(sl, X1, 2.75, CW, "다섯 납입액의 결과")
    quote(sl, X1, 4.3, CW, 2.2, "14년 8개월 · 같은 날 · 같은 비용입니다. 금액만 다릅니다.")
    d, q, p = (run(k)["cagr"] for k in ("Deneb", "QQQ 단독 적립", "Polaris"))
    text(sl, X1, 6.85, CW, 2.5,
         f"이 계산은 세금 전 · 소수 주 허용 · 비율 비용이어서 연복리와 낙폭은 금액과 상관없이 같습니다 "
         f"(Deneb 연 {pct(d)}, QQQ 단독 적립 {pct(q)}, Polaris {pct(p)}). 그래서 아래 금액만 다섯 줄로 나눠 봅니다.",
         size=9.5, after=0)

    chart_head(sl, X2, 2.75, SPAN2, "14년 8개월 적립 후 최종 평가액 차이 — QQQ 단독 적립과 비교",
               "같은 돈으로 QQQ만 샀을 때보다 더 남았으면 오른쪽, 덜 남았으면 왼쪽 · 2026-08-31 · 단위 만 원 · 세전")
    cats = [AM_LABEL[a] for a in AM][::-1]
    dn = [(run("Deneb", a)["final"] - run("QQQ 단독 적립", a)["final"]) / 1e4 for a in AM][::-1]
    po = [(run("Polaris", a)["final"] - run("QQQ 단독 적립", a)["final"]) / 1e4 for a in AM][::-1]
    ch = add_chart(sl, XL_CHART_TYPE.BAR_CLUSTERED, X2, 4.05, SPAN2, 5.4, cats,
                   [("Polaris − QQQ 단독 적립", po), ("Deneb − QQQ 단독 적립", dn)], name="QQQ 대비 차이")
    plot = ch.plots[0]
    _gap(plot, 35, 0)
    for s, col in [(plot.series[0], C["Polaris"]), (plot.series[1], C["Deneb"])]:
        s.format.fill.solid()
        s.format.fill.fore_color.rgb = rgb(col)
        s.invert_if_negative = False
        _labels(s, '"+"#,##0"만";"−"#,##0"만"', XL_LABEL_POSITION.OUTSIDE_END, 8, INK)
    va, ca = _axes(ch, '"+"#,##0;"−"#,##0;0', grid=True)
    va.minimum_scale, va.maximum_scale, va.major_unit = -100000, 300000, 50000
    ca.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.TOP
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(8)
    fin(ch)

    yt = 10.15
    chart_head(sl, X1, yt, FULL, "14년 8개월 적립 후 납입액별 최종 평가액과 투자이익",
               "2012-01 ~ 2026-08 매달 첫 거래일 납입 176회 · 단위 만 원 · 세전 · 투자이익 = 최종 평가액 − 원금")
    hdr1 = ["", "원금", "Deneb", None, "QQQ 단독 적립", None, "Polaris", None, "QQQ 단독 적립과 차이", None]
    hdr2 = ["월 납입액", "", "최종 평가액", "투자이익", "최종 평가액", "투자이익", "최종 평가액", "투자이익", "Deneb", "Polaris"]
    rows = [hdr1, hdr2]
    for a in AM:
        dd, qq, pp = run("Deneb", a), run("QQQ 단독 적립", a), run("Polaris", a)
        rows.append([AM_LABEL[a], man(dd["paid"]), man(dd["final"]), man(dd["profit"]), man(qq["final"]),
                     man(qq["profit"]), man(pp["final"]), man(pp["profit"]),
                     "+" + man(dd["final"] - qq["final"]), man(pp["final"] - qq["final"]).replace("-", "−")])
    widths = [2.6, 2.3, 2.75, 2.75, 2.75, 2.75, 2.75, 2.75, 2.7, 2.7]
    s = sum(widths)
    widths = [w_ * FULL / s for w_ in widths]
    fills = {(i, 2): PANEL for i in range(2, 7)} | {(i, 3): PANEL for i in range(2, 7)}
    tbl = table(sl, X1, yt + 1.35, widths, rows, size=9, head_rows=2, row_h=0.6, bold_first_col=True, fills=fills)
    for c0 in (2, 4, 6, 8):
        tbl.cell(0, c0).merge(tbl.cell(0, c0 + 1))

    y3 = 16.35
    cols = [
        para("읽는 법", "최종 평가액은 넣은 원금이 포함된 금액입니다. 원금을 뺀 투자이익을 따로 봐야 합니다. "
             "예: 월 10만 원 QQQ 단독 적립은 8,805만 원이 남았고 그중 7,045만 원이 투자이익입니다."),
        para("돈으로 본 흔들림", f"비율이 같아도 금액이 크면 체감이 다릅니다. 월 10만 원 Deneb 평가액은 2024년 12월 "
             f"{eok(run('Deneb')['money_drop_peak_value'])}에서 2025년 4월까지 {eok(run('Deneb')['money_drop'])} 줄었습니다. "
             f"월 100만 원이면 같은 기간 {eok(run('Deneb', 1_000_000)['money_drop'])}이 줄어듭니다."),
        para("비례가 깨지는 경우", "실제 계좌에서는 정수 주 매수, 최소 주문 금액, 해외주식 양도세(연 250만 원 공제 뒤 22%), "
             "환율 때문에 금액이 정확히 비례하지 않습니다. 이 표는 실제 주문 결과가 아니라 설명용 계산입니다."),
    ]
    for x, cpara in zip((X1, X2, X3), cols, strict=True):
        line(sl, x, y3, x + CW, y3, RULE, 0.5)
        text(sl, x, y3 + 0.2, CW, 3.6, [cpara], size=9, after=0)
    sl.notes_slide.notes_text_frame.text = (
        "다섯 금액을 각각 따로 실행(blue_manual/calc.py). 금액 ÷ 납입액 비율이 모델마다 상대 오차 1e-9 안에서 같았다(비례 모형 확인). "
        "가장 크게 줄었던 금액 = 일간 평가액 고점에서 이후 저점까지 최대 감소액(납입이 중간에 더해진 상태)."
    )


# ── 4. 기간별 자산 변화 ──────────────────────────────────────────────────────
def page_periods(no: int) -> None:
    sl = new_slide()
    frame(sl, 3, no)
    title(sl, X1, 2.75, CW, "기간별 자산 변화")
    quote(sl, X1, 4.3, CW, 2.2, "14년을 한 그림에 넣으면 앞쪽 몇 해가 바닥에 붙어 버립니다.")
    text(sl, X1, 6.85, CW, 4.2, [
        "그래서 네 구간으로 나눠 구간마다 세로축을 따로 잡았습니다. 칸마다 세로축 숫자가 다르니 칸끼리 선 높이를 비교하지 말고, 같은 칸 안에서 선끼리 비교하세요.",
        "모든 선은 2012년 1월부터 매달 10만 원을 넣은 같은 계좌입니다. 다른 납입액은 3쪽 표처럼 금액만 비례해서 커집니다.",
    ], size=9.5, after=6)
    m = D["monthly_10"]
    keys = [("Deneb", "Deneb"), ("QQQ 단독 적립", "QQQ 단독"), ("Polaris", "Polaris")]
    marks = ["2015-12", "2019-12", "2021-12", "2022-12", "2026-08"]
    allm = sorted(m["Deneb"])
    rows = [["달말", "적립 기간", "원금", "Deneb", "QQQ 단독", "Polaris"]]
    for mk in marks:
        k = allm.index(mk) + 1
        span = f"{k // 12}년" + (f" {k % 12}개월" if k % 12 else "")
        rows.append([mk.replace("-", "."), span, man(k * 1e5)] + [man(m[n][mk]) for n, _ in keys])
    chart_head(sl, X1, 11.2, CW, "구간 끝 평가액 · 월 10만 원", "2012년 1월 첫 납입부터 센 적립 기간 · 단위 만 원 · 세전")
    table(sl, X1, 12.45, [1.4, 1.85, 1.05, 1.4, 1.45, 1.2], rows, size=8.5, row_h=0.58, bold_first_col=True)
    text(sl, X1, 16.5, CW, 3.0,
         f"2022년 한 해 동안 Deneb는 {man(m['Deneb']['2021-12'])}만 원에서 {man(m['Deneb']['2022-12'])}만 원으로, "
         f"QQQ 단독 적립은 {man(m['QQQ 단독 적립']['2021-12'])}만 원에서 {man(m['QQQ 단독 적립']['2022-12'])}만 원으로 줄었습니다. "
         "그사이 넣은 120만 원이 포함된 금액입니다.",
         size=9, after=0)

    # 공통 범례
    lx, ly = X2, 2.85
    items = [("Deneb", C["Deneb"], None, 2.25), ("QQQ 단독 적립", C["QQQ"], MSO_LINE_DASH_STYLE.DASH, 2.0),
             ("Polaris", C["Polaris"], None, 2.0), ("넣은 원금 누계", C["PAID"], MSO_LINE_DASH_STYLE.SQUARE_DOT, 1.0)]
    for t, col, dash, wpt in items:
        line(sl, lx, ly + 0.25, lx + 0.9, ly + 0.25, col, wpt, dash)
        text(sl, lx + 1.05, ly, 3.5, 0.5, t, size=8.5, spacing=1.0, after=0)
        lx += 1.25 + 0.27 * len(t) + 0.6
    panels = D["panels"]
    pos = [(X2, 3.75), (X3, 3.75), (X2, 11.6), (X3, 11.6)]
    for (a, b), (x, y) in zip(panels, pos, strict=True):
        ms = [mm for mm in allm if a <= mm <= b]
        cats = [mm[:4] for mm in ms]
        paid = [(allm.index(mm) + 1) * 10 for mm in ms]
        ser = [(lab, [m[n][mm] / 1e4 for mm in ms]) for n, lab in keys] + [("넣은 원금 누계", paid)]
        chart_head(sl, x, y, CW, f"{a[:4]}~{b[:4]}년" + (" (8월까지)" if b.endswith("08") else ""),
                   f"월말 평가액 · 단위 만 원 · 구간 끝 원금 {paid[-1]:,}만 원")
        ch = add_chart(sl, XL_CHART_TYPE.LINE, x, y + 1.2, CW, 6.3, cats, ser, name=f"{a[:4]}~{b[:4]} 평가액")
        for s, (_, col, dash, wpt) in zip(ch.plots[0].series, items, strict=True):
            s.smooth = False
            s.format.line.color.rgb = rgb(col)
            s.format.line.width = Pt(wpt)
            if dash:
                s.format.line.dash_style = dash
            s.marker.style = None
            s.marker.format.fill.background()
            mk = s._element.find(qn("c:marker"))
            if mk is None:
                mk = etree.SubElement(s._element, qn("c:marker"))
            for chd in list(mk):
                mk.remove(chd)
            etree.SubElement(mk, qn("c:symbol"), val="none")
        _axes(ch, '#,##0', grid=True)
        ch.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
        tick_skip(ch, 12)
        ch.has_legend = False
        fin(ch)
    sl.notes_slide.notes_text_frame.text = (
        "월말 평가액 = 각 달 마지막 거래일 종가 평가(blue_manual/calc_result.json monthly_10). 원금 누계 = 납입 회차 × 10만 원. "
        "구간: 2012-01~2015-12 · 2016-01~2019-12 · 2020-01~2022-12 · 2023-01~2026-08 (결과 보기 전 calc_spec.md 에 고정)."
    )


# ── 5. Polaris 의 채권과 금 관리 ──────────────────────────────────────────────
def page_polaris(no: int) -> None:
    sl = new_slide()
    pw = X1 + CW + 0.4
    box(sl, 0, 0, pw, H, NAVY, name="판넬")
    frame(sl, 2, no, on_dark_left=pw)
    title(sl, X1, 2.75, CW - 0.2, "Polaris의\n채권과 금", color=WHITE, h=2.4)
    text(sl, X1, 5.35, CW - 0.3, 2.4,
         "미국 주식 60%에 미국 국채 20%와 금 20%를 섞어, 주식만 들고 있을 때보다 흔들림을 줄이려는 적립식 배분입니다.",
         size=10.5, color=WHITE, after=0)
    ch = add_chart(sl, XL_CHART_TYPE.DOUGHNUT, X1 - 0.2, 7.6, 4.4, 4.4, ["SPYM", "QQQM", "IEF", "GLDM"],
                   [("비중", [0.3, 0.3, 0.2, 0.2])], name="Polaris 구성")
    s = ch.plots[0].series[0]
    for i, col in enumerate(["7FB0F0", "3D7FD9", "BFD6F5", C["GLD"]]):
        pt = s.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = rgb(col)
        pt.format.line.color.rgb = rgb(NAVY)
        pt.format.line.width = Pt(1.5)
    ch.has_legend = False
    hole = ch._chartSpace.find(".//" + qn("c:holeSize"))
    if hole is not None:
        hole.set("val", "58")
    fin(ch)
    text(sl, X1 + 4.5, 7.75, CW - 4.7, 4.3, [
        [("30%  SPYM", {"bold": True})], "S&P500 미국 대형주 500곳",
        [("30%  QQQM", {"bold": True})], "나스닥100 (QQQ와 같은 지수)",
        [("20%  IEF", {"bold": True})], "미국 국채 7~10년",
        [("20%  GLDM", {"bold": True})], "금",
    ], size=8.5, color=WHITE, after=1, spacing=1.15)
    text(sl, X1, 12.45, CW - 0.3, 7.0, [
        para("주식 60% 안의 겹침", "SPYM과 QQQM에는 애플 · 마이크로소프트 · 엔비디아 같은 대형 기술주가 함께 들어 있습니다. "
             "그래서 주식 60%의 실제 기술주 비중은 30%보다 큽니다.", WHITE),
        para("왜 채권과 금인가", "2020년처럼 주식이 급락할 때 국채나 금이 덜 내리거나 오른 적이 많았습니다. "
             "하지만 2022년처럼 주식과 국채가 함께 내린 해도 있어 항상 막아 주지는 않습니다.", WHITE),
    ], size=9, color="DCE6F5", after=6)

    # 오른쪽 위: 상황별 관리표
    chart_head(sl, X2, 2.75, SPAN2, "상황별 관리 — 현재 프로그램에 있는 규칙만",
               "실제 운영: 나무증권 원화 주문 · 정수 주 · 매달 버튼을 눌러야 그달 매수 · 12월 맞춤도 버튼 승인")
    rows = [
        ["상황", "정해진 규칙", "실제로 하는 일", "주의할 점"],
        ["매달 새 돈이 들어올 때", "목표 비율보다 모자란 종목부터 산다 (사기만)", "주식이 올라 비중이 커졌으면 그달 돈은 채권 · 금 쪽으로 간다", "팔지 않으므로 비율이 한쪽으로 쏠릴 수 있다"],
        ["매년 12월 첫 평일", "목표보다 1주 넘게 많은 종목을 팔고 모자란 종목을 산다", "많이 오른 자산 일부를 팔아 덜 오른 자산을 산다", "판 값이 평균 단가보다 낮으면 손실을 확정하는 매도다"],
        ["채권 · 금이 크게 올랐을 때", "별도 익절 규칙 없음", "12월 맞춤 때 넘친 몫만 판다", "‘오르면 바로 판다’가 아니다"],
        ["채권 · 금이 내렸을 때", "별도 손절 규칙 없음", "비중이 모자라면 새 돈과 12월 맞춤으로 더 산다", "주식 · 채권이 같이 내릴 수 있다 (2022년)"],
        ["주식이 폭락했을 때", "별도 규칙 없음", "채권 · 금 비중이 커지면 12월에 그 몫을 팔아 주식을 산다", "12월 전에는 새 돈으로만 주식을 채운다"],
        ["채권 분배금", "전용 규칙 없음", "계좌에 달러 현금으로 들어온다", "계산은 재투자로 가정해 실제와 차이가 난다"],
    ]
    table(sl, X2, 4.05, [3.3, 4.6, 5.0, SPAN2 - 12.9], rows, size=8.5, row_h=0.78, bold_first_col=True)

    # 오른쪽 아래 왼쪽: 연도별 수익
    yb = 11.15
    chart_head(sl, X2, yb, CW, "자산별 연간 수익률 — 네 해 골라 보기", "실제 ETF 수정 종가 · 금은 GLD, 국채는 IEF · 달러 기준")
    ys = ["2013", "2018", "2020", "2022"]
    Y = D["yearly"]
    ys = ys[::-1]
    ser = [(lab, [round(Y[k][y], 2) for y in ys]) for k, lab in
           [("GLD", "GLD 금"), ("IEF", "IEF 국채"), ("SPY", "SPY"), ("QQQ", "QQQ")]]
    ch = add_chart(sl, XL_CHART_TYPE.BAR_CLUSTERED, X2, yb + 1.3, CW, 6.7, [y + "년" for y in ys], ser,
                   name="연간 수익률")
    plot = ch.plots[0]
    _gap(plot, 40, 0)
    for s, col in zip(plot.series, [C["GLD"], C["IEF"], C["SPY"], C["QQQ"]], strict=True):
        s.format.fill.solid()
        s.format.fill.fore_color.rgb = rgb(col)
        s.invert_if_negative = False
        _labels(s, '0%;"−"0%', XL_LABEL_POSITION.OUTSIDE_END, 7, INK)
    va, ca = _axes(ch, '0%;"−"0%', grid=True)
    va.minimum_scale, va.maximum_scale, va.major_unit = -0.5, 0.6, 0.25
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.TOP
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(8)
    fin(ch)

    # 오른쪽 아래 오른쪽: QQQ 비교 + 예시
    line(sl, X3, yb, X3 + CW, yb, RULE_D, 0.6)
    text(sl, X3, yb + 0.18, CW, 0.5, "14년 8개월 적립 후 · 월 10만 원", size=9.5, bold=True, spacing=1.0, after=0)
    text(sl, X3, yb + 0.68, CW, 0.5, "QQQ 단독 적립과 비교 · 2012-01 ~ 2026-08 · 원금 1,760만 원 · 세전", size=8, color=MUTED, spacing=1.0, after=0)
    po, qq, sp = run("Polaris"), run("QQQ 단독 적립"), run("S&P500 단독 적립(SPY)")
    table(sl, X3, yb + 1.35, [2.35, 2.0, 2.0, 2.0], [
        ["", "Polaris", "QQQ 단독", "S&P500 단독"],
        ["최종 평가액", eok(po["final"], ""), eok(qq["final"], ""), eok(sp["final"], "")],
        ["연복리", pct(po["cagr"]), pct(qq["cagr"]), pct(sp["cagr"])],
        ["최대 낙폭", pct(po["mdd"]), pct(qq["mdd"]), pct(sp["mdd"])],
        ["회복 기간", months(po["recovery_days"]), months(qq["recovery_days"]), months(sp["recovery_days"])],
    ], size=8.5, row_h=0.55, bold_first_col=True, fills={(i, 1): PANEL for i in range(5)})
    text(sl, X3, yb + 4.75, CW, 3.6, [
        f"낙폭은 {pct(qq['mdd'])} → {pct(po['mdd'])}로 줄었지만, 끝 금액은 QQQ 단독 적립보다 {eok(qq['final'] - po['final'])} 적었습니다. "
        f"2022년 하락에서 다시 고점을 넘기까지는 QQQ와 같은 {months(po['recovery_days'])}이 걸렸습니다.",
        [("12월 맞춤 예: ", {"bold": True}), ("계좌 1,000만 원이 주식 680만 · 국채 160만 · 금 160만이면 목표는 600 · 200 · 200만 원입니다. "
          "주식 80만 원어치를 팔아 국채와 금을 40만 원어치씩 삽니다.", {})],
    ], size=8.5, after=5)
    sl.notes_slide.notes_text_frame.text = (
        "규칙 출처: src/auto_trading/ops/polaris.py (WEIGHTS SPYM 30 · QQQM 30 · IEF 20 · GLDM 20, plan_buys 모자란 종목부터 정수 주, "
        "plan_rebalance 목표보다 1주 넘게 많은 만큼 매도), docs/current/CURRENT_SPEC.md Polaris 행. 분배금 처리 코드 없음(확인). "
        "계산: run_namu_core_compare.run_policy YEAR (매년 12월 첫 거래일 전체 맞춤, 소수 주), SPY · QQQ · GLD 를 SPYM · QQQM · GLDM 대리로 사용. "
        "실제 운영은 2026-11부터 100만 원 × 4회 뒤 12월 맞춤 — 보고서는 매달 계속 적립으로 비교(운영 설정 불변)."
    )


# ── 6. 적금과 비교 ────────────────────────────────────────────────────────────
def page_savings(no: int) -> None:
    sl = new_slide()
    frame(sl, 4, no)
    title(sl, X1, 2.75, CW, "적금과 비교")
    quote(sl, X1, 4.3, CW, 2.2, "적금의 이자는 약속된 돈이고, 투자 결과는 지나간 기간의 기록입니다.")
    text(sl, X1, 6.85, CW, 3.0,
         "같은 날 같은 돈을 연 3% · 4% 적금에 넣은 경우와 비교합니다. 적금은 1년 만기마다 원리금을 같은 금리 예금으로 다시 맡긴다고 가정했습니다(세전).",
         size=9.5, after=0)
    names = [("적금 3%", None, "SAV3"), ("적금 4%", None, "SAV4"), ("Polaris", "Polaris", "Polaris"),
             ("QQQ 단독 적립", "QQQ 단독 적립", "QQQ"), ("Deneb", "Deneb", "Deneb")]
    paid = 176 * 1e5
    mult = [sav(3, 100_000) / paid, sav(4, 100_000) / paid] + [run(k)["final"] / paid for _, k, _ in names[2:]]
    mdd = [0.0, 0.0] + [run(k)["mdd"] for _, k, _ in names[2:]]
    cats = [n for n, _, _ in names][::-1]
    keys = [k for _, _, k in names][::-1]
    chart_head(sl, X2, 2.75, CW, "14년 8개월 적립 후 원금 대비 최종 금액", "2012-01 ~ 2026-08-31 · 몇 배가 됐나 · 금액과 무관 · 세전")
    ch = add_chart(sl, XL_CHART_TYPE.BAR_CLUSTERED, X2, 3.95, CW, 5.4, cats, [("배수", mult[::-1])], name="원금 대비 배수")
    _gap(ch.plots[0], 45)
    color_points(ch.plots[0].series[0], keys)
    _labels(ch.plots[0].series[0], '0.00"배"', XL_LABEL_POSITION.OUTSIDE_END, 8, INK)
    va, ca = _axes(ch, '0"배"', grid=True)
    va.minimum_scale, va.maximum_scale, va.major_unit = 0, 25, 5
    ch.has_legend = False
    fin(ch)
    chart_head(sl, X3, 2.75, CW, "그동안 견뎌야 했던 최대 낙폭", "적금은 0% · 투자는 단위 가격의 일간 고점 대비 · 2012-01 ~ 2026-08")
    ch2 = add_chart(sl, XL_CHART_TYPE.BAR_CLUSTERED, X3, 3.95, CW, 5.4, cats, [("최대 낙폭", mdd[::-1])], name="최대 낙폭 비교")
    _gap(ch2.plots[0], 45)
    color_points(ch2.plots[0].series[0], keys)
    _labels(ch2.plots[0].series[0], '0.0%;"−"0.0%;', XL_LABEL_POSITION.OUTSIDE_END, 8, INK)
    va2, ca2 = _axes(ch2, '0%;"−"0%;0%', grid=True)
    va2.minimum_scale, va2.maximum_scale, va2.major_unit = -0.5, 0, 0.1
    ca2.tick_label_position = XL_TICK_LABEL_POSITION.HIGH
    ch2.has_legend = False
    fin(ch2)

    yt = 10.05
    chart_head(sl, X1, yt, FULL, "14년 8개월 적립 후 납입액별 최종 금액 — Deneb · Polaris · QQQ 단독 적립 · 적금 3% · 적금 4%",
               "2012-01 ~ 2026-08 매달 납입 176회 · 단위 만 원 · 모두 세전")
    hdr1 = ["", "원금", "최종 금액", None, None, None, None, "적금 4%보다 더 남은 금액", None, None]
    hdr2 = ["월 납입액", "", "적금 3%", "적금 4%", "Polaris", "QQQ 단독", "Deneb", "Polaris", "QQQ 단독", "Deneb"]
    rows = [hdr1, hdr2]
    for a in AM:
        s4 = sav(4, a)
        vals = [run(k, a)["final"] for k in ("Polaris", "QQQ 단독 적립", "Deneb")]
        rows.append([AM_LABEL[a], man(176 * a), man(sav(3, a)), man(s4)] + [man(v) for v in vals]
                    + ["+" + man(v - s4) for v in vals])
    widths = [2.6] + [(FULL - 2.6) / 9] * 9
    fills = {(i, 6): PANEL for i in range(2, 7)} | {(i, 9): PANEL for i in range(2, 7)}
    tbl = table(sl, X1, yt + 1.35, widths, rows, size=9, head_rows=2, row_h=0.6, bold_first_col=True, fills=fills)
    tbl.cell(0, 2).merge(tbl.cell(0, 6))
    tbl.cell(0, 7).merge(tbl.cell(0, 9))

    y3 = 16.35
    cols = [
        para("원금 위험", "적금은 예금자 보호 한도 안에서 원금과 약정 이자가 정해져 있습니다. 투자 계좌는 넣은 돈보다 적어질 수 있고, "
             "언제 끝내느냐에 따라 결과가 크게 달라집니다."),
        para("기간의 운", "2012~2026년은 미국 성장주가 매우 강했던 기간입니다. 이 기간에는 원금 아래로 내려간 달이 거의 없었지만, "
             "닷컴 붕괴 · 2008년 위기가 들어간 긴 합성 연구에서는 레버리지 섞기의 낙폭이 −98%까지 나왔습니다."),
        para("세금 가정", f"이 표는 모두 세전입니다. 적금 이자에 15.4%를 떼면 월 10만 원 적금 4%는 {man(sav(4, 100_000, 'after_tax'))}만 원입니다. "
             "해외 ETF는 매도 이익에서 연 250만 원을 뺀 뒤 22% 양도세가 붙습니다."),
    ]
    for x, cpara in zip((X1, X2, X3), cols, strict=True):
        line(sl, x, y3, x + CW, y3, RULE, 0.5)
        text(sl, x, y3 + 0.2, CW, 3.6, [cpara], size=9, after=0)
    sl.notes_slide.notes_text_frame.text = (
        "적금: 12개월 만기 단리(납입 달마다 남은 개월 비례), 만기금은 같은 금리 1년 예금으로 해마다 재예치(연 복리), 마지막 8개월은 경과 비례 단리. "
        "5년 값이 기존 원장 save() 식과 같음을 확인. 세후 참고는 이자에 15.4% 차감. 원금 하회 달 수: calc_result.json under_paid_months "
        "(월 10만 원 기준 Deneb 0달, 나머지 2012년 5월 1달). 합성 연구 낙폭: docs/research/181 · 182."
    )


if __name__ == "__main__":
    page_deneb_structure(1)
    page_leverage(2)
    page_amounts(3)
    page_periods(4)
    page_polaris(5)
    page_savings(6)
    prs.save(OUT)
    print(OUT)
