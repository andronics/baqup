# baqup

Label-driven Docker backup controller with Traefik-style autodiscovery.

## Project Overview

baqup discovers Docker containers via labels, executes database dumps and filesystem archives via the Docker API, stages backups locally, then uploads to cloud storage via rclone. It's designed to be minimal configuration for simple setups while allowing full control when needed.

### Core Philosophy

- **Label-based autodiscovery**: Containers declare their backup requirements via Docker labels
- **Sensible defaults**: Works out of the box with minimal configuration
- **Explicit overrides**: Full control when needed via labels or controller config
- **API-first**: Internal state exposed for future Web UI (Traefik-style dashboard planned)

## Technical Stack

- **Language**: Python 3.11+
- **Docker interaction**: docker-py (Docker SDK for Python)
- **Upload backend**: rclone
- **Config format**: YAML
- **Logging**: JSON structured output

## Architecture

```
baqup/
├── config/
│   └── defaults.yml          # Controller defaults
├── src/
│   ├── __init__.py
│   ├── main.py               # Entrypoint, orchestration loop
│   ├── discovery.py          # Docker API, label parsing
│   ├── scheduler.py          # Cron evaluation, job triggering
│   ├── executor.py           # Pre-exec, archive pull, compression
│   ├── uploader.py           # Rclone wrapper
│   ├── retention.py          # Cleanup old backups
│   ├── notify.py             # Alerts on failure/success
│   ├── state.py              # Controller state (for future API/UI)
│   └── models.py             # Data classes
├── tests/
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Label Schema

### Container-Level

```yaml
labels:
  - "backup.enabled=true"       # Required to enable backups
  - "backup.stop=false"         # Stop container during backup (default: false)
```

### Schedules (Optional)

Falls back to controller defaults if not specified. `daily` is the default schedule for targets.

```yaml
labels:
  - "backup.schedule.daily.cron=0 3 * * *"
  - "backup.schedule.daily.retention=7"
  - "backup.schedule.hourly.cron=0 * * * *"
  - "backup.schedule.hourly.retention=24"
  - "backup.schedule.weekly.cron=0 4 * * 0"
  - "backup.schedule.weekly.retention=4"
```

### Database Targets

Pattern: `backup.{type}.{instance}.{property}`

Supported types: `postgres`, `mariadb`, `mysql`, `mongo`, `redis`, `sqlite`

```yaml
labels:
  - "backup.postgres.main.port=5432"
  - "backup.postgres.main.username=postgres"
  - "backup.postgres.main.password_env=POSTGRES_PASSWORD"
  - "backup.postgres.main.databases=all"
  - "backup.postgres.main.compress=true"
  - "backup.postgres.main.schedule=daily"
```

### Filesystem Targets

Pattern: `backup.fs.{instance}.{property}`

```yaml
labels:
  - "backup.fs.config.path=/config"
  - "backup.fs.config.exclude=*.log,cache/*"
  - "backup.fs.config.compress=true"
  - "backup.fs.config.pre_exec=sync"
  - "backup.fs.config.schedule=daily"
```

### Credential Resolution

Three methods supported:

```yaml
# Direct value
- "backup.postgres.main.password=literal"

# Reference container's environment variable
- "backup.postgres.main.password_env=POSTGRES_PASSWORD"

# Reference mounted secret file
- "backup.postgres.main.password_file=/run/secrets/db_pass"
```

## Controller Config

```yaml
# config/defaults.yml

defaults:
  schedules:
    daily:
      cron: "0 3 * * *"
      retention: 7
    hourly:
      cron: "0 * * * *"
      retention: 24
    weekly:
      cron: "0 4 * * 0"
      retention: 4
  
  target:
    schedule: daily
    compress: true

staging:
  path: /path/to/staging  # TBD - avoid "staging" as final name
  cleanup_after_upload: true

upload:
  backend: rclone
  remote: "gdrive:backups"

notifications:
  on_failure:
    - ntfy://ntfy.sh/my-alerts
  on_success: false

logging:
  level: info
  format: json

poll_interval: 60  # seconds between discovery/schedule checks
```

## Data Models

```python
@dataclass
class ScheduleConfig:
    name: str
    cron: str
    retention: int

@dataclass
class TargetConfig:
    type: str          # postgres, mariadb, mongo, redis, sqlite, fs
    instance: str
    schedule: str
    compress: bool
    properties: dict   # type-specific: port, username, path, exclude, etc.

@dataclass
class ContainerBackupConfig:
    container_id: str
    container_name: str
    enabled: bool
    stop: bool
    schedules: dict[str, ScheduleConfig]
    targets: list[TargetConfig]

@dataclass
class BackupJob:
    container: ContainerBackupConfig
    target: TargetConfig
    schedule: ScheduleConfig
    triggered_at: datetime

@dataclass
class BackupResult:
    job: BackupJob
    success: bool
    staging_path: Path | None
    error: str | None
    duration_seconds: float

@dataclass
class TargetState:
    last_run: datetime | None
    last_success: datetime | None
    last_error: str | None
    next_run: datetime
    status: str  # healthy, warning, error

@dataclass
class ControllerState:
    containers: dict[str, ContainerBackupConfig]
    target_states: dict[str, TargetState]
    recent_events: deque[BackupEvent]
```

## Staging Structure

```
{staging_root}/
├── {container}/
│   └── {type}-{instance}/
│       └── {timestamp}.{ext}

# Example:
/staging/
├── nextcloud/
│   ├── postgres-main/
│   │   └── 20240115T030000Z.sql.gz
│   └── fs-config/
│       └── 20240115T030000Z.tar.gz
```

- Timestamp format: `20240115T030000Z` (compact ISO8601, no colons)
- Remote (rclone destination) mirrors this structure exactly

## Docker API Usage

Key endpoints used:

| Endpoint | Purpose |
|----------|---------|
| `GET /containers/json` | List containers with label filters |
| `GET /containers/{id}/json` | Inspect container (labels, env, mounts) |
| `POST /containers/{id}/exec` | Create exec instance for dumps |
| `POST /exec/{id}/start` | Run exec command |
| `GET /containers/{id}/archive?path=` | Extract files as tar stream |

## Error Handling

| Error Source | Behaviour |
|--------------|-----------|
| Pre-exec fails (target container) | Log, alert, skip container, continue queue |
| Archive API fails | Retry 3x with backoff, then skip, alert |
| Staging disk full | Halt, alert, require manual intervention |
| Rclone fails | Retain staging, alert, retry next scheduled run |
| Docker socket unreachable | Fatal - halt entire run, alert |

## Database Dump Strategies

| Type | Command |
|------|---------|
| postgres | `pg_dumpall` or `pg_dump -d {db}` |
| mariadb/mysql | `mariadb-dump` or `mysqldump` |
| mongo | `mongodump` |
| redis | `redis-cli BGSAVE` + copy RDB |
| sqlite | `sqlite3 {db} .backup {dest}` |

## Future Roadmap (Out of Scope for v1)

- Web UI (Traefik-style dashboard)
- REST API for state/control
- Dependency ordering / backup groups
- Parallel execution
- Multi-instance coordination via shared queue
- Sidecar health checks

## Development Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check src/

# Run type checking
mypy src/

# Build container
docker build -t baqup .

# Run locally
python -m src.main --config config/defaults.yml
```

## Code Style

- Type hints on all function signatures
- Dataclasses for structured data
- JSON logging for parseability
- Docstrings on public methods
- Keep modules focused and single-purpose
