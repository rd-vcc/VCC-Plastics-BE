from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


COMPONENT_TYPES = {"RESIN", "MASTERBATCH", "ADDITIVE", "INSERT", "PACKAGING", "CONSUMABLE", "OTHER"}


class BomItemIn(BaseModel):
    material_id: int = Field(gt=0)
    component_type: str = "RESIN"
    qty_per_base: float = Field(gt=0)
    scrap_pct: float = Field(default=0, ge=0, le=100)
    is_critical: bool = True
    remark: Optional[str] = Field(default=None, max_length=500)

    @field_validator("component_type")
    @classmethod
    def _type(cls, value: str):
        value = value.strip().upper()
        if value not in COMPONENT_TYPES:
            raise ValueError(f"Component type must be one of: {', '.join(sorted(COMPONENT_TYPES))}.")
        return value


class BomCreate(BaseModel):
    bom_revision: Optional[str] = Field(default=None, max_length=20)
    product_version_id: Optional[int] = Field(default=None, gt=0)
    base_qty: float = Field(default=1000, gt=0)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    description: Optional[str] = Field(default=None, max_length=500)
    items: List[BomItemIn] = Field(default_factory=list)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("bom_revision")
    @classmethod
    def _revision(cls, value):
        return value.strip().upper() or None if value else None

    @model_validator(mode="after")
    def _checks(self):
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Effective to must be on or after effective from.")
        ids = [i.material_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("A material can appear only once in a BOM.")
        return self


class BomUpdate(BomCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)


class BomAction(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)
    remark: Optional[str] = Field(default=None, max_length=500)
    effective_from: Optional[date] = None


class BomCopy(BaseModel):
    new_revision: Optional[str] = Field(default=None, max_length=20)
    created_by: Optional[str] = Field(default=None, max_length=100)
