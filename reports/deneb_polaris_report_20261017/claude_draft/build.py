"""Claude 시안 6쪽 PPTX — calc_result.json 만 읽는다(숫자 새로 만들지 않음).

    <python-pptx 가 있는 파이썬> reports/deneb_polaris_report_20261017/claude_draft/build.py
"""

from __future__ import annotations


import json
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_TICK_LABEL_POSITION
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Cm, Pt

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "calc_result.json").read_text(encoding="utf-8"))
M = R["models"]
OUT = HERE / "Deneb_Polaris_Claude_시안_6쪽.pptx"

FONT = "Noto Sans KR"
INK = RGBColor(0x1A, 0x1A, 0x1A)
GREY = RGBColor(0x5F, 0x63, 0x68)
RULE = RGBColor(0xD0, 0xD4, 0xD0)
GREEN = RGBColor(0x17, 0x4D, 0x3B)
GREEN_SOFT = RGBColor(0xEC, 0xF2, 0xEE)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
C_DENEB = RGBColor(0x17, 0x4D, 0x3B)
C_POLARIS = RGBColor(0xC2, 0x8A, 0x1E)
C_VOL = RGBColor(0x2F, 0x6D, 0xB5)
C_FIXED = RGBColor(0x8E, 0x4F, 0xB0)
C_SAV4 = RGBColor(0x2F, 0x6D, 0xB5)
C_SAV3 = RGBColor(0x7F, 0xB3, 0xE6)
C_CASH = RGBColor(0xC2, 0x8A, 0x1E)

NAV = ["개요", "조건", "Deneb", "Polaris", "비교", "전망"]
W, H = 29.7, 21.0
LM = 1.6
CW = W - 2 * LM


def man(v: float) -> str:
    return f"{v / 10_000:,.0f}"


def pct(v: float, digits: int = 1) -> str:
    return f"{v * 100:.{digits}f}%".replace("-", "−")


# ── 기본 도형 ────────────────────────────────────────────────────────────────
def text(slide, x, y, w, h, paras, size=10.5, color=INK, bold=False, align=PP_ALIGN.LEFT,
         spacing=1.25, anchor=MSO_ANCHOR.TOP, space_after=0.0):
    """paras: str | list[str | list[(run, {opts})]]"""
    box = slide.shapes.add_textbox(Cm(x), Cm(y), Cm(w), Cm(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    if isinstance(paras, str):
        paras = [paras]
    for i, p in enumerate(paras):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align
        para.line_spacing = spacing
        para.space_after = Pt(space_after)
        ppr = para._p.get_or_add_pPr()
        ppr.set("latinLnBrk", "0")
        runs = p if isinstance(p, list) else [(p, {})]
        for t, o in runs:
            r = para.add_run()
            r.text = t
            f = r.font
            f.name = FONT
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.color.rgb = o.get("color", color)
            rpr = r._r.get_or_add_rPr()
            rpr.set("lang", "ko-KR")
            rpr.set("altLang", "en-US")
            for tag in ("a:ea", "a:cs"):
                el = rpr.find(qn(tag))
                if el is None:
                    el = etree.SubElement(rpr, qn(tag))
                el.set("typeface", FONT)
    return box


def rect(slide, x, y, w, h, fill, line=None):
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Cm(x), Cm(y), Cm(w), Cm(h))
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
    s.shadow.inherit = False
    return s


def hline(slide, x, y, w, color=INK, width=0.75):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Cm(x), Cm(y), Cm(x + w), Cm(y))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    return c


def vline(slide, x, y, h, color=GREEN, width=2.0):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Cm(x), Cm(y), Cm(x), Cm(y + h))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    return c


def frame(prs, page: int, active: str, *, title: str | None, title_x: float = LM,
          footer_white: bool = False):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    text(s, LM, 0.8, 6, 0.5, "Deneb · Polaris", size=9, color=GREEN)
    x = W - LM - len(NAV) * 2.3
    for item in NAV:
        on = item == active
        text(s, x, 0.8, 2.3, 0.5, [[(item, {"color": INK if on else GREY, "bold": on})]],
             size=8.5, align=PP_ALIGN.RIGHT)
        x += 2.3
    if title:
        text(s, title_x, 2.35, W - title_x - LM, 1.3, title, size=25, color=INK)
    fc = WHITE if footer_white else GREY
    text(s, LM, 20.0, 12, 0.4, "Deneb · Polaris 투자전략 보고서 — 시안", size=7.5, color=fc)
    text(s, W - LM - 2, 20.0, 2, 0.4, str(page), size=7.5, color=GREY, align=PP_ALIGN.RIGHT)
    return s


def chart_head(slide, x, y, w, title, sub):
    hline(slide, x, y, w, INK, 0.75)
    text(slide, x, y + 0.2, w, 0.5, title, size=10, bold=True)
    text(slide, x, y + 0.72, w, 0.5, sub, size=8.5, color=GREY)


# ── 표 ───────────────────────────────────────────────────────────────────────
def _border(cell, side, color: RGBColor | None, width=0.75):
    tcpr = cell._tc.get_or_add_tcPr()
    tag = {"L": "a:lnL", "R": "a:lnR", "T": "a:lnT", "B": "a:lnB"}[side]
    old = tcpr.find(qn(tag))
    if old is not None:
        tcpr.remove(old)
    ln = etree.SubElement(tcpr, qn(tag))
    if color is None:
        ln.set("w", "0")
        etree.SubElement(ln, qn("a:noFill"))
    else:
        ln.set("w", str(int(Pt(width))))
        sf = etree.SubElement(ln, qn("a:solidFill"))
        c = etree.SubElement(sf, qn("a:srgbClr"))
        c.set("val", str(color))
    # 스키마 순서: lnL lnR lnT lnB … 채우기
    order = ["a:lnL", "a:lnR", "a:lnT", "a:lnB"]
    els = {t: tcpr.find(qn(t)) for t in order}
    for t in order:
        if els[t] is not None:
            tcpr.remove(els[t])
    for i, t in enumerate(order):
        if els[t] is not None:
            tcpr.insert(i, els[t])


def table(slide, x, y, widths, rows, *, head_size=9, size=9, row_h=0.9, head_h=0.8,
          first_bold=True, aligns=None, fills=None, colors=None):
    nr, nc = len(rows), len(widths)
    gs = slide.shapes.add_table(nr, nc, Cm(x), Cm(y), Cm(sum(widths)), Cm(head_h + row_h * (nr - 1)))
    tbl = gs.table
    tblpr = tbl._tbl.tblPr
    for a in ("firstRow", "bandRow"):
        tblpr.set(a, "0")
    sid = tblpr.find(qn("a:tableStyleId"))
    if sid is not None:
        sid.text = "{5940675A-B579-460E-94D1-54222C63F5DA}"  # No Style, Table Grid → 테두리는 아래서 지움
    for j, w in enumerate(widths):
        tbl.columns[j].width = Cm(w)
    for i, row in enumerate(rows):
        tbl.rows[i].height = Cm(head_h if i == 0 else row_h)
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.margin_left = Cm(0.15)
            cell.margin_right = Cm(0.15)
            cell.margin_top = Cm(0.08)
            cell.margin_bottom = Cm(0.08)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE if i == 0 else MSO_ANCHOR.TOP
            cell.fill.solid()
            fill = (fills or {}).get((i, j), WHITE)
            cell.fill.fore_color.rgb = fill
            tf = cell.text_frame
            tf.word_wrap = True
            lines = val if isinstance(val, list) else [val]
            for k, ln in enumerate(lines):
                p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
                p.line_spacing = 1.15
                p._p.get_or_add_pPr().set("latinLnBrk", "0")
                al = (aligns or [PP_ALIGN.LEFT] * nc)[j]
                p.alignment = al
                r = p.add_run()
                r.text = ln
                f = r.font
                f.name = FONT
                f.size = Pt(head_size if i == 0 else size)
                f.bold = i == 0 or (first_bold and j == 0)
                col = (colors or {}).get((i, j))
                f.color.rgb = col if col is not None else (GREEN if i == 0 else INK)
                rpr = r._r.get_or_add_rPr()
                rpr.set("lang", "ko-KR")
                rpr.set("altLang", "en-US")
                ea = etree.SubElement(rpr, qn("a:ea"))
                ea.set("typeface", FONT)
            _border(cell, "L", None)
            _border(cell, "R", None)
            _border(cell, "T", INK if i == 0 else None, 0.75)
            _border(cell, "B", GREEN if i == 0 else RULE, 1.0 if i == 0 else 0.5)
    return tbl


# ── 차트 ─────────────────────────────────────────────────────────────────────
def _skip(chart, n: int) -> None:
    cat = chart.category_axis._element
    for tag in ("c:tickLblSkip", "c:tickMarkSkip"):
        old = cat.find(qn(tag))
        if old is not None:
            cat.remove(old)
    anchor = cat.find(qn("c:noMultiLvlLbl"))
    for tag in ("c:tickLblSkip", "c:tickMarkSkip"):
        el = etree.Element(qn(tag))
        el.set("val", str(n))
        if anchor is not None:
            anchor.addprevious(el)
        else:
            cat.append(el)


def _style_axes(chart, size=8, vfmt="#,##0", vmin=None, vmax=None, major=None):
    chart.font.name = FONT
    chart.font.size = Pt(size)
    chart.font.color.rgb = GREY
    va = chart.value_axis
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = RULE
    va.major_gridlines.format.line.width = Pt(0.5)
    va.format.line.fill.background()
    va.tick_labels.number_format = vfmt
    va.tick_labels.number_format_is_linked = False
    va.tick_labels.font.size = Pt(size)
    if vmin is not None:
        va.minimum_scale = vmin
    if vmax is not None:
        va.maximum_scale = vmax
    if major is not None:
        va.major_unit = major
    ca = chart.category_axis
    ca.format.line.color.rgb = INK
    ca.format.line.width = Pt(0.75)
    ca.tick_labels.font.size = Pt(size)
    ca.has_major_gridlines = False
    # 한글 글꼴
    for tx in chart._chartSpace.iter(qn("a:defRPr")):
        tx.set("lang", "ko-KR")
        for tag in ("a:latin", "a:ea"):
            el = tx.find(qn(tag))
            if el is None:
                el = etree.SubElement(tx, qn(tag))
            el.set("typeface", FONT)


def line_chart(slide, x, y, w, h, months, series, *, vfmt="#,##0", vmin=None, vmax=None,
               major=None, legend=True, skip=24):
    cats = [m[:4] if m.endswith("-01") else m for m in months]
    cd = CategoryChartData()
    cd.categories = cats
    for name, vals, *_ in series:
        cd.add_series(name, vals)
    gf = slide.shapes.add_chart(XL_CHART_TYPE.LINE, Cm(x), Cm(y), Cm(w), Cm(h), cd)
    ch = gf.chart
    _style_axes(ch, vfmt=vfmt, vmin=vmin, vmax=vmax, major=major)
    _skip(ch, skip)
    for s, (_, _, color, width, dash) in zip(ch.series, series, strict=True):
        s.smooth = False

        s.format.line.color.rgb = color
        s.format.line.width = Pt(width)
        if dash:
            s.format.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        mk = s._element.find(qn("c:marker"))
        if mk is None:
            mk = etree.SubElement(s._element, qn("c:marker"))
        for c in list(mk):
            mk.remove(c)
        sym = etree.SubElement(mk, qn("c:symbol"))
        sym.set("val", "none")
    ch.has_legend = legend
    if legend:
        ch.legend.position = XL_LEGEND_POSITION.TOP
        ch.legend.include_in_layout = False
        ch.legend.font.size = Pt(8.5)
        ch.legend.font.color.rgb = INK
    return ch


def bar_chart(slide, x, y, w, h, cats, series, *, vmin, vmax, major, vfmt='0%;"−"0%'):
    cd = CategoryChartData()
    cd.categories = cats
    for name, vals, _ in series:
        cd.add_series(name, vals)
    gf = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Cm(x), Cm(y), Cm(w), Cm(h), cd)
    ch = gf.chart
    _style_axes(ch, vfmt=vfmt, vmin=vmin, vmax=vmax, major=major)
    ch.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    ch.plots[0].gap_width = 60
    ch.plots[0].overlap = -10
    for s, (_, _, color) in zip(ch.series, series, strict=True):
        s.invert_if_negative = False
        s.format.fill.solid()
        s.format.fill.fore_color.rgb = color
        s.data_labels.show_value = True
        s.data_labels.number_format = '0%;"−"0%'
        s.data_labels.number_format_is_linked = False
        s.data_labels.font.size = Pt(7.5)
        s.data_labels.font.color.rgb = INK
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.TOP
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(8)
    return ch


# ── 쪽 ──────────────────────────────────────────────────────────────────────
def page1(prs):
    s = frame(prs, 1, "개요", title="전략 개요", title_x=10.9)
    rect(s, 0, 1.75, 9.6, H - 1.75, GREEN)
    text(s, 1.3, 2.6, 7.2, 14, [
        "Deneb은 나스닥 100의 3배 상품(TQQQ)과 미국 배당주(SCHD)를 섞어 높은 성장을 노린다. 크게 빠지면 TQQQ를 팔고 달러로 기다린다.",
        "Polaris는 미국 주식·국채·금을 30·30·20·20으로 나눠 든다. 새 돈으로 모자란 쪽을 사고, 1년에 한 번 비율을 맞춘다.",
        "두 전략은 증권사와 계좌가 다르다. Deneb은 토스증권, Polaris는 나무증권에서 따로 운용한다.",
    ], size=13.5, color=WHITE, spacing=1.3, space_after=14)
    text(s, LM, 20.0, 8, 0.4, "Deneb · Polaris 투자전략 보고서 — 시안", size=7.5, color=WHITE)
    rows = [
        ["", "Deneb", "Polaris"],
        ["목적", "높은 장기 성장. 긴 하락의 일부는 피한다", "낮은 낙폭으로 꾸준히 쌓는다"],
        ["보유 자산", "TQQQ(나스닥 100 하루 수익의 3배), SCHD(미국 배당주). 하락 매도 뒤에는 달러",
         "SPYM(S&P 500) 30, QQQM(나스닥 100) 30, IEF(미 국채 7~10년) 20, GLDM(금) 20"],
        ["매달 입금", "TQQQ 목표 비중까지 채우고 나머지는 SCHD", "목표보다 모자란 종목부터 정수 주로 산다"],
        ["비중 조정", "매달. 양쪽을 사고팔아 목표로. TQQQ 목표 = 60% × 변동성 조정",
         "매년 12월 한 번. 목표보다 1주 넘게 많은 종목을 팔고 모자란 종목을 산다"],
        ["매도", "매일 확인. TQQQ가 고점보다 15% 빠지면 절반, 30% 빠지면 전부", "12월 비율 맞추기 말고는 자동 매도·손절 없음"],
        ["재매수", "나스닥 추세가 꺼졌다가 다시 켜진 날, 보관한 달러 전부로 TQQQ", "따로 없음 (하락에 팔지 않으므로)"],
        ["주요 위험", "3배 상품의 큰 낙폭. 늦은 복귀로 반등을 놓침. 나스닥 한 곳에 기댐",
         "주식·채권이 함께 빠지는 해(2022). 낮은 성장률"],
    ]
    table(s, 10.9, 4.3, [2.6, 7.2, 7.2], rows, head_size=10.5, size=9.5, row_h=1.5, head_h=0.9)


def page2(prs):
    s = frame(prs, 2, "조건", title="공통 비교 조건")
    text(s, LM, 3.85, 17, 1.2,
         "모든 전략에 같은 돈을 같은 날 넣는다. 그래야 결과의 차이가 규칙의 차이로만 남는다. 이 시안의 숫자는 모두 아래 조건을 따른 과거 모의 계산이다.",
         size=11, spacing=1.3)
    a = R["models"]["Deneb"]["at"]["end"]
    rows = [
        ["항목", "이 보고서의 가정", "항목", "이 보고서의 가정"],
        ["월 납입액", "20만 원 (본문 대표값)", "비용", "사고팔 때마다 거래 금액의 0.15%. 모든 전략 같음"],
        ["기간", f"2012-01 ~ 2026-08, 176개월. 원금 {man(a['paid'])}만 원", "세금", "세전. 양도세·배당세·이자세는 넣지 않았다"],
        ["납입일", "매달 첫 거래일. 그날 종가에 산다", "환율", "고정. 환전 비용 없음"],
        ["배당·분배금", "수정 종가에 이미 포함(재투자). 따로 더하지 않는다", "소수 주", "허용 (실제 운용은 정수 주)"],
        ["자료", "실제 ETF 일간 종가만. Polaris의 SPYM·QQQM·GLDM은 같은 지수의 SPY·QQQ·GLD로 대신",
         "적금", "1년 만기 단리 적금. 만기금은 같은 금리 예금으로 매년 이월. 세전"],
    ]
    table(s, LM, 5.6, [2.8, 10.45, 2.8, 10.45], rows, size=9.5, row_h=1.15, head_h=0.8)
    boxes = [
        ("과거 모의 계산", "지난 가격에 현행 규칙을 그대로 적용한 결과. 실제 주문·체결이 아니다. 이 시안의 숫자는 모두 여기에 속한다."),
        ("미래 시나리오", "과거 수익을 다시 뽑아 만든 범위. 방법과 가정은 결과를 보기 전에 고정한다. 이 시안에는 아직 없다."),
        ("실제 운용 기록", "Deneb 실거래는 2026년 10월에 시작해 기록이 짧다. Polaris는 2026년 11월 시작 예정이다. 성과 숫자에 섞지 않는다."),
    ]
    bw = (CW - 2 * 0.6) / 3
    for i, (t, b) in enumerate(boxes):
        x = LM + i * (bw + 0.6)
        rect(s, x, 13.3, bw, 3.9, GREEN_SOFT)
        vline(s, x, 13.3, 3.9, GREEN, 3)
        text(s, x + 0.6, 13.7, bw - 1.0, 0.6, t, size=11.5, bold=True, color=GREEN)
        text(s, x + 0.6, 14.6, bw - 1.0, 2.6, b, size=10, spacing=1.35)


def page3(prs):
    s = frame(prs, 3, "Deneb", title="Deneb의 TQQQ 비중")
    d = M["Deneb"]["monthly"]
    chart_head(s, LM, 3.9, CW, "Deneb 계좌에서 TQQQ와 보관 달러가 차지한 비중",
               "2012-01 ~ 2026-08 · 매달 20만 원 · 월말 · 계좌 평가액 대비 % · 나머지는 SCHD · 과거 모의 계산")
    line_chart(s, LM, 5.1, CW, 7.3, d["month"], [
        ("TQQQ 비중", d["tqqq_share"], C_DENEB, 2.0, False),
        ("보관 달러 비중", d["cash_share"], C_CASH, 1.5, False),
        ("TQQQ 기본 목표 60%", [0.6] * len(d["month"]), INK, 0.75, True),
    ], vfmt="0%", vmin=0, vmax=0.8, major=0.2)
    ls = d["tqqq_share"]
    zero = sum(1 for v in ls if v < 0.05)
    avg = sum(ls) / len(ls)
    cw3 = (CW - 2 * 0.9) / 3
    x1, x2, x3 = LM, LM + cw3 + 0.9, LM + 2 * (cw3 + 0.9)
    text(s, x1, 13.0, cw3, 0.6, "두 자산의 역할", size=11.5, bold=True, color=GREEN)
    text(s, x1, 13.8, cw3, 6, [
        "TQQQ는 나스닥 100의 하루 수익을 3배로 따라간다. 계좌의 성장은 주로 여기서 나온다.",
        "SCHD는 나머지를 채우는 미국 배당주다. 다만 SCHD도 주식이라 2020년 같은 폭락에서는 함께 빠진다. 큰 하락을 줄이는 일은 주로 TQQQ 비중 축소와 하락 매도가 맡는다.",
    ], size=9.5, spacing=1.35, space_after=6)
    text(s, x2, 13.0, cw3, 0.6, "TQQQ 목표가 바뀌는 규칙", size=11.5, bold=True, color=GREEN)
    table(s, x2, 13.85, [4.6, cw3 - 4.6], [
        ["상태", "TQQQ 목표"],
        ["나스닥 변동성 연 20% 이하", "60%"],
        ["변동성 30%", "40%"],
        ["변동성 40%", "30%"],
        ["TQQQ 고점 −15% (1단계)", "가진 TQQQ 절반 매도"],
        ["TQQQ 고점 −30% (2단계)", "0%, 판 돈은 달러 보관"],
    ], size=9, head_size=9, row_h=0.78, head_h=0.7, first_bold=False)
    text(s, x3, 13.0, cw3, 0.6, "그래프에서 보이는 것", size=11.5, bold=True, color=GREEN)
    text(s, x3, 13.8, cw3, 6, [
        f"TQQQ 비중은 60%에 머문 기간이 길지만, 평균은 {pct(avg, 0)}였다. 176개월 중 {zero}개월은 월말 TQQQ가 거의 0이었다.",
        "2022년에는 대부분의 달에 TQQQ를 모두 팔고 계좌의 30~40%를 달러로 들고 있었다. 실제 비중은 시장 상태에 따라 크게 움직인다.",
    ], size=9.5, spacing=1.35, space_after=6)


def page4(prs):
    s = frame(prs, 4, "Deneb", title="Deneb의 손실 방어와 복귀")
    names = [("Deneb", "Deneb (변동성 맞춤 + 하락 매도·복귀)", C_DENEB, 2.25),
             ("변동성 맞춤만", "변동성 맞춤만 (하락 매도 없음)", C_VOL, 1.5),
             ("고정 60/40", "고정 60/40 (TQQQ 60 · SCHD 40, 매달 맞춤)", C_FIXED, 1.5)]
    d0 = M["Deneb"]["monthly"]
    lw = 17.2
    chart_head(s, LM, 3.9, lw, "매달 20만 원을 넣은 계좌 평가액 — 세 가지 규칙",
               "2012-01 ~ 2026-08 · 월말 · 단위 만 원 · 세전 · 비용 0.15% · 과거 모의 계산")
    ser = [(lab, [v / 1e4 for v in M[k]["monthly"]["value"]], c, w, False) for k, lab, c, w in names]
    ser.append(("납입 원금 누계", [v / 1e4 for v in d0["paid"]], INK, 1.0, True))
    line_chart(s, LM, 5.1, lw, 8.1, d0["month"], ser, vmin=0, vmax=80000, major=20000)
    rx = LM + lw + 0.8
    rw = CW - lw - 0.8
    chart_head(s, rx, 3.9, rw, "위기 구간 최대 낙폭",
               "구간 안 고점 대비 · 단위 가치 기준 · %")
    crises = list(M["Deneb"]["crises"])
    bar_chart(s, rx, 5.1, rw, 8.1, crises,
              [(lab.split(" (")[0], [M[k]["crises"][c] for c in crises], col) for k, lab, col, _ in names],
              vmin=-0.7, vmax=0, major=0.1)
    rows = [["규칙", "납입 원금", "최종 평가액", "이익", "최대 낙폭", "고점 회복까지", "매도 횟수"]]
    for k, lab, _, _ in names:
        x = M[k]
        a = x["at"]["end"]
        rec = f"{x['recovery_months_from_peak']}개월" if x["recovered"] else "미회복"
        rows.append([lab, f"{man(a['paid'])}만", f"{man(a['value'])}만", f"{man(a['profit'])}만",
                     pct(x["mdd"]), rec, f"{x['sells']}번"])
    R4 = PP_ALIGN.RIGHT
    table(s, LM, 13.75, [9.0, 2.6, 3.0, 3.0, 2.6, 3.3, 3.0], rows, size=9, row_h=0.62, head_h=0.65,
          aligns=[PP_ALIGN.LEFT, R4, R4, R4, R4, R4, R4])
    vline(s, LM, 16.9, 2.0, GREEN, 3)
    text(s, LM + 0.5, 16.85, CW - 0.5, 2.8, [
        "이 기간에는 하락 매도가 낙폭을 −52%에서 −32%로 줄이면서 최종 금액도 늘렸다. 2020년과 2022년의 큰 하락을 상당 부분 피했기 때문이다.",
        "대가도 있었다. 반등이 빠른 해에는 덜 벌었다(2013년 연 수익 52% vs 하락 매도 없음 91%, 연구 184). 1986년부터의 합성 자료에서는 하락 매도가 세후 수익을 오히려 낮췄다(연구 184). 이 우위는 지난 15년에 기댄 결과다.",
    ], size=9.5, spacing=1.3, space_after=4)


def page5(prs):
    s = frame(prs, 5, "Polaris", title="Polaris의 채권·금 관리")
    text(s, LM, 3.85, CW, 1.2,
         "Polaris에는 채권·금만을 위한 익절·손절 규칙이 없다. 채권과 금은 매년 12월 비율 맞추기 때만 팔린다. 그때도 목표보다 많은 만큼만 판다. 12월 맞추기는 텔레그램 버튼을 눌러야 실행되며, 미국 정규장에 지정가로 주문한다.",
         size=10.5, spacing=1.3)
    rows = [
        ["상황", "현행 규칙", "실행되는 행동", "위험"],
        ["새 돈이 들어올 때", "모자란 종목부터 산다. 팔지 않는다", "목표 비율에 가장 모자란 종목을 정수 주로 산다",
         "사기만 하면 주식 비중이 점점 커진다(12월 맞춤이 없으면 82~90%까지, 참고 계산)"],
        ["채권·금 값이 올라 비중이 커졌을 때", "고정 수익률 익절 없음", "12월에 목표보다 1주 넘게 많은 만큼만 판다",
         "12월 전에 값이 다시 내리면 그 이익은 줄어든다"],
        ["주식이 빠져 채권·금 비중이 커졌을 때", "12월 비율 맞추기", "넘친 채권·금을 팔고, 그 달러로 모자란 주식을 산다",
         "주식이 더 빠지면 새로 산 몫도 함께 손실"],
        ["채권·금 자체가 빠졌을 때", "손절·추가 매수 규칙 없음", "새 돈이 모자란 쪽으로 먼저 간다. 12월에 모자란 만큼 산다",
         "2022년처럼 주식과 채권이 함께 빠지면 분산 효과가 작다"],
        ["계좌 전체가 크게 빠졌을 때", "자동 매도 없음", "그대로 들고 다음 매수를 이어간다",
         f"회복이 오래 걸릴 수 있다(2022년 고점 회복까지 {M['Polaris']['recovery_months_from_peak']}개월, 이 계산)"],
        ["분배금·배당이 들어올 때", "현행 코드에 재투자 규칙 없음", "달러로 계좌에 남는다",
         "과거 계산은 재투자를 가정했다. 실제와 차이가 난다 (확인 필요)"],
    ]
    table(s, LM, 5.35, [5.0, 5.2, 7.6, 8.7], rows, size=9, row_h=1.08, head_h=0.75)
    # 12 월 전후 예시 (2025-12, 계산 기록)
    t = next(x for x in M["Polaris"]["trades"] if x["day"].startswith("2025-12"))
    by = t["before"]
    hline(s, LM, 13.9, CW, INK, 0.75)
    text(s, LM, 14.1, 9.5, 0.5, "12월 맞추기 전후 예시", size=10, bold=True)
    text(s, LM, 14.65, 9.5, 0.5, "2025-12-01 · 과거 모의 계산 · 계좌 대비 %", size=8.5, color=GREY)
    text(s, LM, 15.5, 9.3, 4, [
        f"그해 금값이 크게 올라 금 비중이 {pct(by['GLD'], 0)}까지 커졌다. 12월 맞추기에서 넘친 금 {man(t['sell']['GLD'])}만 원어치를 팔고, 그 돈으로 모자란 주식과 국채를 샀다.",
        "실제 프로그램은 정수 주로, 1주 넘게 넘친 종목만 판다. 그래서 결과는 이 예시와 조금 다르다.",
    ], size=9.5, spacing=1.3, space_after=4)
    cats = ["맞추기 전", "맞춘 뒤"]
    syms = [("SPY", "S&P 500", RGBColor(0x17, 0x4D, 0x3B)), ("QQQ", "나스닥 100", RGBColor(0x4F, 0x9D, 0x78)),
            ("IEF", "미 국채", C_VOL), ("GLD", "금", C_POLARIS)]
    cd = CategoryChartData()
    cd.categories = cats
    tgt = {"SPY": 0.3, "QQQ": 0.3, "IEF": 0.2, "GLD": 0.2}
    for sym, lab, _ in syms:
        cd.add_series(lab, [by[sym], tgt[sym]])
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_STACKED_100, Cm(LM + 10.2), Cm(14.15), Cm(CW - 10.2), Cm(5.4), cd)
    ch = gf.chart
    _style_axes(ch, vfmt="0%")
    ch.value_axis.has_major_gridlines = False
    ch.value_axis.visible = False
    ch.category_axis.reverse_order = True
    ch.plots[0].gap_width = 45
    for sr, (_, _, col) in zip(ch.series, syms, strict=True):
        sr.format.fill.solid()
        sr.format.fill.fore_color.rgb = col
        sr.data_labels.show_value = True
        sr.data_labels.number_format = '0%'
        sr.data_labels.number_format_is_linked = False
        sr.data_labels.font.size = Pt(8.5)
        sr.data_labels.font.color.rgb = WHITE
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.TOP
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(8.5)


def page6(prs):
    s = frame(prs, 6, "비교", title="적금과 두 전략")
    text(s, LM, 3.85, CW, 1.2,
         "같은 날 같은 돈을 넣었을 때, 2012~2026년에는 두 전략 모두 적금보다 많이 남았다. 대신 적금에는 없는 중간 손실을 견뎌야 했다. 이 기간은 미국 주식, 특히 나스닥이 강했던 때다.",
         size=10.5, spacing=1.3)
    mo = M["Polaris"]["monthly"]["month"]
    paid = [v / 1e4 for v in M["Polaris"]["monthly"]["paid"]]
    s3 = [v / 1e4 for v in M["적금 3%"]["monthly"]["value"]]
    s4 = [v / 1e4 for v in M["적금 4%"]["monthly"]["value"]]
    hw = (CW - 0.9) / 2
    chart_head(s, LM, 5.2, hw, "Polaris와 적금 — 매달 20만 원의 평가액",
               "2012-01 ~ 2026-08 · 월말 · 단위 만 원 · 세전 · 과거 모의 계산")
    line_chart(s, LM, 6.4, hw, 6.6, mo, [
        ("Polaris", [v / 1e4 for v in M["Polaris"]["monthly"]["value"]], C_POLARIS, 2.0, False),
        ("적금 4%", s4, C_SAV4, 1.5, False), ("적금 3%", s3, C_SAV3, 1.5, False),
        ("납입 원금", paid, INK, 1.0, True)], vmin=0, vmax=12000, major=3000)
    x2 = LM + hw + 0.9
    chart_head(s, x2, 5.2, hw, "Deneb과 적금 — 매달 20만 원의 평가액",
               "2012-01 ~ 2026-08 · 월말 · 단위 만 원 · 세전 · 과거 모의 계산 · 왼쪽과 세로축 다름")
    line_chart(s, x2, 6.4, hw, 6.6, mo, [
        ("Deneb", [v / 1e4 for v in M["Deneb"]["monthly"]["value"]], C_DENEB, 2.0, False),
        ("적금 4%", s4, C_SAV4, 1.5, False), ("적금 3%", s3, C_SAV3, 1.5, False),
        ("납입 원금", paid, INK, 1.0, True)], vmin=0, vmax=80000, major=20000)
    rows = [["시점", "납입 원금", "적금 3%", "적금 4%", "Polaris", "Deneb", "Polaris − 적금 4%", "Deneb − 적금 4%"]]
    lab = {"5y": "5년 (2016-12)", "10y": "10년 (2021-12)", "end": "14년 8개월 (2026-08)"}
    for k in ("5y", "10y", "end"):
        p, d = M["Polaris"]["at"][k], M["Deneb"]["at"][k]
        a3, a4 = M["적금 3%"]["at"][k], M["적금 4%"]["at"][k]
        rows.append([lab[k], f"{man(p['paid'])}만", f"{man(a3['value'])}만", f"{man(a4['value'])}만",
                     f"{man(p['value'])}만", f"{man(d['value'])}만",
                     f"{(p['value'] - a4['value']) / 1e4:+,.0f}만".replace("-", "−"),
                     f"{(d['value'] - a4['value']) / 1e4:+,.0f}만".replace("-", "−")])
    rows.append(["가장 큰 중간 낙폭", "—", "없음", "없음", pct(M["Polaris"]["mdd"]), pct(M["Deneb"]["mdd"]), "", ""])
    R6 = PP_ALIGN.RIGHT
    table(s, LM, 13.6, [5.0, 2.7, 2.7, 2.7, 2.9, 2.9, 3.8, 3.8], rows, size=9, row_h=0.62, head_h=0.65,
          aligns=[PP_ALIGN.LEFT] + [R6] * 7)
    text(s, LM, 17.4, CW, 2.3, [
        "적금은 매달 넣은 돈을 1년 만기 적금으로 굴리고, 만기금은 같은 금리 예금으로 매년 다시 맡긴다고 가정했다. 이자세 15.4%와 중도 해지 금리는 넣지 않았다. 투자 쪽도 세전이라 두 쪽 모두 실제 받는 돈보다 크게 나온다.",
    ], size=9.5, spacing=1.3)


def main() -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Cm(W), Cm(H)
    for fn in (page1, page2, page3, page4, page5, page6):
        fn(prs)
    prs.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
