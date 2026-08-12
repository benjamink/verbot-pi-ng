"""Advertise the control server over mDNS so clients can find it by name."""

import logging
import socket

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from verbot.config import Settings

log = logging.getLogger(__name__)

SERVICE_TYPE = "_verbot._tcp.local."
SERVICE_NAME = f"Verbot.{SERVICE_TYPE}"


def local_address() -> str:
    """Best-effort primary IPv4 address, without needing a reachable target."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def service_info(settings: Settings, hostname: str, address: str) -> ServiceInfo:
    return ServiceInfo(
        SERVICE_TYPE,
        SERVICE_NAME,
        addresses=[socket.inet_aton(address)],
        port=settings.port,
        properties={"path": "/status"},
        server=f"{hostname}.local.",
    )


class ServiceAdvertiser:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._zc: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None

    async def start(self) -> None:
        try:
            self._info = service_info(
                self._settings,
                hostname=socket.gethostname().split(".")[0],
                address=local_address(),
            )
            self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
            await self._zc.async_register_service(self._info)
            log.info("advertised %s on port %d", self._info.server, self._settings.port)
        except OSError as exc:
            # Discovery is a convenience; never let it stop the robot working.
            log.warning("mDNS registration failed: %s", exc)
            await self.close()

    async def close(self) -> None:
        if self._zc is not None:
            if self._info is not None:
                await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()
        self._zc = None
        self._info = None
