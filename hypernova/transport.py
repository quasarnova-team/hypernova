"""UDP transport helpers shared by subscribers, the registry listener and the
relay: multicast-or-unicast receive sockets and send sockets, asyncio-native."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
from urllib.parse import urlparse

__all__ = ["parse_address", "open_receive_socket", "open_send_socket", "DatagramRelayProtocol"]


def parse_address(address: str) -> tuple[str, int]:
    """'opc.udp://239.0.0.5:4840' -> ('239.0.0.5', 4840)."""
    parsed = urlparse(address)
    if parsed.scheme != "opc.udp" or not parsed.hostname or not parsed.port:
        raise ValueError(f"invalid address {address!r}: expected opc.udp://host:port")
    return parsed.hostname, parsed.port


def _is_multicast(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_multicast
    except ValueError:
        return False


def open_receive_socket(host: str, port: int, *, interface: str = "0.0.0.0") -> socket.socket:
    """Bound, non-blocking UDP socket; joins the group when host is multicast.

    SO_REUSEPORT is set only for multicast (several subscribers on one host
    each get a copy of every group datagram). For unicast it would make the
    kernel load-balance datagrams between binders — silent frame stealing —
    so a unicast port is deliberately exclusive per process."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    multicast = _is_multicast(host)
    if multicast and hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("", port))
    if multicast:
        membership = struct.pack("4s4s", socket.inet_aton(host), socket.inet_aton(interface))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    sock.setblocking(False)
    return sock


def open_send_socket(host: str, *, ttl: int = 1, loopback: bool = True) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if _is_multicast(host):
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1 if loopback else 0)
    sock.setblocking(False)
    return sock


class DatagramRelayProtocol(asyncio.DatagramProtocol):
    """Feeds every received datagram to a callback; connection stays open."""

    def __init__(self, on_datagram) -> None:
        self._on_datagram = on_datagram

    def datagram_received(self, data: bytes, addr) -> None:
        self._on_datagram(data, addr)


async def create_receiver(host: str, port: int, on_datagram, *, interface: str = "0.0.0.0"):
    """asyncio datagram endpoint on a (possibly multicast) receive socket."""
    loop = asyncio.get_running_loop()
    sock = open_receive_socket(host, port, interface=interface)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: DatagramRelayProtocol(on_datagram), sock=sock)
    return transport
