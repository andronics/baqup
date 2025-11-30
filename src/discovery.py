"""Docker container discovery for baqup backup controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import docker
from docker.errors import DockerException

from src.models import ContainerBackupConfig, ScheduleConfig

if TYPE_CHECKING:
    from docker import DockerClient
    from docker.models.containers import Container

logger = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """Base exception for discovery errors."""

    pass


class DockerConnectionError(DiscoveryError):
    """Raised when unable to connect to Docker daemon."""

    pass


class Discovery:
    """Discovers and parses backup configurations from Docker containers.

    This class connects to the Docker daemon, finds containers with backup
    labels enabled, and parses their label configuration into structured
    ContainerBackupConfig objects.
    """

    # Label prefix for all backup-related labels
    LABEL_PREFIX = "backup."
    ENABLED_LABEL = "backup.enabled"

    def __init__(
        self,
        docker_client: DockerClient | None = None,
        default_schedules: dict[str, ScheduleConfig] | None = None,
        default_target_schedule: str = "daily",
        default_target_compress: bool = True,
    ) -> None:
        """Initialize the Discovery instance.

        Args:
            docker_client: Optional pre-configured Docker client. If not provided,
                           connects to the local Docker daemon.
            default_schedules: Default schedule configurations to fall back to
                               when not specified in container labels.
            default_target_schedule: Default schedule name for targets without
                                     explicit schedule. Defaults to "daily".
            default_target_compress: Default compression setting for targets.
                                     Defaults to True.

        Raises:
            DockerConnectionError: If unable to connect to Docker daemon.
        """
        self._client = docker_client
        self._default_schedules = default_schedules or self._get_builtin_defaults()
        self._default_target_schedule = default_target_schedule
        self._default_target_compress = default_target_compress

    @property
    def client(self) -> DockerClient:
        """Get the Docker client, initializing if needed."""
        if self._client is None:
            try:
                self._client = docker.from_env()
                # Verify connection
                self._client.ping()
            except DockerException as e:
                raise DockerConnectionError(f"Failed to connect to Docker daemon: {e}") from e
        return self._client

    def find_enabled_containers(self) -> list[ContainerBackupConfig]:
        """Find all containers with backup.enabled=true and parse their config.

        Returns:
            List of ContainerBackupConfig for enabled containers.

        Raises:
            DockerConnectionError: If unable to communicate with Docker daemon.
        """
        configs: list[ContainerBackupConfig] = []

        try:
            # Query containers with the enabled label
            containers = self.client.containers.list(
                all=True, filters={"label": f"{self.ENABLED_LABEL}=true"}
            )
        except DockerException as e:
            raise DockerConnectionError(f"Failed to list containers: {e}") from e

        for container in containers:
            try:
                config = self._parse_container(container)
                if config.enabled:
                    configs.append(config)
                    logger.info(
                        "Discovered container",
                        extra={
                            "container_id": config.container_id[:12],
                            "container_name": config.container_name,
                            "targets": len(config.targets),
                        },
                    )
            except Exception as e:
                # Log warning but continue with other containers
                container_id = container.id or "unknown"
                logger.warning(
                    "Failed to parse container labels",
                    extra={
                        "container_id": container_id[:12],
                        "container_name": container.name or "unknown",
                        "error": str(e),
                    },
                )

        return configs

    def get_container_config(self, container_id: str) -> ContainerBackupConfig | None:
        """Get backup configuration for a specific container.

        Args:
            container_id: Docker container ID (full or short form).

        Returns:
            ContainerBackupConfig if the container exists and has backup enabled,
            None otherwise.

        Raises:
            DockerConnectionError: If unable to communicate with Docker daemon.
        """
        try:
            container = self.client.containers.get(container_id)
            config = self._parse_container(container)
            return config if config.enabled else None
        except docker.errors.NotFound:
            return None
        except DockerException as e:
            raise DockerConnectionError(f"Failed to get container {container_id}: {e}") from e

    def _parse_container(self, container: Container) -> ContainerBackupConfig:
        """Parse a container's labels into a ContainerBackupConfig.

        Args:
            container: Docker container object.

        Returns:
            ContainerBackupConfig with parsed settings.
        """
        labels = container.labels or {}
        backup_labels = self._extract_backup_labels(labels)

        container_id = container.id or ""
        container_name = container.name or ""

        return ContainerBackupConfig.from_labels(
            container_id=container_id,
            container_name=self._clean_container_name(container_name),
            labels=backup_labels,
            default_schedules=self._default_schedules,
            default_target_schedule=self._default_target_schedule,
            default_target_compress=self._default_target_compress,
        )

    def _extract_backup_labels(self, labels: dict[str, str]) -> dict[str, str]:
        """Extract only backup-related labels from container labels.

        Args:
            labels: Full container labels dict.

        Returns:
            Dict containing only labels starting with 'backup.'
        """
        return {k: v for k, v in labels.items() if k.startswith(self.LABEL_PREFIX)}

    def _clean_container_name(self, name: str) -> str:
        """Clean container name by removing leading slash if present.

        Docker sometimes includes a leading slash in container names.

        Args:
            name: Container name from Docker API.

        Returns:
            Cleaned container name.
        """
        return name.lstrip("/")

    @staticmethod
    def _get_builtin_defaults() -> dict[str, ScheduleConfig]:
        """Get built-in default schedules.

        These are used when no controller config is provided.
        """
        return {
            "daily": ScheduleConfig(name="daily", cron="0 3 * * *", retention=7),
            "hourly": ScheduleConfig(name="hourly", cron="0 * * * *", retention=24),
            "weekly": ScheduleConfig(name="weekly", cron="0 4 * * 0", retention=4),
        }

    def refresh(self) -> list[ContainerBackupConfig]:
        """Refresh container discovery.

        This is an alias for find_enabled_containers() for semantic clarity
        in the orchestration loop.

        Returns:
            List of ContainerBackupConfig for enabled containers.
        """
        return self.find_enabled_containers()


def load_default_schedules_from_config(config: dict[str, Any]) -> dict[str, ScheduleConfig]:
    """Load default schedules from a controller configuration dict.

    Args:
        config: Controller configuration dict (parsed from YAML).

    Returns:
        Dict of schedule name to ScheduleConfig.
    """
    schedules: dict[str, ScheduleConfig] = {}

    defaults = config.get("defaults", {})
    schedule_configs = defaults.get("schedules", {})

    for name, sched_config in schedule_configs.items():
        if isinstance(sched_config, dict) and "cron" in sched_config:
            schedules[name] = ScheduleConfig(
                name=name,
                cron=sched_config["cron"],
                retention=sched_config.get("retention", 7),
            )
        else:
            logger.warning(
                "Invalid schedule configuration",
                extra={"schedule_name": name, "config": sched_config},
            )

    return schedules


def create_discovery_from_config(
    config: dict[str, Any], docker_client: DockerClient | None = None
) -> Discovery:
    """Create a Discovery instance from controller configuration.

    Args:
        config: Controller configuration dict (parsed from YAML).
        docker_client: Optional pre-configured Docker client.

    Returns:
        Configured Discovery instance.
    """
    default_schedules = load_default_schedules_from_config(config)

    defaults = config.get("defaults", {})
    target_defaults = defaults.get("target", {})

    return Discovery(
        docker_client=docker_client,
        default_schedules=default_schedules,
        default_target_schedule=target_defaults.get("schedule", "daily"),
        default_target_compress=target_defaults.get("compress", True),
    )
