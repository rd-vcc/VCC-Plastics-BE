from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


EQUIPMENT_CATEGORIES = {"DIMENSIONAL", "ELECTRICAL", "PHYSICAL_TEST", "VISION_OPTICAL", "OTHER"}
EQUIPMENT_STATUSES = {"AVAILABLE", "IN_USE", "OUT_OF_SERVICE", "INACTIVE"}
CALIBRATION_RESULTS = {"PASS", "FAIL", "ADJUSTED"}


def _upper(value: str) -> str:
    return value.strip().upper()


class EquipmentTypeCreate(BaseModel):
    type_code: str = Field(min_length=1, max_length=50)
    type_name: str = Field(min_length=1, max_length=150)
    category: str = "DIMENSIONAL"
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("type_code")
    @classmethod
    def normalize_code(cls, value: str):
        return _upper(value)

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


class EquipmentCreate(BaseModel):
    equipment_code: str = Field(min_length=1, max_length=50)
    equipment_name: str = Field(min_length=1, max_length=200)
    equipment_type_id: int
    factory_node_id: int
    manufacturer: Optional[str] = Field(default=None, max_length=150)
    model: Optional[str] = Field(default=None, max_length=150)
    serial_number: Optional[str] = Field(default=None, max_length=150)
    measurement_range: Optional[str] = Field(default=None, max_length=100)
    resolution: Optional[str] = Field(default=None, max_length=100)
    accuracy: Optional[str] = Field(default=None, max_length=100)
    calibration_cycle_days: int = Field(gt=0)
    status: str = "AVAILABLE"
    description: Optional[str] = None
    image_url: Optional[str] = Field(default=None, max_length=1000)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("equipment_code")
    @classmethod
    def normalize_code(cls, value: str):
        return _upper(value)

    @field_validator("equipment_name")
    @classmethod
    def normalize_name(cls, value: str):
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str):
        value = _upper(value)
        if value not in EQUIPMENT_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(EQUIPMENT_STATUSES))}.")
        return value


class EquipmentUpdate(EquipmentCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)


class EquipmentStatusUpdate(BaseModel):
    status: str
    updated_by: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str):
        value = _upper(value)
        if value not in EQUIPMENT_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(EQUIPMENT_STATUSES))}.")
        return value


class CalibrationRecordCreate(BaseModel):
    calibration_date: date
    result: str = "PASS"
    calibrated_by: Optional[str] = Field(default=None, max_length=150)
    certificate_no: Optional[str] = Field(default=None, max_length=100)
    remark: Optional[str] = Field(default=None, max_length=500)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("result")
    @classmethod
    def validate_result(cls, value: str):
        value = _upper(value)
        if value not in CALIBRATION_RESULTS:
            raise ValueError(f"Result must be one of: {', '.join(sorted(CALIBRATION_RESULTS))}.")
        return value

    @model_validator(mode="after")
    def not_in_future(self):
        if self.calibration_date > date.today():
            raise ValueError("Calibration date cannot be in the future.")
        return self
