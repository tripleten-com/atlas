"""Facilities the signed-in user's organisation runs. Drives the facility switcher."""
from fastapi import APIRouter, Depends
from psycopg import Connection

from ..auth import CurrentUser, get_current_user
from ..db import fetch_all, get_db
from ..schemas import FacilityOut

router = APIRouter(tags=["facilities"])

FACILITY_SQL = """
SELECT f.id, f.code, f.name, f.kind, f.timezone, f.data_source,
       (SELECT count(*) FROM patients p WHERE p.facility_id = f.id) AS patient_count,
       (SELECT count(*) FROM encounters e WHERE e.facility_id = f.id AND e.discharge_date IS NULL) AS active_encounters,
       c.status AS feed_status, c.last_event_at
FROM facilities f
LEFT JOIN integration_connections c ON c.facility_id = f.id
WHERE f.tenant_id = %s
ORDER BY (f.kind = 'demo') DESC, f.name
"""


def list_facilities(conn: Connection, tenant_id: int) -> list[FacilityOut]:
    return [FacilityOut(**row) for row in fetch_all(conn, FACILITY_SQL, (tenant_id,))]


@router.get("/api/facilities", response_model=list[FacilityOut])
def facilities(user: CurrentUser = Depends(get_current_user), conn: Connection = Depends(get_db)) -> list[FacilityOut]:
    return list_facilities(conn, user.tenant_id)
