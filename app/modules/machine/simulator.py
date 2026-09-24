"""IoT simulator: stands in for the PLC / IoT gateway until it is connected.

When system setting iot.simulator_enabled = 1, a daemon thread posts one ingest message per running
equipment every iot.simulator_interval_sec seconds, with source SIMULATOR (the same path a real
gateway uses). Values follow the parameter limits of the equipment (Process Parameter Standard or
defaults) with noise and occasional excursions, so alarms are raised and cleared naturally.
Set iot.simulator_enabled = 0 when the real gateway is connected. Old readings are purged after
iot.reading_retention_days.
"""

import logging
import random
import threading
import time
from datetime import datetime

from app.core.database import get_vcc_plastics_connection
from app.modules.machine.service import COMPONENTS, get_equipment, ingest, parameter_limits, unshelve_expired

log = logging.getLogger("vcc_plastics.simulator")
SOURCE = "SIMULATOR"
_thread = None
_stop = threading.Event()


def _settings(cur):
    cur.execute("SELECT setting_key, setting_value FROM system_settings WHERE setting_key LIKE 'iot.%%'")
    s = {r["setting_key"]: r["setting_value"] for r in cur.fetchall()}
    return (s.get("iot.simulator_enabled", "0") == "1", max(int(float(s.get("iot.simulator_interval_sec", "10"))), 3),
            max(int(float(s.get("iot.reading_retention_days", "30"))), 1))


def _value(lim, rnd):
    target = lim.get("target")
    lo, hi = lim.get("min"), lim.get("max")
    if target is None:
        target = ((lo or 0) + (hi or 0)) / 2 if (lo is not None and hi is not None) else (hi or lo or 1)
    span = ((hi - lo) / 2) if (lo is not None and hi is not None) else abs(target) * 0.1 or 1
    if rnd.random() < 0.0004 and hi is not None:  # rare excursion above the limit (~1-3 alarms per hour for the plant)
        return round(hi + span * rnd.uniform(0.05, 0.3), 2)
    return round(target + rnd.gauss(0, span * 0.18), 2)


def tick(cur, rnd=random):
    cur.execute("""SELECT e.id, e.equipment_code FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
                   WHERE e.asset_status='ACTIVE' AND e.operational_status='RUNNING' AND t.category IN ('MACHINE','UTILITY','ROBOT')""")
    count = 0
    for row in cur.fetchall():
        eq = get_equipment(cur, equipment_id=row["id"])
        category = eq["category"] if eq["category"] in COMPONENTS else "MACHINE"
        limits = parameter_limits(cur, eq)
        readings = [{"code": code, "name": lim["name"], "unit": lim["unit"], "value": _value(lim, rnd)} for code, lim in limits.items()]
        components = []
        for comp in COMPONENTS[category]:
            r = rnd.random()
            status = "WARNING" if r < 0.002 else "OK"
            components.append({"code": comp, "status": status, "message": f"{comp.replace('_', ' ').title()} check" if status != "OK" else None})
        ingest(cur, {"equipment_code": eq["equipment_code"], "readings": readings, "components": components, "timestamp": datetime.now()}, SOURCE)
        count += 1
    unshelve_expired(cur)
    return count


def _loop():
    last_purge = 0.0
    while not _stop.is_set():
        interval = 10
        try:
            conn = get_vcc_plastics_connection()
            cur = conn.cursor(dictionary=True)
            try:
                enabled, interval, retention = _settings(cur)
                if enabled:
                    tick(cur)
                    if time.time() - last_purge > 3600:
                        cur.execute("DELETE FROM machine_parameter_readings WHERE read_at < NOW() - INTERVAL %s DAY", (retention,))
                        last_purge = time.time()
                conn.commit()
            except Exception:  # noqa: BLE001 - never kill the loop
                conn.rollback()
                log.exception("simulator tick failed")
            finally:
                cur.close()
                conn.close()
        except Exception:  # noqa: BLE001 - database not reachable
            log.exception("simulator cannot connect")
        _stop.wait(interval)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="iot-simulator", daemon=True)
    _thread.start()


def stop():
    _stop.set()
