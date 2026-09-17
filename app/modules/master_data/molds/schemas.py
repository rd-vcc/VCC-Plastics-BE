from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ActiveStatus = Literal["ACTIVE", "INACTIVE"]
MoldStatus = Literal[
    "AVAILABLE",
    "IN_PRODUCTION",
    "IN_MAINTENANCE",
    "IN_REPAIR",
    "RETIRED",
    "SCRAPPED",
]
AttributeType = Literal[
    "TEXT", "LONG_TEXT", "INTEGER", "DECIMAL", "BOOLEAN",
    "DATE", "DATETIME", "SELECT", "MULTI_SELECT",
]


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


class MoldNodeTypeCreate(BaseModel):
    type_code: str = Field(min_length=1, max_length=50)
    type_name: str = Field(min_length=1, max_length=150)
    level_order: int = Field(default=1, ge=1)
    icon: str | None = Field(default=None, max_length=100)
    color: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("type_code")
    @classmethod
    def code(cls, value: str):
        return _required(value).upper()

    @field_validator("type_name")
    @classmethod
    def name(cls, value: str):
        return _required(value)


class MoldNodeTypeUpdate(BaseModel):
    type_name: str = Field(min_length=1, max_length=150)
    level_order: int = Field(default=1, ge=1)
    icon: str | None = Field(default=None, max_length=100)
    color: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("type_name")
    @classmethod
    def name(cls, value: str):
        return _required(value)


class MoldNodeCreate(BaseModel):
    node_code: str = Field(min_length=1, max_length=50)
    node_name: str = Field(min_length=1, max_length=200)
    node_type_id: int = Field(gt=0)
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    description: str | None = Field(default=None, max_length=500)
    status: ActiveStatus = "ACTIVE"
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("node_code")
    @classmethod
    def code(cls, value: str):
        return _required(value).upper()

    @field_validator("node_name")
    @classmethod
    def name(cls, value: str):
        return _required(value)


class MoldNodeUpdate(BaseModel):
    node_name: str = Field(min_length=1, max_length=200)
    node_type_id: int = Field(gt=0)
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    description: str | None = Field(default=None, max_length=500)
    status: ActiveStatus = "ACTIVE"
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("node_name")
    @classmethod
    def name(cls, value: str):
        return _required(value)


class MoldNodeMove(BaseModel):
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    updated_by: str | None = Field(default=None, max_length=100)


class StatusUpdate(BaseModel):
    status: ActiveStatus
    updated_by: str | None = Field(default=None, max_length=100)


class MoldStatusUpdate(BaseModel):
    status: MoldStatus
    updated_by: str | None = Field(default=None, max_length=100)


class MoldAttributeValueInput(BaseModel):
    attribute_definition_id: int = Field(gt=0)
    value: Any = None


class MoldProductInput(BaseModel):
    product_id: int = Field(gt=0)
    cavity_quantity: int | None = Field(default=None, gt=0)
    is_primary: bool = False
    valid_from: date | None = None
    valid_to: date | None = None
    status: ActiveStatus = "ACTIVE"
    remark: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def dates(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be greater than or equal to valid_from.")
        return self


class MoldCreate(BaseModel):
    mold_code: str = Field(min_length=1, max_length=50)
    mold_name: str = Field(min_length=1, max_length=255)
    mold_type: str | None = Field(default=None, max_length=100)
    mold_group: str | None = Field(default=None, max_length=100)
    cavity_count: int = Field(default=1, gt=0)
    status: MoldStatus = "AVAILABLE"
    current_location_id: int | None = Field(default=None, gt=0)
    design_shot: int = Field(default=0, ge=0)
    current_shot: int = Field(default=0, ge=0)
    weight_kg: float | None = Field(default=None, ge=0)
    length_mm: float | None = Field(default=None, ge=0)
    width_mm: float | None = Field(default=None, ge=0)
    height_mm: float | None = Field(default=None, ge=0)
    manufacturer: str | None = Field(default=None, max_length=255)
    manufacture_date: date | None = None
    image_url: str | None = Field(default=None, max_length=1000)
    description: str | None = None
    remark: str | None = None
    is_active: bool = True
    attribute_values: list[MoldAttributeValueInput] = Field(default_factory=list)
    products: list[MoldProductInput] = Field(default_factory=list)
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("mold_code")
    @classmethod
    def code(cls, value: str):
        return _required(value).upper()

    @field_validator("mold_name")
    @classmethod
    def name(cls, value: str):
        return _required(value)


class MoldUpdate(BaseModel):
    mold_name: str = Field(min_length=1, max_length=255)
    mold_type: str | None = Field(default=None, max_length=100)
    mold_group: str | None = Field(default=None, max_length=100)
    cavity_count: int = Field(default=1, gt=0)
    status: MoldStatus = "AVAILABLE"
    current_location_id: int | None = Field(default=None, gt=0)
    design_shot: int = Field(default=0, ge=0)
    current_shot: int = Field(default=0, ge=0)
    weight_kg: float | None = Field(default=None, ge=0)
    length_mm: float | None = Field(default=None, ge=0)
    width_mm: float | None = Field(default=None, ge=0)
    height_mm: float | None = Field(default=None, ge=0)
    manufacturer: str | None = Field(default=None, max_length=255)
    manufacture_date: date | None = None
    image_url: str | None = Field(default=None, max_length=1000)
    description: str | None = None
    remark: str | None = None
    is_active: bool = True
    attribute_values: list[MoldAttributeValueInput] = Field(default_factory=list)
    products: list[MoldProductInput] = Field(default_factory=list)
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("mold_name")
    @classmethod
    def name(cls, value: str):
        return _required(value)


class MoldAttributeDefinitionCreate(BaseModel):
    attribute_code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    attribute_name: str = Field(min_length=1, max_length=255)
    data_type: AttributeType
    unit: str | None = Field(default=None, max_length=50)
    default_value: str | None = None
    select_options: list[dict[str, Any]] | None = None
    is_required: bool = False
    is_searchable: bool = False
    is_filterable: bool = False
    show_in_list: bool = False
    sort_order: int = 0
    validation_rules: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("attribute_code")
    @classmethod
    def code(cls, value: str):
        return _required(value).lower()

    @field_validator("attribute_name")
    @classmethod
    def name(cls, value: str):
        return _required(value)

    @model_validator(mode="after")
    def options(self):
        if self.data_type in {"SELECT", "MULTI_SELECT"} and not self.select_options:
            raise ValueError("SELECT and MULTI_SELECT require select_options.")
        return self


class MoldAttributeDefinitionUpdate(BaseModel):
    attribute_name: str = Field(min_length=1, max_length=255)
    unit: str | None = Field(default=None, max_length=50)
    default_value: str | None = None
    select_options: list[dict[str, Any]] | None = None
    is_required: bool = False
    is_searchable: bool = False
    is_filterable: bool = False
    show_in_list: bool = False
    sort_order: int = 0
    validation_rules: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("attribute_name")
    @classmethod
    def name(cls, value: str):
        return _required(value)


class MoldAttributeValuesReplace(BaseModel):
    values: list[MoldAttributeValueInput] = Field(default_factory=list)
    updated_by: str | None = Field(default=None, max_length=100)


class MoldProductsReplace(BaseModel):
    products: list[MoldProductInput] = Field(default_factory=list)
    updated_by: str | None = Field(default=None, max_length=100)
