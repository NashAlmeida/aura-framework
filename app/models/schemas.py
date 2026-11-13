"""Pydantic models for API requests and responses."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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


class AgentRunRequest(BaseModel):
    message: str | None = None
    session_id: str | None = None
    confirmed: bool | None = None

    @model_validator(mode="after")
    def require_message_or_confirmation(self) -> "AgentRunRequest":
        if self.confirmed is None and not (self.message or "").strip():
            raise ValueError("Provide message, or confirmed with session_id")
        if self.confirmed is not None and not self.session_id:
            raise ValueError("session_id is required when confirmed is set")
        return self


class AgentToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None


class AgentRunResponse(BaseModel):
    status: Literal["completed", "needs_confirmation"] = "completed"
    response: str = ""
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    session_id: str = ""
    # Populated when status == needs_confirmation
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    message: str | None = None
