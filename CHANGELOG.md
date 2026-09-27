## 0.7.0 (2026-09-27)

### Feat

- **efs**: back up the mounted EFS volume with an empty-mount guard

### Fix

- **cli**: build only the selected components so non-AWS targets skip boto3

## 0.6.0 (2026-09-27)

### Feat

- **cli**: wire the backup command to the orchestrator
- **orchestrator**: run components, copy to mirrors, gate retention and notify
- **ecs**: export task definitions, clusters and services as JSON

### Fix

- **cli**: widen restore target annotation so click.Choice type-checks
- **backup**: stage exports under the stable STAGING_DIR so snapshot paths persist

### Refactor

- **restic**: parse restic JSON output with Pydantic models

## 0.5.1 (2026-09-26)

### Refactor

- **notify**: send HTTP via httpx2 and validate ntfy responses with Pydantic

## 0.5.0 (2026-09-26)

### Feat

- **cli**: load the .env file named by ENV_FILE into the config environment
- **notify**: add ntfy, email and dead-man ping notifications

### Refactor

- **notify**: report delivery failures as NotificationError values

## 0.4.0 (2026-09-24)

### Feat

- **cli**: add command skeleton with dispatch and exit-code mapping

### Refactor

- **cli**: switch argument parsing from argparse to Click

## 0.3.0 (2026-09-24)

### Feat

- **restic**: add restic CLI wrapper with multi-repo copy support

### Fix

- **config**: require ntfy username and password together

### Refactor

- **ssh**: generate OpenSSH client config to support multi-repo sftp

## 0.2.0 (2026-09-24)

### Feat

- **config**: add optional keep-daily retention setting, disabled by default
- **config**: support multiple ntfy notification targets
- **ssh**: accept sftp:// URL repositories and honour embedded ports
- **ssh**: add SSH material handling and sftp command construction
- **proc**: add subprocess runner seam with error and timeout handling
- **config**: add error types and environment configuration parsing

### Refactor

- move ComponentResult and RunReport into results module
- **config**: replace hand-rolled env parsing with environs
