from __future__ import annotations

import argparse
import asyncio
import logging

import websockets

from radioagent.models import (
    ClientRegistration,
    InjectUserMessage,
    RegisteredMessage,
    SessionUpdateMessage,
    ShutdownMessage,
    dump_socket_message,
    parse_socket_message,
)


class CLIInjector:
    def __init__(self, *, websocket_uri: str, logger: logging.Logger) -> None:
        self.websocket_uri = websocket_uri
        self.logger = logger
        self._seen_entry_ids: set[str] = set()
        self._shutdown_event = asyncio.Event()

    async def run_interactive(self) -> None:
        async with websockets.connect(self.websocket_uri) as websocket:
            await websocket.send(
                dump_socket_message(
                    ClientRegistration(
                        client_id="cli_injector",
                        role="injector",
                        display_name="CLI Injector",
                    )
                )
            )
            listener = asyncio.create_task(self._listen(websocket))
            shutdown_waiter = asyncio.create_task(self._shutdown_event.wait())
            try:
                while True:
                    input_task = asyncio.create_task(asyncio.to_thread(input, "radio> "))
                    done, pending = await asyncio.wait(
                        {input_task, shutdown_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if shutdown_waiter in done:
                        input_task.cancel()
                        return
                    for task in pending:
                        if task is input_task:
                            task.cancel()
                    raw = input_task.result()
                    text = raw.strip()
                    if not text:
                        continue
                    if text in {"/quit", "/exit"}:
                        return
                    await websocket.send(
                        dump_socket_message(InjectUserMessage(text=text, author="user"))
                    )
            finally:
                listener.cancel()
                shutdown_waiter.cancel()
                await asyncio.gather(listener, shutdown_waiter, return_exceptions=True)

    async def send_once(self, text: str) -> None:
        async with websockets.connect(self.websocket_uri) as websocket:
            await websocket.send(
                dump_socket_message(
                    ClientRegistration(
                        client_id="cli_injector_once",
                        role="injector",
                        display_name="CLI Injector",
                    )
                )
            )
            await websocket.send(
                dump_socket_message(InjectUserMessage(text=text, author="user"))
            )

    async def _listen(self, websocket: websockets.WebSocketClientProtocol) -> None:
        async for raw_message in websocket:
            message = parse_socket_message(raw_message)
            if isinstance(message, RegisteredMessage):
                self.logger.info("cli injector connected")
                continue
            if isinstance(message, SessionUpdateMessage):
                self._print_new_history(message)
                continue
            if isinstance(message, ShutdownMessage):
                print(f"\n[session ended] {message.reason}")
                self._shutdown_event.set()
                return

    def _print_new_history(self, message: SessionUpdateMessage) -> None:
        for entry in message.session.history:
            if entry.entry_id in self._seen_entry_ids:
                continue
            self._seen_entry_ids.add(entry.entry_id)
            if entry.source == "system":
                print(f"\n[system] {entry.text}")
                continue
            print(f"\n[{entry.speaker_name}] {entry.text}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inject plain-text messages into a debate")
    parser.add_argument("--uri", required=True)
    parser.add_argument("--text")
    return parser.parse_args()


async def run_cli(websocket_uri: str, logger: logging.Logger, text: str | None = None) -> None:
    injector = CLIInjector(websocket_uri=websocket_uri, logger=logger)
    if text:
        await injector.send_once(text)
        return
    await injector.run_interactive()


def main() -> None:
    from radioagent.config import load_settings
    from radioagent.observability.logging import configure_logging

    args = parse_args()
    settings = load_settings()
    logger = configure_logging(settings.log_level, settings.logs_dir).getChild("cli")
    asyncio.run(run_cli(args.uri, logger, text=args.text))


if __name__ == "__main__":
    main()

