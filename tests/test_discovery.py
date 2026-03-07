"""Tests for network/discovery.py — specifically the update_channel bug."""

import socket
import pytest
from zeroconf import Zeroconf, ServiceInfo

from network.discovery import AgentDiscovery, SERVICE_TYPE


class TestUpdateChannel:
    """Verify that update_channel correctly updates the mDNS service properties."""

    def setup_method(self):
        self.discovery = AgentDiscovery("test1234", port=18765)
        self.discovery.register(channel="news")

    def teardown_method(self):
        self.discovery.shutdown()

    def test_initial_channel_is_news(self):
        props = self.discovery._service_info.properties
        assert props[b"channel"] == b"news"

    def test_update_channel_changes_property(self):
        self.discovery.update_channel("talkshow")
        props = self.discovery._service_info.properties
        assert props[b"channel"] == b"talkshow"

    def test_update_channel_preserves_agent_id(self):
        self.discovery.update_channel("sports")
        props = self.discovery._service_info.properties
        assert props[b"agent_id"] == b"test1234"

    def test_update_channel_preserves_server(self):
        original_server = self.discovery._service_info.server
        self.discovery.update_channel("dj")
        assert self.discovery._service_info.server == original_server

    def test_update_channel_preserves_port(self):
        self.discovery.update_channel("dj")
        assert self.discovery._service_info.port == 18765

    def test_update_channel_preserves_addresses(self):
        original_addrs = self.discovery._service_info.addresses
        self.discovery.update_channel("sports")
        assert self.discovery._service_info.addresses == original_addrs

    def test_multiple_channel_switches(self):
        for ch in ["talkshow", "sports", "dj", "news", "talkshow"]:
            self.discovery.update_channel(ch)
            props = self.discovery._service_info.properties
            assert props[b"channel"] == ch.encode(), f"Failed on channel {ch}"

    def test_update_channel_noop_without_registration(self):
        d = AgentDiscovery("unregistered", port=18766)
        d.update_channel("talkshow")
        assert d._service_info is None
        d.zeroconf.close()


class TestPeerTracking:
    def test_get_peers_on_channel_empty(self):
        d = AgentDiscovery("test", port=18767)
        assert d.get_peers_on_channel("news") == []
        d.zeroconf.close()

    def test_get_peers_on_channel_filters(self):
        d = AgentDiscovery("test", port=18768)
        d.peers = {
            "a": {"agent_id": "a", "channel": "news"},
            "b": {"agent_id": "b", "channel": "sports"},
            "c": {"agent_id": "c", "channel": "news"},
        }
        result = d.get_peers_on_channel("news")
        assert len(result) == 2
        ids = {p["agent_id"] for p in result}
        assert ids == {"a", "c"}
        d.zeroconf.close()
