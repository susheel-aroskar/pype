from fastapi import APIRouter

from pype_server.deps import ServiceRegistryDep
from pype_server.schemas.load import ServiceLoadEntry

router = APIRouter(tags=["load"])


@router.get("/load/services", response_model=list[ServiceLoadEntry])
async def services_load(service_registry: ServiceRegistryDep) -> list[ServiceLoadEntry]:
    return [
        ServiceLoadEntry(service_name=name, load=load)
        for name, load in service_registry.snapshot_loads()
    ]
