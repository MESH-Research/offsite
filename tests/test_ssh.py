import pytest

from offsite_backup.config import RepoConfig
from offsite_backup.errors import ConfigError
from offsite_backup.ssh import parse_sftp_repository, prepare_ssh_home


def repo(**overrides):
    fields = {
        "repository": "sftp:u1-sub1@box.example:./repo",
        "password": "pw",
        "ssh_private_key": "KEYMATERIAL",
        "ssh_known_hosts": "box.example ssh-ed25519 AAAA",
        "ssh_port": 23,
    }
    fields.update(overrides)
    return RepoConfig(**fields)


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


class TestPrepareSshHome:
    def test_writes_config_identity_and_known_hosts(self, tmp_path):
        home = prepare_ssh_home([repo()], tmp_path / "home")
        ssh_dir = home / ".ssh"
        config = (ssh_dir / "config").read_text()
        identity = ssh_dir / "id_1"
        known_hosts = ssh_dir / "known_hosts"

        assert identity.read_text() == "KEYMATERIAL\n"
        assert identity.stat().st_mode & 0o777 == 0o600
        assert known_hosts.read_text() == "box.example ssh-ed25519 AAAA\n"
        assert (ssh_dir / "config").stat().st_mode & 0o777 == 0o600
        assert ssh_dir.stat().st_mode & 0o777 == 0o700

        assert "StrictHostKeyChecking yes" in config
        assert "IdentitiesOnly yes" in config
        assert f"UserKnownHostsFile {known_hosts}" in config
        assert "Match host box.example user u1-sub1" in config
        assert f"IdentityFile {identity}" in config
        assert "Port 23" in config

    def test_url_embedded_port_wins_over_configured_port(self, tmp_path):
        home = prepare_ssh_home(
            [repo(repository="sftp://u1-sub1@box.example:2222/repo")], tmp_path
        )
        config = (home / ".ssh" / "config").read_text()
        assert "Port 2222" in config
        assert "Port 23" not in config

    def test_multiple_hosts_get_separate_match_blocks(self, tmp_path):
        mirror = repo(
            repository="sftp:u9@other.example:./repo",
            ssh_private_key="MIRRORKEY",
            ssh_known_hosts="other.example ssh-ed25519 BBBB",
            ssh_port=24,
        )
        home = prepare_ssh_home([repo(), mirror], tmp_path)
        ssh_dir = home / ".ssh"
        config = (ssh_dir / "config").read_text()

        assert "Match host box.example user u1-sub1" in config
        assert "Match host other.example user u9" in config
        assert "Port 23" in config
        assert "Port 24" in config
        assert (ssh_dir / "id_2").read_text() == "MIRRORKEY\n"
        kh = (ssh_dir / "known_hosts").read_text()
        assert "box.example ssh-ed25519 AAAA" in kh
        assert "other.example ssh-ed25519 BBBB" in kh

    def test_same_host_and_user_written_once(self, tmp_path):
        second = repo(repository="sftp:u1-sub1@box.example:./other-repo")
        home = prepare_ssh_home([repo(), second], tmp_path)
        config = (home / ".ssh" / "config").read_text()
        assert config.count("Match host box.example user u1-sub1") == 1
        kh = (home / ".ssh" / "known_hosts").read_text()
        assert kh.count("box.example ssh-ed25519 AAAA") == 1

    def test_same_host_different_users_get_own_blocks(self, tmp_path):
        second = repo(
            repository="sftp:u1-sub2@box.example:./repo",
            ssh_private_key="SUB2KEY",
        )
        home = prepare_ssh_home([repo(), second], tmp_path)
        config = (home / ".ssh" / "config").read_text()
        assert "Match host box.example user u1-sub1" in config
        assert "Match host box.example user u1-sub2" in config

    def test_non_sftp_repos_write_nothing(self, tmp_path):
        home = prepare_ssh_home(
            [repo(repository="local:/backups/repo", ssh_private_key=None,
                  ssh_known_hosts=None)],
            tmp_path / "home",
        )
        assert not (home / ".ssh").exists()

    def test_sftp_repo_without_material_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            prepare_ssh_home([repo(ssh_private_key=None)], tmp_path)
