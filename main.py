from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.router import api_router
from app.core.config import APP_NAME, APP_VERSION, CORS_ALLOW_ORIGINS
from app.middleware.error_handler import register_error_handlers
from app.middleware.request_logging import request_logging_middleware

app = FastAPI(title=APP_NAME, version=APP_VERSION)

origins = ["*"] if CORS_ALLOW_ORIGINS.strip() == "*" else [
    item.strip() for item in CORS_ALLOW_ORIGINS.split(",") if item.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(request_logging_middleware)
register_error_handlers(app)

app.include_router(health_router)
app.include_router(api_router)
