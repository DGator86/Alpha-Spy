"""Guards for the operational layer: installer, operator scripts, units, doctor.

These are static checks on the shipped artefacts. They exist because the
failures they cover only appear on the VPS, hours after a release, where they
are expensive to diagnose.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
SYSTEMD = REPO_ROOT / "systemd"

SERVICE_UNITS = sorted(SYSTEMD.glob("*.service"))
# Long-running services managed by alpha-spy.target. The oneshot units (backup,
# dojo) are timer-driven and deliberately outside it.
LONG_RUNNING = [u for u in SERVICE_UNITS if "Type=simple" in u.read_text(encoding="utf-8")]
# Everything that runs as the restricted account, oneshot or not.
SUITE_SERVICES = [u for u in SERVICE_UNITS if "User=alphaspy" in u.read_text(encoding="utf-8")]


# --------------------------------------------------------------------------
# Operator scripts
# --------------------------------------------------------------------------


def _shell_scripts() -> list[Path]:
    return sorted([*SCRIPTS.glob("*.sh"), SCRIPTS / "alpha-spy-backup", REPO_ROOT / "install.sh"])


@pytest.mark.parametrize("script", _shell_scripts(), ids=lambda p: p.name)
def test_shell_scripts_are_strict_and_executable(script):
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!"), f"{script.name} has no shebang"
    assert "set -Eeuo pipefail" in text, f"{script.name} does not fail fast"


@pytest.mark.parametrize("script", _shell_scripts(), ids=lambda p: p.name)
def test_shell_scripts_parse(script):
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, f"{script.name}: {result.stderr}"


@pytest.mark.parametrize(
    "script",
    [s for s in SCRIPTS.glob("*.sh") if "import yaml" in s.read_text(encoding="utf-8")],
    ids=lambda p: p.name,
)
def test_yaml_scripts_use_the_suite_interpreter(script):
    """Regression: PyYAML is a wheel dependency, not a system package.

    These scripts edit /etc/alpha-spy/config.yaml. Invoking bare `python3` meant
    they broke on any image without python3-yaml -- which the installer did
    not install -- leaving an operator unable to lock or unlock production.
    """
    text = script.read_text(encoding="utf-8")
    assert "/opt/alpha-spy/venv/bin/python" in text, (
        f"{script.name} parses YAML but never reaches for the suite interpreter"
    )
    assert not re.search(r"^\s*python3 - ", text, re.M), (
        f"{script.name} still invokes bare python3 for a YAML block"
    )


def test_installer_installs_a_system_yaml_fallback():
    # Join backslash continuations so a wrapped apt-get line still reads as one.
    text = (SCRIPTS / "install_vps.sh").read_text(encoding="utf-8").replace("\\\n", " ")
    apt_line = next(line for line in text.splitlines() if "apt-get install" in line)
    assert "python3-yaml" in apt_line


def test_installer_preserves_existing_data_roots():
    """The upgrade contract: never delete the live data directories."""
    text = (SCRIPTS / "install_vps.sh").read_text(encoding="utf-8")
    for protected in ("/var/lib/alpha-spy",):
        assert not re.search(rf"rm\s+-rf\s+{re.escape(protected)}\b", text), (
            f"installer removes {protected}"
        )


def test_uninstall_preserves_data_and_configuration():
    text = (SCRIPTS / "uninstall.sh").read_text(encoding="utf-8")
    for protected in ("/var/lib/alpha-spy", "/etc/alpha-spy"):
        assert not re.search(rf"rm\s+-rf?\s+{re.escape(protected)}\b", text), (
            f"uninstall removes {protected}"
        )


def test_production_unlock_requires_an_explicit_phrase():
    text = (SCRIPTS / "production_unlock.sh").read_text(encoding="utf-8")
    assert "ENABLE_REAL_SPY_OPTIONS_TRADING" in text
    assert "PRODUCTION_UNLOCKED" in text


def test_production_lock_removes_the_sentinel_before_anything_else():
    """Locking must fail closed: the sentinel goes first, so a later error
    still leaves submission blocked."""
    lines = [
        line.strip()
        for line in (SCRIPTS / "production_lock.sh").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    sentinel = next(i for i, line in enumerate(lines) if "PRODUCTION_UNLOCKED" in line)
    config_edit = next(i for i, line in enumerate(lines) if "config.yaml" in line)
    assert sentinel < config_edit


# --------------------------------------------------------------------------
# systemd units
# --------------------------------------------------------------------------


@pytest.mark.parametrize("unit", SUITE_SERVICES, ids=lambda p: p.name)
def test_suite_services_carry_the_documented_hardening(unit):
    """docs/SECURITY.md promises each of these for the suite services."""
    text = unit.read_text(encoding="utf-8")
    for directive in (
        "User=alphaspy",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=full",
        "UMask=0077",
    ):
        assert directive in text, f"{unit.name} is missing {directive}"
    assert "ReadWritePaths=" in text, f"{unit.name} declares no writable paths"


@pytest.mark.parametrize("unit", LONG_RUNNING, ids=lambda p: p.name)
def test_long_running_services_belong_to_the_target(unit):
    """Stopping alpha-spy.target must stop the session services with it."""
    text = unit.read_text(encoding="utf-8")
    assert "PartOf=alpha-spy.target" in text, f"{unit.name} will not stop with the target"


def test_target_pulls_in_every_long_running_service():
    """The services are started via the target's Wants=, not enable symlinks,
    so a service missing from that list would never start at boot."""
    wants = (SYSTEMD / "alpha-spy.target").read_text(encoding="utf-8")
    for unit in LONG_RUNNING:
        assert unit.name in wants, f"alpha-spy.target does not pull in {unit.name}"


@pytest.mark.parametrize("unit", SERVICE_UNITS, ids=lambda p: p.name)
def test_units_launch_only_installed_binaries(unit):
    """ExecStart must match what install_vps.sh actually puts on disk."""
    installed = {
        "/opt/alpha-spy/venv/bin/alpha-spy",
        "/usr/local/sbin/alpha-spy-backup",
        "/usr/local/sbin/alpha-spy-prune",
        "/usr/local/sbin/alpha-spy-ordinary-calendar",
    }
    for line in unit.read_text(encoding="utf-8").splitlines():
        if line.startswith("ExecStart="):
            binary = line.split("=", 1)[1].split()[0].lstrip("-@+!:")
            assert binary in installed, f"{unit.name} launches unmanaged {binary}"


@pytest.mark.parametrize("timer", sorted(SYSTEMD.glob("*.timer")), ids=lambda p: p.name)
def test_timers_are_pinned_to_eastern_time(timer):
    """The schedules are documented in Eastern; systemd defaults to UTC, which
    would drift by an hour across a DST change."""
    text = timer.read_text(encoding="utf-8")
    oncalendar = next(line for line in text.splitlines() if line.startswith("OnCalendar="))
    assert "America/New_York" in oncalendar, f"{timer.name} is not pinned to Eastern"
    assert "Persistent=true" in text, f"{timer.name} would skip a missed run"


def test_apis_bind_to_loopback_only():
    """README and SECURITY.md both promise 127.0.0.1 for the local APIs."""
    from alpha_spy.config import SuiteConfig

    assert SuiteConfig().dashboard.host == "127.0.0.1"
    decision = (REPO_ROOT / "systemd" / "alpha-spy-decision.service").read_text(encoding="utf-8")
    assert "0.0.0.0" not in decision


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def _doctor(tmp_path: Path, **universe) -> tuple[int, dict]:
    import yaml

    config = yaml.safe_load((REPO_ROOT / "config" / "suite.yaml").read_text(encoding="utf-8"))
    state = tmp_path / "state"
    config["paths"] = {
        "state_root": str(state),
        "database": f"{state}/journal/alpha-spy.db",
        "dashboard_database": f"{state}/dashboard/cc.sqlite",
        "universe_cache": f"{state}/reference/universe.csv",
        "model_dir": f"{state}/models",
        "report_dir": f"{state}/reports",
        "log_dir": f"{state}/logs",
        "production_sentinel": f"{state}/PRODUCTION_UNLOCKED",
    }
    config["universe"]["source"] = "fallback"
    config["universe"].update(universe)
    config["dashboard"].update(view_token="v", admin_token="a", ingest_token="i")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "alpha_spy.cli", "--config", str(path), "--state-root", str(state), "doctor"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT / "src"), "PATH": "/usr/bin:/bin"},
        timeout=300,
    )
    return result.returncode, json.loads(result.stdout)


def test_doctor_reports_degraded_when_the_universe_is_too_thin(tmp_path):
    """Regression: doctor answered "ok" while entries were universe-blocked.

    The bundled fallback carries 20 symbols against a configured minimum of
    450, so the coverage gate blocks every entry -- and the health check that
    operators are told to trust said everything was fine.
    """
    code, report = _doctor(tmp_path, minimum_symbols=450, minimum_covered_weight=0.90)
    assert report["doctor"] == "degraded"
    assert report["universe_meets_minimum"] is False
    assert any("universe below" in w for w in report["warnings"])
    assert any("blocked" in w for w in report["warnings"])
    # A thin universe fails closed by itself; it must not abort an install.
    assert code == 0


def test_doctor_reports_ok_when_coverage_is_satisfied(tmp_path):
    code, report = _doctor(tmp_path, minimum_symbols=20, minimum_covered_weight=0.40)
    assert report["doctor"] == "ok"
    assert report["universe_meets_minimum"] is True
    assert report["warnings"] == []
    assert code == 0


def test_doctor_reports_the_shipped_safety_posture(tmp_path):
    _, report = _doctor(tmp_path, minimum_symbols=20, minimum_covered_weight=0.40)
    assert report["trading_enabled"] is False
    assert report["submit_orders"] is False
    assert report["paper_mode"] is True
    assert report["production_unlocked"] is False
    assert report["tradier_environment"] == "sandbox"


# --------------------------------------------------------------------------
# Alpha-SPY canonical naming
# --------------------------------------------------------------------------


def test_no_legacy_identifiers_remain():
    """Alpha-SPY is standalone: no previous system's naming may reappear."""
    result = subprocess.run(
        ["bash", str(SCRIPTS / "check_legacy_identifiers.sh")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "unit",
    sorted(p.name for p in SYSTEMD.iterdir() if p.is_file()),
)
def test_units_use_the_alpha_spy_namespace(unit):
    assert unit.startswith("alpha-spy"), f"{unit} is outside the alpha-spy namespace"


def test_canonical_paths_and_account():
    """The install root, config, data, logs and service account are canonical."""
    from alpha_spy.config import PathsConfig

    paths = PathsConfig()
    assert str(paths.state_root) == "/var/lib/alpha-spy"
    assert str(paths.database) == "/var/lib/alpha-spy/journal/alpha-spy.db"
    assert str(paths.dashboard_database) == "/var/lib/alpha-spy/dashboard/command-center.sqlite"
    assert str(paths.log_dir) == "/var/log/alpha-spy"
    assert str(paths.production_sentinel) == "/etc/alpha-spy/PRODUCTION_UNLOCKED"

    installer = (SCRIPTS / "install_vps.sh").read_text(encoding="utf-8")
    assert "useradd --system --home /var/lib/alpha-spy" in installer
    assert "alphaspy" in installer
    assert "/opt/alpha-spy/venv" in installer


def test_console_script_is_alpha_spy():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'alpha-spy = "alpha_spy.cli:main"' in text


def test_installer_carries_no_migration_path():
    """A fresh install: no credential recovery, no preserved prior tree."""
    text = (SCRIPTS / "install_vps.sh").read_text(encoding="utf-8")
    for forbidden in ("Recover", "recovered", "BACKUP_DIR", "/var/backups/"):
        assert forbidden not in text, f"installer still carries migration logic: {forbidden}"


def test_dojo_is_retained_in_the_new_namespace():
    """Dojo is intentional functionality, not legacy baggage."""
    assert (SYSTEMD / "alpha-spy-dojo.service").is_file()
    assert (SYSTEMD / "alpha-spy-dojo.timer").is_file()
    from alpha_spy.services import DojoService  # noqa: F401
