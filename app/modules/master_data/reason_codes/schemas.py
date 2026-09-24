from typing import Optional

from pydantic import BaseModel, Field, field_validator


CODE_TYPES = {"SYSTEM", "MANUAL", "AUTO"}
APPLICABLE_MODULES = {
    "MACHINE_MONITORING",
    "DOWNTIME_MANAGEMENT",
    "NG_MANAGEMENT",
    "SCRAP_MANAGEMENT",
    "MAINTENANCE_MANAGEMENT",
    "PRODUCTION_EXECUTION",
}
IMPACT_LEVELS = {"LOW", "MEDIUM", "HIGH"}
STATUSES = {"DRAFT", "PENDING_APPROVAL", "ACTIVE", "INACTIVE"}
CATEGORY_STATUSES = {"ACTIVE", "INACTIVE"}


def _required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value


class CategoryCreate(BaseModel):
    category_code: str = Field(min_length=1, max_length=50)
    category_name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    sort_order: int = 0
    status: str = "ACTIVE"
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("category_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("category_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str):
        value = value.strip().upper()
        if value not in CATEGORY_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(CATEGORY_STATUSES))}.")
        return value


class CategoryUpdate(CategoryCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)


class CategoryStatusUpdate(BaseModel):
    status: str
    updated_by: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str):
        value = value.strip().upper()
        if value not in CATEGORY_STATUSES:
            raise ValueError(f"Status must be one of: {', '.join(sorted(CATEGORY_STATUSES))}.")
        return value


class ReasonCodeCreate(BaseModel):
    reason_code: str = Field(min_length=1, max_length=50)
    reason_name: str = Field(min_length=1, max_length=200)
    category_id: int = Field(gt=0)
    code_type: str = "MANUAL"
    applicable_module: str
    description: Optional[str] = None
    impact_level: str = "MEDIUM"
    sort_order: int = 0
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("reason_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()

    @field_validator("reason_name")
    @classmethod
    def _name(cls, value: str):
        return _required(value)

    @field_validator("code_type")
    @classmethod
    def _code_type(cls, value: str):
        value = value.strip().upper()
        if value not in CODE_TYPES:
            raise ValueError(f"Code type must be one of: {', '.join(sorted(CODE_TYPES))}.")
        return value

    @field_validator("applicable_module")
    @classmethod
    def _module(cls, value: str):
        value = value.strip().upper()
        if value not in APPLICABLE_MODULES:
            raise ValueError(f"Applicable module must be one of: {', '.join(sorted(APPLICABLE_MODULES))}.")
        return value

    @field_validator("impact_level")
    @classmethod
    def _impact(cls, value: str):
        value = value.strip().upper()
        if value not in IMPACT_LEVELS:
            raise ValueError(f"Impact level must be one of: {', '.join(sorted(IMPACT_LEVELS))}.")
        return value


class ReasonCodeUpdate(ReasonCodeCreate):
    updated_by: Optional[str] = Field(default=None, max_length=100)
    created_by: Optional[str] = Field(default=None, exclude=True)
    version: int = Field(ge=1)


class ReasonCodeStatusUpdate(BaseModel):
    status: str
    updated_by: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str):
        value = value.strip().upper()
        if value not in {"ACTIVE", "INACTIVE"}:
            raise ValueError("Status must be one of: ACTIVE, INACTIVE.")
        return value


class ActionRequest(BaseModel):
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class RejectRequest(BaseModel):
    remark: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class CopyReasonCodeRequest(BaseModel):
    new_reason_code: str = Field(min_length=1, max_length=50)
    created_by: Optional[str] = Field(default=None, max_length=100)

    @field_validator("new_reason_code")
    @classmethod
    def _code(cls, value: str):
        return _required(value).upper()
