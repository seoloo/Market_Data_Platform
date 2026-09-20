"""설정 로더.

``base.yaml`` + 모드별 오버레이 + 환경변수를 병합해 검증된 :class:`RootConfig` 를 만든다.

**이 모듈은 파일과 환경변수에 접근하므로 순수하지 않다.**
Pure Core (``core``/``features``/``strategy``/``risk``) 는 이 모듈을 import 하지 않는다.
검증된 ``RootConfig`` 객체만 전달받는다 (AGENTS.md §3.1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import Mode
from auto_trading.core.errors import ConfigError
from auto_trading.core.hashing import sha256_hex

__all__ = ["LoadedConfig", "load_config", "merge_dicts"]

#: 설정 파일에 들어 있으면 안 되는 키 조각. 시크릿은 환경변수로만 받는다 (원 명세 §17).
_SECRET_KEY_FRAGMENTS = ("secret", "api_key", "apikey", "token", "password", "passwd", "private")

#: 환경변수 오버라이드 접두사
_ENV_PREFIX = "AT_"


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """검증된 설정과 그 출처.

    ``hash`` 는 ``config_versions`` 테이블에 저장되어 모든 ``decisions`` 행이 참조한다.
    같은 설정은 어떤 실행에서도 같은 해시를 낳아야 한다 (03-data-model.md §2).
    """

    config: RootConfig
    hash: str
    sources: tuple[str, ...]
    raw: dict[str, Any]

    def summary(self) -> str:
        lines = [f"config hash: {self.hash[:12]}", f"sources: {', '.join(self.sources)}"]
        lines.extend(self.config.summary_lines())
        return "\n".join(lines)


def merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """중첩 dict 를 깊게 병합한다. overlay 가 우선한다.

    리스트는 병합하지 않고 **통째로 교체**한다. 부분 병합은 의도를 예측하기 어렵다.
    """
    result = dict(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = merge_dicts(existing, value)
        else:
            result[key] = value
    return result


def _scan_for_secrets(data: Any, path: str = "") -> None:
    """설정 파일에 시크릿으로 보이는 키가 있으면 거부한다.

    누군가 편의상 ``base.yaml`` 에 API 키를 붙여 넣고 그대로 커밋하는 사고를 막는다.
    ``.gitignore`` 와 gitleaks 에 이어지는 세 번째 방어선이다.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            key_lower = str(key).lower()
            for fragment in _SECRET_KEY_FRAGMENTS:
                if fragment in key_lower:
                    raise ConfigError(
                        f"설정 파일에 시크릿으로 보이는 키가 있다: {path}{key!r}. "
                        "API 키·토큰은 설정 파일이 아니라 환경변수(.env)로 전달한다 (원 명세 §17)."
                    )
            _scan_for_secrets(value, f"{path}{key}.")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _scan_for_secrets(item, f"{path}[{i}].")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"설정 파일이 없다: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 파싱 실패: {path}\n{exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"설정 파일의 최상위는 매핑이어야 한다: {path}")
    _scan_for_secrets(loaded, f"{path.name}:")
    return loaded


def _coerce_env_value(text: str) -> Any:
    """환경변수 문자열을 YAML 스칼라로 해석한다 (``true``, ``42``, ``0.01`` 등)."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def _env_overlay(environ: dict[str, str]) -> dict[str, Any]:
    """``AT_RISK__MAX_LEVERAGE=3`` 형태의 환경변수를 중첩 dict 로 바꾼다.

    구분자는 ``__`` (밑줄 2개) 다. 키 이름 자체에 ``_`` 가 들어가기 때문이다.
    ``AT_MODE`` 처럼 최상위 키도 지원한다.
    """
    overlay: dict[str, Any] = {}
    for raw_key, raw_value in environ.items():
        if not raw_key.startswith(_ENV_PREFIX):
            continue
        parts = [p.lower() for p in raw_key[len(_ENV_PREFIX) :].split("__") if p]
        if not parts:
            continue
        cursor = overlay
        for part in parts[:-1]:
            nxt = cursor.setdefault(part, {})
            if not isinstance(nxt, dict):
                raise ConfigError(f"환경변수 경로가 충돌한다: {raw_key}")
            cursor = nxt
        cursor[parts[-1]] = _coerce_env_value(raw_value)
    return overlay


def load_config(
    mode: Mode | str | None = None,
    *,
    config_dir: Path | str = "config",
    environ: dict[str, str] | None = None,
    apply_env: bool = True,
) -> LoadedConfig:
    """설정을 로드하고 검증한다.

    병합 순서 (뒤가 우선):

    1. ``config/base.yaml``
    2. ``config/<mode>.yaml`` (있으면)
    3. 환경변수 ``AT_*`` (``apply_env=True`` 일 때)

    Args:
        mode: 실행 모드. ``None`` 이면 ``AT_MODE`` 환경변수, 그것도 없으면 base.yaml 의 값.
        config_dir: 설정 디렉터리.
        environ: 환경변수 (테스트 주입용). ``None`` 이면 ``os.environ``.
        apply_env: 환경변수 오버레이 적용 여부.

    Raises:
        ConfigError: 파일 없음, 파싱 실패, 검증 실패, 시크릿 포함.
    """
    env = dict(os.environ) if environ is None else dict(environ)
    directory = Path(config_dir)

    base_path = directory / "base.yaml"
    merged = _read_yaml(base_path)
    sources = [str(base_path)]

    resolved_mode = _resolve_mode(mode, env, merged)
    merged["mode"] = resolved_mode.value

    overlay_path = directory / f"{resolved_mode.value}.yaml"
    if overlay_path.is_file():
        merged = merge_dicts(merged, _read_yaml(overlay_path))
        merged["mode"] = resolved_mode.value  # 오버레이가 mode 를 덮어쓰지 못하게 한다
        sources.append(str(overlay_path))

    if apply_env:
        overlay = _env_overlay(env)
        overlay.pop("mode", None)  # mode 는 위에서 이미 반영했다
        if overlay:
            merged = merge_dicts(merged, overlay)
            sources.append("env:AT_*")

    try:
        config = RootConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"설정 검증 실패 ({', '.join(sources)}):\n{_format_errors(exc)}") from exc

    return LoadedConfig(
        config=config,
        hash=compute_config_hash(config),
        sources=tuple(sources),
        raw=merged,
    )


def _resolve_mode(mode: Mode | str | None, env: dict[str, str], merged: dict[str, Any]) -> Mode:
    raw = mode if mode is not None else env.get(f"{_ENV_PREFIX}MODE") or merged.get("mode")
    if raw is None:
        return Mode.BACKTEST
    if isinstance(raw, Mode):
        return raw
    try:
        return Mode(str(raw).lower())
    except ValueError as exc:
        valid = ", ".join(m.value for m in Mode)
        raise ConfigError(f"알 수 없는 mode: {raw!r}. 가능한 값: {valid}") from exc


def _format_errors(exc: ValidationError) -> str:
    lines: list[str] = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"  - {location}: {err['msg']}")
    return "\n".join(lines)


def compute_config_hash(config: RootConfig) -> str:
    """설정의 정규화 해시.

    파일 원문이 아니라 **검증된 모델**을 해시한다. 주석·키 순서·공백이 달라도
    실질 내용이 같으면 같은 해시가 나와야 재현성 비교가 의미를 갖는다.
    """
    return sha256_hex(config.model_dump(mode="python"))
