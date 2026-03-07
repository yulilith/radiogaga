import json
import socket
from typing import Callable

from zeroconf import Zeroconf, ServiceBrowser, ServiceInfo, ServiceStateChange

from log import get_logger

logger = get_logger(__name__)

SERVICE_TYPE = "_radioagent._tcp.local."


class AgentDiscovery:
    """mDNS-based discovery of nearby RadioAgent instances on the local network."""

    def __init__(self, agent_id: str, port: int = 8765):
        self.agent_id = agent_id
        self.port = port
        self.zeroconf = Zeroconf()
        self.peers: dict[str, dict] = {}
        self._service_info: ServiceInfo | None = None
        self._browser: ServiceBrowser | None = None
        self._on_peer_found: Callable | None = None
        self._on_peer_lost: Callable | None = None

    def register(self, interests: list[str] | None = None, channel: str = "dailybrief"):
        """Advertise this agent on the local network."""
        hostname = socket.gethostname()
        try:
            ip = socket.gethostbyname(hostname)
        except socket.gaierror:
            ip = "127.0.0.1"

        properties = {
            b"agent_id": self.agent_id.encode(),
            b"interests": json.dumps(interests or []).encode(),
            b"channel": channel.encode(),
            b"version": b"1.0",
        }

        self._service_info = ServiceInfo(
            SERVICE_TYPE,
            f"RadioAgent-{self.agent_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=self.port,
            properties=properties,
        )
        self.zeroconf.register_service(self._service_info)
        logger.info("Registered as RadioAgent-%s at %s:%d", self.agent_id, ip, self.port)
        logger.debug("mDNS service registered",
                      extra={"service_type": SERVICE_TYPE,
                             "hostname": hostname,
                             "interests": interests or [],
                             "channel": channel})

    def update_channel(self, channel: str):
        """Update the advertised channel (when user switches)."""
        if self._service_info:
            props = dict(self._service_info.properties)
            props[b"channel"] = channel.encode()
            self._service_info.properties = props
            self.zeroconf.update_service(self._service_info)

    def start_browsing(self, on_peer_found: Callable = None,
                       on_peer_lost: Callable = None):
        """Listen for other RadioAgent instances on the network."""
        self._on_peer_found = on_peer_found
        self._on_peer_lost = on_peer_lost
        self._browser = ServiceBrowser(
            self.zeroconf, SERVICE_TYPE, handlers=[self._on_state_change]
        )
        logger.info("Browsing for nearby RadioAgent peers")

    def _on_state_change(self, zeroconf: Zeroconf, service_type: str,
                          name: str, state_change: ServiceStateChange):
        if state_change == ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                peer_id = info.properties.get(b"agent_id", b"").decode()
                if peer_id and peer_id != self.agent_id:
                    addresses = info.parsed_addresses()
                    host = addresses[0] if addresses else "127.0.0.1"
                    peer = {
                        "agent_id": peer_id,
                        "host": host,
                        "port": info.port,
                        "channel": info.properties.get(b"channel", b"").decode(),
                        "interests": json.loads(
                            info.properties.get(b"interests", b"[]").decode()
                        ),
                    }
                    self.peers[peer_id] = peer
                    logger.info("Found peer: %s at %s:%d", peer_id, host, info.port)
                    logger.debug("Peer details",
                                  extra={"peer_id": peer_id,
                                         "host": host,
                                         "port": info.port,
                                         "channel": peer["channel"],
                                         "interests": peer["interests"]})
                    if self._on_peer_found:
                        self._on_peer_found(peer)

        elif state_change == ServiceStateChange.Removed:
            # Find and remove the peer
            for pid, peer in list(self.peers.items()):
                if f"RadioAgent-{pid}" in name:
                    del self.peers[pid]
                    logger.info("Lost peer: %s", pid)
                    if self._on_peer_lost:
                        self._on_peer_lost(peer)
                    break

    def get_peers_on_channel(self, channel: str) -> list[dict]:
        """Get peers currently on the same channel."""
        return [p for p in self.peers.values() if p.get("channel") == channel]

    def shutdown(self):
        """Unregister and clean up."""
        if self._service_info:
            self.zeroconf.unregister_service(self._service_info)
        self.zeroconf.close()
        logger.info("Shutdown complete")
