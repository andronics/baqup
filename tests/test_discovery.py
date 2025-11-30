"""Tests for the discovery module."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.discovery import (
    Discovery,
    DockerConnectionError,
    create_discovery_from_config,
    load_default_schedules_from_config,
)
from src.models import ContainerBackupConfig, ScheduleConfig, TargetConfig


class MockContainer:
    """Mock Docker container for testing."""

    def __init__(self, id: str, name: str, labels: dict[str, str]) -> None:
        self.id = id
        self.name = name
        self.labels = labels


class MockDockerClient:
    """Mock Docker client for testing."""

    def __init__(self, containers: list[MockContainer] | None = None) -> None:
        self._containers = containers or []
        self.containers = MagicMock()
        self.containers.list = MagicMock(return_value=self._containers)
        self.containers.get = MagicMock(side_effect=self._get_container)

    def _get_container(self, container_id: str) -> MockContainer:
        for c in self._containers:
            if c.id.startswith(container_id):
                return c
        raise Exception("Container not found")

    def ping(self) -> bool:
        return True


class TestDiscoveryLabelParsing:
    """Tests for label parsing logic."""

    def test_parse_enabled_container(self) -> None:
        """Test parsing a container with backup.enabled=true."""
        container = MockContainer(
            id="abc123def456",
            name="mycontainer",
            labels={"backup.enabled": "true"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert len(configs) == 1
        assert configs[0].container_id == "abc123def456"
        assert configs[0].container_name == "mycontainer"
        assert configs[0].enabled is True

    def test_parse_stop_flag(self) -> None:
        """Test parsing backup.stop label."""
        container = MockContainer(
            id="abc123def456",
            name="mycontainer",
            labels={"backup.enabled": "true", "backup.stop": "true"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert len(configs) == 1
        assert configs[0].stop is True

    def test_stop_defaults_to_false(self) -> None:
        """Test that backup.stop defaults to false."""
        container = MockContainer(
            id="abc123def456",
            name="mycontainer",
            labels={"backup.enabled": "true"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert configs[0].stop is False


class TestScheduleParsing:
    """Tests for schedule parsing from labels."""

    def test_parse_custom_schedule(self) -> None:
        """Test parsing custom schedule from labels."""
        container = MockContainer(
            id="abc123def456",
            name="mycontainer",
            labels={
                "backup.enabled": "true",
                "backup.schedule.custom.cron": "0 5 * * *",
                "backup.schedule.custom.retention": "14",
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert "custom" in configs[0].schedules
        assert configs[0].schedules["custom"].cron == "0 5 * * *"
        assert configs[0].schedules["custom"].retention == 14

    def test_fallback_to_default_schedules(self) -> None:
        """Test that default schedules are used when not specified."""
        container = MockContainer(
            id="abc123def456",
            name="mycontainer",
            labels={"backup.enabled": "true"},
        )
        client = MockDockerClient([container])

        default_schedules = {
            "daily": ScheduleConfig(name="daily", cron="0 3 * * *", retention=7),
        }
        discovery = Discovery(docker_client=client, default_schedules=default_schedules)

        configs = discovery.find_enabled_containers()

        assert "daily" in configs[0].schedules
        assert configs[0].schedules["daily"].cron == "0 3 * * *"
        assert configs[0].schedules["daily"].retention == 7

    def test_override_default_schedule(self) -> None:
        """Test that container labels override default schedules."""
        container = MockContainer(
            id="abc123def456",
            name="mycontainer",
            labels={
                "backup.enabled": "true",
                "backup.schedule.daily.cron": "0 4 * * *",
                "backup.schedule.daily.retention": "30",
            },
        )
        client = MockDockerClient([container])

        default_schedules = {
            "daily": ScheduleConfig(name="daily", cron="0 3 * * *", retention=7),
        }
        discovery = Discovery(docker_client=client, default_schedules=default_schedules)

        configs = discovery.find_enabled_containers()

        # Container labels should override defaults
        assert configs[0].schedules["daily"].cron == "0 4 * * *"
        assert configs[0].schedules["daily"].retention == 30

    def test_invalid_retention_uses_default(self) -> None:
        """Test that invalid retention value falls back to default."""
        container = MockContainer(
            id="abc123def456",
            name="mycontainer",
            labels={
                "backup.enabled": "true",
                "backup.schedule.test.cron": "0 5 * * *",
                "backup.schedule.test.retention": "invalid",
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert configs[0].schedules["test"].retention == 7  # default


class TestTargetParsing:
    """Tests for target parsing from labels."""

    def test_parse_postgres_target(self) -> None:
        """Test parsing PostgreSQL target."""
        container = MockContainer(
            id="abc123def456",
            name="mydb",
            labels={
                "backup.enabled": "true",
                "backup.postgres.main.port": "5432",
                "backup.postgres.main.username": "postgres",
                "backup.postgres.main.password_env": "POSTGRES_PASSWORD",
                "backup.postgres.main.databases": "all",
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert len(configs[0].targets) == 1
        target = configs[0].targets[0]
        assert target.type == "postgres"
        assert target.instance == "main"
        assert target.properties["port"] == 5432
        assert target.properties["username"] == "postgres"
        assert target.properties["password_env"] == "POSTGRES_PASSWORD"
        assert target.properties["databases"] == "all"

    def test_parse_filesystem_target(self) -> None:
        """Test parsing filesystem target."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={
                "backup.enabled": "true",
                "backup.fs.config.path": "/config",
                "backup.fs.config.exclude": "*.log,cache/*",
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert len(configs[0].targets) == 1
        target = configs[0].targets[0]
        assert target.type == "fs"
        assert target.instance == "config"
        assert target.properties["path"] == "/config"
        assert target.properties["exclude"] == "*.log,cache/*"

    def test_parse_multiple_targets(self) -> None:
        """Test parsing multiple backup targets."""
        container = MockContainer(
            id="abc123def456",
            name="fullstack",
            labels={
                "backup.enabled": "true",
                "backup.postgres.db.port": "5432",
                "backup.fs.uploads.path": "/data/uploads",
                "backup.fs.config.path": "/config",
                "backup.redis.cache.port": "6379",
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert len(configs[0].targets) == 4

        types_instances = {(t.type, t.instance) for t in configs[0].targets}
        assert ("postgres", "db") in types_instances
        assert ("fs", "uploads") in types_instances
        assert ("fs", "config") in types_instances
        assert ("redis", "cache") in types_instances

    def test_target_schedule_override(self) -> None:
        """Test target-specific schedule override."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={
                "backup.enabled": "true",
                "backup.fs.config.path": "/config",
                "backup.fs.config.schedule": "hourly",
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert configs[0].targets[0].schedule == "hourly"

    def test_target_compress_override(self) -> None:
        """Test target-specific compress override."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={
                "backup.enabled": "true",
                "backup.fs.config.path": "/config",
                "backup.fs.config.compress": "false",
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert configs[0].targets[0].compress is False


class TestLabelEdgeCases:
    """Tests for edge cases in label parsing."""

    def test_ignore_non_backup_labels(self) -> None:
        """Test that non-backup labels are ignored."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={
                "backup.enabled": "true",
                "backup.fs.config.path": "/config",
                "traefik.enable": "true",
                "com.docker.compose.service": "myapp",
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        # Should only have the fs target, not confused by other labels
        assert len(configs[0].targets) == 1
        assert configs[0].targets[0].type == "fs"

    def test_empty_labels(self) -> None:
        """Test container with backup.enabled but no other labels."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={"backup.enabled": "true"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert len(configs) == 1
        assert len(configs[0].targets) == 0

    def test_malformed_target_labels_ignored(self) -> None:
        """Test that malformed target labels are handled gracefully."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={
                "backup.enabled": "true",
                "backup.invalid": "value",  # No instance
                "backup.fs.config.path": "/config",  # Valid
            },
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        # Should only parse the valid fs target
        assert len(configs[0].targets) == 1
        assert configs[0].targets[0].type == "fs"

    def test_container_name_with_leading_slash(self) -> None:
        """Test that leading slash is removed from container name."""
        container = MockContainer(
            id="abc123def456",
            name="/myapp",  # Docker sometimes includes leading slash
            labels={"backup.enabled": "true"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        assert configs[0].container_name == "myapp"


class TestDiscoveryConfiguration:
    """Tests for Discovery configuration from YAML config."""

    def test_load_schedules_from_config(self) -> None:
        """Test loading schedules from controller config dict."""
        config = {
            "defaults": {
                "schedules": {
                    "daily": {"cron": "0 2 * * *", "retention": 14},
                    "weekly": {"cron": "0 3 * * 0", "retention": 8},
                }
            }
        }

        schedules = load_default_schedules_from_config(config)

        assert len(schedules) == 2
        assert schedules["daily"].cron == "0 2 * * *"
        assert schedules["daily"].retention == 14
        assert schedules["weekly"].cron == "0 3 * * 0"
        assert schedules["weekly"].retention == 8

    def test_create_discovery_from_config(self) -> None:
        """Test creating Discovery instance from config."""
        config = {
            "defaults": {
                "schedules": {
                    "daily": {"cron": "0 2 * * *", "retention": 14},
                },
                "target": {
                    "schedule": "daily",
                    "compress": False,
                },
            }
        }

        client = MockDockerClient([])
        discovery = create_discovery_from_config(config, docker_client=client)

        assert discovery._default_target_schedule == "daily"
        assert discovery._default_target_compress is False
        assert "daily" in discovery._default_schedules

    def test_invalid_schedule_config_logged(self) -> None:
        """Test that invalid schedule configs are logged and skipped."""
        config = {
            "defaults": {
                "schedules": {
                    "valid": {"cron": "0 2 * * *", "retention": 14},
                    "invalid": "not a dict",  # Invalid format
                }
            }
        }

        schedules = load_default_schedules_from_config(config)

        assert len(schedules) == 1
        assert "valid" in schedules
        assert "invalid" not in schedules


class TestDiscoveryMethods:
    """Tests for Discovery class methods."""

    def test_get_container_config(self) -> None:
        """Test getting config for a specific container."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={"backup.enabled": "true"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        config = discovery.get_container_config("abc123")

        assert config is not None
        assert config.container_id == "abc123def456"

    def test_get_container_config_disabled(self) -> None:
        """Test getting config for a disabled container returns None."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={"backup.enabled": "false"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        config = discovery.get_container_config("abc123")

        assert config is None

    def test_refresh_alias(self) -> None:
        """Test that refresh() is an alias for find_enabled_containers()."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={"backup.enabled": "true"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.refresh()

        assert len(configs) == 1
        assert configs[0].container_name == "myapp"


class TestBuiltinDefaults:
    """Tests for built-in default schedules."""

    def test_builtin_defaults(self) -> None:
        """Test that built-in defaults are used when no defaults provided."""
        container = MockContainer(
            id="abc123def456",
            name="myapp",
            labels={"backup.enabled": "true"},
        )
        client = MockDockerClient([container])
        discovery = Discovery(docker_client=client)

        configs = discovery.find_enabled_containers()

        # Should have built-in defaults
        assert "daily" in configs[0].schedules
        assert "hourly" in configs[0].schedules
        assert "weekly" in configs[0].schedules
