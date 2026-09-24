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
