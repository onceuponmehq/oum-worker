from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from oum_worker import launchd  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def test_parse_delay_compound():
    assert launchd.parse_delay("3h") == 10_800
    assert launchd.parse_delay("1h30m") == 5_400
    assert launchd.parse_delay("2d 4h 15m") == 188_100


def test_parse_delay_rejects_no_unit():
    with pytest.raises(ValueError):
        launchd.parse_delay("90")


def test_parse_target_delay_rounds_up():
    now = datetime(2026, 5, 6, 10, 5, 20, tzinfo=IST)
    assert launchd.parse_target("3h", None, now=now) == datetime(2026, 5, 6, 13, 6, tzinfo=IST)


def test_parse_target_time_rolls_to_tomorrow():
    now = datetime(2026, 5, 6, 18, 0, tzinfo=IST)
    assert launchd.parse_target(None, "17:30", now=now) == datetime(2026, 5, 7, 17, 30, tzinfo=IST)


def test_normalize_label_adds_prefix():
    assert launchd.normalize_label("nightly").startswith("com.oum.schedule.")


def test_normalize_label_preserves_explicit_com_prefix():
    assert launchd.normalize_label("com.acme.x") == "com.acme.x"


def test_resolve_workdir_known_alias(tmp_path):
    """Known repo aliases resolve to the OUM monorepo paths."""
    p = launchd.resolve_workdir(repo="oum-os", cwd=None)
    assert p.name == "oum-os"


import plistlib


def test_build_plist_contains_calendar_interval(tmp_path):
    target = datetime(2026, 5, 6, 17, 30, tzinfo=IST)
    payload = launchd.build_plist(
        label="com.oum.schedule.demo",
        cwd=tmp_path,
        command="echo hi",
        target=target,
        stdout_path=tmp_path / "o", stderr_path=tmp_path / "e",
    )
    parsed = plistlib.loads(payload)
    assert parsed["Label"] == "com.oum.schedule.demo"
    assert parsed["StartCalendarInterval"]["Hour"] == 17
    assert parsed["StartCalendarInterval"]["Minute"] == 30
    assert parsed["LaunchOnlyOnce"] is True
    assert parsed["AbandonProcessGroup"] is True


def test_write_plist_refuses_overwrite_without_replace(tmp_path):
    p = tmp_path / "x.plist"
    launchd.write_plist(p, b"<plist></plist>", replace=False)
    with pytest.raises(FileExistsError):
        launchd.write_plist(p, b"<plist></plist>", replace=False)
    launchd.write_plist(p, b"<plist></plist>", replace=True)  # no error
