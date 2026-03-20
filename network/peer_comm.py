import asyncio
import json
import sys
from typing import Callable, Any

import websockets
from websockets.asyncio.server import serve

from log import get_logger

logger = get_logger(__name__)


class PeerServer:
    """WebSocket server for receiving messages from peer agents."""

    def __init__(self, port: int = 8765):
        self.port = port
        self.handlers: dict[str, Callable] = {}
        self._server = None

    def on(self, msg_type: str, handler: Callable):
        """Register a handler for a message type."""
        self.handlers[msg_type] = handler

    async def start(self):
        """Start the WebSocket server. Tries the configured port, falls back if busy."""
        for attempt_port in (self.port, self.port + 1, self.port + 2):
            try:
                self._server = await serve(self._handle, "0.0.0.0", attempt_port)
                if attempt_port != self.port:
                    logger.warning("Port %d busy, PeerServer using port %d instead",
                                   self.port, attempt_port)
                    self.port = attempt_port
                logger.info("PeerServer listening on port %d", self.port)
                return
            except OSError as e:
                if e.errno == 48 and attempt_port != self.port + 2:
                    continue
                logger.error("PeerServer failed to bind: %s", e)
                raise

    async def _handle(self, websocket):
        remote = websocket.remote_address
        logger.info("Connection opened from %s", remote)
        try:
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get("type")
                msg_size = sys.getsizeof(message)
                logger.debug("Incoming message",
                             extra={"type": msg_type, "size_bytes": msg_size,
                                    "remote": str(remote)})
                if msg_type in self.handlers:
                    response = await self.handlers[msg_type](data)
                    if response:
                        await websocket.send(json.dumps(response))
                else:
                    logger.warning("Unknown message type: %s", msg_type)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            logger.info("Connection closed from %s", remote)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("PeerServer stopped")


class PeerClient:
    """WebSocket client for sending messages to peer agents."""

    def __init__(self):
        self._connections: dict[str, Any] = {}

    async def send(self, host: str, port: int, message: dict) -> dict | None:
        """Send a message to a peer and optionally get a response."""
        uri = f"ws://{host}:{port}"
        msg_type = message.get("type", "unknown")
        logger.debug("Outgoing message",
                     extra={"type": msg_type, "destination": uri})
        try:
            async with websockets.connect(uri, close_timeout=5) as ws:
                logger.info("Connected to %s", uri)
                await ws.send(json.dumps(message))
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=10)
                    return json.loads(response)
                except asyncio.TimeoutError:
                    logger.warning("Response timeout from %s", uri)
                    return None
        except Exception as e:
            logger.error("Error connecting to %s: %s", uri, e)
            return None

    async def send_to_peer(self, peer: dict, message: dict) -> dict | None:
        """Send a message to a peer (using peer dict from discovery)."""
        return await self.send(peer["host"], peer["port"], message)


# Message constructors
def msg_hello(agent_id: str, interests: list[str], channel: str) -> dict:
    return {"type": "hello", "agent_id": agent_id,
            "interests": interests, "current_channel": channel}

def msg_goodbye(agent_id: str) -> dict:
    return {"type": "goodbye", "agent_id": agent_id}

def msg_cohost_prompt(statement: str, topic: str, channel: str) -> dict:
    return {"type": "cohost_prompt", "statement": statement,
            "topic": topic, "channel": channel}

def msg_cohost_response(response: str, voice_id: str) -> dict:
    return {"type": "cohost_response", "response": response, "voice_id": voice_id}

def msg_callin_forward(transcript: str, caller_agent_id: str) -> dict:
    return {"type": "callin_forward", "transcript": transcript,
            "caller_agent_id": caller_agent_id}

def msg_channel_sync(channel: str, subchannel: str) -> dict:
    return {"type": "channel_sync", "channel": channel, "subchannel": subchannel}

def msg_status_update(agent_id: str, agent_name: str, channel: str,
                      subchannel: str, activity: str) -> dict:
    return {"type": "status_update", "agent_id": agent_id,
            "agent_name": agent_name, "channel": channel,
            "subchannel": subchannel, "activity": activity}

def msg_status_request(agent_id: str) -> dict:
    return {"type": "status_request", "agent_id": agent_id}
