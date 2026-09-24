from datetime import UTC, datetime
from pathlib import Path

import pytest

from offsite_backup.config import RepoConfig
from offsite_backup.errors import CommandError, ConfigError
from offsite_backup.restic import Restic

BACKUP_OUTPUT = (
    '{"message_type":"status","percent_done":1}\n'
    '{"message_type":"summary","files_new":3,"total_bytes_processed":1024,'
    '"snapshot_id":"deadbeefcafe"}\n'
)

SNAPSHOTS_OUTPUT = (
    '[{"time":"2026-09-24T03:00:00.123456789+02:00","paths":["/mnt/efs"],'
    '"hostname":"mesh-offsite","tags":["efs"],"id":"abcdef1234567890",'
    '"short_id":"abcdef12"},'
    '{"time":"2026-09-23T03:00:00Z","paths":["/db/wordpress/commons.sql"],'
    '"hostname":"mesh-offsite","tags":["db","source:wordpress"],'
    '"id":"feedface0000","short_id":"feedface"}]\n'
)


def repo(**overrides):
    fields = {
        "repository": "sftp:u1@box.example:./repo",
        "password": "repo-pw",
        "ssh_private_key": "KEY",
        "ssh_known_hosts": "box.example ssh-ed25519 AAAA",
        "ssh_port": 23,
    }
    fields.update(overrides)
    return RepoConfig(**fields)


def restic(fake_runner, **kwargs):
    kwargs.setdefault("host", "mesh-offsite")
    return Restic(repo(), runner=fake_runner, **kwargs)


class TestEnvironment:
    def test_repository_and_password_travel_via_env_not_argv(self, fake_runner):
        restic(fake_runner).unlock()
        (call,) = fake_runner.calls
        assert call.env["RESTIC_REPOSITORY"] == "sftp:u1@box.example:./repo"
        assert call.env["RESTIC_PASSWORD"] == "repo-pw"
        assert "repo-pw" not in call.cmd

    def test_cache_dir_and_ssh_home_exported_when_configured(self, fake_runner):
        restic(
            fake_runner,
            cache_dir=Path("/var/cache/restic"),
            ssh_home=Path("/work/ssh"),
        ).unlock()
        (call,) = fake_runner.calls
        assert call.env["RESTIC_CACHE_DIR"] == "/var/cache/restic"
        assert call.env["HOME"] == "/work/ssh"

    def test_no_cache_or_home_by_default(self, fake_runner):
        restic(fake_runner).unlock()
        (call,) = fake_runner.calls
        assert "RESTIC_CACHE_DIR" not in call.env
        assert "HOME" not in call.env


class TestEnsureRepo:
    def test_existing_repo_is_not_reinitialised(self, fake_runner):
        fake_runner.on("cat", "config", stdout="{}")
        restic(fake_runner).ensure_repo()
        assert fake_runner.command_matching("init") is None

    def test_missing_repo_is_initialised(self, fake_runner):
        fake_runner.on("cat", "config", returncode=1, stderr="repository does not exist")
        restic(fake_runner).ensure_repo()
        assert fake_runner.command_matching("init") is not None

    def test_failing_init_propagates(self, fake_runner):
        fake_runner.on("cat", "config", returncode=1, stderr="no repo")
        fake_runner.on("init", returncode=1, stderr="permission denied")
        with pytest.raises(CommandError):
            restic(fake_runner).ensure_repo()

    def test_mirror_init_copies_chunker_params_with_source_password_in_env(
        self, fake_runner
    ):
        fake_runner.on("cat", "config", returncode=1, stderr="no repo")
        source = repo(repository="sftp:u0@primary.example:./repo", password="src-pw")
        restic(fake_runner).ensure_repo_from(source)
        cmd = fake_runner.command_matching("init")
        assert "--copy-chunker-params" in cmd
        assert "--from-repo" in cmd
        assert "sftp:u0@primary.example:./repo" in cmd
        init_call = next(c for c in fake_runner.calls if "init" in c.cmd)
        assert init_call.env["RESTIC_FROM_PASSWORD"] == "src-pw"
        assert "src-pw" not in cmd

    def test_mirror_init_skipped_when_repo_exists(self, fake_runner):
        fake_runner.on("cat", "config", stdout="{}")
        restic(fake_runner).ensure_repo_from(repo())
        assert fake_runner.command_matching("init") is None


class TestBackup:
    def test_backup_path_argv_and_snapshot_id(self, fake_runner):
        fake_runner.on("backup", stdout=BACKUP_OUTPUT)
        snapshot_id = restic(fake_runner).backup_path(Path("/mnt/efs"), tags=["efs"])
        assert snapshot_id == "deadbeefcafe"
        cmd = fake_runner.command_matching("backup")
        assert "--json" in cmd
        assert ["--host", "mesh-offsite"] == cmd[cmd.index("--host") : cmd.index("--host") + 2]
        assert ["--tag", "efs"] == cmd[cmd.index("--tag") : cmd.index("--tag") + 2]
        assert cmd[-1] == "/mnt/efs"

    def test_backup_path_multiple_tags(self, fake_runner):
        fake_runner.on("backup", stdout=BACKUP_OUTPUT)
        restic(fake_runner).backup_path(Path("/stage/s3/assets"), tags=["s3", "bucket:assets"])
        cmd = fake_runner.command_matching("backup")
        assert cmd.count("--tag") == 2
        assert "s3" in cmd
        assert "bucket:assets" in cmd

    def test_backup_without_summary_line_raises(self, fake_runner):
        fake_runner.on("backup", stdout='{"message_type":"status"}\n')
        with pytest.raises(CommandError):
            restic(fake_runner).backup_path(Path("/mnt/efs"), tags=["efs"])

    def test_stdin_command_argv_separator_and_extra_env(self, fake_runner):
        fake_runner.on("backup", stdout=BACKUP_OUTPUT)
        snapshot_id = restic(fake_runner).backup_stdin_command(
            ["mariadb-dump", "--single-transaction", "commons"],
            filename="/db/wordpress/commons.sql",
            tags=["db", "source:wordpress"],
            extra_env={"MYSQL_PWD": "db-pw"},
        )
        assert snapshot_id == "deadbeefcafe"
        cmd = fake_runner.command_matching("backup")
        assert "--stdin-from-command" in cmd
        assert ["--stdin-filename", "/db/wordpress/commons.sql"] == cmd[
            cmd.index("--stdin-filename") : cmd.index("--stdin-filename") + 2
        ]
        separator = cmd.index("--")
        assert cmd[separator + 1 :] == ["mariadb-dump", "--single-transaction", "commons"]
        backup_call = next(c for c in fake_runner.calls if "backup" in c.cmd)
        assert backup_call.env["MYSQL_PWD"] == "db-pw"
        assert "db-pw" not in cmd

    def test_failed_dump_propagates(self, fake_runner):
        fake_runner.on("backup", returncode=1, stderr="mariadb-dump: Access denied")
        with pytest.raises(CommandError, match="Access denied"):
            restic(fake_runner).backup_stdin_command(
                ["mariadb-dump", "commons"], filename="/db/x.sql", tags=["db"]
            )


class TestSnapshots:
    def test_parses_ids_tags_paths_and_nanosecond_times(self, fake_runner):
        fake_runner.on("snapshots", stdout=SNAPSHOTS_OUTPUT)
        first, second = restic(fake_runner).snapshots()
        assert first.id == "abcdef1234567890"
        assert first.short_id == "abcdef12"
        assert first.tags == ("efs",)
        assert first.paths == ("/mnt/efs",)
        assert first.hostname == "mesh-offsite"
        assert first.time.microsecond == 123456
        assert first.time.utcoffset() is not None
        assert second.time == datetime(2026, 9, 23, 3, 0, tzinfo=UTC)

    def test_tag_and_latest_filters_in_argv(self, fake_runner):
        fake_runner.on("snapshots", stdout="[]")
        restic(fake_runner).snapshots(tags=["db", "source:wordpress"], latest=1)
        cmd = fake_runner.command_matching("snapshots")
        assert cmd.count("--tag") == 2
        assert ["--latest", "1"] == cmd[cmd.index("--latest") : cmd.index("--latest") + 2]

    def test_empty_repository_gives_empty_list(self, fake_runner):
        fake_runner.on("snapshots", stdout="[]")
        assert restic(fake_runner).snapshots() == []


class TestRetention:
    def test_forget_argv_groups_by_tags_and_never_prunes(self, fake_runner):
        restic(fake_runner).forget(keep_daily=0, keep_weekly=4, keep_monthly=6)
        cmd = fake_runner.command_matching("forget")
        assert ["--group-by", "tags"] == cmd[cmd.index("--group-by") : cmd.index("--group-by") + 2]
        assert ["--keep-weekly", "4"] == cmd[
            cmd.index("--keep-weekly") : cmd.index("--keep-weekly") + 2
        ]
        assert ["--keep-monthly", "6"] == cmd[
            cmd.index("--keep-monthly") : cmd.index("--keep-monthly") + 2
        ]
        assert "--prune" not in cmd

    def test_zero_valued_keep_flags_are_omitted(self, fake_runner):
        restic(fake_runner).forget(keep_daily=0, keep_weekly=4, keep_monthly=6)
        cmd = fake_runner.command_matching("forget")
        assert "--keep-daily" not in cmd

    def test_enabled_keep_daily_is_passed(self, fake_runner):
        restic(fake_runner).forget(keep_daily=7, keep_weekly=4, keep_monthly=6)
        cmd = fake_runner.command_matching("forget")
        assert ["--keep-daily", "7"] == cmd[
            cmd.index("--keep-daily") : cmd.index("--keep-daily") + 2
        ]

    def test_policy_keeping_nothing_raises_instead_of_forgetting_all(self, fake_runner):
        with pytest.raises(ConfigError):
            restic(fake_runner).forget(keep_daily=0, keep_weekly=0, keep_monthly=0)
        assert fake_runner.command_matching("forget") is None


class TestMaintenance:
    def test_check_without_subset(self, fake_runner):
        restic(fake_runner).check()
        cmd = fake_runner.command_matching("check")
        assert "--read-data-subset" not in cmd

    def test_check_with_subset(self, fake_runner):
        restic(fake_runner).check(read_data_subset="10%")
        cmd = fake_runner.command_matching("check")
        assert ["--read-data-subset", "10%"] == cmd[
            cmd.index("--read-data-subset") : cmd.index("--read-data-subset") + 2
        ]

    def test_unlock_and_prune_argv(self, fake_runner):
        r = restic(fake_runner)
        r.unlock()
        r.prune()
        assert fake_runner.command_matching("unlock") is not None
        assert fake_runner.command_matching("prune") is not None

    def test_copy_argv_and_source_password_env(self, fake_runner):
        source = repo(repository="sftp:u0@primary.example:./repo", password="src-pw")
        restic(fake_runner).copy_from(source, ["deadbeef", "feedface"])
        cmd = fake_runner.command_matching("copy")
        assert ["--from-repo", "sftp:u0@primary.example:./repo"] == cmd[
            cmd.index("--from-repo") : cmd.index("--from-repo") + 2
        ]
        assert cmd[-2:] == ["deadbeef", "feedface"]
        copy_call = next(c for c in fake_runner.calls if "copy" in c.cmd)
        assert copy_call.env["RESTIC_FROM_PASSWORD"] == "src-pw"
        assert "src-pw" not in cmd
