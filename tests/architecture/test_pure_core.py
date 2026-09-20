"""Pure Core 경계 검사 (AGENTS.md §3.1).

import-linter 는 모듈 간 import 만 본다. 이 테스트는 그 아래 층위에서
"전략 코드가 시스템 시계나 난수에 손대는가"를 **AST 로** 검사한다.

이 규칙이 깨지면 백테스트와 실거래가 조용히 달라진다. 사람 리뷰로는 놓치므로
테스트로 막는다 (09-failure-modes.md F6).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "auto_trading"

#: I/O 와 비결정론이 금지된 패키지
PURE_PACKAGES = ("core", "features", "strategy", "risk")

#: 순수 계층에서 import 가 금지된 표준 라이브러리
FORBIDDEN_IMPORTS = {
    "random",
    "socket",
    "urllib",
    "http",
    "sqlite3",
    "requests",
    "httpx",
    "asyncio",
    "threading",
    "subprocess",
    "os",
}

#: 순수 계층에서 호출이 금지된 함수 (모듈.함수)
FORBIDDEN_CALLS = {
    ("time", "time"),
    ("time", "time_ns"),
    ("time", "monotonic"),
    ("time", "sleep"),
    ("datetime", "now"),
    ("datetime", "today"),
    ("datetime", "utcnow"),
    ("random", "random"),
    ("random", "choice"),
    ("uuid", "uuid4"),
}

#: 순수 계층에서 금지된 내장 함수
FORBIDDEN_BUILTINS = {"open", "input", "print", "eval", "exec"}


def pure_source_files() -> list[Path]:
    files: list[Path] = []
    for pkg in PURE_PACKAGES:
        files.extend(sorted((SRC / pkg).rglob("*.py")))
    return files


def test_pure_packages_exist():
    """경계를 검사하려면 대상 패키지가 실제로 있어야 한다."""
    for pkg in PURE_PACKAGES:
        assert (SRC / pkg).is_dir(), f"{pkg} 패키지가 없다"


def test_there_are_files_to_check():
    """검사 대상이 0개인데 통과하는 상황을 막는다."""
    assert pure_source_files(), "순수 계층에 검사할 .py 파일이 없다"


@pytest.mark.parametrize("path", pure_source_files(), ids=lambda p: str(p.name))
def test_no_forbidden_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in FORBIDDEN_IMPORTS, (
                    f"{path.name}:{node.lineno} 순수 계층에서 '{alias.name}' import 금지"
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            assert root not in FORBIDDEN_IMPORTS, (
                f"{path.name}:{node.lineno} 순수 계층에서 '{node.module}' import 금지"
            )


@pytest.mark.parametrize("path", pure_source_files(), ids=lambda p: str(p.name))
def test_no_wall_clock_or_randomness(path: Path):
    """``datetime.now()`` / ``time.time()`` / 난수 호출 금지.

    시각이 필요하면 ``snapshot.as_of_ms`` 를 쓴다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            pair = (func.value.id, func.attr)
            assert pair not in FORBIDDEN_CALLS, (
                f"{path.name}:{node.lineno} 순수 계층에서 {pair[0]}.{pair[1]}() 호출 금지. "
                "시각은 snapshot.as_of_ms 를 쓴다."
            )
        elif isinstance(func, ast.Name):
            assert func.id not in FORBIDDEN_BUILTINS, (
                f"{path.name}:{node.lineno} 순수 계층에서 {func.id}() 호출 금지"
            )


@pytest.mark.parametrize("path", pure_source_files(), ids=lambda p: str(p.name))
def test_no_float_literals_in_money_paths(path: Path):
    """``Decimal(0.1)`` 처럼 float 를 Decimal 에 직접 넘기는 코드를 막는다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("Decimal", "dec")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, float)
        ):
            pytest.fail(
                f"{path.name}:{node.lineno} Decimal/dec 에 float 리터럴을 넘기면 정밀도가 깨진다"
            )


def test_core_does_not_import_other_internal_packages():
    """core 는 최하위 계층이다. auto_trading 내부에서 core 외의 것을 import 하지 않는다."""
    for path in sorted((SRC / "core").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            else:
                continue
            for module in modules:
                if module.startswith("auto_trading."):
                    assert module.startswith("auto_trading.core"), (
                        f"{path.name}:{node.lineno} core 가 '{module}' 을 import 한다"
                    )
