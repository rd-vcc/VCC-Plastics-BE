from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


PRIORITIES = {"LOW", "MEDIUM", "HIGH", "URGENT"}
SOURCES = {"MANUAL", "SALES_ORDER", "FORECAST", "STOCK"}


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


def _choice(value: str, allowed: set, label: str) -> str:
    value = value.strip().upper()
    if value not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}.")
    return value


class PlanCreate(BaseModel):
    plan_date: date
    shift_id: int = Field(gt=0)
    factory_node_id: Optional[int] = Field(default=None, gt=0)
    remark: Optional[str] = Field(default=None, max_length=500)
    created_by: Optional[str] = Field(default=None, max_length=100)


class PlanAction(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)
    remark: Optional[str] = Field(default=None, max_length=500)


class WorkOrderCreate(BaseModel):
    wo_no: str = Field(min_length=1, max_length=50)
    production_order_id: Optional[int] = Field(default=None, gt=0)
    product_id: int = Field(gt=0)
    mold_id: Optional[int] = Field(default=None, gt=0)
    material_id: Optional[int] = Field(default=None, gt=0)
    planned_qty: int = Field(gt=0)
    cavity: int = Field(default=1, gt=0)
    cycle_time_sec: float = Field(gt=0)
    setup_minutes: int = Field(default=0, ge=0)
    part_weight_g: Optional[float] = Field(default=None, gt=0)
    due_date: Optional[datetime] = None
    priority: str = "MEDIUM"
    source: str = "MANUAL"
    source_ref: Optional[str] = Field(default=None, max_length=100)
    backlog_reason: Optional[str] = Field(default=None, max_length=200)
    remark: Optional[str] = Field(default=None, max_length=500)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("wo_no")
    @classmethod
    def _wo_no(cls, value: str):
        return _required(value).upper()

    @field_validator("priority")
    @classmethod
    def _priority(cls, value: str):
        return _choice(value, PRIORITIES, "Priority")

    @field_validator("source")
    @classmethod
    def _source(cls, value: str):
        return _choice(value, SOURCES, "Source")


class WorkOrderUpdate(WorkOrderCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)


class WorkOrderProgress(BaseModel):
    """Manual actual quantity until Production Execution / Result feed it automatically."""
    actual_qty: int = Field(ge=0)
    reject_qty: Optional[int] = Field(default=None, ge=0)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class WorkOrderSchedule(BaseModel):
    plan_id: int = Field(gt=0)
    machine_id: int = Field(gt=0)
    planned_start: datetime
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class WorkOrderAction(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)
    remark: Optional[str] = Field(default=None, max_length=500)


class SimulationChange(BaseModel):
    id: int = Field(gt=0)
    machine_id: Optional[int] = Field(default=None, gt=0)
    planned_start: Optional[datetime] = None
    planned_qty: Optional[int] = Field(default=None, gt=0)


class SimulationRequest(BaseModel):
    plan_id: int = Field(gt=0)
    area_id: Optional[int] = Field(default=None, gt=0)
    changes: List[SimulationChange] = Field(default_factory=list)


class MaterialStockUpdate(BaseModel):
    """Safety stock only: on-hand stock now comes from material lots (Material Management)."""
    safety_stock_qty: float = Field(default=0, ge=0)
    updated_by: Optional[str] = Field(default=None, max_length=100)
