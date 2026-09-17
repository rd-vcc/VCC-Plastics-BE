"""Thin orchestration layer over app.modules.administration.roles.repository."""

from app.modules.administration.roles import repository
from app.modules.administration.roles.schemas import (
    RoleCreate,
    RolePermissionsReplace,
    RoleUpdate,
)


def list_roles():
    return repository.list_roles()


def get_role(role_id: int):
    return repository.get_role(role_id)


def create_role(payload: RoleCreate):
    role_code = payload.role_code.strip().upper()
    role_name = payload.role_name.strip()
    return repository.create_role(
        role_code, role_name, payload.description, payload.is_active, payload.created_by
    )


def update_role(role_id: int, payload: RoleUpdate):
    return repository.update_role(
        role_id, payload.role_name.strip(), payload.description, payload.is_active, payload.updated_by
    )


def delete_role(role_id: int):
    return repository.delete_role(role_id)


def get_role_permissions(role_id: int):
    return repository.get_role_permissions(role_id)


def replace_role_permissions(role_id: int, payload: RolePermissionsReplace):
    permission_ids = sorted(set(payload.permission_ids))
    return repository.replace_role_permissions(role_id, permission_ids, payload.updated_by)


def remove_role_permission(role_id: int, permission_id: int):
    return repository.remove_role_permission(role_id, permission_id)
