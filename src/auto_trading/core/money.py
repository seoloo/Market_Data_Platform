"""금액·수량 연산.

**규칙 (AGENTS.md §4): 금액과 수량에 ``float`` 를 쓰지 않는다.**
부동소수점 오차는 거래소 정밀도(stepSize/tickSize) 위반 → 주문 거부 또는
청산 후 dust 잔량으로 이어진다 (09-failure-modes.md F10).

``dec()`` 는 ``float`` 입력을 런타임에 거부한다. 규칙을 문서가 아니라 코드로 강제한다.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from auto_trading.core.errors import PrecisionError

__all__ = [
    "ONE",
    "ZERO",
    "add_bps",
    "bps_between",
    "ceil_to_step",
    "dec",
    "floor_to_step",
    "is_dust",
    "round_to_tick",
    "safe_div",
]

ZERO = Decimal("0")
ONE = Decimal("1")
_BPS_DIVISOR = Decimal("10000")


def dec(value: str | int | Decimal) -> Decimal:
    """``Decimal`` 로 변환한다. ``float`` 는 거부한다.

    ``Decimal(0.1)`` 은 ``0.1000000000000000055511151231257827021181583404541015625`` 가 되며,
    이런 값이 수량 계산에 섞이면 거래소 정밀도 필터를 통과하지 못한다.
    거래소 응답의 숫자는 **문자열 그대로** 넘긴다.

    Raises:
        PrecisionError: ``float`` 입력이거나 변환할 수 없는 값일 때.
    """
    if isinstance(value, float):
        raise PrecisionError(
            f"금액/수량에 float 를 쓸 수 없다: {value!r}. 문자열이나 Decimal 로 전달할 것."
        )
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PrecisionError(f"유한하지 않은 Decimal: {value!r}")
        return value
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PrecisionError(f"Decimal 로 변환할 수 없다: {value!r}") from exc
    if not result.is_finite():
        raise PrecisionError(f"유한하지 않은 값: {value!r}")
    return result


def _require_positive_step(step: Decimal, name: str) -> None:
    if step <= ZERO:
        raise PrecisionError(f"{name} 는 0보다 커야 한다: {step}")


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """``step`` 의 배수로 **내림**한다.

    수량 반올림은 항상 내림이다. 올림하면 의도한 위험 예산이나 가용 증거금을
    초과하는 주문이 나간다.
    """
    _require_positive_step(step, "step")
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    """``step`` 의 배수로 올림한다. 수량에는 쓰지 않는다 (최소 주문 탐색 등 한정)."""
    _require_positive_step(step, "step")
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def round_to_tick(value: Decimal, tick: Decimal, *, rounding: str = ROUND_HALF_UP) -> Decimal:
    """가격을 ``tick`` 의 배수로 맞춘다.

    스톱 가격은 보수적인 방향으로 맞춰야 하므로 호출부에서 ``rounding`` 을 지정한다.
    (롱 스톱은 ROUND_FLOOR, 숏 스톱은 ROUND_CEILING 이 보수적)
    """
    _require_positive_step(tick, "tick")
    return (value / tick).to_integral_value(rounding=rounding) * tick


def is_dust(qty: Decimal, step: Decimal) -> bool:
    """수량이 최소 단위 미만인가.

    청산 후 남은 미세 잔량을 "포지션 있음"으로 오판하지 않기 위한 기준
    (09-failure-modes.md F10).
    """
    _require_positive_step(step, "step")
    return abs(qty) < step


def safe_div(numerator: Decimal, denominator: Decimal, *, default: Decimal = ZERO) -> Decimal:
    """0 나눗셈에서 예외 대신 ``default`` 를 반환한다.

    분모가 0인 상황(변동성 0, 손절폭 0 등)에서 사이징이 폭주하지 않게 한다.
    """
    if denominator == ZERO:
        return default
    return numerator / denominator


def add_bps(value: Decimal, bps: Decimal) -> Decimal:
    """``value`` 에 ``bps`` 베이시스포인트를 적용한다. 음수면 차감."""
    return value * (ONE + bps / _BPS_DIVISOR)


def bps_between(reference: Decimal, actual: Decimal) -> Decimal:
    """``reference`` 대비 ``actual`` 의 차이를 bps 로 반환한다.

    슬리피지 실측에 쓴다. ``reference`` 가 0이면 0을 반환한다.
    """
    if reference == ZERO:
        return ZERO
    return (actual - reference) / reference * _BPS_DIVISOR
