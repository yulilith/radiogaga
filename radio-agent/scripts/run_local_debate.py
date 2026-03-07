from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from radioagent.config import load_settings
from radioagent.interface.cli_injector import CLIInjector
from radioagent.observability.logging import EventRecorder, configure_logging
from radioagent.preflight import ProviderPreflightError, run_provider_preflight
from radioagent.transport.ws_hub import WebsocketHub


async def launch_agent(
    *,
    agent_id: str,
    prompt_path: Path,
    websocket_uri: str,
    workspace_dir: Path,
    mute: bool,
) -> asyncio.subprocess.Process:
    command = [
        sys.executable,
        "-m",
        "radioagent.agents.runtime",
        "--agent-id",
        agent_id,
        "--prompt-path",
        str(prompt_path),
        "--uri",
        websocket_uri,
    ]
    if mute:
        command.append("--mute")
    return await asyncio.create_subprocess_exec(*command, cwd=str(workspace_dir))


async def run_local_debate(args: argparse.Namespace) -> None:
    settings = load_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)

    logger = configure_logging(settings.log_level, settings.logs_dir).getChild("launcher")
    try:
        await run_provider_preflight(settings)
    except ProviderPreflightError as exc:
        logger.error("startup preflight failed", extra={"error": str(exc)})
        print(
            "\nStartup preflight failed.\n"
            f"{exc}\n\n"
            "Use mock mode to test locally right now:\n"
            "RADIO_AGENT_BACKEND=mock RADIO_TTS_PROVIDER=mock RADIO_AUDIO_ENABLED=false "
            "python scripts/run_local_debate.py --mute-agents\n"
        )
        return

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    recorder = EventRecorder(
        log_path=settings.logs_dir / "sessions" / f"debate_{timestamp}.jsonl",
        logger=logger,
    )

    expected_agents = ["agent_a", "agent_b"]
    hub = WebsocketHub(
        host=settings.host,
        port=settings.port,
        prompt_dir=settings.prompt_dir,
        logger=logger.getChild("hub"),
        recorder=recorder,
        expected_agent_ids=expected_agents,
    )

    processes: list[asyncio.subprocess.Process] = []
    try:
        await hub.start()
        for agent_id in expected_agents:
            process = await launch_agent(
                agent_id=agent_id,
                prompt_path=settings.prompt_dir / f"{agent_id}.yaml",
                websocket_uri=settings.websocket_uri,
                workspace_dir=settings.workspace_dir,
                mute=args.mute_agents,
            )
            processes.append(process)

        await hub.wait_until_agents_ready(timeout_seconds=30)
        logger.info(
            "debate stack ready",
            extra={
                "websocket_uri": settings.websocket_uri,
                "agent_backend": settings.agent_backend,
                "tts_provider": settings.tts_provider,
                "audio_enabled": settings.audio_enabled and not args.mute_agents,
                "prompt_dir": str(settings.prompt_dir),
            },
        )
        await hub.start_debate(args.topic or settings.topic, args.max_turns or settings.max_turns)

        injector = CLIInjector(websocket_uri=settings.websocket_uri, logger=logger.getChild("cli"))
        for text in args.inject:
            await injector.send_once(text)

        if args.no_cli:
            await hub.wait_until_session_complete(timeout_seconds=180)
            return

        print(
            "\nLocal debate is live.\n"
            "Type text to inject it into the conversation.\n"
            "Use /quit to stop the session.\n"
        )
        await injector.run_interactive()
    finally:
        await hub.stop()
        for process in processes:
            if process.returncode is None:
                process.terminate()
        if processes:
            await asyncio.gather(
                *(process.wait() for process in processes),
                return_exceptions=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local two-agent debate session")
    parser.add_argument("--topic")
    parser.add_argument("--max-turns", type=int)
    parser.add_argument("--inject", action="append", default=[])
    parser.add_argument("--no-cli", action="store_true")
    parser.add_argument("--mute-agents", action="store_true")
    return parser.parse_args()


def main() -> None:
    asyncio.run(run_local_debate(parse_args()))


if __name__ == "__main__":
    main()

