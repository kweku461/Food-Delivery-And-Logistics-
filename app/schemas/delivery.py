from datetime import datetime
from pydantic import BaseModel, Field

from app.enums import DeliveryStatus
from app.schemas.common import ORMModel, Timestamped


class DriverCreate(BaseModel):
    user_id: int
    vehicle_type: str | None = Field(None, max_length=50)
    plate_number: str | None = Field(None, max_length=30)
    license_number: str | None = Field(None, max_length=50)


class DriverUpdate(BaseModel):
    vehicle_type: str | None = Field(None, max_length=50)
    plate_number: str | None = Field(None, max_length=30)
    license_number: str | None = Field(None, max_length=50)
    is_available: bool | None = None
    current_latitude: float | None = Field(None, ge=-90, le=90)
    current_longitude: float | None = Field(None, ge=-180, le=180)


class DriverRead(Timestamped):
    user_id: int
    vehicle_type: str | None
    plate_number: str | None
    license_number: str | None
    is_available: bool
    current_latitude: float | None
    current_longitude: float | None


class AssignDriver(BaseModel):
    driver_id: int


class DeliveryStatusUpdate(BaseModel):  # transition rules live in the service layer
    status: DeliveryStatus
    note: str | None = Field(None, max_length=255)


class DeliveryStatusHistoryRead(ORMModel):
    id: int
    delivery_id: int
    from_status: DeliveryStatus | None
    to_status: DeliveryStatus
    changed_by_id: int | None
    note: str | None
    created_at: datetime


class DeliveryRead(Timestamped):
    order_id: int
    driver_id: int | None
    status: DeliveryStatus
    pickup_address: str
    dropoff_address: str
    assigned_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None


class DeliveryFilter(BaseModel):
    status: DeliveryStatus | None = None
    driver_id: int | None = None