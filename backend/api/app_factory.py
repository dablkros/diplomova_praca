from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.inventory import router as inventory_router
from backend.api.routes.ops import router as ops_router
from backend.api.routes.interface_config import router as iface_router
from backend.api.routes.ws import router as ws_router

def create_app() -> FastAPI:
    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(inventory_router)
    app.include_router(ops_router)
    app.include_router(iface_router)
    app.include_router(ws_router)

    return app
