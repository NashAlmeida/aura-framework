"""Pydantic models for API requests and responses."""

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class ToolInfo(BaseModel):
    name: str
    description: str = ""
    source: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolListResponse(BaseModel):
    tools: list[ToolInfo] = Field(default_factory=list)


class ToolRunRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolRunResponse(BaseModel):
    success: bool
    tool_name: str
    result: Any = None
    message: str = ""
