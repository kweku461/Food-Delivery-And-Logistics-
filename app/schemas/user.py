from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.enums import PaymentMethod, Role
from app.schemas.common import ORMModel, Timestamped


def _normalize_email(v: str) -> str:
    return v.strip().lower()


class UserRegister(BaseModel):          # public: role is always CUSTOMER
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=150)
    phone: str | None = Field(None, max_length=30)

    @field_validator("email")
    @classmethod
    def _lowercase_email(cls, v: str) -> str:
        return _normalize_email(v)


class UserAdminCreate(UserRegister):    # admin-only: can set any role
    role: Role = Role.CUSTOMER


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=150)
    phone: str | None = Field(None, max_length=30)
    is_active: bool | None = None


class UserRead(Timestamped):            # no password field, ever
    email: EmailStr
    full_name: str
    phone: str | None
    role: Role
    is_active: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def _lowercase_email(cls, v: str) -> str:
        return _normalize_email(v)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
    role: Role
    exp: int


class CustomerProfileCreate(BaseModel):
    date_of_birth: datetime | None = None
    dietary_preferences: str | None = Field(None, max_length=255)
    preferred_payment_method: PaymentMethod | None = None


class CustomerProfileUpdate(CustomerProfileCreate):
    pass


class CustomerProfileRead(Timestamped, CustomerProfileCreate):
    user_id: int
    loyalty_points: int


class AddressCreate(BaseModel):
    label: str = Field("Home", max_length=50)
    address_line: str = Field(max_length=255)
    city: str = Field(max_length=100)
    landmark: str | None = Field(None, max_length=255)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    is_default: bool = False


class AddressUpdate(BaseModel):
    label: str | None = Field(None, max_length=50)
    address_line: str | None = Field(None, max_length=255)
    city: str | None = Field(None, max_length=100)
    landmark: str | None = Field(None, max_length=255)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    is_default: bool | None = None


class AddressRead(Timestamped, AddressCreate):
    user_id: int