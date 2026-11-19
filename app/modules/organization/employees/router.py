from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.modules.organization.employees import service

router = APIRouter(prefix="/api/user-management", tags=["User Management"])


class EmployeeRolesReplace(BaseModel):
    role_ids: list[int] = Field(default_factory=list)
    is_active: bool = True
    updated_by: str | None = Field(default=None, max_length=50)


class UserStatusUpdate(BaseModel):
    is_active: bool
    updated_by: str | None = Field(default=None, max_length=50)


class LocalAccountCreate(BaseModel):
    employee_code: str = Field(min_length=1, max_length=50)
    full_name: str = Field(min_length=1, max_length=150)
    department: str | None = Field(default=None, max_length=150)
    position: str | None = Field(default=None, max_length=150)
    password: str | None = Field(default=None, max_length=128)  # empty -> default password
    external_user_id: int | None = None  # VCC Group employee id when picked from the directory
    role_ids: list[int] = Field(default_factory=list)
    is_active: bool = True
    created_by: str | None = Field(default=None, max_length=50)


class PasswordSet(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    updated_by: str | None = Field(default=None, max_length=50)


@router.get("/employees")
def list_employee_role_assignments(
    keyword: str | None = Query(default=None, max_length=100),
    org_id: int | None = None,
    employment_status: str = Query(default="active", pattern="^(active|inactive|all)$"),
    assigned_only: bool = False,
):
    """Ghép hồ sơ nhân viên VCC Group với vai trò của VCC Plastics."""
    return service.list_employee_role_assignments(keyword, org_id, employment_status, assigned_only)


@router.put("/employees/{employee_code}/roles")
def replace_employee_roles(employee_code: str, payload: EmployeeRolesReplace):
    """Thêm/cập nhật MES user và thay toàn bộ vai trò trong một transaction."""
    return service.replace_employee_roles(
        employee_code, payload.role_ids, payload.is_active, payload.updated_by
    )


@router.patch("/users/{user_id}/status")
def update_user_status(user_id: int, payload: UserStatusUpdate):
    return service.update_user_status(user_id, payload.is_active, payload.updated_by)


@router.post("/accounts")
def create_local_account(payload: LocalAccountCreate):
    """Thêm nhân viên = tạo tài khoản đăng nhập VCC Plastics (mật khẩu mặc định khi không nhập)."""
    return service.create_local_account(payload)


@router.put("/accounts/{employee_code}/password")
def set_password(employee_code: str, payload: PasswordSet):
    """Đặt / đổi mật khẩu đăng nhập VCC Plastics của một người dùng."""
    return service.set_password(employee_code, payload.password, payload.updated_by)
