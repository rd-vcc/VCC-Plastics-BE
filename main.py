from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import APP_NAME, APP_VERSION, CORS_ALLOW_ORIGINS
from routers.auth import router as auth_router
from routers.users import router as users_router
from routers.roles import router as roles_router
from routers.permissions import router as permissions_router
from routers.role_permissions import router as role_permissions_router
from routers.user_management import router as user_management_router
from routers.factory_structure import router as factory_structure_router
from routers.product_master import router as product_master_router

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

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(roles_router)
app.include_router(permissions_router)
app.include_router(role_permissions_router)
app.include_router(user_management_router)
app.include_router(factory_structure_router)
app.include_router(product_master_router)

@app.get("/")
def root():
    return {"message": APP_NAME, "version": APP_VERSION}


@app.get("/health")
def health():
    return {"status": "ok"}
