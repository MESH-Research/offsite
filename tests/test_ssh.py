import pytest

from offsite_backup.config import RepoConfig
from offsite_backup.errors import ConfigError
from offsite_backup.ssh import (
    materialize_key,
    materialize_known_hosts,
    parse_sftp_repository,
    prepare_ssh,
    sftp_command,
)


class TestMaterialize:
    def test_key_written_with_trailing_newline_and_0600(self, tmp_path):
        dest = materialize_key("KEYMATERIAL", tmp_path / "id")
        assert dest.read_text() == "KEYMATERIAL\n"
        assert dest.stat().st_mode & 0o777 == 0o600

    def test_key_existing_newline_not_doubled(self, tmp_path):
        dest = materialize_key("KEYMATERIAL\n", tmp_path / "id")
        assert dest.read_text() == "KEYMATERIAL\n"

    def test_known_hosts_written_with_trailing_newline(self, tmp_path):
        dest = materialize_known_hosts("host ssh-ed25519 AAAA", tmp_path / "kh")
        assert dest.read_text() == "host ssh-ed25519 AAAA\n"


class TestParseSftpRepository:
    def test_scp_like_form_extracts_user_at_host_without_port(self):
        target = parse_sftp_repository("sftp:u123-sub1@u123.your-storagebox.de:./repo")
        assert target.user_host == "u123-sub1@u123.your-storagebox.de"
        assert target.port is None

    def test_url_form_extracts_user_host_and_port(self):
        target = parse_sftp_repository("sftp://u1@box.example:2222/repo")
        assert target.user_host == "u1@box.example"
        assert target.port == 2222

    def test_url_form_without_port_has_none(self):
        target = parse_sftp_repository("sftp://u1@box.example/repo")
        assert target.user_host == "u1@box.example"
        assert target.port is None

    @pytest.mark.parametrize(
        "bad",
        [
            "local:/backups/repo",
            "/plain/path",
            "sftp:no-path-separator",
            "sftp:missinguser:./repo",
            "sftp://box.example/repo",
            "sftp://u1@box.example:notaport/repo",
            "sftp://u1@box.example",
        ],
    )
    def test_rejects_non_sftp_or_malformed(self, bad):
        with pytest.raises(ConfigError):
            parse_sftp_repository(bad)


class TestSftpCommand:
    def test_renders_exact_command(self, tmp_path):
        cmd = sftp_command(
            "u1@box.example", 23, tmp_path / "id", tmp_path / "kh"
        )
        assert cmd == (
            f"ssh -p 23 -i {tmp_path / 'id'} -o UserKnownHostsFile={tmp_path / 'kh'} "
            "-o StrictHostKeyChecking=yes u1@box.example -s sftp"
        )


class TestPrepareSsh:
    def repo(self, **overrides):
        fields = {
            "repository": "sftp:u1@box.example:./repo",
            "password": "pw",
            "ssh_private_key": "KEYMATERIAL",
            "ssh_known_hosts": "box.example ssh-ed25519 AAAA",
            "ssh_port": 23,
        }
        fields.update(overrides)
        return RepoConfig(**fields)

    def test_sftp_repo_materialises_files_and_returns_command(self, tmp_path):
        workdir = tmp_path / "ssh" / "repo0"
        cmd = prepare_ssh(self.repo(), workdir)
        key = workdir / "id_offsite"
        kh = workdir / "known_hosts"
        assert key.read_text() == "KEYMATERIAL\n"
        assert key.stat().st_mode & 0o777 == 0o600
        assert kh.read_text() == "box.example ssh-ed25519 AAAA\n"
        assert cmd == (
            f"ssh -p 23 -i {key} -o UserKnownHostsFile={kh} "
            "-o StrictHostKeyChecking=yes u1@box.example -s sftp"
        )

    def test_port_from_repo_config(self, tmp_path):
        cmd = prepare_ssh(self.repo(ssh_port=2222), tmp_path)
        assert "-p 2222 " in cmd

    def test_url_form_port_wins_over_configured_port(self, tmp_path):
        cmd = prepare_ssh(
            self.repo(repository="sftp://u1@box.example:2222/repo", ssh_port=23),
            tmp_path,
        )
        assert "-p 2222 " in cmd
        assert "u1@box.example -s sftp" in cmd

    def test_url_form_without_port_uses_configured_port(self, tmp_path):
        cmd = prepare_ssh(
            self.repo(repository="sftp://u1@box.example/repo", ssh_port=2200),
            tmp_path,
        )
        assert "-p 2200 " in cmd

    def test_non_sftp_repo_returns_none_and_writes_nothing(self, tmp_path):
        workdir = tmp_path / "ssh"
        result = prepare_ssh(
            self.repo(repository="local:/backups/repo", ssh_private_key=None,
                      ssh_known_hosts=None),
            workdir,
        )
        assert result is None
        assert not workdir.exists()
