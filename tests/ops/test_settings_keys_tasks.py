"""설정 '연결 · 키' (.env 저장) · '자동 실행' (Windows 예약) — 값이 새지 않는지, 덮어쓰지 않는지."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from auto_trading.ops import env_keys, task_setup
from auto_trading.ops.task_setup import TaskState

SECRET = "fake-value-for-test-only"


def test_validate_skips_blank_and_hides_value_in_error() -> None:
    updates, errors = env_keys.validate(
        {"TOSS_CLIENT_ID": f"  {SECRET} ", "UPBIT_API_KEY": "", "TELEGRAM_BOT_TOKEN": "bad token!"}
    )
    assert updates == {"TOSS_CLIENT_ID": SECRET}
    assert set(errors) == {"TELEGRAM_BOT_TOKEN"}
    assert "bad" not in errors["TELEGRAM_BOT_TOKEN"]
    on, _ = env_keys.validate({"toss_trading": "on"})
    off, _ = env_keys.validate({"toss_trading": "off"})
    assert on == {"TOSS_TRADING_ENABLED": "true"} and off == {"TOSS_TRADING_ENABLED": "false"}


def test_save_keeps_other_lines_dedupes_and_logs_names_only(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    old = "# 내 메모\nOTHER=1\nTOSS_CLIENT_ID=old\nTOSS_CLIENT_ID=older\n"
    env.write_text(old, encoding="utf-8")
    saved = env_keys.save_keys(
        tmp_path,
        {"TOSS_CLIENT_ID": SECRET, "ECOS_API_KEY": "fake-ecos-value"},
        datetime(2026, 10, 10, 1, 0, tzinfo=UTC),
    )
    assert saved == ["TOSS_CLIENT_ID", "ECOS_API_KEY"]
    lines = env.read_text(encoding="utf-8").splitlines()
    want = ["# 내 메모", "OTHER=1", f"TOSS_CLIENT_ID={SECRET}", "ECOS_API_KEY=fake-ecos-value"]
    assert lines == want
    log = (tmp_path / "data" / "settings" / "changes.jsonl").read_text(encoding="utf-8")
    assert SECRET not in log and "fake-ecos-value" not in log and "TOSS_CLIENT_ID" in log
    status = env_keys.key_status(tmp_path)
    assert status["TOSS_CLIENT_ID"] == {"set": True, "saved": "2026-10-10 10:00"}
    assert status["UPBIT_API_KEY"]["set"] is False
    assert SECRET not in json.dumps(status)
    assert env_keys.save_keys(tmp_path, {}) == []


def test_keys_tab_never_renders_values(tmp_path: Path) -> None:
    from auto_trading.ui.settings_extra import keys_tab

    (tmp_path / ".env").write_text(f"TOSS_CLIENT_ID={SECRET}\n", encoding="utf-8")
    tab, forms = keys_tab(env_keys.key_status(tmp_path), token="T", errors={}, tests={})
    html = tab + forms
    assert SECRET not in html
    assert 'type="password"' in html and 'value=""' in html and "설정됨" in html


class FakePS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, script: str) -> str:
        self.calls.append(script)
        return ""


def test_register_only_missing_and_reenable(tmp_path: Path) -> None:
    tdir = tmp_path / task_setup.TEMPLATE_DIR
    tdir.mkdir(parents=True)
    for t in task_setup.TASKS:
        (tdir / f"{t.name}.xml").write_text(f"<Task>{{REPO}}\\{t.name}</Task>", encoding="utf-8")
    existing = {
        "AutoTrading_Toss_Accumulate": TaskState("AutoTrading_Toss_Accumulate", "Ready"),
        "AutoTrading_Toss_FX": TaskState("AutoTrading_Toss_FX", "Disabled"),
    }
    ps = FakePS()
    made, enabled = task_setup.register_missing(tmp_path, ["glide"], existing, ps)
    # glide 는 toss 묶음도 함께
    assert made == ["AutoTrading_Toss_M3_Chat", "AutoTrading_Toss_M3_Retry"]
    assert enabled == ["AutoTrading_Toss_FX"]
    assert not any("AutoTrading_Toss_Accumulate" in c for c in ps.calls)  # 있던 예약은 그대로
    assert sum("Register-ScheduledTask" in c for c in ps.calls) == 2


def test_disable_only_enabled_ones() -> None:
    existing = {
        "AutoTrading_Upbit_T2_Live": TaskState("AutoTrading_Upbit_T2_Live", "Ready"),
        "AutoTrading_Upbit_T2_Weekly": TaskState("AutoTrading_Upbit_T2_Weekly", "Disabled"),
    }
    ps = FakePS()
    assert task_setup.disable(["cruise"], existing, ps) == ["AutoTrading_Upbit_T2_Live"]
    assert len(ps.calls) == 1 and "Disable-ScheduledTask" in ps.calls[0]


def test_status_parses_single_and_list() -> None:
    one = '{"n":"AutoTrading_Backup","s":"Ready","nx":"10-12 23:30","lr":"","res":267011}'
    got = task_setup.status(lambda _s: one)
    assert got["AutoTrading_Backup"].last_result == 267011
    assert task_setup.status(lambda _s: "") == {}


def test_normalize_export_strips_user_and_path() -> None:
    repo = Path(r"C:\Users\me\Auto_Trading")
    xml = (
        '<?xml version="1.0" encoding="UTF-16"?><Task><RegistrationInfo><Author>PC\\me</Author>'
        "</RegistrationInfo><Principals><Principal><UserId>S-1-5-21</UserId>"
        "<LogonType>S4U</LogonType></Principal></Principals>"
        "<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>"
        r"<Command>C:\Users\me\Auto_Trading\.venv\python.exe</Command></Task>"
    )
    out = task_setup.normalize_export(xml, repo)
    assert "\\me" not in out and "S-1-5-21" not in out
    assert r"{REPO}\.venv" in out and "InteractiveToken" in out and "UTF-8" in out
    assert "<DisallowStartIfOnBatteries>false" in out


def test_templates_are_clean() -> None:
    root = Path(__file__).resolve().parents[2]
    for t in task_setup.TASKS:
        xml = (root / task_setup.TEMPLATE_DIR / f"{t.name}.xml").read_text(encoding="utf-8")
        bare = xml.replace("<UserId>{USER}</UserId>", "")  # 로그인 예약은 등록할 때 지금 사용자로
        assert "{REPO}" in xml and "<UserId>" not in bare and "S4U" not in xml, t.name
        assert "seolh" not in xml.lower(), t.name


def test_render_xml_fills_user() -> None:
    out = task_setup.render_xml("<U>{USER}</U><R>{REPO}</R>", Path("C:/r"), user=r"PC\me&you")
    assert out == r"<U>PC\me&amp;you</U><R>" + str(Path("C:/r")) + "</R>"
