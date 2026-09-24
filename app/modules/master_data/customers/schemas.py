from typing import Optional

from pydantic import BaseModel, Field, field_validator


STATUSES = {"ACTIVE", "INACTIVE"}


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


class CustomerCreate(BaseModel):
    customer_code: str = Field(min_length=1, max_length=50)
    customer_name: str = Field(min_length=1, max_length=255)
    short_name: Optional[str] = Field(default=None, max_length=100)
    country: Optional[str] = Field(default=None, max_length=100)
    contact_name: Optional[str] = Field(default=None, max_length=150)
    contact_email: Optional[str] = Field(default=None, max_length=150)
    contact_phone: Optional[str] = Field(default=None, max_length=50)
    status: str = "ACTIVE"
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("customer_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("customer_name")
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


class CustomerUpdate(CustomerCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)
