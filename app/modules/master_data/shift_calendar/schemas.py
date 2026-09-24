from datetime import date, time
from typing import Optional

from pydantic import BaseModel, Field, field_validator


DAY_TYPES = {"HOLIDAY", "SPECIAL_DAY", "COMPANY_EVENT", "FACTORY_SHUTDOWN", "OVERTIME"}
STATUSES = {"ACTIVE", "INACTIVE"}


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


class ShiftCreate(BaseModel):
    shift_code: str = Field(min_length=1, max_length=50)
    shift_name: str = Field(min_length=1, max_length=200)
    shift_type: Optional[str] = Field(default=None, max_length=100)
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0)
    applicable_area_id: Optional[int] = Field(default=None, gt=0)
    effective_date: date
    overtime_allowed: bool = False
    allow_overlap: bool = False
    color_code: Optional[str] = Field(default=None, max_length=20)
    description: Optional[str] = None
    status: str = "ACTIVE"
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("shift_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("shift_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str):
        value = value.strip().upper()
        if value not in STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(STATUSES))}.")
        return value


class ShiftUpdate(ShiftCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)


class ShiftStatusUpdate(BaseModel):
    status: str
    updated_by: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str):
        value = value.strip().upper()
        if value not in STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(STATUSES))}.")
        return value


class CopyShiftRequest(BaseModel):
    new_shift_code: str = Field(min_length=1, max_length=50)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("new_shift_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()


class CalendarDayCreate(BaseModel):
    calendar_date: date
    day_type: str
    name: str = Field(min_length=1, max_length=200)
    applicable_area_id: Optional[int] = Field(default=None, gt=0)
    description: Optional[str] = Field(default=None, max_length=500)
    status: str = "ACTIVE"
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("day_type")
    @classmethod
    def _day_type(cls, value: str):
        value = value.strip().upper()
        if value not in DAY_TYPES:
            raise ValueError(f"Day type must be one of: {', '.join(sorted(DAY_TYPES))}.")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str):
        value = value.strip().upper()
        if value not in STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(STATUSES))}.")
        return value


class CalendarDayUpdate(CalendarDayCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)


class CalendarDayStatusUpdate(BaseModel):
    status: str
    updated_by: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str):
        value = value.strip().upper()
        if value not in STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(STATUSES))}.")
        return value
