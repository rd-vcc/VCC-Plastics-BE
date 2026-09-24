"""Machine data ingest for the PLC / IoT gateway (and the simulator's contract).

POST /api/machine-data/ingest
    {"equipment_code": "IMM-001", "timestamp": "...", "status": "RUNNING|IDLE|DOWN|MAINTENANCE|OFFLINE",
     "readings": [{"code": "INJECTION_PRESSURE", "name": "...", "value": 1100.5, "unit": "bar"}],
     "components": [{"code": "HOPPER", "status": "OK|WARNING|ERROR", "value": "...", "message": "..."}],
     "alarms": [{"code": "E101", "message": "...", "priority": "HIGH"}]}
If system setting iot.gateway_key is set, the header X-Gateway-Key must match it.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.machine.service import ingest

router = APIRouter(prefix="/api/machine-data", tags=["Machine Data"])


class Reading(BaseModel):
    code: Optional[str] = Field(default=None, max_length=50)
    name: Optional[str] = Field(default=None, max_length=100)
    value: float
    unit: Optional[str] = Field(default=None, max_length=20)


class Component(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    status: str = Field(default="OK", max_length=20)
    value: Optional[str] = Field(default=None, max_length=100)
    message: Optional[str] = Field(default=None, max_length=255)


class GatewayAlarm(BaseModel):
    code: Optional[str] = Field(default=None, max_length=50)
    message: str = Field(min_length=1, max_length=500)
    priority: str = "MEDIUM"


class IngestIn(BaseModel):
    equipment_code: str = Field(min_length=1, max_length=50)
    timestamp: Optional[datetime] = None
    status: Optional[str] = Field(default=None, max_length=20)
    reason: Optional[str] = Field(default=None, max_length=500)
    readings: list[Reading] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    alarms: list[GatewayAlarm] = Field(default_factory=list)
    source: str = Field(default="IOT", max_length=20)


@router.post("/ingest")
def ingest_data(payload: IngestIn, x_gateway_key: str | None = Header(default=None)):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT setting_value FROM system_settings WHERE setting_key='iot.gateway_key'")
        row = cur.fetchone()
        if row and row["setting_value"] and row["setting_value"] != x_gateway_key:
            raise HTTPException(status_code=401, detail="Invalid gateway key.")
        source = payload.source.upper() if payload.source.upper() in {"PLC", "IOT"} else "IOT"
        data = payload.model_dump()
        data["readings"] = [r for r in data["readings"] if r.get("code") or r.get("name")]
        result = ingest(cur, data, source)
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()
