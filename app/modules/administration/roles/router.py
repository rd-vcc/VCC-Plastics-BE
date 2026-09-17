from fastapi import APIRouter

from app.modules.administration.roles import service
from app.modules.administration.roles.schemas import (
    RoleCreate,
    RolePermissionsReplace,
    RoleUpdate,
)

router = APIRouter(prefix="/api/roles", tags=["Roles"])


@router.get("")
def list_roles():
    return service.list_roles()


@router.get("/{role_id}")
def get_role(role_id: int):
    return service.get_role(role_id)


@router.post("")
def create_role(payload: RoleCreate):
    return service.create_role(payload)


@router.put("/{role_id}")
def update_role(role_id: int, payload: RoleUpdate):
    return service.update_role(role_id, payload)


@router.delete("/{role_id}")
def delete_role(role_id: int):
    """Soft delete: role da co lich su se duoc chuyen Inactive thay vi xoa vat ly."""
    return service.delete_role(role_id)


@router.get("/{role_id}/permissions")
def get_role_permissions(role_id: int):
    return service.get_role_permissions(role_id)


@router.put("/{role_id}/permissions")
def replace_role_permissions(role_id: int, payload: RolePermissionsReplace):
    return service.replace_role_permissions(role_id, payload)


@router.delete("/{role_id}/permissions/{permission_id}")
def remove_role_permission(role_id: int, permission_id: int):
    return service.remove_role_permission(role_id, permission_id)
