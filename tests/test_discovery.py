import socket

from verbot.config import Settings
from verbot.discovery import service_info


def test_service_info_uses_a_dotted_local_hostname():
    """mDNS requires a hostname ending in `.local.`, not an IP address."""
    info = service_info(Settings(port=8080), hostname="verbot", address="192.168.1.50")
    assert info.server == "verbot.local."
    assert info.port == 8080
    assert info.type == "_verbot._tcp.local."


def test_service_info_encodes_the_address():
    info = service_info(Settings(), hostname="verbot", address="192.168.1.50")
    assert info.addresses == [socket.inet_aton("192.168.1.50")]


def test_service_info_advertises_the_api_path():
    info = service_info(Settings(), hostname="verbot", address="192.168.1.50")
    assert info.properties[b"path"] == b"/status"
