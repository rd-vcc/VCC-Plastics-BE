from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ActiveStatus = Literal["ACTIVE", "INACTIVE"]
MaterialType = Literal["RAW_MATERIAL", "COMPOUND", "ADDITIVE", "RECYCLED", "PACKAGING"]


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


FieldDataType = Literal["TEXT", "INTEGER", "DECIMAL", "BOOLEAN", "DATE"]


class MaterialFieldDefinitionCreate(BaseModel):
    field_code: str = Field(min_length=1, max_length=50)
    field_name: str = Field(min_length=1, max_length=150)
    data_type: FieldDataType = "TEXT"
    unit: str | None = Field(default=None, max_length=50)
    is_required: bool = False
    is_active: bool = True
    sort_order: int = 0
    description: str | None = Field(default=None, max_length=500)
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("field_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("field_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)


class MaterialFieldDefinitionUpdate(BaseModel):
    field_name: str = Field(min_length=1, max_length=150)
    unit: str | None = Field(default=None, max_length=50)
    is_required: bool = False
    is_active: bool = True
    sort_order: int = 0
    description: str | None = Field(default=None, max_length=500)
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("field_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)


class MaterialNodeTypeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=150)
    parent_type_id: int | None = Field(default=None, gt=0)
    level_order: int = Field(default=1, ge=1)
    color: str | None = Field(default=None, max_length=20)
    icon: str | None = Field(default=None, max_length=100)
    sort_order: int = 0
    is_active: bool = True
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)


class MaterialNodeTypeUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    parent_type_id: int | None = Field(default=None, gt=0)
    level_order: int = Field(default=1, ge=1)
    color: str | None = Field(default=None, max_length=20)
    icon: str | None = Field(default=None, max_length=100)
    sort_order: int = 0
    is_active: bool = True
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)


class MaterialNodeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    node_type_id: int = Field(gt=0)
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    description: str | None = Field(default=None, max_length=500)
    status: ActiveStatus = "ACTIVE"
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)


class MaterialNodeUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    node_type_id: int = Field(gt=0)
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    description: str | None = Field(default=None, max_length=500)
    status: ActiveStatus = "ACTIVE"
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)


class MaterialNodeMove(BaseModel):
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    updated_by: str | None = Field(default=None, max_length=100)


class StatusUpdate(BaseModel):
    status: ActiveStatus
    updated_by: str | None = Field(default=None, max_length=100)


class MaterialCreate(BaseModel):
    material_code: str = Field(min_length=1, max_length=50)
    material_name: str = Field(min_length=1, max_length=200)
    material_node_id: int | None = Field(default=None, gt=0)
    material_type: MaterialType
    base_resin: str | None = Field(default=None, max_length=100)
    material_group: str | None = Field(default=None, max_length=100)
    supplier: str | None = Field(default=None, max_length=200)
    unit: str = Field(default="KG", max_length=20)
    status: ActiveStatus = "ACTIVE"
    density: Decimal | None = Field(default=None, ge=0)
    mfi: Decimal | None = Field(default=None, ge=0)
    moisture_content: Decimal | None = Field(default=None, ge=0)
    melting_point_min: Decimal | None = None
    melting_point_max: Decimal | None = None
    image_url: str | None = Field(default=None, max_length=1000)
    description: str | None = None
    custom_fields: dict[str, Any] | None = None
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("material_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("material_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @field_validator("unit")
    @classmethod
    def _unit(cls, value: str):
        return _required(value).upper()

    @model_validator(mode="after")
    def _melting_range(self):
        if self.melting_point_min is not None and self.melting_point_max is not None:
            if self.melting_point_max < self.melting_point_min:
                raise ValueError("melting_point_max must be greater than or equal to melting_point_min.")
        return self


class MaterialUpdate(BaseModel):
    material_name: str = Field(min_length=1, max_length=200)
    material_node_id: int | None = Field(default=None, gt=0)
    material_type: MaterialType
    base_resin: str | None = Field(default=None, max_length=100)
    material_group: str | None = Field(default=None, max_length=100)
    supplier: str | None = Field(default=None, max_length=200)
    unit: str = Field(default="KG", max_length=20)
    status: ActiveStatus = "ACTIVE"
    density: Decimal | None = Field(default=None, ge=0)
    mfi: Decimal | None = Field(default=None, ge=0)
    moisture_content: Decimal | None = Field(default=None, ge=0)
    melting_point_min: Decimal | None = None
    melting_point_max: Decimal | None = None
    image_url: str | None = Field(default=None, max_length=1000)
    description: str | None = None
    custom_fields: dict[str, Any] | None = None
    updated_by: str | None = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("material_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @field_validator("unit")
    @classmethod
    def _unit(cls, value: str):
        return _required(value).upper()

    @model_validator(mode="after")
    def _melting_range(self):
        if self.melting_point_min is not None and self.melting_point_max is not None:
            if self.melting_point_max < self.melting_point_min:
                raise ValueError("melting_point_max must be greater than or equal to melting_point_min.")
        return self


class MaterialStatusUpdate(BaseModel):
    status: ActiveStatus
    updated_by: str | None = Field(default=None, max_length=100)
    version: int = Field(ge=1)
