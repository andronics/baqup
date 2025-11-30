"""Data models for baqup backup controller."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

# Supported database types for backup targets
DATABASE_TYPES = frozenset({"postgres", "mariadb", "mysql", "mongo", "redis", "sqlite"})

# All supported target types (databases + filesystem)
TARGET_TYPES = DATABASE_TYPES | {"fs"}


class TargetStatus(str, Enum):
    """Status of a backup target."""

    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"


class BackupEventType(str, Enum):
    """Types of backup events."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ScheduleConfig:
    """Configuration for a backup schedule."""

    name: str
    cron: str
    retention: int

    @classmethod
    def from_labels(
        cls, name: str, labels: dict[str, str], prefix: str = "backup.schedule"
    ) -> ScheduleConfig | None:
        """Create a ScheduleConfig from container labels.

        Args:
            name: The schedule name (e.g., 'daily', 'hourly')
            labels: Container labels dict
            prefix: Label prefix to look for

        Returns:
            ScheduleConfig if both cron and retention are found, None otherwise
        """
        cron_key = f"{prefix}.{name}.cron"
        retention_key = f"{prefix}.{name}.retention"

        cron = labels.get(cron_key)
        retention_str = labels.get(retention_key)

        if cron is None:
            return None

        try:
            retention = int(retention_str) if retention_str else 7
        except ValueError:
            retention = 7

        return cls(name=name, cron=cron, retention=retention)


@dataclass
class TargetConfig:
    """Configuration for a backup target (database or filesystem)."""

    type: str
    instance: str
    schedule: str
    compress: bool
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate target type."""
        if self.type not in TARGET_TYPES:
            raise ValueError(f"Invalid target type: {self.type}. Must be one of {TARGET_TYPES}")

    @classmethod
    def from_labels(
        cls,
        target_type: str,
        instance: str,
        labels: dict[str, str],
        default_schedule: str = "daily",
        default_compress: bool = True,
    ) -> TargetConfig:
        """Create a TargetConfig from container labels.

        Args:
            target_type: The target type (e.g., 'postgres', 'fs')
            instance: The instance name (e.g., 'main', 'config')
            labels: Container labels dict
            default_schedule: Default schedule if not specified
            default_compress: Default compress setting if not specified

        Returns:
            TargetConfig with parsed properties
        """
        prefix = f"backup.{target_type}.{instance}"
        properties: dict[str, Any] = {}

        # Extract all properties for this target
        for key, value in labels.items():
            if key.startswith(prefix + "."):
                prop_name = key[len(prefix) + 1 :]
                # Skip schedule and compress as they're handled separately
                if prop_name not in ("schedule", "compress"):
                    properties[prop_name] = _parse_label_value(value)

        # Get schedule and compress settings
        schedule = labels.get(f"{prefix}.schedule", default_schedule)
        compress_str = labels.get(f"{prefix}.compress")
        compress = _parse_bool(compress_str) if compress_str else default_compress

        return cls(
            type=target_type,
            instance=instance,
            schedule=schedule,
            compress=compress,
            properties=properties,
        )


@dataclass
class ContainerBackupConfig:
    """Complete backup configuration for a container."""

    container_id: str
    container_name: str
    enabled: bool
    stop: bool
    schedules: dict[str, ScheduleConfig] = field(default_factory=dict)
    targets: list[TargetConfig] = field(default_factory=list)

    @classmethod
    def from_labels(
        cls,
        container_id: str,
        container_name: str,
        labels: dict[str, str],
        default_schedules: dict[str, ScheduleConfig] | None = None,
        default_target_schedule: str = "daily",
        default_target_compress: bool = True,
    ) -> ContainerBackupConfig:
        """Create a ContainerBackupConfig from container labels.

        Args:
            container_id: Docker container ID
            container_name: Docker container name
            labels: Container labels dict
            default_schedules: Default schedules to fall back to
            default_target_schedule: Default schedule for targets
            default_target_compress: Default compress setting for targets

        Returns:
            ContainerBackupConfig with parsed configuration
        """
        # Parse enabled and stop flags
        enabled = _parse_bool(labels.get("backup.enabled", "false"))
        stop = _parse_bool(labels.get("backup.stop", "false"))

        # Parse schedules from labels
        schedules = _parse_schedules(labels, default_schedules or {})

        # Parse targets from labels
        targets = _parse_targets(labels, default_target_schedule, default_target_compress)

        return cls(
            container_id=container_id,
            container_name=container_name,
            enabled=enabled,
            stop=stop,
            schedules=schedules,
            targets=targets,
        )


@dataclass
class BackupJob:
    """A scheduled backup job to execute."""

    container: ContainerBackupConfig
    target: TargetConfig
    schedule: ScheduleConfig
    triggered_at: datetime


@dataclass
class BackupResult:
    """Result of a backup job execution."""

    job: BackupJob
    success: bool
    staging_path: Path | None
    error: str | None
    duration_seconds: float


@dataclass
class BackupEvent:
    """An event that occurred during backup operations."""

    timestamp: datetime
    event_type: BackupEventType
    container_name: str
    target_type: str
    target_instance: str
    message: str | None = None


@dataclass
class TargetState:
    """Current state of a backup target."""

    last_run: datetime | None
    last_success: datetime | None
    last_error: str | None
    next_run: datetime
    status: TargetStatus


@dataclass
class ControllerState:
    """Overall state of the backup controller."""

    containers: dict[str, ContainerBackupConfig] = field(default_factory=dict)
    target_states: dict[str, TargetState] = field(default_factory=dict)
    recent_events: deque[BackupEvent] = field(default_factory=lambda: deque(maxlen=100))


def _parse_bool(value: str | None) -> bool:
    """Parse a string value to boolean."""
    if value is None:
        return False
    return value.lower() in ("true", "1", "yes", "on")


def _parse_label_value(value: str) -> Any:
    """Parse a label value to appropriate type.

    Attempts to convert to int, then bool, otherwise returns string.
    """
    # Try int
    try:
        return int(value)
    except ValueError:
        pass

    # Check for boolean
    if value.lower() in ("true", "false", "yes", "no", "on", "off"):
        return _parse_bool(value)

    return value


def _parse_schedules(
    labels: dict[str, str], default_schedules: dict[str, ScheduleConfig]
) -> dict[str, ScheduleConfig]:
    """Parse schedule configurations from labels.

    Falls back to default schedules for any schedule not fully defined in labels.
    """
    schedules: dict[str, ScheduleConfig] = {}

    # Find all schedule names referenced in labels
    schedule_names: set[str] = set()
    for key in labels:
        if key.startswith("backup.schedule."):
            parts = key.split(".")
            if len(parts) >= 4:
                schedule_names.add(parts[2])

    # Parse each schedule from labels
    for name in schedule_names:
        schedule = ScheduleConfig.from_labels(name, labels)
        if schedule is not None:
            schedules[name] = schedule

    # Fall back to defaults for missing schedules
    for name, default in default_schedules.items():
        if name not in schedules:
            schedules[name] = default

    return schedules


def _parse_targets(
    labels: dict[str, str], default_schedule: str, default_compress: bool
) -> list[TargetConfig]:
    """Parse backup targets from labels."""
    targets: list[TargetConfig] = []
    seen: set[tuple[str, str]] = set()

    for key in labels:
        if not key.startswith("backup."):
            continue

        parts = key.split(".")
        if len(parts) < 4:
            continue

        target_type = parts[1]
        instance = parts[2]

        # Skip non-target labels
        if target_type in ("enabled", "stop", "schedule"):
            continue

        # Check if this is a valid target type
        if target_type not in TARGET_TYPES:
            continue

        # Avoid duplicates
        if (target_type, instance) in seen:
            continue
        seen.add((target_type, instance))

        target = TargetConfig.from_labels(
            target_type=target_type,
            instance=instance,
            labels=labels,
            default_schedule=default_schedule,
            default_compress=default_compress,
        )
        targets.append(target)

    return targets
