"""도메인 예외.

bare ``except:`` 대신 이 예외들을 쓴다 (AGENTS.md §4).
"""

from __future__ import annotations


class AutoTradingError(Exception):
    """이 시스템의 모든 예외의 기반."""


# --- 설정 -----------------------------------------------------------------


class ConfigError(AutoTradingError):
    """설정값이 없거나 스키마를 위반함."""


# --- 데이터 ---------------------------------------------------------------


class DataIntegrityError(AutoTradingError):
    """캔들 갭·중복·OHLC 관계 위반 등 시장 데이터 무결성 문제."""


class StaleDataError(AutoTradingError):
    """판단에 필요한 데이터가 너무 오래됨."""


class CausalityError(AutoTradingError):
    """as_of 이후의 데이터에 접근하려 함 (look-ahead).

    이 예외가 발생하면 백테스트 결과 전체를 신뢰할 수 없다.
    """


# --- 수치 -----------------------------------------------------------------


class PrecisionError(AutoTradingError):
    """부동소수점 오용, 거래소 정밀도 위반 등 수치 정확성 문제."""


# --- 거래소 / 실행 --------------------------------------------------------


class ExchangeError(AutoTradingError):
    """거래소 API 오류의 기반."""


class OrderRejectedError(ExchangeError):
    """거래소가 주문을 거부함."""


class RateLimitError(ExchangeError):
    """레이트 리밋 도달."""


class OrderStateUnknownError(ExchangeError):
    """주문 결과를 알 수 없음.

    가장 위험한 상태다. 해소될 때까지 신규 진입을 금지한다
    (docs/phase0/04-state-machine.md §2).
    """


class ReconciliationError(AutoTradingError):
    """거래소와 로컬 상태를 일치시키지 못함."""


class InvariantViolationError(AutoTradingError):
    """상태 불변식 위반 (04-state-machine.md §3).

    예: OPEN 포지션인데 거래소에 보호주문이 없음.
    """
