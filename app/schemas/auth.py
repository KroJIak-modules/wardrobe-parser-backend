from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class AdminTokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    access_expires_in: int
    refresh_expires_in: int


class AdminRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class AdminMeResponse(BaseModel):
    login: str
    role: str = "superadmin"
