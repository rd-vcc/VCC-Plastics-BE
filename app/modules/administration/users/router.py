from fastapi import APIRouter

from app.modules.administration.users import service
from app.modules.administration.users.schemas import (
    MesUserCreate,
    MesUserUpdate,
    UserRolesReplace,
)

router = APIRouter(prefix="/api/users", tags=["MES Users"])


@router.get("")
def list_mes_users():
    return service.list_mes_users()


@router.get("/{user_id}")
def get_mes_user(user_id: int):
    return service.get_mes_user(user_id)


@router.post("")
def create_mes_user(payload: MesUserCreate):
    return service.create_mes_user(payload)


@router.put("/{user_id}")
def update_mes_user(user_id: int, payload: MesUserUpdate):
    return service.update_mes_user(user_id, payload)


@router.get("/{user_id}/roles")
def get_user_roles(user_id: int):
    return service.get_user_roles(user_id)


@router.put("/{user_id}/roles")
def replace_user_roles(user_id: int, payload: UserRolesReplace):
    return service.replace_user_roles(user_id, payload)


@router.delete("/{user_id}/roles/{role_id}")
def remove_user_role(user_id: int, role_id: int):
    return service.remove_user_role(user_id, role_id)
