from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


Status = Literal["ACTIVE", "INACTIVE"]
FieldType = Literal[
    "TEXT",
    "LONG_TEXT",
    "INTEGER",
    "DECIMAL",
    "DATE",
    "DATETIME",
    "BOOLEAN",
    "SELECT",
    "MULTI_SELECT",
    "FILE",
    "IMAGE",
]


def _clean_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


class ProductFamilyCreate(BaseModel):
    family_code: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    sort_order: int = 0
    description: str | None = None
    status: Status = "ACTIVE"
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("family_code")
    @classmethod
    def normalize_code(cls, value: str):
        return _clean_required(value).upper()

    @field_validator("family_name")
    @classmethod
    def normalize_name(cls, value: str):
        return _clean_required(value)


class ProductFamilyUpdate(BaseModel):
    family_name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    sort_order: int = 0
    description: str | None = None
    status: Status = "ACTIVE"
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("family_name")
    @classmethod
    def normalize_name(cls, value: str):
        return _clean_required(value)


class ProductFamilyMove(BaseModel):
    parent_id: int | None = None
    sort_order: int = 0
    updated_by: str | None = Field(default=None, max_length=100)


class StatusUpdate(BaseModel):
    status: Status
    updated_by: str | None = Field(default=None, max_length=100)


class ProductFieldOptionInput(BaseModel):
    option_value: str = Field(min_length=1, max_length=255)
    option_label: str = Field(min_length=1, max_length=255)
    color: str | None = Field(default=None, max_length=30)
    sort_order: int = 0
    is_active: bool = True

    @field_validator("option_value", "option_label")
    @classmethod
    def normalize_option(cls, value: str):
        return _clean_required(value)


class ProductFieldDefinitionCreate(BaseModel):
    field_code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    field_name: str = Field(min_length=1, max_length=255)
    data_type: FieldType
    field_group: str | None = Field(default="GENERAL", max_length=100)
    description: str | None = None
    placeholder: str | None = Field(default=None, max_length=255)
    default_value: str | None = None
    unit_label: str | None = Field(default=None, max_length=50)
    validation_rules: dict[str, Any] | None = None
    is_required: bool = False
    is_unique: bool = False
    is_filterable: bool = False
    is_list_visible: bool = False
    applies_to_all_families: bool = True
    sort_order: int = 0
    options: list[ProductFieldOptionInput] = Field(default_factory=list)
    family_ids: list[int] = Field(default_factory=list)
    include_descendants: bool = True
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("field_code")
    @classmethod
    def normalize_field_code(cls, value: str):
        return _clean_required(value).lower()

    @field_validator("field_name")
    @classmethod
    def normalize_field_name(cls, value: str):
        return _clean_required(value)

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.data_type in {"SELECT", "MULTI_SELECT"} and not self.options:
            raise ValueError("SELECT and MULTI_SELECT fields require options.")
        if self.data_type not in {"SELECT", "MULTI_SELECT"} and self.options:
            raise ValueError("Only SELECT and MULTI_SELECT fields can have options.")
        if not self.applies_to_all_families and not self.family_ids:
            raise ValueError("At least one family is required for a family-specific field.")
        return self


class ProductFieldDefinitionUpdate(BaseModel):
    field_name: str = Field(min_length=1, max_length=255)
    field_group: str | None = Field(default="GENERAL", max_length=100)
    description: str | None = None
    placeholder: str | None = Field(default=None, max_length=255)
    default_value: str | None = None
    unit_label: str | None = Field(default=None, max_length=50)
    validation_rules: dict[str, Any] | None = None
    is_required: bool = False
    is_unique: bool = False
    is_filterable: bool = False
    is_list_visible: bool = False
    applies_to_all_families: bool = True
    is_active: bool = True
    sort_order: int = 0
    options: list[ProductFieldOptionInput] = Field(default_factory=list)
    family_ids: list[int] = Field(default_factory=list)
    include_descendants: bool = True
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("field_name")
    @classmethod
    def normalize_field_name(cls, value: str):
        return _clean_required(value)


class ProductDynamicValue(BaseModel):
    field_id: int
    value: Any = None


class ProductCreate(BaseModel):
    product_code: str = Field(min_length=1, max_length=100)
    product_name: str = Field(min_length=1, max_length=255)
    product_family_id: int
    status: Status = "ACTIVE"
    values: list[ProductDynamicValue] = Field(default_factory=list)
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("product_code")
    @classmethod
    def normalize_product_code(cls, value: str):
        return _clean_required(value).upper()

    @field_validator("product_name")
    @classmethod
    def normalize_product_name(cls, value: str):
        return _clean_required(value)


class ProductUpdate(BaseModel):
    product_name: str = Field(min_length=1, max_length=255)
    product_family_id: int
    status: Status = "ACTIVE"
    values: list[ProductDynamicValue] = Field(default_factory=list)
    updated_by: str | None = Field(default=None, max_length=100)

    @field_validator("product_name")
    @classmethod
    def normalize_product_name(cls, value: str):
        return _clean_required(value)


class ProductVersionCreate(BaseModel):
    revision_code: str = Field(min_length=1, max_length=50)
    effective_from: date | None = None
    effective_to: date | None = None
    status: Status = "ACTIVE"
    description: str | None = None
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("revision_code")
    @classmethod
    def normalize_revision(cls, value: str):
        return _clean_required(value).upper()

    @model_validator(mode="after")
    def validate_dates(self):
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be greater than or equal to effective_from.")
        return self
