"""Aggregates every currently-real, functional module router.

Each included router keeps its exact existing URL prefix (verified against
the original ``routers/*.py`` ``APIRouter(prefix=...)`` declarations). Empty
placeholder module routers (scaffolding with no routes) are intentionally
NOT included here.
"""

from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.administration.users.router import router as users_router
from app.modules.administration.roles.router import router as roles_router
from app.modules.administration.permissions.router import router as permissions_router
from app.modules.administration.system_settings.router import router as system_settings_router
from app.modules.organization.employees.router import router as employees_router
from app.modules.master_data.factory_structure.router import router as factory_structure_router
from app.modules.master_data.products.router import router as products_router
from app.modules.master_data.machines.router import router as machines_router
from app.modules.master_data.molds.router import router as molds_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(roles_router)
api_router.include_router(permissions_router)
api_router.include_router(system_settings_router)
api_router.include_router(employees_router)
api_router.include_router(factory_structure_router)
api_router.include_router(products_router)
api_router.include_router(machines_router)
api_router.include_router(molds_router)
