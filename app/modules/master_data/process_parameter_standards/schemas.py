from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


class ParameterItemInput(BaseModel):
    parameter_name: str = Field(min_length=1, max_length=150)
    min_value: Optional[Decimal] = None
    target_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    unit: Optional[str] = Field(default=None, max_length=50)
    sort_order: int = 0

    @field_validator("parameter_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @model_validator(mode="after")
    def _bounds(self):
        if self.min_value is not None and self.max_value is not None and self.max_value < self.min_value:
            raise ValueError("Max Value must be greater than or equal to Min Value.")
        if self.target_value is not None:
            if self.min_value is not None and self.target_value < self.min_value:
                raise ValueError("Target Value must be greater than or equal to Min Value.")
            if self.max_value is not None and self.target_value > self.max_value:
                raise ValueError("Target Value must be less than or equal to Max Value.")
        return self


def _check_no_duplicate_parameters(items: list[ParameterItemInput]):
    seen = set()
    for item in items:
        key = item.parameter_name.strip().upper()
        if key in seen:
            raise ValueError(f"Duplicate parameter in this revision: {item.parameter_name}.")
        seen.add(key)
    return items


class StandardCreate(BaseModel):
    standard_code: str = Field(min_length=1, max_length=50)
    standard_name: str = Field(min_length=1, max_length=200)
    product_id: Optional[int] = Field(default=None, gt=0)
    mold_id: Optional[int] = Field(default=None, gt=0)
    machine_type_id: Optional[int] = Field(default=None, gt=0)
    material_id: Optional[int] = Field(default=None, gt=0)
    process: Optional[str] = Field(default=None, max_length=200)
    effective_date: Optional[date] = None
    remark: Optional[str] = None
    parameters: list[ParameterItemInput] = []
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("standard_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("standard_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @field_validator("parameters")
    @classmethod
    def _no_dupes(cls, value):
        return _check_no_duplicate_parameters(value)


class RevisionUpdate(BaseModel):
    standard_name: str = Field(min_length=1, max_length=200)
    product_id: Optional[int] = Field(default=None, gt=0)
    mold_id: Optional[int] = Field(default=None, gt=0)
    machine_type_id: Optional[int] = Field(default=None, gt=0)
    material_id: Optional[int] = Field(default=None, gt=0)
    process: Optional[str] = Field(default=None, max_length=200)
    effective_date: Optional[date] = None
    remark: Optional[str] = None
    parameters: list[ParameterItemInput] = []
    updated_by: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("standard_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @field_validator("parameters")
    @classmethod
    def _no_dupes(cls, value):
        return _check_no_duplicate_parameters(value)


class StandardStatusUpdate(BaseModel):
    status: str = Field(pattern="^(ACTIVE|INACTIVE)$")
    actor: Optional[str] = Field(default=None, max_length=100)


class CopyStandardRequest(BaseModel):
    new_standard_code: str = Field(min_length=1, max_length=50)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("new_standard_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()
