"""AWS task identity and the terminal event used as purchase recovery evidence."""

import os
from datetime import UTC, datetime
from typing import Literal
from urllib.request import urlopen

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from castle_chef.settings import get_settings


class AwsModel(BaseModel):
    # Accept AWS's wire names and the canonical names stored by ModelJSONB.
    model_config = ConfigDict(populate_by_name=True)


class WorkerTask(AwsModel):
    task_arn: str = Field(alias="TaskARN")
    cluster: str = Field(alias="Cluster")


def current_task() -> WorkerTask | None:
    """Read Fargate's own identity before claiming. Local dry runs have none."""
    expected = get_settings().worker_cluster_arn
    endpoint = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if expected is None:
        if endpoint or os.environ.get("AWS_EXECUTION_ENV") == "AWS_ECS_FARGATE":
            raise RuntimeError("The ECS worker cluster is not configured.")
        return None
    if endpoint is None:
        raise RuntimeError("The ECS task metadata endpoint is missing; refusing to claim work.")
    with urlopen(f"{endpoint}/task", timeout=5) as response:
        task = WorkerTask.model_validate_json(response.read())
    # Metadata may identify Cluster by name rather than ARN.
    if task.cluster not in (expected, expected.rsplit("/", 1)[-1]):
        raise RuntimeError("The worker metadata identifies a different ECS cluster.")
    prefix = expected.replace(":cluster/", ":task/") + "/"
    if not task.task_arn.startswith(prefix):
        raise RuntimeError("The worker task ARN does not belong to the configured cluster.")
    return task


class EnvironmentVariable(BaseModel):
    name: str
    value: str


class ContainerOverride(AwsModel):
    name: str
    environment: list[EnvironmentVariable] = []


class TaskOverrides(AwsModel):
    container_overrides: list[ContainerOverride] = Field(default=[], alias="containerOverrides")


class StoppedContainer(AwsModel):
    name: str
    # Task-level STOPPED is authoritative; container diagnostics can be absent
    # or still describe the previous state when a task never starts.
    last_status: str | None = Field(default=None, alias="lastStatus")
    exit_code: int | None = Field(default=None, alias="exitCode")
    reason: str | None = None


class StoppedDetail(AwsModel):
    cluster_arn: str = Field(alias="clusterArn")
    task_arn: str = Field(alias="taskArn")
    last_status: Literal["STOPPED"] = Field(alias="lastStatus")
    stopped_at: AwareDatetime = Field(alias="stoppedAt")
    stop_code: str | None = Field(default=None, alias="stopCode")
    stopped_reason: str | None = Field(default=None, alias="stoppedReason")
    version: int
    containers: list[StoppedContainer]
    overrides: TaskOverrides


class TaskStopped(AwsModel):
    id: str
    source: Literal["aws.ecs"]
    detail_type: Literal["ECS Task State Change"] = Field(alias="detail-type")
    account: str
    region: str
    time: AwareDatetime
    detail: StoppedDetail

    def check_origin(self) -> None:
        expected = get_settings().worker_cluster_arn
        if expected is None or self.detail.cluster_arn != expected:
            raise ValueError("Stopped task event is not from the configured cluster.")
        arn = expected.split(":", 5)
        if self.region != arn[3] or self.account != arn[4]:
            raise ValueError("Stopped task event has a different account or region.")
        if not self.detail.task_arn.startswith(expected.replace(":cluster/", ":task/") + "/"):
            raise ValueError("Stopped task event has a task outside the configured cluster.")


class WorkerStop(BaseModel):
    event: TaskStopped
    received_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class QueueRecord(AwsModel):
    source: Literal["aws:sqs"] = Field(alias="eventSource")
    queue_arn: str = Field(alias="eventSourceARN")
    body: str


class QueueBatch(AwsModel):
    records: list[QueueRecord] = Field(alias="Records")
