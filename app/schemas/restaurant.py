from decimal import Decimal
from pydantic import BaseModel, EmailStr, Field, model_validator

from app.schemas.common import Timestamped


class RestaurantCreate(BaseModel):      # owner_id comes from the token
    name: str = Field(min_length=1, max_length=150)
    description: str | None = None
    cuisine_type: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    logo_url: str | None = Field(None, max_length=500)


class RestaurantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = None
    cuisine_type: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    logo_url: str | None = Field(None, max_length=500)
    is_active: bool | None = None


class RestaurantRead(Timestamped):
    owner_id: int
    name: str
    description: str | None
    cuisine_type: str | None
    phone: str | None
    email: str | None
    logo_url: str | None
    is_active: bool


class RestaurantFilter(BaseModel):
    search: str | None = None
    cuisine_type: str | None = None
    is_active: bool | None = None
    city: str | None = None             # location lives on branches, so this joins RestaurantBranch
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    radius_km: float | None = Field(None, gt=0, le=100)

    @model_validator(mode="after")
    def _geo_all_or_none(self):
        geo = (self.latitude, self.longitude, self.radius_km)
        if any(v is not None for v in geo) and any(v is None for v in geo):
            raise ValueError("latitude, longitude and radius_km must be provided together")
        return self


class BranchCreate(BaseModel):          # restaurant_id comes from the URL
    name: str = Field(max_length=150)
    address_line: str = Field(max_length=255)
    city: str = Field(max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    phone: str | None = Field(None, max_length=30)
    delivery_fee: Decimal = Field(Decimal("0.00"), ge=0, max_digits=10, decimal_places=2)


class BranchUpdate(BaseModel):
    name: str | None = Field(None, max_length=150)
    address_line: str | None = Field(None, max_length=255)
    city: str | None = Field(None, max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    phone: str | None = Field(None, max_length=30)
    delivery_fee: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    is_active: bool | None = None


class BranchRead(Timestamped):
    restaurant_id: int
    name: str
    address_line: str
    city: str
    latitude: float | None
    longitude: float | None
    phone: str | None
    delivery_fee: Decimal
    is_active: bool


class StaffCreate(BaseModel):           # branch_id comes from the URL
    user_id: int
    position: str = Field("Staff", max_length=100)


class StaffUpdate(BaseModel):
    position: str | None = Field(None, max_length=100)
    is_active: bool | None = None


class StaffRead(Timestamped):
    user_id: int
    branch_id: int
    position: str
    is_active: bool