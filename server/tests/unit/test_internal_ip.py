"""Unit tests for `detect_internal_ip()` from pype_server.config.

The function is `lru_cache`d at module level, so tests must clear the cache before
each run to get a deterministic outcome.
"""

import socket
from unittest.mock import patch

import pytest
from pype_server import config


@pytest.fixture(autouse=True)
def _clear_ip_cache() -> None:
    config.detect_internal_ip.cache_clear()


def test_returns_rfc_1918_address_from_gethostbyname_ex() -> None:
    fake_hostname = "host.local"
    fake_ips = ["192.168.1.42"]
    with (
        patch("socket.gethostname", return_value=fake_hostname),
        patch("socket.gethostbyname_ex", return_value=(fake_hostname, [], fake_ips)),
        # Make the UDP-probe path raise so we don't accidentally return the box's
        # actual primary IP if it happens to also be private.
        patch("socket.socket", side_effect=OSError("blocked in test")),
    ):
        assert config.detect_internal_ip() == "192.168.1.42"


def test_picks_first_rfc_1918_when_public_addresses_present() -> None:
    """When the host has both public and RFC 1918 addresses, the private one wins."""
    with (
        patch("socket.gethostname", return_value="host.local"),
        patch(
            "socket.gethostbyname_ex",
            return_value=("host.local", [], ["8.8.8.8", "10.0.5.20", "1.2.3.4"]),
        ),
        patch("socket.socket", side_effect=OSError("blocked in test")),
    ):
        assert config.detect_internal_ip() == "10.0.5.20"


def test_172_16_is_private() -> None:
    """172.16/12 is RFC 1918 too (commonly seen on Docker bridges)."""
    with (
        patch("socket.gethostname", return_value="host.local"),
        patch(
            "socket.gethostbyname_ex",
            return_value=("host.local", [], ["172.17.0.5"]),
        ),
        patch("socket.socket", side_effect=OSError("blocked in test")),
    ):
        assert config.detect_internal_ip() == "172.17.0.5"


def test_returns_127_0_0_1_when_only_loopback_available() -> None:
    """Boxes whose hostname only resolves to 127.0.0.1 (and the UDP probe fails)
    should get the explicit fallback."""
    with (
        patch("socket.gethostname", return_value="host.local"),
        patch(
            "socket.gethostbyname_ex",
            return_value=("host.local", [], ["127.0.0.1"]),
        ),
        patch("socket.socket", side_effect=OSError("blocked in test")),
    ):
        assert config.detect_internal_ip() == "127.0.0.1"


def test_returns_127_0_0_1_when_all_lookups_fail() -> None:
    with (
        patch("socket.gethostname", side_effect=OSError("dns down")),
        patch("socket.gethostbyname_ex", side_effect=socket.gaierror("dns down")),
        patch("socket.socket", side_effect=OSError("network down")),
    ):
        assert config.detect_internal_ip() == "127.0.0.1"


def test_returns_127_0_0_1_when_only_public_addresses() -> None:
    """A box whose only IPs are public-routable doesn't qualify; we fall back."""
    with (
        patch("socket.gethostname", return_value="host.local"),
        patch(
            "socket.gethostbyname_ex",
            return_value=("host.local", [], ["8.8.8.8", "1.2.3.4"]),
        ),
        patch("socket.socket", side_effect=OSError("blocked in test")),
    ):
        assert config.detect_internal_ip() == "127.0.0.1"
