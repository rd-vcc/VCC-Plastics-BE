from pydantic import BaseModel, Field


class RoleCreate(BaseModel):
    role_code: str = Field(min_length=1, max_length=100)
    role_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    created_by: str | None = None


class RoleUpdate(BaseModel):
    role_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    updated_by: str | None = None


class RolePermissionsReplace(BaseModel):
    permission_ids: list[int]
    updated_by: str | None = None
