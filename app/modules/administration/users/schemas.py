from pydantic import BaseModel, Field


class MesUserCreate(BaseModel):
    external_user_id: int | None = None
    employee_code: str = Field(min_length=1, max_length=50)
    created_by: str | None = None


class MesUserUpdate(BaseModel):
    is_active: bool
    updated_by: str | None = None


class UserRolesReplace(BaseModel):
    role_ids: list[int]
    updated_by: str | None = None
