import asyncio
import json
from typing import Callable, Any

import websockets
from websockets.asyncio.server import serve


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
        """Start the WebSocket server."""
        self._server = await serve(self._handle, "0.0.0.0", self.port)
        print(f"[PeerServer] Listening on port {self.port}")

    async def _handle(self, websocket):
        try:
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type in self.handlers:
                    response = await self.handlers[msg_type](data)
                    if response:
                        await websocket.send(json.dumps(response))
                else:
                    print(f"[PeerServer] Unknown message type: {msg_type}")
        except websockets.exceptions.ConnectionClosed:
            pass

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()


class PeerClient:
    """WebSocket client for sending messages to peer agents."""

    def __init__(self):
        self._connections: dict[str, Any] = {}

    async def send(self, host: str, port: int, message: dict) -> dict | None:
        """Send a message to a peer and optionally get a response."""
        uri = f"ws://{host}:{port}"
        try:
            async with websockets.connect(uri, close_timeout=5) as ws:
                await ws.send(json.dumps(message))
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=10)
                    return json.loads(response)
                except asyncio.TimeoutError:
                    return None
        except Exception as e:
            print(f"[PeerClient] Error connecting to {uri}: {e}")
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
