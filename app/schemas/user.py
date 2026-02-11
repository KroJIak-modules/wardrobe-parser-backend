from pydantic import BaseModel, field_validator
from datetime import datetime

class ExampleCreate(BaseModel):
    # ...: int
    # ...: str | None = None


class ExampleUpdate(BaseModel):
    # ...: int
    # ...: str | None = None


class ExampleResponse(BaseModel):
    id: int
    # ...: int
    # ...: str | None = None