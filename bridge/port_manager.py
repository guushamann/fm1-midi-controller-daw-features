from __future__ import annotations

import asyncio
import importlib
import queue
import re
import threading
import time
from typing import Callable

import mido

from bridge.engine import MidiEngine


class PortManager:
    def __init__(
        self,
        devices_config: dict,
        controllers_config: dict,
        synths_config: dict,
        monitor_queue: queue.Queue | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.devices = devices_config
        self.controllers_config = controllers_config
        self.synths_config = synths_config
        self.monitor_queue = monitor_queue
        self.loop = loop

        controller_profile = devices_config["controller"]["profile"]
        synth_profile = devices_config["synth"]["profile"]

        self.controller_config = controllers_config[controller_profile]
        self.synth_config = synths_config[synth_profile]

        self.driver = self._load_driver(self.controller_config)
        self.engine = MidiEngine(
            self.synth_config,
            self.driver,
            monitor_queue,
        )

        self._ports: dict[str, mido.Port | None] = {
            "daw_in": None,
            "daw_out": None,
            "midi_in": None,
            "midi_out": None,
            "synth_in": None,
            "synth_out": None,
        }
        self._callbacks: dict[str, Callable] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._connected_names: dict[str, str | None] = {
            k: None for k in self._ports
        }

    def _load_driver(self, config: dict):
        driver_path = config.get("driver", "bridge.drivers.base:BaseControllerDriver")
        module_name, class_name = driver_path.split(":")
        module = importlib.import_module(module_name)
        return getattr(module, class_name)(config)

    def _compile_patterns(self) -> dict[str, re.Pattern]:
        c = self.devices["controller"]["port_patterns"]
        s = self.devices["synth"]["port_patterns"]
        return {
            "daw_in": re.compile(c["daw_in"], re.IGNORECASE),
            "daw_out": re.compile(c["daw_out"], re.IGNORECASE),
            "midi_in": re.compile(c["midi_in"], re.IGNORECASE),
            "midi_out": re.compile(c["midi_out"], re.IGNORECASE),
            "synth_in": re.compile(s["midi_in"], re.IGNORECASE),
            "synth_out": re.compile(s["midi_out"], re.IGNORECASE),
        }

    def _match_port(self, names: list[str], pattern: re.Pattern) -> str | None:
        for name in names:
            if pattern.search(name):
                return name
        return None

    def _open_port(self, name: str, port_type: str) -> mido.Port | None:
        try:
            if "in" in port_type:
                port = mido.open_input(name, callback=self._make_callback(port_type))
            else:
                port = mido.open_output(name)
            return port
        except Exception:
            return None

    def _make_callback(self, port_type: str) -> Callable:
        def callback(msg: mido.Message) -> None:
            self._on_message(port_type, msg)

        return callback

    def _on_message(self, port_type: str, msg: mido.Message) -> None:
        if self.monitor_queue is not None:
            try:
                if "daw" in port_type:
                    port_label = "LK DAW"
                elif "synth" in port_type:
                    port_label = "FM-1"
                else:
                    port_label = "LK MIDI"
                self.monitor_queue.put_nowait(
                    {
                        "direction": "IN",
                        "port": port_label,
                        "bytes": msg.bytes(),
                    }
                )
            except Exception:
                pass

        if port_type == "daw_in":
            event = self.driver.parse_daw_message(msg)
            if event is not None and self.loop is not None:
                try:
                    self.loop.call_soon_threadsafe(self.engine.handle_event, event)
                except Exception:
                    pass
        elif port_type == "midi_in":
            self._route_midi_input(msg)

    def _route_midi_input(self, msg: mido.Message) -> None:
        from bridge.events import NoteEvent

        if msg.type in ("note_on", "note_off"):
            event = NoteEvent(
                channel=msg.channel + 1,
                note=msg.note,
                velocity=msg.velocity,
                is_on=msg.type == "note_on" and msg.velocity > 0,
            )
            if self.loop is not None:
                try:
                    self.loop.call_soon_threadsafe(self.engine.handle_event, event)
                except Exception:
                    pass

    def _close_port(self, port_type: str) -> None:
        port = self._ports.get(port_type)
        if port is not None:
            try:
                port.close()
            except Exception:
                pass
        self._ports[port_type] = None
        self._connected_names[port_type] = None

    def _update(self) -> None:
        patterns = self._compile_patterns()
        inputs = mido.get_input_names()
        outputs = mido.get_output_names()

        port_lists = {
            "daw_in": inputs,
            "midi_in": inputs,
            "synth_in": inputs,
            "daw_out": outputs,
            "midi_out": outputs,
            "synth_out": outputs,
        }

        for port_type, names in port_lists.items():
            pattern = patterns[port_type]
            matched = self._match_port(names, pattern)
            current = self._connected_names[port_type]

            if matched != current:
                self._close_port(port_type)
                if matched:
                    port = self._open_port(matched, port_type)
                    if port is not None:
                        self._ports[port_type] = port
                        self._connected_names[port_type] = matched

        daw_out = self._ports.get("daw_out")
        synth_out = self._ports.get("synth_out")
        self.engine.set_ports(synth_out, daw_out)

        if self._all_controller_connected() and daw_out is not None:
            self.driver.on_connect(daw_out)

        self._notify_status()

    def _all_controller_connected(self) -> bool:
        return all(
            self._ports.get(k) is not None
            for k in ("daw_in", "daw_out", "midi_in", "midi_out")
        )

    def _notify_status(self) -> None:
        cb = self._callbacks.get("status")
        if cb is None:
            return
        try:
            if self.loop is not None:
                self.loop.call_soon_threadsafe(cb, self.status())
            else:
                cb(self.status())
        except Exception:
            pass

    def status(self) -> dict:
        return {
            "controller": {
                "daw_in": self._connected_names.get("daw_in"),
                "daw_out": self._connected_names.get("daw_out"),
                "midi_in": self._connected_names.get("midi_in"),
                "midi_out": self._connected_names.get("midi_out"),
            },
            "synth": {
                "midi_in": self._connected_names.get("synth_in"),
                "midi_out": self._connected_names.get("synth_out"),
            },
            "profile": {
                "controller": self.devices["controller"]["profile"],
                "synth": self.devices["synth"]["profile"],
            },
            "state": self.engine.state.snapshot(),
        }

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def stop(self) -> None:
        self._running = False
        daw_out = self._ports.get("daw_out")
        self.driver.on_disconnect(daw_out)
        for port_type in list(self._ports):
            self._close_port(port_type)
        if self._thread is not None:
            self._thread.join(timeout=3)

    def resend_handshake(self) -> None:
        daw_out = self._ports.get("daw_out")
        if daw_out is not None:
            self.driver.on_connect(daw_out)

    def panic(self) -> None:
        self.engine.panic()

    async def led_test(self) -> None:
        daw_out = self._ports.get("daw_out")
        await self.driver.send_led_diagnostic(daw_out)

    def on_status_change(self, callback: Callable) -> None:
        self._callbacks["status"] = callback
