from __future__ import annotations

import asyncio
import os
import queue
import time
from pathlib import Path
from typing import Any

import mido
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from bridge.port_manager import PortManager


class AppState:
    def __init__(self) -> None:
        self.port_manager: PortManager | None = None
        self.monitor_queue: queue.Queue = queue.Queue(maxsize=1024)
        self.websockets: set[WebSocket] = set()
        self.config_dir = Path(__file__).parent.parent / "config"
        self.devices_config: dict = {}
        self.controllers_config: dict = {}
        self.synths_config: dict = {}

    def load_config(self) -> None:
        with open(self.config_dir / "devices.yaml") as f:
            self.devices_config = yaml.safe_load(f)

        self.controllers_config = {}
        for path in (self.config_dir / "controllers").glob("*.yaml"):
            with open(path) as f:
                data = yaml.safe_load(f)
            self.controllers_config[path.stem] = data

        self.synths_config = {}
        for path in (self.config_dir / "synths").glob("*.yaml"):
            with open(path) as f:
                data = yaml.safe_load(f)
            self.synths_config[path.stem] = data

    def save_mapping(self, profile: str, data: dict) -> None:
        path = self.config_dir / "synths" / f"{profile}.yaml"
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)

    def list_ports(self) -> list[str]:
        return mido.get_input_names() + mido.get_output_names()


state = AppState()
app = FastAPI(title="FM-1 MIDI Bridge")
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
async def startup() -> None:
    state.load_config()
    loop = asyncio.get_running_loop()
    state.port_manager = PortManager(
        state.devices_config,
        state.controllers_config,
        state.synths_config,
        monitor_queue=state.monitor_queue,
        loop=loop,
    )
    state.port_manager.on_status_change(lambda s: asyncio.create_task(broadcast_status(s)))
    state.port_manager.start()
    asyncio.create_task(monitor_broadcaster())
    asyncio.create_task(status_broadcaster())


@app.on_event("shutdown")
async def shutdown() -> None:
    if state.port_manager is not None:
        state.port_manager.stop()


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/status")
async def api_status() -> JSONResponse:
    return JSONResponse(
        {
            "ports": state.port_manager.status() if state.port_manager else {},
            "profiles": {
                "controllers": list(state.controllers_config.keys()),
                "synths": list(state.synths_config.keys()),
            },
            "active": state.devices_config,
        }
    )


@app.get("/api/ports")
async def api_ports() -> JSONResponse:
    return JSONResponse({"ports": state.list_ports()})


@app.post("/api/ports")
async def api_ports_update(payload: dict[str, Any]) -> JSONResponse:
    if "controller" in payload:
        state.devices_config["controller"] = payload["controller"]
    if "synth" in payload:
        state.devices_config["synth"] = payload["synth"]
    with open(state.config_dir / "devices.yaml", "w") as f:
        yaml.safe_dump(state.devices_config, f, sort_keys=False)
    return JSONResponse({"ok": True})


@app.get("/api/mappings")
async def api_mappings(profile: str = "fm1_melodee") -> JSONResponse:
    data = state.synths_config.get(profile, {})
    return JSONResponse({"profile": profile, "data": data})


@app.post("/api/mappings")
async def api_mappings_update(payload: dict[str, Any]) -> JSONResponse:
    profile = payload.get("profile", "fm1_melodee")
    data = payload.get("data", {})
    state.synths_config[profile] = data
    state.save_mapping(profile, data)
    return JSONResponse({"ok": True})


@app.post("/api/actions/handshake")
async def api_handshake() -> JSONResponse:
    if state.port_manager is not None:
        state.port_manager.resend_handshake()
    return JSONResponse({"ok": True})


@app.post("/api/actions/panic")
async def api_panic() -> JSONResponse:
    if state.port_manager is not None:
        state.port_manager.panic()
    return JSONResponse({"ok": True})


@app.post("/api/actions/led-test")
async def api_led_test() -> JSONResponse:
    if state.port_manager is not None:
        asyncio.create_task(state.port_manager.led_test())
    return JSONResponse({"ok": True})


async def broadcast_status(status: dict) -> None:
    message = {"type": "status", "data": status}
    disconnected: set[WebSocket] = set()
    for ws in state.websockets:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    state.websockets -= disconnected


async def status_broadcaster() -> None:
    while True:
        await asyncio.sleep(2)
        if state.port_manager is not None:
            await broadcast_status(state.port_manager.status())


async def monitor_broadcaster() -> None:
    while True:
        await asyncio.sleep(0.02)
        messages: list[dict] = []
        try:
            while True:
                messages.append(state.monitor_queue.get_nowait())
        except queue.Empty:
            pass

        if not messages:
            continue

        disconnected: set[WebSocket] = set()
        for ws in state.websockets:
            try:
                for msg in messages:
                    await ws.send_json({"type": "midi", "data": msg})
            except Exception:
                disconnected.add(ws)
        state.websockets -= disconnected


@app.websocket("/ws/monitor")
async def websocket_monitor(ws: WebSocket) -> None:
    await ws.accept()
    state.websockets.add(ws)
    if state.port_manager is not None:
        await ws.send_json({"type": "status", "data": state.port_manager.status()})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        state.websockets.discard(ws)
    except Exception:
        state.websockets.discard(ws)
