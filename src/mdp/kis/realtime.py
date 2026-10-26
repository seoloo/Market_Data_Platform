"""한국투자증권 실시간 — 웹소켓 체결 글자 읽기 · 구독 메시지 만들기 (순수 함수, K-4).

언제 쓰나: 실시간 받기(:mod:`mdp.stream.kis_produce`)와 시험이 부른다. Spark 스트림
          (:mod:`mdp.stream.kis_bars`)은 같은 칸 번호(:data:`FIELD`)로 Spark 안에서 읽는다.
읽고 쓰는 것: 없음.

웹소켓 메시지 두 가지 (공식 예제 저장소 ``koreainvestment/open-trading-api``, 2026-10-26 확인):

- 제어(JSON): 구독 응답 ``{"header": {"tr_id": …}, "body": {"rt_cd": "0", "msg1": …}}``,
  연결 확인 ``{"header": {"tr_id": "PINGPONG", …}}`` — 받은 그대로 pong 으로 돌려준다.
- 체결(글자): ``0|H0STCNT0|003|<47 칸>^<47 칸>^<47 칸>`` — 첫 칸 0 = 평문 · 1 = 암호문,
  둘째 = TR, 셋째 = 건수, 넷째 = ``^`` 로 이은 값(한 건 47 칸, 건수만큼 이어 붙음).

[이유] 체결 TR 은 ``H0STCNT0``(국내 주식 실시간 체결가, KRX) 하나만 허용한다. 주문 · 체결통보
       TR(암호문으로 오는 것)은 구독 메시지를 만들 때부터 막는다(절대 규칙 2). 체결 번호가 없으므로
       '누적 거래량(``ACML_VOL``)' 을 체결 번호처럼 쓴다 — 체결마다 그 체결량만큼 늘어난다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from mdp.errors import DataIntegrityError, ForbiddenRequestError

__all__ = [
    "ALLOWED_REALTIME",
    "FIELD",
    "TRADE_COLUMNS",
    "TRADE_TR",
    "Frame",
    "FrameKind",
    "parse_frame",
    "subscribe_message",
    "trade_records",
]

TRADE_TR: Final = "H0STCNT0"
"""국내 주식 실시간 체결가(KRX). 넥스트레이드까지 합친 통합(``H0UNCNT0``)은 아직 쓰지 않는다 —
분봉 조회(K-2)가 KRX(``J``)라 대사하려면 같은 시장이어야 한다."""
ALLOWED_REALTIME: Final = frozenset({TRADE_TR})

TRADE_COLUMNS: Final = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN", "PRDY_VRSS", "PRDY_CTRT",
    "WGHN_AVRG_STCK_PRC", "STCK_OPRC", "STCK_HGPR", "STCK_LWPR", "ASKP1", "BIDP1", "CNTG_VOL",
    "ACML_VOL", "ACML_TR_PBMN", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU", "CTTR",
    "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "CCLD_DVSN", "SHNU_RATE", "PRDY_VOL_VRSS_ACML_VOL_RATE",
    "OPRC_HOUR", "OPRC_VRSS_PRPR_SIGN", "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR", "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE",
    "NEW_MKOP_CLS_CODE", "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1", "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN", "VOL_TNRT", "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE", "VI_STND_PRC", "MARKET_CLS_CODE",
)  # fmt: skip
"""체결 한 건의 칸 순서 (공식 예제 ``ccnl_krx`` 그대로, 47 칸)."""
FIELD: Final = {name: index for index, name in enumerate(TRADE_COLUMNS)}
"""칸 이름 → 번호 — Spark 스트림이 같은 번호로 읽는다."""


class FrameKind(StrEnum):
    """[쓰임] 받은 메시지 종류."""

    TRADES = "trades"
    PINGPONG = "pingpong"
    CONTROL = "control"
    """구독 응답 등 그 밖의 JSON."""


@dataclass(frozen=True, slots=True)
class Frame:
    """[쓰임] 메시지 하나를 읽은 결과 — 종류 · TR · 체결이면 종목(첫 건) · 제어면 성공 여부와
    문구."""

    kind: FrameKind
    tr_id: str
    symbol: str | None = None
    ok: bool | None = None
    message: str | None = None


def subscribe_message(
    approval_key: str, symbol: str, *, tr_id: str = TRADE_TR, subscribe: bool = True
) -> str:
    """[쓰임] 구독(``tr_type=1``) · 해지(``2``) 메시지 — 허용한 실시간 TR 만.
    [이유] 주문 · 체결통보 TR 은 허용 목록에 없어 보내기 전에 멈춘다."""
    if tr_id not in ALLOWED_REALTIME:
        raise ForbiddenRequestError(f"허용하지 않은 실시간 TR 이라 구독하지 않는다: {tr_id}")
    if not (symbol.isalnum() and len(symbol) == 6):
        raise ValueError(f"종목코드는 6 자리: {symbol!r}")
    return json.dumps(
        {
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": "1" if subscribe else "2",
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": tr_id, "tr_key": symbol}},
        },
        separators=(",", ":"),
    )


def parse_frame(text: str) -> Frame:
    """[쓰임] 받은 메시지 하나 → 종류. 체결이면 TR 과 첫 건 종목코드를, 제어면 성공 여부를 본다.
    [이유] 체결은 받은 글자를 그대로 토픽에 넣고(종목코드는 토픽 키로만 씀), 제어 메시지는 받기가
           처리한다. 암호문 체결(첫 칸 1)은 체결통보에만 쓰이고 우리는 구독하지 않으므로 오면
           멈춘다."""
    if text[:1] in ("0", "1"):
        parts = text.split("|", 3)
        if len(parts) < 4:
            raise DataIntegrityError(f"체결 메시지 칸이 모자라다: {text[:40]!r}")
        if parts[0] == "1":
            raise DataIntegrityError(f"암호문 메시지가 왔다(구독하지 않은 TR): {parts[1]}")
        return Frame(FrameKind.TRADES, parts[1], symbol=parts[3].split("^", 1)[0])
    decoded = json.loads(text)
    header = decoded.get("header", {})
    tr_id = str(header.get("tr_id", ""))
    if tr_id == "PINGPONG":
        return Frame(FrameKind.PINGPONG, tr_id)
    body = decoded.get("body") or {}
    return Frame(
        FrameKind.CONTROL,
        tr_id,
        symbol=header.get("tr_key"),
        ok=str(body.get("rt_cd")) == "0" if body else None,
        message=str(body.get("msg1", "")).strip() or None,
    )


def trade_records(text: str) -> list[dict[str, str]]:
    """[쓰임] 체결 메시지 → 건마다 {칸 이름: 글자}.
    칸 수가 47 의 배수가 아니거나 건수가 다르면 멈춘다.
    [이유] 글자를 숫자로 바꾸지 않는다 — 바꾸는 일은 Spark 스트림이 한다(받은 그대로)."""
    flag, tr_id, count, payload = text.split("|", 3)
    if flag != "0" or tr_id != TRADE_TR:
        raise DataIntegrityError(f"체결 메시지가 아니다: {flag}|{tr_id}")
    values = payload.split("^")
    width = len(TRADE_COLUMNS)
    if len(values) % width or len(values) // width != int(count):
        raise DataIntegrityError(f"건수 {count} 와 칸 수 {len(values)} 가 맞지 않다")
    return [
        dict(zip(TRADE_COLUMNS, values[i : i + width], strict=True))
        for i in range(0, len(values), width)
    ]
