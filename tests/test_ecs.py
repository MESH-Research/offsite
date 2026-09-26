import json

import pytest

from conftest import FakeRestic
from offsite_backup.components.ecs import EcsComponent
from offsite_backup.config import EcsConfig
from offsite_backup.errors import CommandError

CONTAINER = [{"name": "app", "image": "ghcr.io/example/app:1", "memory": 256}]


def register(ecs, family):
    return ecs.register_task_definition(family=family, containerDefinitions=CONTAINER)[
        "taskDefinition"
    ]


def component(ecs_client, tmp_path, clusters=None):
    return EcsComponent(EcsConfig(clusters=clusters), ecs_client, tmp_path / "ecs-export")


class TestTaskDefinitions:
    def test_exports_active_task_definitions_only(self, ecs_client, tmp_path):
        web1 = register(ecs_client, "web")
        register(ecs_client, "web")
        register(ecs_client, "worker")
        ecs_client.deregister_task_definition(taskDefinition=web1["taskDefinitionArn"])
        restic = FakeRestic()

        ids = component(ecs_client, tmp_path).backup(restic)

        assert ids == ["snap1"]
        ((tags, files),) = restic.backups
        assert tags == ["ecs"]
        assert "task-definitions/web.2.json" in files
        assert "task-definitions/worker.1.json" in files
        assert "task-definitions/web.1.json" not in files
        web2 = json.loads(files["task-definitions/web.2.json"])
        assert web2["family"] == "web"
        assert web2["revision"] == 2
        assert web2["containerDefinitions"][0]["image"] == "ghcr.io/example/app:1"

    def test_pagination_beyond_one_page(self, ecs_client, tmp_path):
        for _ in range(101):
            register(ecs_client, "bulk")
        restic = FakeRestic()
        component(ecs_client, tmp_path).backup(restic)
        ((_, files),) = restic.backups
        bulk = [name for name in files if name.startswith("task-definitions/bulk.")]
        assert len(bulk) == 101


class TestClusters:
    def test_exports_clusters_with_their_services(self, ecs_client, tmp_path):
        ecs_client.create_cluster(clusterName="prod")
        td = register(ecs_client, "web")
        ecs_client.create_service(
            cluster="prod", serviceName="api", taskDefinition=td["taskDefinitionArn"],
            desiredCount=1,
        )
        restic = FakeRestic()
        component(ecs_client, tmp_path).backup(restic)
        ((_, files),) = restic.backups
        prod = json.loads(files["clusters/prod.json"])
        assert prod["cluster"]["clusterName"] == "prod"
        assert [s["serviceName"] for s in prod["services"]] == ["api"]
        assert prod["services"][0]["taskDefinition"] == td["taskDefinitionArn"]

    def test_configured_clusters_restrict_the_export(self, ecs_client, tmp_path):
        ecs_client.create_cluster(clusterName="prod")
        ecs_client.create_cluster(clusterName="staging")
        restic = FakeRestic()
        component(ecs_client, tmp_path, clusters=("prod",)).backup(restic)
        ((_, files),) = restic.backups
        assert "clusters/prod.json" in files
        assert "clusters/staging.json" not in files

    def test_unset_clusters_discovers_all(self, ecs_client, tmp_path):
        ecs_client.create_cluster(clusterName="prod")
        ecs_client.create_cluster(clusterName="staging")
        restic = FakeRestic()
        component(ecs_client, tmp_path).backup(restic)
        ((_, files),) = restic.backups
        assert {"clusters/prod.json", "clusters/staging.json"} <= set(files)


class TestCleanup:
    def test_export_dir_removed_after_success(self, ecs_client, tmp_path):
        register(ecs_client, "web")
        component(ecs_client, tmp_path).backup(FakeRestic())
        assert not (tmp_path / "ecs-export").exists()

    def test_export_dir_removed_even_when_snapshot_fails(self, ecs_client, tmp_path):
        register(ecs_client, "web")
        with pytest.raises(CommandError):
            component(ecs_client, tmp_path).backup(FakeRestic(fail=True))
        assert not (tmp_path / "ecs-export").exists()

    def test_stale_export_dir_from_a_previous_run_is_replaced(self, ecs_client, tmp_path):
        stale = tmp_path / "ecs-export" / "task-definitions"
        stale.mkdir(parents=True)
        (stale / "old.1.json").write_text("{}")
        register(ecs_client, "web")
        restic = FakeRestic()
        component(ecs_client, tmp_path).backup(restic)
        ((_, files),) = restic.backups
        assert "task-definitions/old.1.json" not in files
