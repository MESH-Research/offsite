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
