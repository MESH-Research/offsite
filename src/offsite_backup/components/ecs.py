"""Export ECS task definitions, clusters and services as JSON files.

The images themselves are open source and rebuildable; what needs keeping is
the configuration. Every boto3 response is validated with a Pydantic model
(unknown fields are retained so the export is complete), written as pretty
JSON, and snapshotted with the ``ecs`` tag.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from offsite_backup.config import EcsConfig
from offsite_backup.restic import Restic

TAG = "ecs"
DESCRIBE_CLUSTERS_BATCH = 100
DESCRIBE_SERVICES_BATCH = 10


class ListTaskDefinitionsPage(BaseModel):
    """One page of ``list_task_definitions``."""

    taskDefinitionArns: list[str] = []


class TaskDefinition(BaseModel):
    """A task definition; unknown fields are kept for the export."""

    model_config = ConfigDict(extra="allow")

    taskDefinitionArn: str
    family: str
    revision: int


class DescribeTaskDefinitionResponse(BaseModel):
    """Envelope of ``describe_task_definition``."""

    taskDefinition: TaskDefinition


class ListClustersPage(BaseModel):
    """One page of ``list_clusters``."""

    clusterArns: list[str] = []


class Cluster(BaseModel):
    """A cluster description; unknown fields are kept for the export."""

    model_config = ConfigDict(extra="allow")

    clusterArn: str
    clusterName: str


class DescribeClustersResponse(BaseModel):
    """Envelope of ``describe_clusters``."""

    clusters: list[Cluster] = []


class ListServicesPage(BaseModel):
    """One page of ``list_services``."""

    serviceArns: list[str] = []


class Service(BaseModel):
    """A service description; unknown fields are kept for the export."""

    model_config = ConfigDict(extra="allow")

    serviceArn: str
    serviceName: str


class DescribeServicesResponse(BaseModel):
    """Envelope of ``describe_services``."""

    services: list[Service] = []


class ClusterExport(BaseModel):
    """What is written per cluster: the cluster plus its services."""

    cluster: Cluster
    services: list[Service]


class EcsComponent:
    """Export ECS configuration and snapshot the resulting JSON tree."""

    name = TAG

    def __init__(self, cfg: EcsConfig, ecs_client: Any, export_dir: Path) -> None:
        """Bind to config, a boto3 ECS client, and a scratch directory."""
        self._cfg = cfg
        self._ecs = ecs_client
        self._export_dir = export_dir

    def backup(self, restic: Restic) -> list[str]:
        """Write ACTIVE task definitions and clusters (with services), snapshot them.

        Layout: ``task-definitions/<family>.<revision>.json`` and
        ``clusters/<name>.json``. The export directory is removed afterwards
        even if the snapshot fails.
        """
        export = self._export_dir
        shutil.rmtree(export, ignore_errors=True)
        export.mkdir(parents=True)
        try:
            self._export_task_definitions(export / "task-definitions")
            self._export_clusters(export / "clusters")
            return [restic.backup_path(export, tags=[TAG])]
        finally:
            shutil.rmtree(export, ignore_errors=True)

    def _export_task_definitions(self, directory: Path) -> None:
        directory.mkdir()
        pages = self._ecs.get_paginator("list_task_definitions").paginate(status="ACTIVE")
        for page in pages:
            for arn in ListTaskDefinitionsPage.model_validate(page).taskDefinitionArns:
                response = self._ecs.describe_task_definition(taskDefinition=arn)
                definition = DescribeTaskDefinitionResponse.model_validate(response).taskDefinition
                target = directory / f"{definition.family}.{definition.revision}.json"
                target.write_text(definition.model_dump_json(indent=2))

    def _export_clusters(self, directory: Path) -> None:
        directory.mkdir()
        for cluster in self._describe_clusters(self._cluster_identifiers()):
            export = ClusterExport(cluster=cluster, services=self._services_of(cluster))
            (directory / f"{cluster.clusterName}.json").write_text(export.model_dump_json(indent=2))

    def _cluster_identifiers(self) -> list[str]:
        if self._cfg.clusters is not None:
            return list(self._cfg.clusters)
        arns: list[str] = []
        for page in self._ecs.get_paginator("list_clusters").paginate():
            arns += ListClustersPage.model_validate(page).clusterArns
        return arns

    def _describe_clusters(self, identifiers: Sequence[str]) -> list[Cluster]:
        clusters: list[Cluster] = []
        for batch in _chunks(identifiers, DESCRIBE_CLUSTERS_BATCH):
            response = self._ecs.describe_clusters(clusters=batch)
            clusters += DescribeClustersResponse.model_validate(response).clusters
        return clusters

    def _services_of(self, cluster: Cluster) -> list[Service]:
        arns: list[str] = []
        for page in self._ecs.get_paginator("list_services").paginate(cluster=cluster.clusterArn):
            arns += ListServicesPage.model_validate(page).serviceArns
        services: list[Service] = []
        for batch in _chunks(arns, DESCRIBE_SERVICES_BATCH):
            response = self._ecs.describe_services(cluster=cluster.clusterArn, services=batch)
            services += DescribeServicesResponse.model_validate(response).services
        return services


def _chunks(items: Sequence[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])
