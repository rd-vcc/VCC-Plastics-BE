"""Thin orchestration layer over app.modules.administration.users.repository."""

from app.modules.administration.users import repository
from app.modules.administration.users.schemas import (
    MesUserCreate,
    MesUserUpdate,
    UserRolesReplace,
)


def list_mes_users():
    return repository.list_mes_users()


def get_mes_user(user_id: int):
    return repository.get_mes_user(user_id)


def create_mes_user(payload: MesUserCreate):
    return repository.create_mes_user(
        payload.employee_code.strip(),
        payload.external_user_id,
        payload.created_by,
    )


def update_mes_user(user_id: int, payload: MesUserUpdate):
    return repository.update_mes_user(user_id, payload.is_active, payload.updated_by)


def get_user_roles(user_id: int):
    return repository.get_user_roles(user_id)


def replace_user_roles(user_id: int, payload: UserRolesReplace):
    role_ids = sorted(set(payload.role_ids))
    return repository.replace_user_roles(user_id, role_ids, payload.updated_by)


def remove_user_role(user_id: int, role_id: int):
    return repository.remove_user_role(user_id, role_id)
