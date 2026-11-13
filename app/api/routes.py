"""REST endpoints: health, list tools, run tool."""

from fastapi import APIRouter, HTTPException, Request

from app.bridge.executor import (
    ExecutorValidationError,
    ToolNotFoundError,
    execute_tool,
)
from app.models.schemas import (
    HealthResponse,
    ToolInfo,
    ToolListResponse,
    ToolRunRequest,
    ToolRunResponse,
)

router = APIRouter(tags=["api"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/tools", response_model=ToolListResponse)
async def list_tools(request: Request) -> ToolListResponse:
    """List tools discovered from mark_lvii/actions and mark_lvii/plugins."""
    registry = getattr(request.app.state, "tool_registry", None)
    raw = registry.list_tools() if registry else []
    tools = [
        ToolInfo(
            name=t.get("name", ""),
            description=t.get("description", ""),
            source=t.get("source", ""),
            parameters=t.get("parameters") or {},
        )
        for t in raw
    ]
    return ToolListResponse(tools=tools)


@router.post("/tools/run", response_model=ToolRunResponse)
async def run_tool(payload: ToolRunRequest, request: Request) -> ToolRunResponse:
    """Execute a registered tool by name (simulated in Phase 1)."""
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Tool registry is not available")

    try:
        outcome = await execute_tool(
            payload.tool_name,
            payload.arguments,
            registry=registry,
        )
    except ExecutorValidationError as exc:
        # Bad tool name (path traversal) or oversized arguments → 400
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except ToolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc

    return ToolRunResponse(
        success=outcome["success"],
        tool_name=outcome["tool_name"],
        result=outcome.get("result"),
        message=outcome.get("message", ""),
    )
