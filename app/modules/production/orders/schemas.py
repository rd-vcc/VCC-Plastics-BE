from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


PRIORITIES = {"LOW", "MEDIUM", "HIGH", "URGENT"}
SOURCES = {"MANUAL", "SALES_ORDER", "FORECAST", "MRP", "PLANNING"}


def _choice(value: str, allowed: set, label: str) -> str:
    value = value.strip().upper()
    if value not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}.")
    return value


class OrderCreate(BaseModel):
    order_no: str = Field(min_length=1, max_length=50)
    customer_id: Optional[int] = Field(default=None, gt=0)
    product_id: int = Field(gt=0)
    order_qty: int = Field(gt=0)
    priority: str = "MEDIUM"
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    due_date: Optional[datetime] = None
    factory_node_id: Optional[int] = Field(default=None, gt=0)
    source: str = "MANUAL"
    source_ref: Optional[str] = Field(default=None, max_length=100)
    remark: Optional[str] = Field(default=None, max_length=500)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("order_no")
    @classmethod
    def _order_no(cls, value: str):
        value = value.strip().upper()
        if not value:
            raise ValueError("Order number cannot be blank.")
        return value

    @field_validator("priority")
    @classmethod
    def _priority(cls, value: str):
        return _choice(value, PRIORITIES, "Priority")

    @field_validator("source")
    @classmethod
    def _source(cls, value: str):
        return _choice(value, SOURCES, "Source")

    @model_validator(mode="after")
    def _window(self):
        if self.planned_start and self.planned_end and self.planned_end <= self.planned_start:
            raise ValueError("Plan end must be after plan start.")
        return self


class OrderUpdate(OrderCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)


class OrderAction(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)
    remark: Optional[str] = Field(default=None, max_length=500)


class PriorityChange(OrderAction):
    priority: str

    @field_validator("priority")
    @classmethod
    def _priority(cls, value: str):
        return _choice(value, PRIORITIES, "Priority")


class CopyOrder(BaseModel):
    new_order_no: Optional[str] = Field(default=None, max_length=50)
    created_by: Optional[str] = Field(default=None, max_length=100)


class ImportRow(BaseModel):
    order_no: Optional[str] = Field(default=None, max_length=50)
    customer_code: Optional[str] = Field(default=None, max_length=50)
    product_code: str = Field(min_length=1, max_length=100)
    order_qty: int = Field(gt=0)
    priority: str = "MEDIUM"
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    due_date: Optional[datetime] = None
    source: str = "SALES_ORDER"
    source_ref: Optional[str] = Field(default=None, max_length=100)
    remark: Optional[str] = Field(default=None, max_length=500)

    @field_validator("priority")
    @classmethod
    def _priority(cls, value: str):
        return _choice(value or "MEDIUM", PRIORITIES, "Priority")

    @field_validator("source")
    @classmethod
    def _source(cls, value: str):
        return _choice(value or "SALES_ORDER", SOURCES, "Source")


class ImportRequest(BaseModel):
    rows: List[dict] = Field(min_length=1, max_length=1000)
    factory_node_id: Optional[int] = Field(default=None, gt=0)
    created_by: Optional[str] = Field(default=None, max_length=100)


class NoteCreate(BaseModel):
    note_text: str = Field(min_length=1, max_length=2000)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("note_text")
    @classmethod
    def _text(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("Note cannot be blank.")
        return value
