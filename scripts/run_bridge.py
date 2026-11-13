"""Convenience launcher for the Web Agent Bridge."""

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.bridge_host,
        port=settings.bridge_port,
        reload=True,
    )


if __name__ == "__main__":
    main()
