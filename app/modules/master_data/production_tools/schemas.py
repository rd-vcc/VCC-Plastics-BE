from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ActiveStatus = Literal["ACTIVE", "INACTIVE"]
ToolType = Literal["CUTTING", "ASSEMBLY", "MEASURING", "HANDLING"]
ToolStatus = Literal["IN_USE", "AVAILABLE", "IN_MAINTENANCE", "OUT_OF_SERVICE", "INACTIVE"]
FieldDataType = Literal["TEXT", "INTEGER", "DECIMAL", "BOOLEAN", "DATE"]


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


class ProductionToolFieldDefinitionCreate(BaseModel):
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


class ProductionToolFieldDefinitionUpdate(BaseModel):
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


class ProductionToolNodeTypeCreate(BaseModel):
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


class ProductionToolNodeTypeUpdate(BaseModel):
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


class ProductionToolNodeCreate(BaseModel):
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


class ProductionToolNodeUpdate(BaseModel):
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


class ProductionToolNodeMove(BaseModel):
    parent_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    updated_by: str | None = Field(default=None, max_length=100)


class StatusUpdate(BaseModel):
    status: ActiveStatus
    updated_by: str | None = Field(default=None, max_length=100)


class ToolCreate(BaseModel):
    tool_code: str = Field(min_length=1, max_length=50)
    tool_name: str = Field(min_length=1, max_length=200)
    production_tool_node_id: int | None = Field(default=None, gt=0)
    tool_type: ToolType
    tool_category: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=150)
    model: str | None = Field(default=None, max_length=100)
    serial_number: str | None = Field(default=None, max_length=100)
    assigned_process: str | None = Field(default=None, max_length=200)
    status: ToolStatus = "AVAILABLE"
    calibration_required: bool = False
    calibration_due_date: date | None = None
    last_calibration_date: date | None = None
    calibration_interval_days: int | None = Field(default=None, ge=1)
    measurement_range: str | None = Field(default=None, max_length=100)
    measurement_resolution: str | None = Field(default=None, max_length=100)
    description: str | None = None
    image_url: str | None = Field(default=None, max_length=1000)
    custom_fields: dict[str, Any] | None = None
    created_by: str | None = Field(default=None, max_length=100)

    @field_validator("tool_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("tool_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @model_validator(mode="after")
    def _calibration_dates(self):
        if self.calibration_due_date is not None and self.last_calibration_date is not None:
            if self.last_calibration_date > self.calibration_due_date:
                raise ValueError("last_calibration_date must be before or equal to calibration_due_date.")
        return self


class ToolUpdate(BaseModel):
    tool_name: str = Field(min_length=1, max_length=200)
    production_tool_node_id: int | None = Field(default=None, gt=0)
    tool_type: ToolType
    tool_category: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=150)
    model: str | None = Field(default=None, max_length=100)
    serial_number: str | None = Field(default=None, max_length=100)
    assigned_process: str | None = Field(default=None, max_length=200)
    status: ToolStatus = "AVAILABLE"
    calibration_required: bool = False
    calibration_due_date: date | None = None
    last_calibration_date: date | None = None
    calibration_interval_days: int | None = Field(default=None, ge=1)
    measurement_range: str | None = Field(default=None, max_length=100)
    measurement_resolution: str | None = Field(default=None, max_length=100)
    description: str | None = None
    image_url: str | None = Field(default=None, max_length=1000)
    custom_fields: dict[str, Any] | None = None
    updated_by: str | None = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("tool_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @model_validator(mode="after")
    def _calibration_dates(self):
        if self.calibration_due_date is not None and self.last_calibration_date is not None:
            if self.last_calibration_date > self.calibration_due_date:
                raise ValueError("last_calibration_date must be before or equal to calibration_due_date.")
        return self


class ToolStatusUpdate(BaseModel):
    status: ToolStatus
    updated_by: str | None = Field(default=None, max_length=100)
    version: int = Field(ge=1)
