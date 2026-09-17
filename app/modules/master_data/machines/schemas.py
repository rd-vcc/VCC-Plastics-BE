from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


EQUIPMENT_CATEGORIES = {"MACHINE", "ROBOT", "AUXILIARY", "UTILITY", "OTHER"}
ASSET_STATUSES = {"DRAFT", "ACTIVE", "INACTIVE", "RETIRED", "SCRAPPED"}
OPERATIONAL_STATUSES = {"UNKNOWN", "RUNNING", "IDLE", "DOWN", "MAINTENANCE", "OFFLINE"}
SPEC_DATA_TYPES = {"TEXT", "INTEGER", "DECIMAL", "BOOLEAN", "DATE"}


def _upper(value: str) -> str:
    return value.strip().upper()


class EquipmentTypeCreate(BaseModel):
    type_code: str = Field(min_length=1, max_length=50)
    type_name: str = Field(min_length=1, max_length=150)
    category: str = "MACHINE"
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("type_code")
    @classmethod
    def normalize_code(cls, value: Optional[str]):
        return _upper(value) if value is not None else None

    @field_validator("type_name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str):
        value = _upper(value)
        if value not in EQUIPMENT_CATEGORIES:
            raise ValueError(f"Category must be one of: {', '.join(sorted(EQUIPMENT_CATEGORIES))}.")
        return value


class EquipmentTypeUpdate(BaseModel):
    type_name: str = Field(min_length=1, max_length=150)
    category: str
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0
    updated_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("type_name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str):
        value = _upper(value)
        if value not in EQUIPMENT_CATEGORIES:
            raise ValueError(f"Category must be one of: {', '.join(sorted(EQUIPMENT_CATEGORIES))}.")
        return value


class EquipmentGroupCreate(BaseModel):
    group_code: str = Field(min_length=1, max_length=50)
    group_name: str = Field(min_length=1, max_length=150)
    equipment_type_id: Optional[int] = None
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("group_code")
    @classmethod
    def normalize_code(cls, value: str):
        return _upper(value)

    @field_validator("group_name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()


class EquipmentGroupUpdate(BaseModel):
    group_name: str = Field(min_length=1, max_length=150)
    equipment_type_id: Optional[int] = None
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0
    updated_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("group_name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()


class EquipmentCreate(BaseModel):
    equipment_code: str = Field(min_length=1, max_length=50)
    equipment_name: str = Field(min_length=1, max_length=200)
    equipment_type_id: int
    equipment_group_id: Optional[int] = None
    factory_node_id: int
    manufacturer: Optional[str] = Field(default=None, max_length=150)
    model: Optional[str] = Field(default=None, max_length=150)
    serial_number: Optional[str] = Field(default=None, max_length=150)
    manufacturing_year: Optional[int] = Field(default=None, ge=1900, le=2200)
    installation_date: Optional[date] = None
    commissioning_date: Optional[date] = None
    image_url: Optional[str] = Field(default=None, max_length=1000)
    asset_status: str = "DRAFT"
    operational_status: str = "UNKNOWN"
    description: Optional[str] = None
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("equipment_code")
    @classmethod
    def normalize_code(cls, value: Optional[str]):
        return _upper(value) if value is not None else None

    @field_validator("equipment_name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()

    @field_validator("asset_status")
    @classmethod
    def validate_asset_status(cls, value: str):
        value = _upper(value)
        if value not in ASSET_STATUSES:
            raise ValueError(f"Asset status must be one of: {', '.join(sorted(ASSET_STATUSES))}.")
        return value

    @field_validator("operational_status")
    @classmethod
    def validate_operational_status(cls, value: str):
        value = _upper(value)
        if value not in OPERATIONAL_STATUSES:
            raise ValueError(f"Operational status must be one of: {', '.join(sorted(OPERATIONAL_STATUSES))}.")
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if self.installation_date and self.commissioning_date:
            if self.commissioning_date < self.installation_date:
                raise ValueError("Commissioning date cannot be before installation date.")
        return self


class EquipmentUpdate(EquipmentCreate):
    equipment_code: Optional[str] = Field(default=None, min_length=1, max_length=50)
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)

    @field_validator("equipment_code")
    @classmethod
    def normalize_optional_code(cls, value: Optional[str]):
        return _upper(value) if value is not None else None


class EquipmentStatusUpdate(BaseModel):
    asset_status: Optional[str] = None
    operational_status: Optional[str] = None
    updated_by: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("asset_status")
    @classmethod
    def validate_asset_status(cls, value: Optional[str]):
        if value is None:
            return value
        value = _upper(value)
        if value not in ASSET_STATUSES:
            raise ValueError(f"Asset status must be one of: {', '.join(sorted(ASSET_STATUSES))}.")
        return value

    @field_validator("operational_status")
    @classmethod
    def validate_operational_status(cls, value: Optional[str]):
        if value is None:
            return value
        value = _upper(value)
        if value not in OPERATIONAL_STATUSES:
            raise ValueError(f"Operational status must be one of: {', '.join(sorted(OPERATIONAL_STATUSES))}.")
        return value

    @model_validator(mode="after")
    def at_least_one_status(self):
        if self.asset_status is None and self.operational_status is None:
            raise ValueError("At least one status must be provided.")
        return self


class SpecDefinitionCreate(BaseModel):
    equipment_type_id: int
    spec_code: str = Field(min_length=1, max_length=50)
    spec_name: str = Field(min_length=1, max_length=150)
    data_type: str = "TEXT"
    unit: Optional[str] = Field(default=None, max_length=50)
    is_required: bool = False
    is_active: bool = True
    sort_order: int = 0
    description: Optional[str] = Field(default=None, max_length=500)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("spec_code")
    @classmethod
    def normalize_code(cls, value: str):
        return _upper(value)

    @field_validator("spec_name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, value: str):
        value = _upper(value)
        if value not in SPEC_DATA_TYPES:
            raise ValueError(f"Data type must be one of: {', '.join(sorted(SPEC_DATA_TYPES))}.")
        return value


class SpecDefinitionUpdate(BaseModel):
    spec_name: str = Field(min_length=1, max_length=150)
    data_type: str
    unit: Optional[str] = Field(default=None, max_length=50)
    is_required: bool = False
    is_active: bool = True
    sort_order: int = 0
    description: Optional[str] = Field(default=None, max_length=500)
    updated_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("spec_name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, value: str):
        value = _upper(value)
        if value not in SPEC_DATA_TYPES:
            raise ValueError(f"Data type must be one of: {', '.join(sorted(SPEC_DATA_TYPES))}.")
        return value


class SpecValueUpsert(BaseModel):
    spec_definition_id: int
    value_text: Optional[str] = Field(default=None, max_length=1000)
    value_integer: Optional[int] = None
    value_decimal: Optional[Decimal] = None
    value_boolean: Optional[bool] = None
    value_date: Optional[date] = None
    updated_by: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def exactly_one_value(self):
        values = [
            self.value_text,
            self.value_integer,
            self.value_decimal,
            self.value_boolean,
            self.value_date,
        ]
        if sum(value is not None for value in values) != 1:
            raise ValueError("Exactly one specification value must be provided.")
        return self
