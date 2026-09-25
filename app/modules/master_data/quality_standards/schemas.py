from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


REVISION_STATUSES = {"DRAFT", "PENDING_APPROVAL", "APPROVED", "ACTIVE", "OBSOLETE"}


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


class CharacteristicInput(BaseModel):
    characteristic_name: str = Field(min_length=1, max_length=200)
    nominal_value: Optional[Decimal] = None
    lsl: Optional[Decimal] = None
    usl: Optional[Decimal] = None
    unit: Optional[str] = Field(default=None, max_length=50)
    inspection_method: Optional[str] = Field(default=None, max_length=150)
    equipment_type_id: Optional[int] = Field(default=None, gt=0)
    inspection_frequency: Optional[str] = Field(default=None, max_length=100)
    characteristic_type: str = Field(default="VARIABLE", pattern="^(VARIABLE|ATTRIBUTE)$")
    is_spc: bool = False
    chart_type: str = Field(default="XBAR_R", pattern="^(XBAR_R|IMR)$")
    subgroup_size: int = Field(default=5, ge=1, le=10)
    cpk_target: Optional[Decimal] = Field(default=Decimal("1.33"), ge=0)
    is_critical: bool = False
    sort_order: int = 0

    @field_validator("characteristic_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @model_validator(mode="after")
    def _bounds(self):
        if self.lsl is not None and self.usl is not None and self.usl < self.lsl:
            raise ValueError("USL must be greater than or equal to LSL.")
        if self.nominal_value is not None:
            if self.lsl is not None and self.nominal_value < self.lsl:
                raise ValueError("Nominal value must be greater than or equal to LSL.")
            if self.usl is not None and self.nominal_value > self.usl:
                raise ValueError("Nominal value must be less than or equal to USL.")
        return self


class StandardCreate(BaseModel):
    standard_code: str = Field(min_length=1, max_length=50)
    standard_name: str = Field(min_length=1, max_length=200)
    product_id: Optional[int] = Field(default=None, gt=0)
    customer: Optional[str] = Field(default=None, max_length=200)
    process: Optional[str] = Field(default=None, max_length=200)
    effective_date: Optional[date] = None
    remark: Optional[str] = None
    characteristics: list[CharacteristicInput] = []
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("standard_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("standard_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)


class RevisionUpdate(BaseModel):
    standard_name: str = Field(min_length=1, max_length=200)
    product_id: Optional[int] = Field(default=None, gt=0)
    customer: Optional[str] = Field(default=None, max_length=200)
    process: Optional[str] = Field(default=None, max_length=200)
    effective_date: Optional[date] = None
    remark: Optional[str] = None
    characteristics: list[CharacteristicInput] = []
    updated_by: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("standard_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)


class NewRevisionCreate(BaseModel):
    source_revision_id: int = Field(gt=0)
    created_by: Optional[str] = Field(default=None, max_length=100)


class ActionRequest(BaseModel):
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class RejectRequest(BaseModel):
    remark: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class CopyStandardRequest(BaseModel):
    new_standard_code: str = Field(min_length=1, max_length=50)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("new_standard_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()
