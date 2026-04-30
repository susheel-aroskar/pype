import pytest
from pype_client.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(base_url="http://test.local", network_buffer_ms=100)


BASE_URL = "http://test.local"
