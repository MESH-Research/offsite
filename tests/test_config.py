from pathlib import Path

import pytest

from offsite_backup.config import load_config
from offsite_backup.errors import ConfigError


def minimal_env(**overrides):
    env = {
        "RESTIC_REPOSITORY": "local:/backups/repo",
        "RESTIC_PASSWORD": "repo-pw",
    }
    env.update(overrides)
    return env


def sftp_env(**overrides):
    return minimal_env(
        RESTIC_REPOSITORY="sftp:u123-sub1@u123.your-storagebox.de:./repo",
        SSH_PRIVATE_KEY="KEYMATERIAL",
        SSH_KNOWN_HOSTS="u123.your-storagebox.de ssh-ed25519 AAAA",
        **overrides,
    )


def wordpress_env(**overrides):
    env = {
        "DB_SOURCES": "wordpress",
        "DB_WORDPRESS_ENGINE": "mariadb",
        "DB_WORDPRESS_INSTANCE_ID": "kc-wordpress-prod",
        "DB_WORDPRESS_USER": "backup",
        "DB_WORDPRESS_PASSWORD": "db-pw",
        "DB_WORDPRESS_DATABASES": "commons",
    }
    env.update(overrides)
    return sftp_env(**env)


class TestDefaults:
    def test_minimal_env_parses_with_defaults(self):
        cfg = load_config(minimal_env())
        assert cfg.primary.repository == "local:/backups/repo"
        assert cfg.primary.password == "repo-pw"
        assert cfg.primary.ssh_private_key is None
        assert cfg.primary.ssh_port == 23
        assert cfg.mirrors == ()
        assert cfg.restic_host == "mesh-offsite"
        assert cfg.cache_dir is None
        assert cfg.keep_daily == 0
        assert cfg.keep_weekly == 4
        assert cfg.keep_monthly == 6
        assert cfg.max_snapshot_age_hours == 48
        assert cfg.components == ("ecs", "db", "efs", "s3")
        assert cfg.db_sources == ()
        assert cfg.replica_timeout_s == 7200
        assert cfg.replica_lag_wait_s == 1800
        assert cfg.s3.buckets is None
        assert cfg.s3.exclude_buckets == ()
        assert cfg.s3.staging_budget_bytes == 170 * 2**30
        assert cfg.s3.staging_dir == Path("/mnt/staging")
        assert cfg.efs.mount_path == Path("/mnt/efs")
        assert cfg.ecs.clusters is None
        assert cfg.notify.ntfy == ()
        assert cfg.notify.email is None
        assert cfg.notify.ping_url is None

    def test_overridden_scalars(self):
        cfg = load_config(
            minimal_env(
                RESTIC_HOST="kc-backup",
                RESTIC_CACHE_DIR="/var/cache/restic",
                KEEP_DAILY="7",
                KEEP_WEEKLY="8",
                KEEP_MONTHLY="12",
                MAX_SNAPSHOT_AGE_HOURS="72",
            )
        )
        assert cfg.restic_host == "kc-backup"
        assert cfg.cache_dir == Path("/var/cache/restic")
        assert cfg.keep_daily == 7
        assert cfg.keep_weekly == 8
        assert cfg.keep_monthly == 12
        assert cfg.max_snapshot_age_hours == 72

    def test_repos_property_is_primary_then_mirrors(self):
        cfg = load_config(
            sftp_env(
                RESTIC_MIRROR_1_REPOSITORY="sftp:u9@u9.example.net:./repo",
                RESTIC_MIRROR_1_PASSWORD="mirror-pw",
            )
        )
        assert cfg.repos[0] is cfg.primary
        assert cfg.repos[1:] == cfg.mirrors


class TestRequiredVars:
    @pytest.mark.parametrize("missing", ["RESTIC_REPOSITORY", "RESTIC_PASSWORD"])
    def test_missing_core_var_raises_naming_it(self, missing):
        env = minimal_env()
        del env[missing]
        with pytest.raises(ConfigError, match=missing):
            load_config(env)

    @pytest.mark.parametrize("missing", ["SSH_PRIVATE_KEY", "SSH_KNOWN_HOSTS"])
    def test_sftp_repo_requires_ssh_material(self, missing):
        env = sftp_env()
        del env[missing]
        with pytest.raises(ConfigError, match=missing):
            load_config(env)

    def test_sftp_repo_with_ssh_material_parses(self):
        cfg = load_config(sftp_env(SSH_PORT="2222"))
        assert cfg.primary.ssh_private_key == "KEYMATERIAL"
        assert cfg.primary.ssh_known_hosts.startswith("u123.your-storagebox.de")
        assert cfg.primary.ssh_port == 2222

    @pytest.mark.parametrize(
        "var", ["KEEP_DAILY", "KEEP_WEEKLY", "STAGING_BUDGET_BYTES", "SSH_PORT"]
    )
    def test_invalid_int_raises_naming_var(self, var):
        with pytest.raises(ConfigError, match=var):
            load_config(sftp_env(**{var: "not-a-number"}))


class TestDbSources:
    def test_sources_parsed_with_whitespace_and_all_fields(self):
        cfg = load_config(
            wordpress_env(
                DB_SOURCES="wordpress, idms",
                DB_WORDPRESS_REPLICA_CLASS="db.r6g.large",
                DB_WORDPRESS_SECURITY_GROUP_IDS="sg-1, sg-2",
                DB_IDMS_ENGINE="postgres",
                DB_IDMS_INSTANCE_ID="kc-idms-prod",
                DB_IDMS_USER="pgbackup",
                DB_IDMS_PASSWORD="pg-pw",
                DB_IDMS_DATABASES="idms, profiles",
                DB_IDMS_PARAMETER_GROUP="idms-replica-pg",
            )
        )
        assert [s.name for s in cfg.db_sources] == ["wordpress", "idms"]
        wp, idms = cfg.db_sources
        assert wp.engine == "mariadb"
        assert wp.instance_id == "kc-wordpress-prod"
        assert wp.user == "backup"
        assert wp.password == "db-pw"
        assert wp.databases == ("commons",)
        assert wp.replica_class == "db.r6g.large"
        assert wp.security_group_ids == ("sg-1", "sg-2")
        assert wp.parameter_group is None
        assert idms.engine == "postgres"
        assert idms.databases == ("idms", "profiles")
        assert idms.replica_class is None
        assert idms.parameter_group == "idms-replica-pg"

    def test_unknown_engine_raises_naming_var(self):
        with pytest.raises(ConfigError, match="DB_WORDPRESS_ENGINE"):
            load_config(wordpress_env(DB_WORDPRESS_ENGINE="oracle"))

    @pytest.mark.parametrize(
        "missing",
        [
            "DB_WORDPRESS_ENGINE",
            "DB_WORDPRESS_INSTANCE_ID",
            "DB_WORDPRESS_USER",
            "DB_WORDPRESS_PASSWORD",
            "DB_WORDPRESS_DATABASES",
        ],
    )
    def test_missing_source_var_raises_naming_it(self, missing):
        env = wordpress_env()
        del env[missing]
        with pytest.raises(ConfigError, match=missing):
            load_config(env)

    def test_reserved_source_name_rejected(self):
        with pytest.raises(ConfigError, match="efs"):
            load_config(sftp_env(DB_SOURCES="efs"))


class TestS3:
    def test_explicit_buckets_and_excludes(self):
        cfg = load_config(
            minimal_env(
                S3_BUCKETS="assets, uploads",
                S3_EXCLUDE_BUCKETS="scratch",
                STAGING_BUDGET_BYTES="1000000",
                STAGING_DIR="/scratch",
            )
        )
        assert cfg.s3.buckets == ("assets", "uploads")
        assert cfg.s3.exclude_buckets == ("scratch",)
        assert cfg.s3.staging_budget_bytes == 1_000_000
        assert cfg.s3.staging_dir == Path("/scratch")

    def test_unset_buckets_means_discover(self):
        assert load_config(minimal_env()).s3.buckets is None


class TestMirrors:
    def test_mirror_inherits_primary_ssh_material(self):
        cfg = load_config(
            sftp_env(
                RESTIC_MIRROR_1_REPOSITORY="sftp:u9@u9.example.net:./repo",
                RESTIC_MIRROR_1_PASSWORD="mirror-pw",
            )
        )
        (mirror,) = cfg.mirrors
        assert mirror.repository == "sftp:u9@u9.example.net:./repo"
        assert mirror.password == "mirror-pw"
        assert mirror.ssh_private_key == "KEYMATERIAL"
        assert mirror.ssh_known_hosts == "u123.your-storagebox.de ssh-ed25519 AAAA"
        assert mirror.ssh_port == 23

    def test_mirror_own_ssh_material_wins(self):
        cfg = load_config(
            sftp_env(
                RESTIC_MIRROR_1_REPOSITORY="sftp:u9@u9.example.net:./repo",
                RESTIC_MIRROR_1_PASSWORD="mirror-pw",
                RESTIC_MIRROR_1_SSH_PRIVATE_KEY="MIRRORKEY",
                RESTIC_MIRROR_1_SSH_KNOWN_HOSTS="u9.example.net ssh-ed25519 BBBB",
                RESTIC_MIRROR_1_SSH_PORT="24",
            )
        )
        (mirror,) = cfg.mirrors
        assert mirror.ssh_private_key == "MIRRORKEY"
        assert mirror.ssh_known_hosts == "u9.example.net ssh-ed25519 BBBB"
        assert mirror.ssh_port == 24

    def test_mirror_missing_password_raises(self):
        with pytest.raises(ConfigError, match="RESTIC_MIRROR_1_PASSWORD"):
            load_config(
                sftp_env(RESTIC_MIRROR_1_REPOSITORY="sftp:u9@u9.example.net:./repo")
            )

    def test_sftp_mirror_of_local_primary_needs_own_ssh_material(self):
        with pytest.raises(ConfigError, match="RESTIC_MIRROR_1_SSH_PRIVATE_KEY"):
            load_config(
                minimal_env(
                    RESTIC_MIRROR_1_REPOSITORY="sftp:u9@u9.example.net:./repo",
                    RESTIC_MIRROR_1_PASSWORD="mirror-pw",
                )
            )

    def test_gap_in_mirror_numbering_raises(self):
        with pytest.raises(ConfigError, match="RESTIC_MIRROR_3"):
            load_config(
                sftp_env(
                    RESTIC_MIRROR_1_REPOSITORY="sftp:u9@u9.example.net:./repo",
                    RESTIC_MIRROR_1_PASSWORD="pw1",
                    RESTIC_MIRROR_3_REPOSITORY="sftp:u10@u10.example.net:./repo",
                    RESTIC_MIRROR_3_PASSWORD="pw3",
                )
            )


class TestComponents:
    def test_subset_including_named_source(self):
        cfg = load_config(wordpress_env(COMPONENTS="efs, wordpress"))
        assert cfg.components == ("efs", "wordpress")

    def test_unknown_component_raises(self):
        with pytest.raises(ConfigError, match="COMPONENTS"):
            load_config(minimal_env(COMPONENTS="bogus"))


class TestNotify:
    def test_single_ntfy_target(self):
        cfg = load_config(
            minimal_env(
                NTFY_1_URL="https://ntfy.example.org",
                NTFY_1_TOPIC="kc-backups",
                NTFY_1_USER="backup",
                NTFY_1_PASSWORD="ntfy-pw",
                PING_URL="https://hc.example.org/ping/abc",
            )
        )
        (target,) = cfg.notify.ntfy
        assert target.url == "https://ntfy.example.org"
        assert target.topic == "kc-backups"
        assert target.user == "backup"
        assert target.password == "ntfy-pw"
        assert cfg.notify.ping_url == "https://hc.example.org/ping/abc"

    def test_multiple_ntfy_targets_in_order(self):
        cfg = load_config(
            minimal_env(
                NTFY_1_URL="https://ntfy.example.org",
                NTFY_1_TOPIC="kc-backups",
                NTFY_2_URL="https://ntfy.other.example",
                NTFY_2_TOPIC="oncall",
            )
        )
        assert [t.topic for t in cfg.notify.ntfy] == ["kc-backups", "oncall"]
        assert cfg.notify.ntfy[1].user is None
        assert cfg.notify.ntfy[1].password is None

    def test_ntfy_target_requires_topic(self):
        with pytest.raises(ConfigError, match="NTFY_1_TOPIC"):
            load_config(minimal_env(NTFY_1_URL="https://ntfy.example.org"))

    def test_ntfy_target_requires_url(self):
        with pytest.raises(ConfigError, match="NTFY_1_URL"):
            load_config(minimal_env(NTFY_1_TOPIC="kc-backups"))

    def test_ntfy_user_requires_password(self):
        with pytest.raises(ConfigError, match="NTFY_1_PASSWORD"):
            load_config(
                minimal_env(
                    NTFY_1_URL="https://ntfy.example.org",
                    NTFY_1_TOPIC="kc-backups",
                    NTFY_1_USER="backup",
                )
            )

    def test_gap_in_ntfy_numbering_raises(self):
        with pytest.raises(ConfigError, match="NTFY_3"):
            load_config(
                minimal_env(
                    NTFY_1_URL="https://ntfy.example.org",
                    NTFY_1_TOPIC="kc-backups",
                    NTFY_3_URL="https://ntfy.other.example",
                    NTFY_3_TOPIC="oncall",
                )
            )

    def test_email_full_config(self):
        cfg = load_config(
            minimal_env(
                EMAIL_TO="martin@eve.gd, ops@example.org",
                SMTP_HOST="smtp.example.org",
                SMTP_PORT="2525",
                SMTP_USER="mailer",
                SMTP_PASSWORD="mail-pw",
                SMTP_FROM="backups@example.org",
            )
        )
        assert cfg.notify.email.to == ("martin@eve.gd", "ops@example.org")
        assert cfg.notify.email.smtp_host == "smtp.example.org"
        assert cfg.notify.email.smtp_port == 2525
        assert cfg.notify.email.smtp_user == "mailer"
        assert cfg.notify.email.smtp_password == "mail-pw"
        assert cfg.notify.email.smtp_from == "backups@example.org"

    def test_email_smtp_port_defaults(self):
        cfg = load_config(
            minimal_env(
                EMAIL_TO="martin@eve.gd",
                SMTP_HOST="smtp.example.org",
                SMTP_FROM="backups@example.org",
            )
        )
        assert cfg.notify.email.smtp_port == 587

    @pytest.mark.parametrize("missing", ["SMTP_HOST", "SMTP_FROM"])
    def test_email_requires_smtp_host_and_from(self, missing):
        env = minimal_env(
            EMAIL_TO="martin@eve.gd",
            SMTP_HOST="smtp.example.org",
            SMTP_FROM="backups@example.org",
        )
        del env[missing]
        with pytest.raises(ConfigError, match=missing):
            load_config(env)
