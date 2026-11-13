"""FastAPI application entrypoint for the Web Agent Bridge."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.api.websocket import router as ws_router
from app.bridge.tool_registry import ToolRegistry
from app.logging_config import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    registry = ToolRegistry()
    registry.load()
    app.state.tool_registry = registry
    yield


app = FastAPI(
    title="Web Agent Bridge",
    description="Local API bridge for Mark-LVII tools/actions",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(api_router)
app.include_router(ws_router)
