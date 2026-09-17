"""Strict typed tool models for the five allow-listed V1 tools.

Model output is untrusted proposal data: unknown or extra fields fail, every
string is bounded and pattern-checked, and every enum is closed. These models
are the only route from a raw proposal into the canonical intent.
"""

from __future__ import annotations

import enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class UnknownToolError(ValueError):
    """Raised when a proposal names a tool outside the allow-list."""


class ParameterValidationError(ValueError):
    """Raised when proposal parameters fail the strict tool schema."""


class EnvironmentName(enum.StrEnum):
    STAGING = "staging"
    PRODUCTION = "production"


class RestartStrategy(enum.StrEnum):
    ROLLING = "rolling"
    IMMEDIATE = "immediate"


class ResourceType(enum.StrEnum):
    POSTGRES_INSTANCE = "postgres_instance"
    REDIS_CACHE = "redis_cache"
    OBJECT_STORE = "object_store"


class RegionName(enum.StrEnum):
    US_EAST_1 = "us-east-1"
    US_WEST_2 = "us-west-2"
    EU_WEST_1 = "eu-west-1"
    AP_SOUTH_1 = "ap-south-1"


class ResourceSize(enum.StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class CostClass(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DeletionMode(enum.StrEnum):
    SOFT = "soft"
    HARD = "hard"


BoundedName = Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]*$")]
BoundedField = Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_.]+$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InspectServiceParameters(_StrictModel):
    environment: EnvironmentName
    service: BoundedName
    requested_fields: tuple[BoundedField, ...] = Field(default=(), max_length=10)


class RestartServiceParameters(_StrictModel):
    environment: EnvironmentName
    service: BoundedName
    strategy: RestartStrategy


class ScaleServiceParameters(_StrictModel):
    environment: EnvironmentName
    service: BoundedName
    replicas: int = Field(ge=0, le=1000)


class ProvisionResourceParameters(_StrictModel):
    environment: EnvironmentName
    resource_type: ResourceType
    name: BoundedName
    region: RegionName
    size: ResourceSize
    cost_class: CostClass


class DeleteResourceParameters(_StrictModel):
    environment: EnvironmentName
    resource_type: ResourceType
    resource_id: BoundedName
    deletion_mode: DeletionMode


TOOL_PARAMETER_MODELS: dict[str, type[_StrictModel]] = {
    "inspect_service": InspectServiceParameters,
    "restart_service": RestartServiceParameters,
    "scale_service": ScaleServiceParameters,
    "provision_resource": ProvisionResourceParameters,
    "delete_resource": DeleteResourceParameters,
}


def parse_tool_parameters(tool: str, parameters: dict[str, object]) -> _StrictModel:
    """Validate an untrusted proposal against the allow-listed tool schema."""

    model = TOOL_PARAMETER_MODELS.get(tool)
    if model is None:
        raise UnknownToolError(f"unknown tool: {tool}")
    try:
        return model.model_validate(parameters)
    except ValueError as exc:
        raise ParameterValidationError("proposal parameters failed validation") from exc
