from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


LOCATION_TYPES = {"WAREHOUSE", "SILO", "DRYER", "MACHINE", "IQC_AREA", "HOLD_AREA", "OTHER"}


class LocationIn(BaseModel):
    location_code: str = Field(min_length=1, max_length=50)
    location_name: str = Field(min_length=1, max_length=200)
    location_type: str
    equipment_id: Optional[int] = Field(default=None, gt=0)
    capacity_kg: Optional[float] = Field(default=None, ge=0)
    sort_order: int = 0
    status: str = "ACTIVE"
    actor: Optional[str] = Field(default=None, max_length=100)
    version: Optional[int] = Field(default=None, ge=1)

    @field_validator("location_code")
    @classmethod
    def _code(cls, value: str):
        return value.strip().upper()

    @field_validator("location_type")
    @classmethod
    def _type(cls, value: str):
        value = value.strip().upper()
        if value not in LOCATION_TYPES:
            raise ValueError(f"Location type must be one of: {', '.join(sorted(LOCATION_TYPES))}.")
        return value

    @field_validator("status")
    @classmethod
    def _status(cls, value: str):
        value = value.strip().upper()
        if value not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("Status must be ACTIVE or INACTIVE.")
        return value


class ReceiveIn(BaseModel):
    material_id: int = Field(gt=0)
    qty: float = Field(gt=0)
    location_id: int = Field(gt=0)
    lot_no: Optional[str] = Field(default=None, max_length=50)
    supplier: Optional[str] = Field(default=None, max_length=200)
    supplier_lot_no: Optional[str] = Field(default=None, max_length=100)
    received_at: Optional[datetime] = None
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    iqc_required: bool = True
    reference_no: Optional[str] = Field(default=None, max_length=100)
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _dates(self):
        if self.manufacture_date and self.expiry_date and self.expiry_date < self.manufacture_date:
            raise ValueError("Expiry date must be on or after manufacture date.")
        return self


class RegrindIn(BaseModel):
    material_id: int = Field(gt=0)
    qty: float = Field(gt=0)
    location_id: int = Field(gt=0)
    source_work_order_id: Optional[int] = Field(default=None, gt=0)
    iqc_required: bool = False
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class IqcIn(BaseModel):
    result: str
    to_location_id: Optional[int] = Field(default=None, gt=0)
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("result")
    @classmethod
    def _result(cls, value: str):
        value = value.strip().upper()
        if value not in {"PASS", "FAIL"}:
            raise ValueError("IQC result must be PASS or FAIL.")
        return value


class TransferIn(BaseModel):
    to_location_id: int = Field(gt=0)
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class HoldIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class LotAction(BaseModel):
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class AdjustIn(BaseModel):
    new_qty: float = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class SafetyStockIn(BaseModel):
    safety_stock_qty: float = Field(ge=0)
    actor: Optional[str] = Field(default=None, max_length=100)
