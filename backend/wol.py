"""Minimal Wake-on-LAN magic-packet sender. No external deps."""
import socket


def send_magic_packet(mac_address: str, broadcast_ip: str = "255.255.255.255", port: int = 9) -> None:
    mac_bytes = bytes.fromhex(mac_address.replace(":", "").replace("-", ""))
    if len(mac_bytes) != 6:
        raise ValueError(f"Invalid MAC address: {mac_address}")

    magic_packet = b"\xff" * 6 + mac_bytes * 16

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic_packet, (broadcast_ip, port))
