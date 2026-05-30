"""Phase 8G + 8H tests: forecasting + drift + backup/restore + migrations."""

import gzip
import json
import tarfile
from pathlib import Path

import pytest

from execution.ops_platform import (
    audit_log, backup_restore, cache_bus, capability_versions, controls,
    forecasting, migrations, runtime_queue, signed_audit, workflow_runner,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(workflow_runner, "_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(backup_restore, "_BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup_restore, "_OPS_ROOT", tmp_path / "ops_root")
    monkeypatch.setattr(migrations, "_MIGRATIONS_DIR", tmp_path / "migrations")
    monkeypatch.setattr(migrations, "_APPLIED_PATH", tmp_path / "migrations" / "applied.json")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    controls._RATE_LIMIT_HITS.clear()
    cache_bus.reset_for_tests()
    migrations._REGISTRY.clear()
    yield
    migrations._REGISTRY.clear()


# ── forecasting ───────────────────────────────────────────────────────


def test_queue_forecast_returns_dataclass():
    f = forecasting.forecast_queue_saturation(horizon_minutes=30)
    assert hasattr(f, "metric")
    assert f.metric == "queue.depth"


def test_queue_forecast_low_confidence_when_no_data():
    f = forecasting.forecast_queue_saturation()
    assert f.confidence == 0.0


def test_incident_forecast_returns_probability():
    f = forecasting.forecast_incident_probability(horizon_hours=6)
    assert 0.0 <= (f.predicted_value or 0.0) <= 1.0


def test_alert_storm_forecast():
    f = forecasting.forecast_alert_storm()
    assert hasattr(f, "metric")


def test_capacity_recommendations_returns_list():
    out = forecasting.capacity_recommendations()
    assert isinstance(out, list)


def test_routing_drift_empty_when_no_data():
    assert forecasting.detect_routing_drift() == []


def test_approval_bottlenecks_returns_list():
    out = forecasting.detect_approval_bottlenecks(age_hours=8)
    assert isinstance(out, list)


# ── backup_restore ────────────────────────────────────────────────────


def test_snapshot_creates_archive(tmp_path):
    # Seed the ops_root with a file
    ops_root = backup_restore._OPS_ROOT
    ops_root.mkdir(parents=True, exist_ok=True)
    (ops_root / "hello.json").write_text('{"x":1}')
    result = backup_restore.snapshot(actor="alice")
    assert Path(result.archive_path).exists()
    assert result.file_count >= 1
    assert result.sha256


def test_restore_extracts_archive(tmp_path):
    ops_root = backup_restore._OPS_ROOT
    ops_root.mkdir(parents=True, exist_ok=True)
    (ops_root / "test_file.json").write_text('{"a":2}')
    result = backup_restore.snapshot()
    restored = backup_restore.restore(archive_path=result.archive_path,
                                          restore_to=str(tmp_path / "restored"))
    restored_file = Path(restored.restored_to) / "test_file.json"
    assert restored_file.exists()


def test_list_snapshots_returns_metadata(tmp_path):
    backup_restore._OPS_ROOT.mkdir(parents=True, exist_ok=True)
    (backup_restore._OPS_ROOT / "x.txt").write_text("y")
    backup_restore.snapshot()
    snaps = backup_restore.list_snapshots()
    assert snaps and snaps[0].get("bytes", 0) > 0


def test_restore_unsafe_archive_skips_bad_member(tmp_path):
    # Craft an archive with a parent-relative member that should be skipped
    bad_archive = tmp_path / "bad.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = 5
        import io
        tar.addfile(info, io.BytesIO(b"hello"))
    result = backup_restore.restore(archive_path=str(bad_archive),
                                        restore_to=str(tmp_path / "restored"))
    # Bad member skipped → file_count == 0
    assert result.file_count == 0


def test_restore_missing_archive_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_restore.restore(archive_path=str(tmp_path / "nope.tar.gz"))


# ── migrations ────────────────────────────────────────────────────────


def test_register_then_pending():
    migrations.register_migration(version="2026.05.0001", description="x",
                                      up=lambda: None, down=lambda: None)
    p = migrations.pending()
    assert any(m.version == "2026.05.0001" for m in p)


def test_apply_pending_runs_in_order():
    order = []
    migrations.register_migration(version="2026.05.0002", description="b",
                                      up=lambda: order.append("b"))
    migrations.register_migration(version="2026.05.0001", description="a",
                                      up=lambda: order.append("a"))
    results = migrations.apply_pending()
    assert order == ["a", "b"]
    assert all(r["applied"] for r in results)


def test_apply_pending_skips_already_applied():
    migrations.register_migration(version="2026.05.0010", description="x",
                                      up=lambda: None)
    migrations.apply_pending()
    second = migrations.apply_pending()
    assert second == []


def test_rollback_one_calls_down():
    seen = []
    migrations.register_migration(version="2026.05.0020", description="r",
                                      up=lambda: None,
                                      down=lambda: seen.append("down"))
    migrations.apply_pending()
    result = migrations.rollback_one()
    assert result["rolled_back"] is True
    assert seen == ["down"]


def test_rollback_without_down_returns_reason():
    migrations.register_migration(version="2026.05.0030", description="r",
                                      up=lambda: None, down=None)
    migrations.apply_pending()
    result = migrations.rollback_one()
    assert result["rolled_back"] is False
    assert "no down()" in result["reason"]


def test_status_reports_registered():
    migrations.register_migration(version="2026.05.0040", description="x",
                                      up=lambda: None)
    status = migrations.status()
    assert any(m["version"] == "2026.05.0040" for m in status["registered"])
