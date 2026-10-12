"""포트폴리오 보고서 만들기 — HTML(A4) + Edge 로 PDF.

python -m scripts.portfolio.build_report [--no-pdf]
결과: reports/portfolio/portfolio.html · portfolio.pdf (reports/ 는 git 밖 — 화면에 실제 금액이 보인다)
"""

from __future__ import annotations

import argparse
import base64
import io
import subprocess
import sys
import tempfile
import time
from collections import Counter
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from scripts.portfolio import (
    cards_alloc,
    cards_carry,
    cards_event,
    cards_index,
    cards_pattern,
    cards_relative,
    cards_short,
    cards_stock,
    cards_trend,
)
from scripts.portfolio import figures as F
from scripts.portfolio.cards_base import FAMILIES, VERDICTS, Card

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "reports" / "report_assets"
OUT = ROOT / "reports" / "portfolio"
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")

CARDS: list[Card] = (
    cards_trend.CARDS
    + cards_short.CARDS
    + cards_pattern.CARDS
    + cards_relative.CARDS
    + cards_carry.CARDS
    + cards_event.CARDS
    + cards_stock.CARDS
    + cards_index.CARDS
    + cards_alloc.CARDS
)

FAMILY_NOTE = {
    "trend": "BTC · ETH를 몇 달 단위 추세로 들고 있는 규칙과, 그 위에 선물 노출을 더하는 변형이다. 이 프로젝트에서 "
    "통과가 나온 거의 유일한 부류이며, 이후의 개선 시도(낙폭 조절 · 변동성 차단 · 실행 시점)는 대부분 "
    "원래 규칙을 넘지 못했다.",
    "short": "몇 분에서 하루 사이의 방향을 맞히려는 시도다. 방향 정보가 있는 경우에도 비용 전 우위가 거래당 "
    "+0 ~ 7bp에 머물러, 왕복 비용 약 19bp를 넘은 신호가 하나도 없었다.",
    "pattern": "차트 패턴, 보조지표, 엘리엇 파동을 기계 규칙으로 옮겨 시험하였다. 합성 자료에서는 읽혀도 실제 "
    "시장에서는 비용을 넘는 정보가 없었고, 생존 편향과 과최적화가 좋아 보이는 결과를 만든 사례가 "
    "여럿 나왔다.",
    "relative": "숏 다리, 롱숏, 상대 강도처럼 하락에서도 벌려는 시도다. 코인 숏은 장기 상승과 funding 때문에 거의 "
    "항상 손실이었고, 낮은 상관만으로는 포트폴리오가 나아지지 않았다.",
    "carry": "방향을 맞히지 않는 구조적 수익원이다. 수익원은 실재했지만 안전한 레버리지에서 연 1 ~ 5% 수준이었고, "
    "그리드 계열은 측정 단계에서 설 자리가 없다는 결론을 냈다.",
    "event": "자금 흐름과 공개 이벤트를 노린 연구다. 효과가 실재해도 살 수 있는 시점에는 이미 반영되어 있거나, "
    "자료가 당시 알 수 없던 값이라 판정 자체를 하지 않은 경우가 있었다.",
    "stock": "주도주 · 테마 · 수급으로 종목을 고르는 연구다. 생존 편향을 없애고 다른 시장에서 재현하자, 비교 "
    "대상은 이겨도 지수를 넘는 근거는 남지 않았다.",
    "index": "지수 적립과 레버리지 ETF 설계 연구다. 하락을 피하는 규칙은 대부분 놓친 상승이 피한 손실보다 컸고, "
    "'2008년 뒤 나스닥'에서만 좋은 규칙이 반복해서 나왔다.",
    "alloc": "통과한 전략과 자산을 어떻게 섞을지, 그리고 머신러닝 · LLM이 정보를 더하는지를 다룬다. 섞기는 "
    "낙폭을 줄이는 쪽으로 역사적 지지를 받았고, 모델은 규칙이 이미 쓰는 정보를 넘지 못했다.",
}


# ------------------------------------------------------------------ 이미지
def _img(
    path: Path,
    crop_h: int | None = None,
    width: int = 1440,
    mask: tuple[int, int, int, int] | None = None,
    quality: int = 82,
) -> str:
    im = Image.open(path).convert("RGB")
    if crop_h:
        im = im.crop((0, 0, im.width, min(crop_h, im.height)))
    if mask:
        d = ImageDraw.Draw(im)
        d.rounded_rectangle(mask, radius=8, fill=(238, 243, 251), outline=(200, 212, 232))
        try:
            fnt = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 17)
        except OSError:
            fnt = ImageFont.load_default()
        d.text(
            (mask[0] + 14, (mask[1] + mask[3]) / 2 - 10),
            "https://  (hidden)  .ts.net/live",
            fill=(90, 100, 120),
            font=fnt,
        )
    if im.width > width:
        im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ 번호 붙는 그림 · 표
class Doc:
    def __init__(self) -> None:
        self.fig_n = 0
        self.tbl_n = 0

    def fig(self, body: str, cap: str, cls: str = "") -> str:
        self.fig_n += 1
        return f'<figure class="{cls}">{body}<figcaption><b>그림 {self.fig_n}</b> {escape(cap)}</figcaption></figure>'

    def shot(self, src: str, cap: str, cls: str = "") -> str:
        return self.fig(f'<img src="{src}" alt="{escape(cap)}">', cap, "shot " + cls)

    def table(self, head: list[str], rows: list[list[str]], cap: str, cls: str = "") -> str:
        self.tbl_n += 1
        th = "".join(f"<th>{h}</th>" for h in head)
        tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        return (
            f'<div class="tbl {cls}"><div class="tcap"><b>표 {self.tbl_n}</b> {escape(cap)}</div>'
            f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>"
        )


def pill(v: str) -> str:
    name, col = VERDICTS[v]
    return f'<span class="pill" style="--c:{col}">{name}</span>'


def card_html(c: Card) -> str:
    col = VERDICTS[c.verdict][1]
    return (
        f'<article class="card" style="--c:{col}"><header><span class="key">{escape(c.key)}</span>'
        f'<span class="nm">{escape(c.name)}</span><span class="tags">{pill(c.verdict)}'
        f'<span class="tag">{escape(c.market)} · {escape(c.horizon)}</span>'
        f'<span class="dt">{c.date[5:].replace("-", ".")}</span></span></header>'
        f'<p>{escape(c.p1)}</p><p>{escape(c.p2)}</p><div class="fig">{escape(c.fig)}</div></article>'
    )


def family_head(fam: str) -> str:
    cs = [c for c in CARDS if c.fam == fam]
    cnt = Counter(c.verdict for c in cs)
    chips = " ".join(f'{pill(v)}<span class="n">{cnt[v]}</span>' for v in VERDICTS if cnt[v])
    return (
        f'<div class="famhead"><div class="famtitle"><h3>{escape(FAMILIES[fam])}</h3>'
        f'<span class="famn">{len(cs)}건</span></div><div class="chips">{chips}</div>'
        f"<p>{escape(FAMILY_NOTE[fam])}</p></div>"
    )


# ------------------------------------------------------------------ 본문
def build() -> str:
    d = Doc()
    cnt = Counter(c.verdict for c in CARDS)
    oa = ASSETS / "orbit_app"
    od = ASSETS / "orbit"
    P = []  # 쪽 조각

    # ---------------- 표지
    P.append(f"""
<section class="cover">
  <div class="kicker">개인 연구 · 개발 프로젝트 보고서</div>
  <h1>검증 중심 퀀트 리서치와<br>운영 시스템 「오르빗」</h1>
  <p class="lede">좋아 보이는 전략을 찾는 대신, 전략이 틀렸다는 증거를 먼저 찾도록 설계한 개인 퀀트 연구 · 운영 기록이다.
  가설마다 규칙과 기준을 결과 전에 고정하고 한 번만 판정했으며, 살아남은 규칙만 가상 장부로 운영한다.</p>
  <div class="covergrid">
    <div><span>기간</span>2026년 9월 20일 ~ 10월 12일</div>
    <div><span>역할</span>1인 기획 · 연구 · 개발 · 운영</div>
    <div><span>범위</span>코인 · 미국 주식 · 국내 주식 · 금 · 지수 ETF</div>
    <div><span>기술</span>Python · SQLite · pytest · mypy · 거래소/증권사 API · HTML 대시보드 · 텔레그램 · LLM</div>
  </div>
  <div class="bignums">
    <div><b>223</b><span>연구 문서</span></div>
    <div><b>{len(CARDS)}</b><span>판정 · 파일럿 기록</span></div>
    <div><b>{cnt["pass"] + cnt["hist"]}</b><span>통과 · 역사적 지지</span></div>
    <div><b>2,887</b><span>자동 시험</span></div>
  </div>
</section>""")

    # ---------------- 요약
    P.append(f"""
<section class="page">
<h2 class="nonum">요약</h2>
<div class="summary">
<div><h4>문제의식</h4><p>개인 투자자가 만든 자동매매 전략은 대부분 백테스트에서만 좋다. 같은 자료로 규칙을 고쳐 가며 맞추는
과최적화, 살아남은 종목만 쓰는 생존 편향, 비용과 체결을 낙관하는 가정, 그때 알 수 없던 정보를 쓰는 미래 정보가 주된 원인이다.
이 프로젝트는 전략을 찾기 전에 이 네 가지를 막는 절차와 도구를 먼저 만들었다.</p></div>
<div><h4>접근</h4><p>모든 연구는 결과를 보기 전에 가설 · 자료 · 비용 · 판정 기준을 문서로 고정하고 커밋한 뒤 한 번만 판정하였다.
시작 전에 검정력을 계산해 표본으로 가릴 수 없는 질문은 시험하지 않았고, 기각된 가설은 숫자만 바꿔 되살리지 않았다.
폐지 종목을 포함한 종목군, 완성된 봉만 쓰는 신호, 1분봉 지정가 체결 재생, 보수적 비용으로 판정하였다.</p></div>
<div><h4>결과</h4><p>판정을 받은 수익 가설 약 107개 중 통과는 9개였고, 다중 검정 보정(BH, q 0.10) 뒤에도 남는 것은
BTC · ETH 몇 달 단위 추세 보유와 그 레버리지 변형 3개였다. 몇 분 ~ 하루 단위 신호는 방향 정보가 있어도 비용 전 우위가
+0 ~ 7bp로 왕복 비용 약 19bp를 넘지 못했다. 통과한 규칙은 바로 운영하지 않고 Paper(가상 계좌)로 운영 중이다.</p></div>
<div><h4>시스템</h4><p>Paper 장부 여러 개를 매일 같은 시각에 진행하고, 저장된 장부를 처음부터 다시 계산해 한 글자라도 다르면
멈춘다. 일일 점검 · 텔레그램 알림 · 백업과 함께, 주문 버튼이 없는 읽기 전용 대시보드 「오르빗」으로 상태를 본다.
코드 약 5만 7천 줄, 자동 시험 2,887개가 이 과정을 받친다.</p></div>
</div>
{d.fig(F.system_map(), "전체 구성 — 자료에서 운영 후보까지 단계마다 관문을 두고, 아래의 운영 기반이 모든 단계를 받친다.")}
</section>""")

    # ---------------- 1장
    P.append(f"""
<section class="page">
<h2><span>1</span>문제 정의와 설계 원칙</h2>
<h3>1.1 배경</h3>
<p>백테스트의 수익률은 쉽게 부풀려진다. 매개변수를 수십 개 바꿔 가며 가장 좋은 것을 고르면 우연만으로도 화려한 곡선이 나온다.
지금 살아 있는 종목만으로 과거를 시험하면 사라진 종목의 손실이 빠진다. 시장가 체결과 낮은 수수료를 가정하면 실제로는
남지 않는 몇 bp의 우위가 수익처럼 보인다. 같은 날의 고가 · 저가로 그날 거래를 거르면 미래를 보고 매매한 셈이 된다.
이 프로젝트의 첫 목표는 수익률이 아니라, 이런 착시를 걸러 내는 절차를 갖추는 것이었다.</p>
<h3>1.2 설계 원칙</h3>
{
        d.table(
            ["원칙", "이유", "구현"],
            [
                [
                    "결과 전 규칙 고정 · 판정 1회",
                    "같은 자료로 고쳐 맞추는 과최적화를 막는다",
                    "사전 등록 문서를 커밋한 뒤 판정, 기각 항목에 '재시도 금지' 기록",
                ],
                [
                    "검정력 먼저",
                    "표본이 작으면 통과도 기각도 의미가 없다",
                    "기대 효과 − 비용이 80% 검정력에 잡히지 않으면 시작하지 않음",
                ],
                [
                    "보수적 비용",
                    "작은 우위는 비용 가정 하나로 뒤집힌다",
                    "코인 현물 편도 0.15% · 무기한 0.095% + 실제 funding",
                ],
                [
                    "생존 편향 없는 자료",
                    "살아남은 종목만 쓰면 결과가 부풀려진다",
                    "상장폐지 · 상폐 코인을 포함한 그 시점 종목군",
                ],
                [
                    "지정가만 사용",
                    "체결 비용을 통제하고 급변 때 폭주를 막는다",
                    "가격 한도가 있는 지정가 · 시장가는 비상 정리에만",
                ],
                [
                    "계좌 낙폭 정지",
                    "손실이 커질수록 판단이 흐려진다",
                    "누적 낙폭 30% 정지(자동 해제 없음) · 일 손실 2%",
                ],
                [
                    "LLM은 매매 결정을 하지 않음",
                    "근거를 재현할 수 없는 판단을 배제한다",
                    "LLM은 요약 · 대조 · 분류에만, 매수 · 매도는 규칙 코드",
                ],
                [
                    "장부 매일 재계산",
                    "운영 중 생긴 오류를 늦게 발견하면 되돌릴 수 없다",
                    "저장 장부를 처음부터 다시 계산해 다르면 정지",
                ],
            ],
            "설계 원칙과 그 이유 · 구현",
        )
    }
<h3>1.3 운영 단계</h3>
<p>연구를 통과한 규칙은 바로 돈을 넣지 않는다. 먼저 같은 하루 함수로 Paper 장부를 진행하며 앞으로의 성과를 쌓고,
그다음 주문 없이 실계좌를 읽기만 하는 그림자 운영과 거래소 데모 계정에서 실제 체결 비용을 잰다. 각 단계의 기간과
중단 기준은 시작 전에 문서로 정하며, 다음 단계로 넘어가는 결정은 사람이 내린다(그림 1).</p>
</section>""")

    # ---------------- 2장
    qflow = F.hbar(
        [
            ("공개 월 파일에서 의심된 날", 268, "#d48a1c"),
            ("일 파일로 교체해 통과", 233, "#1f8a5b"),
            ("교체 불가로 제외", 35, "#8a8f98"),
        ],
        "건",
        label_w=230,
    )
    P.append(f"""
<section class="page">
<h2><span>2</span>데이터와 검증 기반</h2>
<h3>2.1 자료 원천</h3>
{
        d.table(
            ["시장", "자료", "쓰임"],
            [
                [
                    "코인",
                    "바이낸스 현물 · 무기한 1분봉, funding, 미결제약정, 체결 기록(aggTrades 약 86GB), 원화 거래소 1분봉",
                    "추세 · 단기 · 캐리 · 체결 재생",
                ],
                [
                    "미국 주식",
                    "S&P 500 · 400 일봉, 지수 편입 · 편출 이력, 고배당 포트폴리오 장기 자료",
                    "주도주 · 이벤트 · 배분",
                ],
                [
                    "국내 주식",
                    "상장폐지 포함 일봉, 종목별 투자자 매매(769종목), 공시 재무 4만 8천여 건",
                    "주도주 · 수급 · 패턴 · 실적",
                ],
                [
                    "지수 · 거시",
                    "나스닥 · S&P 1927년 ~, 해외 지수 12개, 금리 · 신용 스프레드, 금 1분봉",
                    "지수 설계 · 다른 시장 검증",
                ],
                [
                    "대체 자료",
                    "현물 BTC ETF 흐름, 온체인 거래소 흐름, 도미넌스, 뉴스 제목",
                    "흐름 · 이벤트 · 정보 봇",
                ],
            ],
            "자료 원천과 쓰임",
        )
    }
<h3>2.2 자료 품질 감사</h3>
<p>자료를 받으면 수익을 계산하기 전에 결함부터 찾는다. 15분 경계 체결 연구에서는 공개 월 단위 체결 파일에서 2천만 줄
잘림, 같은 달의 중복, 빠진 날을 발견하였다. 하루 거래량을 다른 출처와 0.5% 안에서 대조해 의심 날을 골라내고, 일 단위 파일로
바꿔 다시 검사하였다(그림 2). 온체인 연구에서는 무료 자료가 거래소 라벨을 과거로 소급해 다시 계산한 값이라는 것을 인터넷
아카이브의 과거 공개본과 비교해 확인하고, 수익을 계산하지 않은 채 멈췄다.</p>
{
        d.fig(
            qflow,
            "체결 기록 감사 — 하루 거래량 대조로 의심 날 268일을 찾아 233일을 교체하고 35일을 제외하였다.",
        )
    }
<h3>2.3 미래 정보와 생존 편향 막기</h3>
<ul>
<li>신호는 완성된 봉만 쓰고, 주문은 신호 다음 시점 가격으로 넣는다. 일봉으로 당일 체결을 흉내 낼 때 같은 날 고저로 거르지 않는다.</li>
<li>종목군은 매달 그 시점의 거래대금 상위로 다시 정하며, 상장폐지 종목과 폐지 코인을 포함한다. 거래 중단 뒤 재상장한 종목은 이어 붙이지 않는다.</li>
<li>최근 구간(2026년 9월 이후)은 연구에서 열지 않고 앞으로의 검증용으로 남겨 둔다.</li>
<li>판정 자료는 해시로 고정해, 같은 명령이 같은 자료로 같은 판정을 다시 내는지 확인할 수 있게 한다.</li>
</ul>
<h3>2.4 체결 모형</h3>
<p>하루 한 번 재조정하는 전략은 1분봉으로 지정가 체결을 재생한다. post-only 지정가를 넣고 240분 안에 안 채워지면 추격하는
방식(EXEC-L240)의 현물 모형 비용은 약 10.2bp, 데모 계정 실측은 9.99bp였다. 판정은 이보다 높은 보수적 비용과 실제 funding
기록으로 하고, 보호 주문이 닿으면 비상 체결 비용을 더한다.</p>
</section>""")

    # ---------------- 3장
    P.append(f"""
<section class="page">
<h2><span>3</span>연구 방법론</h2>
<h3>3.1 연구 절차</h3>
<p>아이디어는 책, 논문, 커뮤니티, 외부 AI의 제안 등 어디서든 온다. 다만 시험에 들어가기 전에 이미 같은 질문을 했는지,
가진 표본으로 답할 수 있는지를 먼저 묻는다. 통과한 질문만 규칙과 기준을 문서로 고정해 커밋하고, 결과는 한 번만 연다(그림 3).</p>
{
        d.fig(
            F.research_pipeline(),
            "연구 절차 — 사전 등록과 1회 판정이 핵심이며, 앞의 두 관문에서 걸러진 질문은 시험하지 않는다.",
        )
    }
<h3>3.2 결론 범주</h3>
{
        d.table(
            ["결론", "뜻"],
            [
                [
                    pill("pass"),
                    "미리 정한 기준을 모두 넘었다. 그래도 바로 운영하지 않고 Paper로 확인한다.",
                ],
                [
                    pill("hist"),
                    "이미 본 자료에서 기준을 넘었다. 표본 밖 증거가 아니므로 통과보다 약한 결론이다.",
                ],
                [
                    pill("design"),
                    "수익 가설이 아니라 설계 선택지 비교다. 미리 정한 규칙으로 하나를 고른다.",
                ],
                [
                    pill("unclear"),
                    "차이가 우연과 구별되지 않는다. 효과가 없다는 뜻도, 있다는 뜻도 아니다.",
                ],
                [pill("nodata"), "검정력 · 자료 관문을 넘지 못해 수익을 보지 않고 멈췄다."],
                [pill("reject"), "기준에 미달했다. 같은 자료로 숫자만 바꿔 다시 판정하지 않는다."],
            ],
            "결론 범주",
        )
    }
<h3>3.3 검정력과 다중 검정</h3>
<p>사전 등록 전에, 기대 효과에서 비용을 뺀 크기가 가진 표본으로 80% 검정력에 잡히는지 계산한다. 국내 실적 개선 연구는
20종목 집중 포트폴리오의 추적 잡음이 연 15%라 1,069개월이 필요하다는 계산이 나와, 수익을 보지 않고 닫았다. 판정이
쌓인 뒤에는 전체 가설 수로 보정하였다. 약 100개 기준 BH(q 0.10) 보정을 통과한 것은 코인 추세 규칙 두 판과 선물 1.5배
변형뿐이었고, Bonferroni 기준으로는 모두 미달이었다. 이 결과를 숨기지 않고 통과 연구의 한계로 함께 적는다.</p>
<h3>3.4 통계 도구</h3>
<ul>
<li><b>블록 · 군집 부트스트랩</b> — 시계열 자기상관과 같은 날 사건 몰림을 반영한 신뢰구간.</li>
<li><b>두 반쪽 조건</b> — 기간을 둘로 나눠 양쪽 모두에서 같은 부호가 나와야 통과. 한 시기에 몰린 결과를 걸러 낸다.</li>
<li><b>조건 맞춘 대조군</b> — 같은 시장 상승 · 변동성 상태의 다른 시점과 비교해, 신호가 시장 전체 움직임을 다시 센 것인지 가린다.</li>
<li><b>무작위 시점 비교</b> — 같은 종목을 무작위 날에 산 100번과 비교해, 진입 시점 자체의 가치를 잰다.</li>
<li><b>다른 시장 재현</b> — 규칙을 숫자 하나 바꾸지 않고 처음 보는 시장 · 시대에 적용한다.</li>
</ul>
</section>""")

    # ---------------- 4장
    cost = F.hbar(
        [
            ("돌파 실패 반전 (FB)", 7.4, "#5b7bd5"),
            ("청산 급증 반전 (LQ)", 4.0, "#5b7bd5"),
            ("경제지표 반응 (MD)", 5.3, "#5b7bd5"),
            ("지지선 붕괴 숏 (BRS)", 6.0, "#5b7bd5"),
            ("15분 경계 체결 (QFLOW)", 5.9, "#5b7bd5"),
            ("BTC 밤 시간대 (BTC-HOUR)", 1.6, "#5b7bd5"),
            ("현물 − 선물 흐름 (FLOWDIFF)", 0.8, "#5b7bd5"),
            ("급락 바닥권 6시간 (WA)", -4.2, "#8a8f98"),
        ],
        "bp",
        ref=(19.0, "왕복 비용 약 19bp"),
        lo=-8,
        hi=22,
        label_w=210,
    )
    kb = F.hbar(
        [
            ("BTC · ETH 일봉 (살아남은 2종목)", 1.22, "#5b7bd5"),
            ("살아 있는 알트 8개", 0.49, "#7d93cf"),
            ("폐지 포함 상위 20 종목군", 0.04, "#8a8f98"),
            ("같은 종목군, 상위 2% 진입 제외", -0.14, "#c0392b"),
        ],
        "R",
        label_w=250,
    )
    P.append(f"""
<section class="page">
<h2><span>4</span>연구 결과</h2>
<h3>4.1 연구 전체 지도</h3>
<p>3주 동안 {
        len(CARDS)
    }건의 판정과 후향 파일럿을 기록하였다. 시장과 보유 기간으로 나눠 보면 결과는 한쪽에 몰린다.
통과와 역사적 지지는 거의 모두 '몇 달' 열에서 나왔고, '분 · 시간'과 '며칠' 열은 시험이 많았지만 통과가 없다(그림 4).
부류로 보면 코인 추세 보유와 자산 배분에서 통과가 나왔고, 단기 방향 · 패턴 · 숏 · 그리드는 모두 기각되었다(그림 5).</p>
{
        d.fig(
            F.heatmap(CARDS),
            "시장 × 보유 기간 — 칸마다 시험 수와 판정 구성. 통과는 몇 달 단위에 몰려 있다.",
        )
    }
{
        d.fig(
            F.family_bars(CARDS),
            "부류별 판정 구성 — 기각이 다수이며, 통과는 추세 보유 · 배분 · 일부 주식 재현에서만 나왔다.",
        )
    }
</section>
<section class="page">
<h3>4.2 연구 연표</h3>
<p>처음부터 답을 안 것은 아니다. 첫 주에는 몇 시간 단위 방향 예측과 funding 캐리를 탐색했고, 그 끝에 BTC · ETH 추세 보유와
선물 레버리지 변형이 통과하였다. 둘째 · 셋째 주에는 같은 원리가 주식 · 지수 · 배분에서도 통하는지 넓혔고, 이때 생존 편향이
없는 국내 자료와 '다른 시장 재현' 방식을 도입하였다. 마지막에는 판정 대신 계좌 단위 후향 파일럿과 조건 맞춘 대조군을 표준
절차로 삼았다(그림 6).</p>
{
        d.fig(
            F.timeline(CARDS),
            "연구 연표 — 점 하나가 판정 또는 파일럿 하나이며, 색은 결론이다. 위쪽 띠는 방향이 바뀐 구간이다.",
        )
    }
<h3>4.3 살아남은 것</h3>
{
        d.table(
            ["연구", "규칙", "핵심 수치", "한계"],
            [
                [
                    "T",
                    "BTC · ETH 20 · 60 · 120일 추세 보유, 변동성 15% 목표",
                    "샤프 0.95(보유 0.64) · 낙폭 25.4%(보유 87.9%)",
                    "2022년 뒤 샤프 0.46으로 약화",
                ],
                [
                    "O/v1 · O/v2",
                    "추세 비중 × 1.5, 추가분은 선물 · −20% 비상 스톱",
                    "연 33.3% vs 23.4% · 낙폭 23.1%",
                    "샤프는 오르지 않음",
                ],
                [
                    "EXEC-L240",
                    "post-only 지정가, 240분 뒤 추격",
                    "현물 약 10.2bp · 실측 9.99bp",
                    "하루 한 번 재조정 전제",
                ],
                [
                    "QS",
                    "지수 적립, 추세 꺼지면 멈추고 켜지면 3배",
                    "세후 IRR 23.2% vs 19.6%",
                    "3배만 이김 · 다른 지수 12개 중 2곳",
                ],
                ["AG", "소액 공격형 세 계좌", "−70% 정리 0회", "코인 자료 6년"],
                [
                    "ALLOC",
                    "코인 추세 × 나스닥 추세 위험 반반",
                    "뒤 반쪽 샤프 우세",
                    "두 규칙 모두 추세",
                ],
                [
                    "FLOW-KR · KQ",
                    "외국인 + 기관 순매수 주도주",
                    "샤프 0.37 vs 0.12 · 코스닥 재현",
                    "지수를 넘지 못함",
                ],
                [
                    "배분 3건",
                    "추세 추종 20% · 60/20/20 · 낙폭 브레이크",
                    "낙폭 81 → 57 ~ 69%",
                    "이미 본 자료(역사적 지지)",
                ],
            ],
            "통과 · 역사적 지지 연구와 그 한계",
            "small",
        )
    }
<p>남은 것은 대부분 '몇 달 단위로 추세를 들고 있기'와 '섞어서 낙폭 줄이기'다. 추세 추종의 실제 가치는 초과 수익보다
낙폭 축소에 가까웠다. 이 규칙들은 지금 Paper로 운영 중이며, 앞으로의 기록이 쌓이기 전에는 운영 근거로 쓰지 않는다.</p>
</section>""")

    P.append(f"""
<section class="page">
<h3>4.4 기각된 부류와 그 이유</h3>
<p><b>비용 벽.</b> 몇 분 ~ 하루 단위 연구의 비용 전 우위를 한 축에 모으면, 방향이 맞은 경우에도 크기가 +0 ~ 7bp에
머문다. 왕복 비용 약 19bp에 닿은 신호는 없었다(그림 7). 이 그림이 이 프로젝트가 단기 매매를 하지 않는 이유다.</p>
{d.fig(cost, "비용 벽 — 단기 신호의 거래당 비용 전 수익(bp). 붉은 선은 판정에 쓴 왕복 비용이다.")}
<p><b>생존 편향.</b> EMA 정배열 돌파 규칙은 BTC · ETH에서 진입당 +1.22R, 살아 있는 알트 8개에서 +0.49R이었다.
같은 규칙을 폐지 코인을 포함한 종목군에서 처음 보는 자료로 다시 재자 +0.04R로 0과 구별되지 않았다(그림 8).
같은 자료로 비교 기준만 고쳐 통과시켰다면 이 결과를 놓쳤을 것이다.</p>
{d.fig(kb, "생존 편향 — 같은 돌파 규칙의 진입당 평균(R). 종목군을 공정하게 바꾸자 우위가 사라졌다.")}
<p><b>과최적화.</b> 보조지표 · 일목 · 엘리엇 · 캔들 등 27개 요소의 점수 360개 설정 중 학습 구간 최고를 일부러 마음껏
고른 뒤, 봉인한 구간에서 한 번만 시험하였다. 학습 구간의 연 +75 ~ 117%는 봉인 구간에서 연 −20 ~ −32%가 되었다(그림 9).</p>
{d.fig(F.range_bars(), "과최적화 — 고르는 자유도가 크면 학습 성과가 거의 그대로 표본 외 손실이 된다.")}
</section>""")

    P.append(f"""
<section class="page">
<h3>4.5 대표 사례</h3>
<div class="case"><h4>① 논문이 보고한 현상은 실재했지만, 비용을 넘지 못했다 <small>QFLOW</small></h4>
<p>2026년 논문은 15분 경계 첫 10초에 알고리즘 체결이 몰리고, 그 불균형이 이후 수익을 예측한다고 보고하였다.
선물 체결 기록 약 86GB를 받아 경계마다 공격 매수 · 매도 불균형을 계산하고, 주문할 수 있는 1분 뒤에 들어가도록 했다.
8만 4천여 사건의 비용 전 수익은 +5.9bp로 논문과 같은 크기였다. 현상은 재현되었다. 그러나 왕복 비용의 1/3이라 비용 후
−13.2bp였고, 경계를 5분 밀거나 3시간 전체 흐름을 써도 차이가 없었다. 공개 자료의 결함을 찾아 고친 감사(2.2절)도 이 연구에서 나왔다.</p></div>
<div class="case"><h4>② 새 신호가 시장 평균을 다시 센 것인지 가렸다 <small>BREADTH-EXP</small></h4>
<p>상위 20개 코인 중 오른 종목 비율이 4시간 만에 30% 이하에서 80% 이상으로 넓어지면 BTC를 사는 규칙이다. 계좌는 −2,525(1만 기준)였다.
더 중요한 것은 대조군이었다. BTC 상승 · 시장 평균 상승 · 변동성을 맞춘 시점과 비교하자 추가 가치는 +0.10%p로 구간이 0을 포함했고,
오른 종목 비율은 시장 평균 수익과 상관이 0.80이었다. 이후 새 신호는 같은 시점의 단순 가격 대조군을 먼저 넘어야 한다는 원칙이 생겼다.</p></div>
<div class="case"><h4>③ 두 신호가 구조적으로 같은 신호임을 밝혔다 <small>DOMINANCE</small></h4>
<p>스테이블코인 비중 하락 + BTC 비중 상승을 위험 선호 신호로 보는 가설이다. 무료 자료가 없어 한 번 멈췄다가, 유료 차트에서 직접
내보낸 자료로 다시 진행하였다. 결과는 계좌 −1,687이었고, 'TOTAL 상승 + BTC 비중 상승' 신호와 99.5% 일치하였다. USDT 시가총액의
4시간 변화가 전체 시장 변화의 1/50이라, 'USDT 비중 하락'은 수식상 거의 '시장 전체 상승'과 같은 말이었다.</p></div>
<div class="case"><h4>④ 결과가 좋아 보일 때 먼저 엔진을 의심하였다</h4>
{
        d.table(
            ["연구", "처음 결과", "찾은 결함", "고친 뒤"],
            [
                [
                    "V (변동성 돌파)",
                    "거래당 +56.5bp",
                    "같은 날 고저로 거래를 거른 미래 정보",
                    "−0.5bp · 무효 처리",
                ],
                [
                    "BNF-KR (이격 매수)",
                    "가짜 +100% 거래",
                    "1 ~ 2원짜리 수정 종가의 정수 반올림",
                    "가격 하한 추가 · 재판정",
                ],
                [
                    "KB 계열 (돌파)",
                    "+1.22R · +0.49R",
                    "살아남은 종목만 쓴 종목군",
                    "+0.04R · 계열 종료",
                ],
                [
                    "CYCLE-RECLAIM",
                    "가짜 −4.2R",
                    "거래 중단 · 재상장 종목 이어 붙임",
                    "7일 이상 공백에서 끊음 · 결론 같음",
                ],
                ["RESID-REV", "계좌 −435", "진입 전 헤지 수량 검사 누락", "−276 · 결론 같음"],
            ],
            "결함 감사 — 유리하든 불리하든, 숫자가 이상하면 판정 전에 원인을 찾는다",
            "small",
        )
    }
</div>
</section>""")

    traps = [
        ("비용 벽", "며칠 이하 신호의 비용 전 우위는 +0 ~ 7bp라 왕복 19 ~ 24bp를 넘지 못한다."),
        ("숏의 비대칭", "코인 숏 다리는 장기 상승과 funding 때문에 거의 항상 손실이었다."),
        ("생존 편향", "살아남은 코인 · 지금의 지수 종목으로 낸 +는 폐지 포함 자료에서 사라졌다."),
        (
            "시기 쏠림",
            "통과처럼 보인 결과가 한 시기(2019 ~ 20, 2025 ~ 26, V자 회복기)에 몰린 경우가 많았다.",
        ),
        ("승률 ≠ 기대 수익", "승률 75 ~ 97% 전략(갭 메우기 · 분할 매수)은 드문 큰 손실로 진다."),
        ("과최적화", "설정을 많이 탐색하면 학습은 화려하고 표본 밖은 음수다."),
        ("미래 정보", "일봉 고저로 당일 거래를 거르는 실수는 결과를 크게 부풀린다."),
        ("섞기의 함정", "상관이 낮아도 자체 샤프가 없으면 포트폴리오가 나아지지 않는다."),
        ("방어의 대가", "하락을 피하는 규칙은 복귀가 늦어 놓친 상승이 피한 손실보다 컸다."),
        (
            "판독 문제",
            "엘리엇 · 패턴은 합성 자료에선 읽혀도 실제 시장에선 정의대로 나타나는 빈도가 낮다.",
        ),
        (
            "시장 신호의 재표현",
            "도미넌스 · 상승 참여율은 결국 '시장 전체가 올랐나'를 다시 센 것이었다.",
        ),
        ("실재하지만 작은 효과", "공개 현상은 방향 정보가 있어도 +5 ~ 10bp라 비용을 넘지 못한다."),
        (
            "가까운 보호가",
            "신호 봉 저가 같은 가까운 손절은 자주 닿아 비상 체결 비용이 수익보다 커진다.",
        ),
        (
            "실행 차이의 한계",
            "체결 시점 · 지정가 대기 · 연속 비중은 같은 신호의 성과를 거의 바꾸지 않았다.",
        ),
    ]
    P.append(f"""
<section class="page">
<h3>4.6 반복된 함정 14가지</h3>
<p>기각 이유를 모으면 같은 함정이 반복된다. 새 아이디어는 시험 전에 이 목록부터 대조한다.</p>
{d.table(["#", "함정", "내용"], [[str(i + 1), f"<b>{a}</b>", b] for i, (a, b) in enumerate(traps)], "기각에서 반복된 함정", "small")}
</section>""")

    # ---------------- 5장 카탈로그
    cat = []
    for fam in FAMILIES:
        if fam == "ml":
            continue
        cs = [c for c in CARDS if c.fam == fam or (fam == "alloc" and c.fam == "ml")]
        cs.sort(key=lambda c: c.date)
        head = family_head(fam) if fam != "alloc" else family_head_alloc()
        cat.append(f'<div class="fam">{head}{"".join(card_html(c) for c in cs)}</div>')
    P.append(f"""
<section class="page catalog">
<h2><span>5</span>전체 연구 카탈로그</h2>
<p class="intro">판정과 후향 파일럿 {len(CARDS)}건을 부류별로 모두 싣는다. 카드마다 첫 문단은 가설과 시험 방법, 둘째 문단은
결과와 그 이유, 맨 아래 줄은 핵심 숫자다. 왼쪽 띠의 색이 결론이다. 숫자는 각 연구 문서의 판정 기록에서 옮겼다.</p>
<div class="legend">{"".join(pill(v) for v in VERDICTS)}</div>
{"".join(cat)}
</section>""")

    # ---------------- 6장 운영
    P.append(f"""
<section class="page">
<h2><span>6</span>운영 시스템과 위험 관리</h2>
<h3>6.1 하루 운영</h3>
<p>PC 한 대의 예약 작업으로 돈다. 저녁 21시부터 20분 동안 Paper 장부들을 정해진 순서로 하루씩 진행하고, 21시 20분에 일일 점검이
모든 장부를 검사한다. 각 장부는 연구와 같은 하루 함수를 쓰므로, 저장된 장부를 처음부터 다시 계산하면 비트 단위로 같아야 한다.
PC가 꺼져 있어 작업을 놓치면 예정 시각 순서대로 하나씩 다시 실행한다(그림 10).</p>
{d.fig(F.schedule(), "하루 운영 시간표 — 저녁 20분 구간에 Paper 장부와 점검이 몰려 있다.")}
<h3>6.2 위험 장치</h3>
{
        d.table(
            ["장치", "동작"],
            [
                ["계좌 낙폭 정지", "누적 낙폭 30%에서 정지, 자동 해제 없음. 일 손실 2% 한도."],
                ["보호 주문", "선물 추가분에 전날 종가 대비 −20% 비상 스톱을 거래소에 걸어 둔다."],
                ["긴급 정지 파일", "정해진 파일 하나를 만들면 모든 주문 경로가 멈춘다."],
                ["지정가 IOC", "주문은 가격 한도가 있는 지정가. 즉시 체결분만 받고 나머지는 취소."],
                ["장부 재계산 대조", "매일 저장 장부를 처음부터 다시 계산해 다르면 CRITICAL."],
                ["읽기 전용 키", "그림자 운영은 조회 권한만. 키에 다른 권한이 보이면 CRITICAL."],
            ],
            "위험 장치",
        )
    }
<h3>6.3 결함과 수정</h3>
{
        d.table(
            ["증상", "원인", "조치 · 재발 방지"],
            [
                [
                    "놓친 작업을 켜는 순간 한꺼번에 실행 → 점검이 장부보다 먼저 돌아 CRITICAL",
                    "스케줄러의 '가능할 때 시작'이 놓친 작업을 동시에 시작",
                    "예정 시각 순서로 하나씩 다시 실행하는 기능, 점검은 마지막",
                ],
                [
                    "선물 레버리지 상한이 비용 차감 전 자산으로 계산됨",
                    "상한 계산 시점이 비용 반영보다 앞섬",
                    "날짜 경계 이후에만 고친 계산 적용 — 과거 장부의 재계산 일치 유지, 회귀 시험",
                ],
                [
                    "테스트넷 지정가 IOC 체결의 평균가 · 수수료 누락",
                    "주문 응답에 체결 상세가 없는 경우",
                    "체결 기록 조회로 보완, 확인 못 한 수수료는 'UNKNOWN'으로 표시",
                ],
            ],
            "운영 중 발견한 결함 — 증상 · 원인 · 조치",
            "small",
        )
    }
<h3>6.4 알림과 정보 봇</h3>
<p>텔레그램으로 아침 브리핑, 장부별 하루 요약, 점검 결과, 주간 테마 정리를 받는다. 정보 봇은 종목 뉴스를 모아 요약하고 기사마다
호재 · 악재와 그 확률을 붙인다. LLM이 쓴 문장은 표의 숫자와 대조하는 검사를 거치며(5장 JEV 숫자 트랙), AI 의견은 20거래일 뒤
실제 가격으로 채점해 기록한다. 어떤 알림도 주문을 내지 않는다.</p>
{
        d.shot(
            _img(od / "risk.png", 940),
            "예전 대시보드의 위험 · 알림 화면 — 장부별 위험 한도와 일일 점검 기록.",
        )
    }
</section>""")

    # ---------------- 7장 오르빗
    P.append(f"""
<section class="page">
<h2><span>7</span>오르빗 — 운영 대시보드와 UI</h2>
<h3>7.1 설계 목표</h3>
<ul>
<li><b>읽기 전용.</b> 주문 버튼이 없다. 화면은 마지막으로 저장된 보고서만 읽고, 계좌를 실시간으로 조회하지 않는다.</li>
<li><b>이 PC 안에서만.</b> 127.0.0.1에만 열리며, 휴대폰으로 볼 때도 내 기기끼리만 잇는 사설망을 쓰고 보기만 허용한다.</li>
<li><b>꾸미지 않는 숫자.</b> 추정치에는 '추정', 확정되지 않은 수익에는 '확정 수익 아님'을 붙이고, 기록이 없으면 빈칸으로 둔다. 가상 장부와 실제 계좌를 합산하지 않는다.</li>
<li><b>바꾸는 일은 한 번 더 확인.</b> 설정은 바뀌는 값을 확인 창으로 다시 보여 준 뒤 저장되고, 다음 실행부터 적용된다. 연구로 정한 매매 규칙은 잠겨 있다.</li>
</ul>
<h3>7.2 두 세대의 화면</h3>
<p>처음에는 연구 · Paper · 점검을 한 곳에서 보는 운영자용 대시보드를 만들었다. 장부가 늘면서 '지금 내 자산이 어떤가'를 한눈에
보는 화면이 필요해졌고, 이를 위해 하늘 · 구름 테마와 마스코트 '오비'를 쓴 새 화면 「오르빗」을 만들었다. 예전 화면은 Paper와
연구를 깊게 보는 용도로 남겼다.</p>
{d.shot(_img(od / "compare.png", 1120), "예전 대시보드 — 코인 전략 비교. Paper 장부들의 누적 곡선과 백테스트 기준을 한 화면에 겹친다.")}
{d.shot(_img(oa / "home.png", 1300), "오르빗 홈 — 총 자산과 평가 차액, 장부별 카드(보유 종목 · 업데이트 시각 · 오래된 기록 경고), 자산 변화와 최근 거래.")}
<div class="two">
{d.shot(_img(oa / "home-dark.png", 1300, width=1100), "다크 모드 — 우주 테마.")}
{d.shot(_img(oa / "home-hidden.png", 1300, width=1100), "금액 숨기기 — 남에게 화면을 보일 때.")}
</div>
<h3>7.3 화면 읽는 법과 확인 창</h3>
<p>처음 방문하면 각 영역이 무엇을 뜻하는지 안내가 뜬다. 연결 계좌 · 전체 거래 내역 · 화면 읽는 법 · 확인할 사항은 홈을 떠나지
않고 창으로 연다. 전체 거래 내역은 매수 · 매도 · 입금으로 거를 수 있다.</p>
<div class="two">
{d.shot(_img(oa / "pop-accounts.jpg", width=800), "연결 계좌.")}
{d.shot(_img(oa / "pop-all-trades.jpg", width=800), "전체 거래 내역.")}
{d.shot(_img(oa / "pop-guide.jpg", width=800), "화면 읽는 법.")}
{d.shot(_img(oa / "pop-status.jpg", width=800), "확인할 사항.")}
</div>
{d.shot(_img(oa / "onboard.png", 1390), "첫 방문 안내 — 장부 · 평가 차액 · 오래된 기록 표시를 짧게 설명한다.")}
<h3>7.4 설정</h3>
<p>설정은 열 개 탭으로 나뉜다. API 키는 값 대신 '설정됨'만 보이고, 자동 실행 탭은 예약 작업의 마지막 실행과 결과를 보여 준다.
모든 변경은 화면 아래 확인 띠에서 한 번 더 보여 준 뒤 저장된다.</p>
<div class="two">
{d.shot(_img(oa / "settings-keys.png", 820, width=1100), "연결 · 키 — 값은 보이지 않는다.")}
{d.shot(_img(oa / "settings-tasks.png", 820, width=1100), "자동 실행 — 예약 작업 상태.")}
{d.shot(_img(oa / "settings-phone.png", 700, width=1100, mask=(418, 548, 1230, 590)), "휴대폰 — 사설망 연결 순서(주소는 가림).")}
{d.shot(_img(oa / "settings-alerts.png", 740, width=1100), "알림 — 종류별 켜기 · 끄기.")}
{d.shot(_img(oa / "settings-safety.png", 680, width=1100), "안전 · 기록.")}
{d.shot(_img(oa / "settings-screen.png", 680, width=1100), "화면 — 테마 · 금액 숨기기.")}
{d.shot(_img(oa / "settings-glide.png", 940, width=1100), "장부별 설정 — 연구로 정한 규칙은 잠겨 있다.")}
{d.shot(_img(oa / "settings-dark.png", 820, width=1100), "설정 다크 모드.")}
</div>
<div class="keep"><h3>7.5 휴대폰 보기</h3>
<div class="three">
{d.shot(_img(oa / "mobile-home.jpg", width=600), "휴대폰 홈.")}
{d.shot(_img(oa / "mobile-dark.jpg", width=600), "휴대폰 다크 모드.")}
{d.shot(_img(oa / "mobile-settings.jpg", width=600), "휴대폰 설정 — 보기만 가능.")}
</div></div>
<div class="keep"><h3>7.6 Paper와 검증 화면</h3>
<p>Paper 장부와 거래소 테스트넷 · 데모 계정의 검증 기록은 예전 대시보드에서 본다. 소액 공격형 세 계좌는 정리선(−70%)까지의 거리를
함께 보여 주고, 테스트넷 화면은 날마다의 주문 · 체결 · 보호 주문 검증 결과를 줄로 남긴다.</p>
<div class="two">
{d.shot(_img(od / "ag-paper.png", 1000, width=1100), "소액 공격형 Paper — 세 계좌와 정리선.")}
{d.shot(_img(od / "testnet.png", 1000, width=1100), "테스트넷 검증 — 날짜별 주문 · 체결 확인.")}
</div></div>
</section>""")

    # ---------------- 8장 · 9장
    P.append("""
<section class="page">
<h2><span>8</span>품질 보증과 재현성</h2>
<ul>
<li><b>자동 시험 2,887개</b>(파일 332개). 위험 규칙 · 체결 · 장부 · 연구 엔진 · 화면 렌더링을 덮고, 결함을 고칠 때마다 그 결함을 재현하는 회귀 시험을 먼저 쓴다.</li>
<li><b>정적 검사.</b> 타입 검사(mypy)와 린트(ruff)를 통과해야 반영한다.</li>
<li><b>판정 재현.</b> 판정 자료는 해시로 고정하고, 명령 한 줄로 같은 판정을 다시 낼 수 있게 한다.</li>
<li><b>장부 재현.</b> Paper 장부는 연구와 같은 하루 함수를 쓰며, 매일 처음부터 다시 계산해 저장본과 대조한다. 계산을 고칠 때는 날짜 경계를 두어 과거 장부의 재현을 깨지 않는다.</li>
<li><b>비밀과 백업.</b> 키는 저장소 밖 환경 파일에만 두고 백업에도 넣지 않는다. 상태 묶음은 매일 다른 물리 디스크에 파일별 해시 목록과 함께 저장한다.</li>
</ul>
<h2><span>9</span>한계와 향후 과제</h2>
<h3>9.1 한계</h3>
<ul>
<li><b>이미 본 기간.</b> 대부분의 판정은 공개된 과거 자료로 한 후향 연구다. 사전 등록은 고쳐 맞추기를 막지만, 큰 추세 구간이 공지의 사실이라는 점은 지우지 못한다.</li>
<li><b>단일 시장 경로.</b> 통과한 추세 규칙의 근거는 사실상 BTC · ETH 두 자산의 8.7년이다. 뒤 절반의 샤프가 0.46으로 약해졌다.</li>
<li><b>짧은 Paper 기간.</b> Paper는 몇 주째다. 운영 판단에 필요한 기간에 한참 못 미친다.</li>
<li><b>체결 모형의 근사.</b> 1분봉 재생은 호가 대기열과 시장 충격을 반영하지 못한다. 하루 한 번 재조정에서는 작지만 0은 아니다.</li>
<li><b>다중 검정.</b> 보정 뒤 남는 것은 3개뿐이며, 엄격한 보정(Bonferroni)으로는 하나도 남지 않는다.</li>
</ul>
<h3>9.2 향후 과제</h3>
<ul>
<li>Paper 장부를 미리 정한 기간까지 쌓고, 정한 기준으로만 다음 단계를 판단한다.</li>
<li>PC 밖에서 장부 갱신 여부를 감시하는 독립 감시 장치.</li>
<li>신호가 나온 근거(입력 자료와 중간값)를 날마다 저장해, 운영 중 결정을 사후에 그대로 재현한다.</li>
<li>새 아이디어는 14가지 함정 목록과 조건 맞춘 대조군을 먼저 통과한 것만 시험한다.</li>
</ul>
<h2 class="nonum">용어</h2>
<dl class="gloss">
<dt>bp</dt><dd>0.01%. 왕복 비용 19bp는 사고팔 때 합쳐 0.19%.</dd>
<dt>funding</dt><dd>무기한 선물 보유자끼리 8시간마다 주고받는 이자. 롱이 많으면 롱이 낸다.</dd>
<dt>샤프</dt><dd>수익을 변동성으로 나눈 값. 위험 한 단위당 수익.</dd>
<dt>MDD · 낙폭</dt><dd>고점에서 저점까지 가장 크게 빠진 비율.</dd>
<dt>R</dt><dd>한 거래에서 감수한 손실 폭을 1로 본 수익 단위.</dd>
<dt>2SE 하한</dt><dd>평균에서 표준오차 두 배를 뺀 값. 0보다 커야 우연과 구별된다고 본다.</dd>
<dt>검정력</dt><dd>효과가 실제로 있을 때 시험이 그것을 잡아낼 확률.</dd>
<dt>Paper</dt><dd>실제 돈 없이 같은 규칙으로 하루씩 진행하는 가상 장부.</dd>
<dt>후향 파일럿</dt><dd>판정이 아니라, 이미 본 자료에서 계좌 단위로 미리 정한 비교를 한 번 해 보는 연구.</dd>
</dl>
</section>""")
    return "\n".join(P)


def family_head_alloc() -> str:
    cs = [c for c in CARDS if c.fam in ("alloc", "ml")]
    cnt = Counter(c.verdict for c in cs)
    chips = " ".join(f'{pill(v)}<span class="n">{cnt[v]}</span>' for v in VERDICTS if cnt[v])
    return (
        f'<div class="famhead"><div class="famtitle"><h3>자산 배분 · 머신러닝 · LLM 판독</h3>'
        f'<span class="famn">{len(cs)}건</span></div><div class="chips">{chips}</div>'
        f"<p>{escape(FAMILY_NOTE['alloc'])}</p></div>"
    )


CSS = """
:root{--ink:#1b2333;--sub:#5b6475;--line:#d9dde5;--soft:#f3f5f8;--acc:#2350a8;--bg:#ffffff}
@page{size:A4;margin:16mm 15mm 16mm 15mm;@bottom-center{content:counter(page);font:9pt 'Noto Sans KR',sans-serif;color:#8a8f98}}
@page:first{@bottom-center{content:none}}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:var(--bg);color:var(--ink);font:9.6pt/1.62 'Noto Sans KR','Malgun Gothic',sans-serif;word-break:keep-all}
.page{break-before:page}
h1,h2,h3,h4{font-family:'Noto Serif KR','Batang',serif;color:var(--ink);text-wrap:balance}
h2{font-size:19pt;margin:0 0 10pt;padding-bottom:6pt;border-bottom:1.5pt solid var(--ink);display:flex;gap:10pt;align-items:baseline}
h2 span{font-family:'Noto Sans KR',sans-serif;color:var(--acc);font-size:13pt;font-weight:700}
h2.nonum{display:block}
h3{font-size:12.5pt;margin:16pt 0 6pt}
h4{font-size:10.5pt;margin:0 0 4pt}
p{margin:0 0 7pt}
ul{margin:0 0 8pt;padding-left:16pt}li{margin:2pt 0}
b{font-weight:700}
figure{margin:10pt 0 12pt;break-inside:avoid}
figure svg{display:block}
figcaption{font-size:8.5pt;color:var(--sub);margin-top:4pt}
figcaption b{color:var(--ink)}
.shot img{width:100%;border:0.6pt solid var(--line);border-radius:4pt;display:block}
.two{display:grid;grid-template-columns:1fr 1fr;gap:8pt 12pt}
.three{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10pt}
.two figure,.three figure{margin:4pt 0}
.tbl{margin:8pt 0 12pt;break-inside:avoid}
.tcap{font-size:8.5pt;color:var(--sub);margin-bottom:3pt}.tcap b{color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:8.8pt;font-variant-numeric:tabular-nums}
th{text-align:left;font-weight:700;border-bottom:1pt solid var(--ink);padding:4pt 6pt;background:var(--soft)}
td{border-bottom:0.5pt solid var(--line);padding:4pt 6pt;vertical-align:top}
.tbl.small table{font-size:8.3pt}
.pill{display:inline-block;font-size:7.6pt;font-weight:700;color:#fff;background:var(--c);border-radius:8pt;padding:0.5pt 6pt;white-space:nowrap;line-height:1.5}
/* 표지 */
.cover{height:262mm;display:flex;flex-direction:column;justify-content:center;padding:0 6mm;border-left:5pt solid var(--acc)}
.kicker{font-size:10pt;letter-spacing:0.12em;color:var(--acc);font-weight:700;margin-bottom:12pt}
.cover h1{font-size:30pt;line-height:1.28;margin:0 0 16pt}
.lede{font-size:11pt;color:#3a4252;max-width:150mm;line-height:1.7}
.covergrid{display:grid;grid-template-columns:1fr 1fr;gap:8pt 20pt;margin:22pt 0;font-size:9.5pt}
.covergrid div{border-top:0.6pt solid var(--line);padding-top:5pt}
.covergrid span{display:block;font-size:8pt;color:var(--sub);letter-spacing:0.06em}
.bignums{display:grid;grid-template-columns:repeat(4,1fr);gap:10pt;margin-top:10pt}
.bignums div{background:var(--soft);border-radius:6pt;padding:10pt 12pt}
.bignums b{display:block;font:700 22pt 'Noto Serif KR',serif;color:var(--acc);font-variant-numeric:tabular-nums}
.bignums span{font-size:8.5pt;color:var(--sub)}
/* 요약 */
.summary{display:grid;grid-template-columns:1fr 1fr;gap:10pt 16pt;margin-bottom:6pt}
.summary div{border-top:1.2pt solid var(--acc);padding-top:6pt}
.summary p{font-size:9.1pt}
/* 사례 */
.case{border-left:2.5pt solid var(--acc);padding:2pt 0 2pt 10pt;margin:10pt 0 12pt;break-inside:avoid}
.case small{font-family:'Noto Sans KR',sans-serif;font-size:8pt;color:var(--acc);margin-left:6pt;font-weight:700}
/* 카탈로그 */
.catalog .intro{max-width:none}
.legend{display:flex;gap:6pt;margin:4pt 0 10pt}
.famhead{break-inside:avoid;break-after:avoid;background:var(--soft);border-radius:6pt;padding:9pt 12pt;margin:16pt 0 8pt}
.famtitle{display:flex;align-items:baseline;gap:8pt}
.famhead h3{margin:0;font-size:13pt}
.famn{font-weight:700;color:var(--acc)}
.chips{margin:4pt 0 5pt;display:flex;flex-wrap:wrap;gap:4pt;align-items:center}
.chips .n{font-size:8.5pt;font-weight:700;margin-right:6pt}
.famhead p{margin:0;font-size:9pt;color:#3a4252}
.card{break-inside:avoid;border-left:3.5pt solid var(--c);padding:5pt 0 5pt 10pt;margin:0 0 9pt}
.card header{display:flex;align-items:baseline;gap:7pt;flex-wrap:wrap;margin-bottom:3pt}
.card .key{font-weight:700;font-size:8.6pt;color:var(--c);letter-spacing:0.02em}
.card .nm{font-weight:700;font-size:10.2pt;flex:1;min-width:0}
.card .tags{display:flex;gap:5pt;align-items:center}
.card .tag{font-size:7.8pt;color:var(--sub);border:0.5pt solid var(--line);border-radius:8pt;padding:0 5pt}
.card .dt{font-size:7.8pt;color:var(--sub);font-variant-numeric:tabular-nums}
.card p{font-size:8.9pt;margin:0 0 3pt;line-height:1.58}
.card .fig{font-size:8.3pt;color:var(--ink);font-weight:700;font-variant-numeric:tabular-nums;background:var(--soft);display:inline-block;padding:1pt 6pt;border-radius:3pt}
.keep{break-inside:avoid}
.gloss{display:grid;grid-template-columns:auto 1fr;gap:3pt 12pt;font-size:9pt}
.gloss dt{font-weight:700}.gloss dd{margin:0;color:#3a4252}
@media screen{body{max-width:210mm;margin:0 auto;padding:12mm 15mm}.page{margin-top:18mm;padding-top:10mm;border-top:1px dashed var(--line)}}
"""


def write_html(body: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>퀀트 리서치 포트폴리오</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&family=Noto+Serif+KR:wght@600;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>{body}</body></html>"""
    p = OUT / "portfolio.html"
    p.write_text(html, encoding="utf-8")
    return p


def print_pdf(html: Path) -> Path:
    pdf = OUT / "portfolio.pdf"
    if pdf.exists():
        pdf.unlink()
    with tempfile.TemporaryDirectory() as prof:
        subprocess.run(
            [
                str(EDGE),
                "--headless=new",
                "--disable-gpu",
                f"--user-data-dir={prof}",
                "--no-pdf-header-footer",
                "--virtual-time-budget=15000",
                f"--print-to-pdf={pdf}",
                html.as_uri(),
            ],
            check=False,
            timeout=180,
        )
        for _ in range(60):
            if pdf.exists() and pdf.stat().st_size > 0:
                time.sleep(2)
                break
            time.sleep(1)
    return pdf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    html = write_html(build())
    print(html, f"{html.stat().st_size / 1e6:.1f} MB")
    if not a.no_pdf:
        pdf = print_pdf(html)
        print(pdf, f"{pdf.stat().st_size / 1e6:.1f} MB" if pdf.exists() else "PDF 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
