from typing import Optional

from pydantic import BaseModel, Field, field_validator


class FactoryNodeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    node_type_id: int
    parent_id: Optional[int] = None
    sort_order: int = 0
    description: Optional[str] = Field(default=None, max_length=500)
    status: str = "ACTIVE"
    created_by: Optional[str] = Field(default=None, max_length=50)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str):
        return value.strip().upper()

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str):
        value = value.strip().upper()

        if value not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("Status must be ACTIVE or INACTIVE.")

        return value


class FactoryNodeUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    node_type_id: int
    parent_id: Optional[int] = None
    sort_order: int = 0
    description: Optional[str] = Field(default=None, max_length=500)
    status: str = "ACTIVE"
    updated_by: Optional[str] = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str):
        value = value.strip().upper()

        if value not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("Status must be ACTIVE or INACTIVE.")

        return value


class FactoryNodeMove(BaseModel):
    parent_id: Optional[int] = None
    sort_order: int = 0
    updated_by: Optional[str] = Field(default=None, max_length=50)


class FactoryNodeStatusUpdate(BaseModel):
    status: str
    updated_by: Optional[str] = Field(default=None, max_length=50)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str):
        value = value.strip().upper()

        if value not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("Status must be ACTIVE or INACTIVE.")

        return value
class FactoryNodeTypeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=100)
    parent_type_id: Optional[int] = None
    color: Optional[str] = Field(default=None, max_length=20)
    icon: Optional[str] = Field(default=None, max_length=100)
    sort_order: int = 0
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str):
        return value.strip().upper()

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()


class FactoryNodeTypeUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_type_id: Optional[int] = None
    color: Optional[str] = Field(default=None, max_length=20)
    icon: Optional[str] = Field(default=None, max_length=100)
    sort_order: int = 0
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()