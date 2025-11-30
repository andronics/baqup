# baqup

Label-driven Docker backup controller with Traefik-style autodiscovery.

## Concept

Traefik-inspired Docker backup controller using label-based autodiscovery.

## Label Schema
```yaml
labels:
  - "backup.enabled=true"
  - "backup.stop=false"
  
  # Schedules (optional - falls back to controller defaults)
  - "backup.schedule.daily.cron=0 3 * * *"
  - "backup.schedule.daily.retention=7"
  
  # Database targets: backup.{type}.{instance}.{property}
  - "backup.postgres.main.port=5432"
  - "backup.postgres.main.username=postgres"
  - "backup.postgres.main.password_env=POSTGRES_PASSWORD"
  - "backup.postgres.main.schedule=hourly"
  
  # Filesystem targets: backup.fs.{instance}.{property}
  - "backup.fs.config.path=/config"
  - "backup.fs.config.exclude=*.log"
  - "backup.fs.config.schedule=daily"
```

## Supported DB Types
mariadb, postgres, mongo, redis, sqlite

## Credential Resolution
- Literal: `password=secret`
- Container env: `password_env=POSTGRES_PASSWORD`
- Secret file: `password_file=/run/secrets/db_pass`

## Architecture
- Python (v1), docker-py
- Modules: discovery, scheduler, executor, uploader, retention, notify
- Staging structure: `{root}/{container}/{type}-{instance}/{timestamp}.{ext}`
- Timestamp format: `20240115T030000Z`
- Upload via rclone
- Future: REST API + Traefik-style Web UI

## Error Handling
- Target container errors: log, alert, continue
- Controller errors: retry, recover, halt if fatal

## Defaults
- Schedule: daily if not specified
- Compression: true
- Retention: from controller config
