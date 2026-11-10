"""Compute a narrow systemd network exception for the JP transport before handover."""
import ipaddress
import socket
from pathlib import Path

HOST = "evolution.codigo5.com.br"


def network_allowance(addresses, resolver_config):
    edges = {ipaddress.ip_address(a) for a in addresses}
    if not edges or len(edges) > 32 or any(not ip.is_global or ip.is_multicast for ip in edges):
        raise ValueError("unexpected_evolution_dns")
    resolvers = set()
    for line in resolver_config.splitlines():
        parts = line.partition("#")[0].split()
        if parts and parts[0] == "nameserver":
            ip = ipaddress.ip_address(parts[1])
            if ip.is_unspecified or ip.is_multicast:
                raise ValueError("invalid_resolver")
            resolvers.add(ip)
    return sorted({f"{ip}/{ip.max_prefixlen}" for ip in edges | resolvers})


def system_allowance():
    rows = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)
    return network_allowance([r[4][0] for r in rows], Path("/etc/resolv.conf").read_text())


if __name__ == "__main__":
    print("[Service]\nIPAddressAllow="+" ".join(system_allowance()))
