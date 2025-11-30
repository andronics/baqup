"""Tests for the models module."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.models import (
    BackupEvent,
    BackupEventType,
    BackupJob,
    BackupResult,
    ContainerBackupConfig,
    ControllerState,
    ScheduleConfig,
    TargetConfig,
    TargetState,
    TargetStatus,
    _parse_bool,
    _parse_label_value,
)


class TestScheduleConfig:
    """Tests for ScheduleConfig dataclass."""

    def test_from_labels_basic(self) -> None:
        """Test creating ScheduleConfig from labels."""
        labels = {
            "backup.schedule.daily.cron": "0 3 * * *",
            "backup.schedule.daily.retention": "7",
        }

        config = ScheduleConfig.from_labels("daily", labels)

        assert config is not None
        assert config.name == "daily"
        assert config.cron == "0 3 * * *"
        assert config.retention == 7

    def test_from_labels_missing_cron(self) -> None:
        """Test that missing cron returns None."""
        labels = {"backup.schedule.daily.retention": "7"}

        config = ScheduleConfig.from_labels("daily", labels)

        assert config is None

    def test_from_labels_default_retention(self) -> None:
        """Test that missing retention uses default of 7."""
        labels = {"backup.schedule.daily.cron": "0 3 * * *"}

        config = ScheduleConfig.from_labels("daily", labels)

        assert config is not None
        assert config.retention == 7


class TestTargetConfig:
    """Tests for TargetConfig dataclass."""

    def test_from_labels_postgres(self) -> None:
        """Test creating postgres TargetConfig from labels."""
        labels = {
            "backup.postgres.main.port": "5432",
            "backup.postgres.main.username": "postgres",
            "backup.postgres.main.databases": "all",
            "backup.postgres.main.schedule": "hourly",
            "backup.postgres.main.compress": "false",
        }

        config = TargetConfig.from_labels("postgres", "main", labels)

        assert config.type == "postgres"
        assert config.instance == "main"
        assert config.schedule == "hourly"
        assert config.compress is False
        assert config.properties["port"] == 5432
        assert config.properties["username"] == "postgres"
        assert config.properties["databases"] == "all"

    def test_from_labels_fs(self) -> None:
        """Test creating fs TargetConfig from labels."""
        labels = {
            "backup.fs.data.path": "/data",
            "backup.fs.data.exclude": "*.log",
        }

        config = TargetConfig.from_labels("fs", "data", labels)

        assert config.type == "fs"
        assert config.instance == "data"
        assert config.properties["path"] == "/data"
        assert config.properties["exclude"] == "*.log"

    def test_from_labels_defaults(self) -> None:
        """Test that defaults are applied."""
        labels = {"backup.fs.data.path": "/data"}

        config = TargetConfig.from_labels(
            "fs", "data", labels, default_schedule="weekly", default_compress=False
        )

        assert config.schedule == "weekly"
        assert config.compress is False

    def test_invalid_target_type(self) -> None:
        """Test that invalid target type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid target type"):
            TargetConfig(
                type="invalid",
                instance="main",
                schedule="daily",
                compress=True,
            )

    def test_valid_target_types(self) -> None:
        """Test all valid target types."""
        valid_types = ["postgres", "mariadb", "mysql", "mongo", "redis", "sqlite", "fs"]

        for target_type in valid_types:
            config = TargetConfig(
                type=target_type,
                instance="test",
                schedule="daily",
                compress=True,
            )
            assert config.type == target_type


class TestContainerBackupConfig:
    """Tests for ContainerBackupConfig dataclass."""

    def test_from_labels_minimal(self) -> None:
        """Test creating config with minimal labels."""
        labels = {"backup.enabled": "true"}

        config = ContainerBackupConfig.from_labels(
            container_id="abc123",
            container_name="mycontainer",
            labels=labels,
        )

        assert config.container_id == "abc123"
        assert config.container_name == "mycontainer"
        assert config.enabled is True
        assert config.stop is False
        assert len(config.targets) == 0

    def test_from_labels_full(self) -> None:
        """Test creating config with full labels."""
        labels = {
            "backup.enabled": "true",
            "backup.stop": "true",
            "backup.schedule.daily.cron": "0 2 * * *",
            "backup.schedule.daily.retention": "14",
            "backup.postgres.main.port": "5432",
            "backup.fs.config.path": "/config",
        }

        config = ContainerBackupConfig.from_labels(
            container_id="abc123",
            container_name="mycontainer",
            labels=labels,
        )

        assert config.enabled is True
        assert config.stop is True
        assert "daily" in config.schedules
        assert len(config.targets) == 2


class TestBackupJob:
    """Tests for BackupJob dataclass."""

    def test_creation(self) -> None:
        """Test creating a BackupJob."""
        container = ContainerBackupConfig(
            container_id="abc123",
            container_name="mycontainer",
            enabled=True,
            stop=False,
        )
        target = TargetConfig(
            type="postgres",
            instance="main",
            schedule="daily",
            compress=True,
        )
        schedule = ScheduleConfig(name="daily", cron="0 3 * * *", retention=7)
        now = datetime.now()

        job = BackupJob(
            container=container,
            target=target,
            schedule=schedule,
            triggered_at=now,
        )

        assert job.container == container
        assert job.target == target
        assert job.schedule == schedule
        assert job.triggered_at == now


class TestBackupResult:
    """Tests for BackupResult dataclass."""

    def test_success_result(self) -> None:
        """Test creating a successful result."""
        container = ContainerBackupConfig(
            container_id="abc123",
            container_name="mycontainer",
            enabled=True,
            stop=False,
        )
        target = TargetConfig(
            type="postgres",
            instance="main",
            schedule="daily",
            compress=True,
        )
        schedule = ScheduleConfig(name="daily", cron="0 3 * * *", retention=7)
        job = BackupJob(
            container=container,
            target=target,
            schedule=schedule,
            triggered_at=datetime.now(),
        )

        result = BackupResult(
            job=job,
            success=True,
            staging_path=Path("/staging/mycontainer/postgres-main/backup.sql.gz"),
            error=None,
            duration_seconds=12.5,
        )

        assert result.success is True
        assert result.staging_path is not None
        assert result.error is None

    def test_failure_result(self) -> None:
        """Test creating a failure result."""
        container = ContainerBackupConfig(
            container_id="abc123",
            container_name="mycontainer",
            enabled=True,
            stop=False,
        )
        target = TargetConfig(
            type="postgres",
            instance="main",
            schedule="daily",
            compress=True,
        )
        schedule = ScheduleConfig(name="daily", cron="0 3 * * *", retention=7)
        job = BackupJob(
            container=container,
            target=target,
            schedule=schedule,
            triggered_at=datetime.now(),
        )

        result = BackupResult(
            job=job,
            success=False,
            staging_path=None,
            error="Connection refused",
            duration_seconds=0.5,
        )

        assert result.success is False
        assert result.staging_path is None
        assert result.error == "Connection refused"


class TestControllerState:
    """Tests for ControllerState dataclass."""

    def test_default_values(self) -> None:
        """Test that defaults are correctly initialized."""
        state = ControllerState()

        assert state.containers == {}
        assert state.target_states == {}
        assert len(state.recent_events) == 0

    def test_add_event(self) -> None:
        """Test adding events to state."""
        state = ControllerState()

        event = BackupEvent(
            timestamp=datetime.now(),
            event_type=BackupEventType.STARTED,
            container_name="mycontainer",
            target_type="postgres",
            target_instance="main",
            message="Starting backup",
        )
        state.recent_events.append(event)

        assert len(state.recent_events) == 1
        assert state.recent_events[0].event_type == BackupEventType.STARTED


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_parse_bool_true_values(self) -> None:
        """Test parsing true boolean values."""
        assert _parse_bool("true") is True
        assert _parse_bool("True") is True
        assert _parse_bool("TRUE") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("on") is True

    def test_parse_bool_false_values(self) -> None:
        """Test parsing false boolean values."""
        assert _parse_bool("false") is False
        assert _parse_bool("False") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False
        assert _parse_bool("off") is False
        assert _parse_bool("") is False
        assert _parse_bool(None) is False

    def test_parse_label_value_int(self) -> None:
        """Test parsing integer values."""
        assert _parse_label_value("5432") == 5432
        assert _parse_label_value("0") == 0
        assert _parse_label_value("-1") == -1

    def test_parse_label_value_bool(self) -> None:
        """Test parsing boolean values."""
        assert _parse_label_value("true") is True
        assert _parse_label_value("false") is False
        assert _parse_label_value("yes") is True
        assert _parse_label_value("no") is False

    def test_parse_label_value_string(self) -> None:
        """Test parsing string values."""
        assert _parse_label_value("hello") == "hello"
        assert _parse_label_value("/config") == "/config"
        assert _parse_label_value("*.log,cache/*") == "*.log,cache/*"
