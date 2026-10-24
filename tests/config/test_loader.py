"""설정 로더 테스트 — 병합, 환경변수, 해시 재현성, 시크릿 차단."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from auto_trading.config.loader import (
    LoadedConfig,
    compute_config_hash,
    load_config,
    merge_dicts,
)
from auto_trading.core.enums import Mode
from auto_trading.core.errors import ConfigError
from auto_trading.core.hashing import sha256_hex

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

MINIMAL_BASE = """
mode: backtest
symbol: BTCUSDT
features:
  b1_trend:   {timeframe: 4h}
  b2_volregime: {timeframe: 1h}
  t1_pullback: {timeframe: 15m}
  t2_momentum: {timeframe: 5m}
  t3_volume:  {timeframe: 5m, weight: 0.0}
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "base.yaml").write_text(MINIMAL_BASE, encoding="utf-8")
    return tmp_path


class TestMergeDicts:
    def test_deep_merge(self):
        base = {"a": 1, "nested": {"x": 1, "y": 2}}
        overlay = {"nested": {"y": 99, "z": 3}}
        assert merge_dicts(base, overlay) == {"a": 1, "nested": {"x": 1, "y": 99, "z": 3}}

    def test_lists_are_replaced_not_merged(self):
        """부분 병합은 의도를 예측하기 어렵다."""
        assert merge_dicts({"tf": ["1m", "5m"]}, {"tf": ["1h"]}) == {"tf": ["1h"]}

    def test_does_not_mutate_inputs(self):
        base = {"nested": {"x": 1}}
        merge_dicts(base, {"nested": {"y": 2}})
        assert base == {"nested": {"x": 1}}


class TestLoadBasics:
    def test_loads_minimal(self, config_dir: Path):
        loaded = load_config(config_dir=config_dir, apply_env=False)
        assert isinstance(loaded, LoadedConfig)
        assert loaded.config.mode is Mode.BACKTEST
        assert len(loaded.hash) == 64

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="설정 파일이 없다"):
            load_config(config_dir=tmp_path, apply_env=False)

    def test_bad_yaml_raises(self, tmp_path: Path):
        (tmp_path / "base.yaml").write_text("a: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigError, match="YAML 파싱 실패"):
            load_config(config_dir=tmp_path, apply_env=False)

    def test_non_mapping_root_raises(self, tmp_path: Path):
        (tmp_path / "base.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="최상위는 매핑"):
            load_config(config_dir=tmp_path, apply_env=False)

    def test_validation_error_is_readable(self, tmp_path: Path):
        (tmp_path / "base.yaml").write_text(
            MINIMAL_BASE + "\nrisk:\n  max_leverage: 999\n", encoding="utf-8"
        )
        with pytest.raises(ConfigError, match="설정 검증 실패") as exc:
            load_config(config_dir=tmp_path, apply_env=False)
        assert "max_leverage" in str(exc.value)


class TestModeOverlay:
    def test_overlay_is_applied(self, config_dir: Path):
        (config_dir / "paper.yaml").write_text("ui:\n  enabled: true\n", encoding="utf-8")
        loaded = load_config("paper", config_dir=config_dir, apply_env=False)
        assert loaded.config.mode is Mode.PAPER
        assert loaded.config.ui.enabled is True
        assert len(loaded.sources) == 2

    def test_missing_overlay_is_fine(self, config_dir: Path):
        loaded = load_config("testnet", config_dir=config_dir, apply_env=False)
        assert loaded.config.mode is Mode.TESTNET
        assert len(loaded.sources) == 1

    def test_overlay_cannot_change_mode(self, config_dir: Path):
        """오버레이가 mode 를 바꾸면 어떤 파일이 적용됐는지 알 수 없게 된다."""
        (config_dir / "paper.yaml").write_text("mode: live\n", encoding="utf-8")
        loaded = load_config("paper", config_dir=config_dir, apply_env=False)
        assert loaded.config.mode is Mode.PAPER

    def test_unknown_mode_raises(self, config_dir: Path):
        with pytest.raises(ConfigError, match="알 수 없는 mode"):
            load_config("production", config_dir=config_dir, apply_env=False)

    def test_mode_from_env(self, config_dir: Path):
        loaded = load_config(config_dir=config_dir, environ={"AT_MODE": "paper"})
        assert loaded.config.mode is Mode.PAPER

    def test_explicit_mode_beats_env(self, config_dir: Path):
        loaded = load_config("testnet", config_dir=config_dir, environ={"AT_MODE": "paper"})
        assert loaded.config.mode is Mode.TESTNET


class TestEdgeTableOverlay:
    def _write_table(self, config_dir: Path) -> Path:
        buckets: list[dict[str, object]] = []
        payload: dict[str, object] = {
            "symbol": "BTCUSDT",
            "source_config_hash": "a" * 64,
            "train_from_ms": 1,
            "train_to_ms": 2,
            "decision_timeframe": "5m",
            "score_boundaries": [70.0, 80.0],
            "horizons_bars": [24],
            "min_samples": 30,
            "observations": 0,
            "buckets": buckets,
        }
        payload["digest"] = sha256_hex(payload)
        payload["aggregation"] = "all_horizons_min"
        path = config_dir / "edge_table.yaml"
        path.write_text(
            yaml.safe_dump({"edge_table": payload}, sort_keys=False),
            encoding="utf-8",
        )
        return path

    def test_default_edge_table_is_loaded_and_hashed(self, config_dir: Path):
        path = self._write_table(config_dir)
        with_table = load_config(config_dir=config_dir, apply_env=False)
        without_table = load_config(
            config_dir=config_dir,
            apply_env=False,
            include_edge_table=False,
        )
        assert with_table.config.edge_table is not None
        assert str(path) in with_table.sources
        assert with_table.hash != without_table.hash

    def test_explicit_missing_edge_table_raises(self, config_dir: Path):
        with pytest.raises(ConfigError, match="설정 파일이 없다"):
            load_config(
                config_dir=config_dir,
                apply_env=False,
                edge_table_path=config_dir / "missing.yaml",
            )


class TestEnvOverlay:
    def test_nested_override(self, config_dir: Path):
        loaded = load_config(
            config_dir=config_dir,
            environ={"AT_RISK__MAX_LEVERAGE": "5", "AT_RISK__MIN_STOP_DISTANCE": "0.02"},
        )
        assert loaded.config.risk.max_leverage == 5

    def test_boolean_coercion(self, config_dir: Path):
        loaded = load_config(config_dir=config_dir, environ={"AT_UI__ENABLED": "true"})
        assert loaded.config.ui.enabled is True

    def test_env_is_recorded_in_sources(self, config_dir: Path):
        loaded = load_config(config_dir=config_dir, environ={"AT_UI__PORT": "9000"})
        assert "env:AT_*" in loaded.sources

    def test_unrelated_env_ignored(self, config_dir: Path):
        loaded = load_config(config_dir=config_dir, environ={"PATH": "/usr/bin"})
        assert "env:AT_*" not in loaded.sources

    def test_env_can_break_consistency_and_is_caught(self, config_dir: Path):
        """환경변수로 한 값만 바꿔 정합성이 깨지면 기동을 막아야 한다."""
        with pytest.raises(ConfigError, match="레버리지 상한이 함의하는"):
            load_config(config_dir=config_dir, environ={"AT_RISK__MAX_LEVERAGE": "5"})

    def test_apply_env_false_ignores_env(self, config_dir: Path):
        loaded = load_config(
            config_dir=config_dir,
            environ={"AT_RISK__MAX_LEVERAGE": "5"},
            apply_env=False,
        )
        assert loaded.config.risk.max_leverage == 10


class TestSecretScanning:
    @pytest.mark.parametrize(
        "line",
        [
            "binance_api_key: abc123",
            "exchange:\n  secret: shhh",
            "telegram_bot_token: 123:ABC",
            "db_password: hunter2",
            "private_key: xyz",
        ],
    )
    def test_secret_like_keys_rejected(self, tmp_path: Path, line: str):
        (tmp_path / "base.yaml").write_text(MINIMAL_BASE + "\n" + line + "\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="시크릿으로 보이는 키"):
            load_config(config_dir=tmp_path, apply_env=False)

    def test_normal_keys_pass(self, config_dir: Path):
        load_config(config_dir=config_dir, apply_env=False)


class TestHashReproducibility:
    def test_same_config_same_hash(self, config_dir: Path):
        a = load_config(config_dir=config_dir, apply_env=False)
        b = load_config(config_dir=config_dir, apply_env=False)
        assert a.hash == b.hash

    def test_comments_and_key_order_do_not_matter(self, tmp_path: Path):
        """파일 원문이 아니라 검증된 모델을 해시하므로 형식 차이는 무시된다."""
        (tmp_path / "base.yaml").write_text(MINIMAL_BASE, encoding="utf-8")
        a = load_config(config_dir=tmp_path, apply_env=False)

        reordered = "# 주석 추가\nsymbol: BTCUSDT\nmode: backtest\n" + MINIMAL_BASE.split(
            "features:", 1
        )[0].replace("mode: backtest", "").replace("symbol: BTCUSDT", "")
        (tmp_path / "base.yaml").write_text(
            reordered + "features:" + MINIMAL_BASE.split("features:", 1)[1], encoding="utf-8"
        )
        b = load_config(config_dir=tmp_path, apply_env=False)
        assert a.hash == b.hash

    def test_value_change_changes_hash(self, config_dir: Path):
        a = load_config(config_dir=config_dir, apply_env=False)
        b = load_config(config_dir=config_dir, environ={"AT_UI__PORT": "9999"})
        assert a.hash != b.hash

    def test_hash_is_stable_across_many_calls(self, config_dir: Path):
        first = load_config(config_dir=config_dir, apply_env=False).hash
        for _ in range(50):
            assert load_config(config_dir=config_dir, apply_env=False).hash == first

    def test_compute_hash_matches_loader(self, config_dir: Path):
        loaded = load_config(config_dir=config_dir, apply_env=False)
        assert compute_config_hash(loaded.config) == loaded.hash


class TestRealConfigFiles:
    """저장소에 실제로 들어 있는 설정 파일이 유효한지 검증한다.

    설정 파일이 깨진 채로 커밋되는 것을 CI 에서 막는다.
    """

    # testnet · live 오버레이(config/testnet.yaml · live.yaml)는 실주문 코드를 걷어 낼 때
    # 지웠다(README 6-3).
    @pytest.mark.parametrize("mode", ["backtest", "paper"])
    def test_all_modes_load(self, mode: str):
        loaded = load_config(mode, config_dir=CONFIG_DIR, apply_env=False)
        assert loaded.config.mode.value == mode
        assert loaded.config.risk.daily_trade_limit is None
        assert loaded.config.risk.same_direction_limit is None

    def test_base_matches_confirmed_values(self):
        """13-decisions.md D9 확정값과 base.yaml 이 일치하는지 확인한다."""
        from decimal import Decimal

        cfg = load_config("backtest", config_dir=CONFIG_DIR, apply_env=False).config
        assert cfg.risk.risk_per_trade == Decimal("0.01")
        assert cfg.risk.margin_allocation == Decimal("0.10")
        assert cfg.risk.max_leverage == 10
        assert cfg.risk.min_stop_distance == Decimal("0.01")
        assert cfg.risk.daily_trade_limit is None
        assert cfg.risk.same_direction_limit is None
        assert cfg.risk.max_drawdown_pct == Decimal("0.30")  # 2026-09-24 사용자 정정

    def test_a1_overlay_is_backtest_only_and_has_no_h1_edge_table(self):
        loaded = load_config("backtest", strategy="a1_v1", config_dir=CONFIG_DIR, apply_env=False)
        assert loaded.config.strategy_id == "a1_v1"
        assert loaded.config.a1 is not None
        assert loaded.config.edge_table is None
        assert loaded.config.risk.tight_stop_action.value == "REDUCE_POSITION"
        assert loaded.config.risk.cost.slippage_entry_rate == Decimal("0.00055")
        assert not loaded.config.risk.take_profit.enabled
        with pytest.raises(ConfigError):
            load_config("live", strategy="a1_v1", config_dir=CONFIG_DIR, apply_env=False)

    def test_ui_disabled_by_default_enabled_for_paper(self):
        assert not load_config("backtest", config_dir=CONFIG_DIR, apply_env=False).config.ui.enabled
        assert load_config("paper", config_dir=CONFIG_DIR, apply_env=False).config.ui.enabled

    def test_summary_is_printable(self):
        loaded = load_config("backtest", config_dir=CONFIG_DIR, apply_env=False)
        text = loaded.summary()
        assert loaded.hash[:12] in text
        assert "base.yaml" in text
