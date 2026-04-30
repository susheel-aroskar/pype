from pydantic import BaseModel


class ServiceLoadEntry(BaseModel):
    service_name: str
    load: int
