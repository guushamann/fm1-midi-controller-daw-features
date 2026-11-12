from __future__ import annotations

import copy
import threading
import time
from typing import TYPE_CHECKING

import mido

if TYPE_CHECKING:
    from bridge.drivers.base import BaseControllerDriver
    from bridge.events import InternalEvent


class EngineState:
    def __init__(self) -> None:
        self.active_track = 1
        self.mutes: dict[int, bool] = {}
        self.patterns: dict[int, bool] = {}
        self.encoder_bank = "device"
        self.playing = False
        self.recording_armed = False

    def snapshot(self) -> dict:
        return {
            "active_track": self.active_track,
            "mutes": copy.copy(self.mutes),
            "patterns": copy.copy(self.patterns),
            "playing": self.playing,
            "recording_armed": self.recording_armed,
        }


class MidiEngine:
    def __init__(
        self,
        synth_config: dict,
        driver: BaseControllerDriver,
        monitor_queue,
    ) -> None:
        self.config = synth_config
        self.driver = driver
        self.monitor_queue = monitor_queue
        self.state = EngineState()
        self._output_port: mido.Output | None = None
        self._daw_out_port: mido.Output | None = None
        self._clock_thread: threading.Thread | None = None
        self._clock_running = threading.Event()
        self._clock_bpm = self.config.get("transport", {}).get("clock_bpm", 120)

    def set_ports(
        self,
        synth_out: mido.Output | None,
        daw_out: mido.Output | None,
    ) -> None:
        self._output_port = synth_out
        self._daw_out_port = daw_out

    def _to_synth(self, msg: mido.Message) -> None:
        if self._output_port is not None:
            try:
                self._output_port.send(msg)
            except Exception:
                pass
        if self.monitor_queue is not None and msg.type != "clock":
            try:
                self.monitor_queue.put_nowait(
                    {"direction": "OUT", "port": "FM-1", "bytes": msg.bytes()}
                )
            except Exception:
                pass

    def _to_synth_sysex(self, data: list[int]) -> None:
        sysex_config = self.config.get("sysex")
        if sysex_config is None:
            return
        manufacturer = sysex_config.get("manufacturer", [0x7D, 0x46, 0x4C])
        msg = mido.Message("sysex", data=manufacturer + data)
        self._to_synth(msg)

    @staticmethod
    def _ed_v(value: int) -> list[int]:
        # Melodee editor protocol: 14-bit values are offset by 8192.
        u = max(0, min(16383, value + 8192))
        return [u & 0x7F, (u >> 7) & 0x7F]

    def _start_clock(self) -> None:
        if self._clock_thread is not None and self._clock_thread.is_alive():
            return
        self._clock_running.set()
        self._clock_thread = threading.Thread(target=self._clock_loop, daemon=True)
        self._clock_thread.start()

    def _stop_clock(self) -> None:
        self._clock_running.clear()
        if self._clock_thread is not None:
            self._clock_thread.join(timeout=0.1)
            self._clock_thread = None

    def _clock_loop(self) -> None:
        if self._clock_bpm <= 0:
            return
        interval = 60.0 / (self._clock_bpm * 24)
        next_time = time.perf_counter()
        while self._clock_running.is_set():
            self._to_synth(mido.Message("clock"))
            next_time += interval
            sleep_time = next_time - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _update_feedback(self) -> None:
        self.driver.update_feedback(self._daw_out_port, self.state.snapshot())

    def handle_event(self, event: InternalEvent) -> None:
        from bridge.events import (
            EncoderBankChange,
            EncoderEvent,
            NoteEvent,
            PadEvent,
            TransportEvent,
        )

        if isinstance(event, NoteEvent):
            msg_type = "note_on" if event.is_on else "note_off"
            self._to_synth(
                mido.Message(
                    msg_type,
                    channel=self.state.active_track - 1,
                    note=event.note,
                    velocity=event.velocity,
                )
            )
            return

        if isinstance(event, PadEvent):
            self._handle_pad(event)
            self._update_feedback()
            return

        if isinstance(event, EncoderEvent):
            self._handle_encoder(event)
            return

        if isinstance(event, TransportEvent):
            self._handle_transport(event)
            return

        if isinstance(event, EncoderBankChange):
            self.state.encoder_bank = event.bank
            return

    def _handle_pad(self, event: PadEvent) -> None:
        if not event.pressed:
            return

        tracks = self.config.get("tracks", [])
        sysex = self.config.get("sysex")
        global_channel = self.config.get("global_channel", 16) - 1

        if 0 <= event.pad_index < 8:
            track_id = event.pad_index + 1
            current = self.state.mutes.get(track_id, False)
            self.state.mutes[track_id] = not current
            muted = self.state.mutes[track_id]

            if sysex:
                mute_param = sysex.get("params", {}).get("mute", 40)
                self._to_synth_sysex(
                    [sysex.get("track_param_cmd", 31), track_id - 1, mute_param]
                    + self._ed_v(1 if muted else 0)
                )
            else:
                track = next((t for t in tracks if t["id"] == track_id), None)
                if track:
                    self._to_synth(
                        mido.Message(
                            "control_change",
                            channel=track["channel"] - 1,
                            control=track["mute_cc"],
                            value=127 if muted else 0,
                        )
                    )

        elif 8 <= event.pad_index < 12:
            track_id = event.pad_index - 8 + 1
            if track_id <= len(tracks):
                self.state.active_track = track_id
                if sysex:
                    self._to_synth_sysex(
                        [sysex.get("track_select_cmd", 27), track_id - 1]
                    )

        elif 12 <= event.pad_index < 16:
            pattern_id = event.pad_index - 12 + 1
            for pid in self.state.patterns:
                self.state.patterns[pid] = False
            self.state.patterns[pattern_id] = True

            if sysex:
                active_track = self.state.active_track - 1
                if 0 <= active_track < 4:
                    self._to_synth_sysex(
                        [
                            sysex.get("pattern_cmd", 73),
                            active_track,
                            sysex.get("pattern_op", 1),
                            pattern_id - 1,
                        ]
                    )
            else:
                self._to_synth(
                    mido.Message(
                        "program_change",
                        channel=global_channel,
                        program=pattern_id - 1,
                    )
                )

    def _handle_encoder(self, event: EncoderEvent) -> None:
        tracks = self.config.get("tracks", [])
        external = self.config.get("external_tracks", [])
        all_tracks = tracks + external
        fx = self.config.get("fx", {})

        if event.bank == "device":
            macros = self.config.get("macros", [])
            if event.encoder_index < len(macros):
                macro = macros[event.encoder_index]
                if self.state.active_track <= len(tracks):
                    track = tracks[self.state.active_track - 1]
                    self._to_synth(
                        mido.Message(
                            "control_change",
                            channel=track["channel"] - 1,
                            control=macro["cc"],
                            value=event.value,
                        )
                    )

        elif event.bank == "mixer":
            if event.encoder_index < len(all_tracks):
                track = all_tracks[event.encoder_index]
                self._to_synth(
                    mido.Message(
                        "control_change",
                        channel=track["channel"] - 1,
                        control=track.get("volume_cc", 7),
                        value=event.value,
                    )
                )

        elif event.bank == "sends":
            reverb_cc = fx.get("reverb_send_cc", 91)
            delay_cc = fx.get("delay_send_cc", 93)
            if event.encoder_index < len(all_tracks):
                track = all_tracks[event.encoder_index]
                cc = reverb_cc if event.encoder_index % 2 == 0 else delay_cc
                self._to_synth(
                    mido.Message(
                        "control_change",
                        channel=track["channel"] - 1,
                        control=cc,
                        value=event.value,
                    )
                )
            else:
                master = fx.get("master_fx_ccs", [])
                idx = event.encoder_index - len(all_tracks)
                if 0 <= idx < len(master):
                    self._to_synth(
                        mido.Message(
                            "control_change",
                            channel=self.config.get("global_channel", 16) - 1,
                            control=master[idx]["cc"],
                            value=event.value,
                        )
                    )

    def _handle_transport(self, event: TransportEvent) -> None:
        transport = self.config.get("transport", {})
        global_channel = self.config.get("global_channel", 16) - 1

        if event.action == "play":
            if self.state.playing:
                self._stop_clock()
                if transport.get("stop") == "realtime_stop":
                    self._to_synth(mido.Message("stop"))
                self.state.playing = False
            else:
                if transport.get("start") == "realtime_start":
                    self._to_synth(mido.Message("start"))
                self._start_clock()
                self.state.playing = True
        elif event.action == "stop" and transport.get("stop") == "realtime_stop":
            self._stop_clock()
            self._to_synth(mido.Message("stop"))
            self.state.playing = False
        elif event.action == "record":
            self.state.recording_armed = not self.state.recording_armed
            cc = transport.get("record_arm_cc", 64)
            self._to_synth(
                mido.Message(
                    "control_change",
                    channel=global_channel,
                    control=cc,
                    value=127 if self.state.recording_armed else 0,
                )
            )

    def panic(self) -> None:
        for ch in range(16):
            self._to_synth(
                mido.Message(
                    "control_change",
                    channel=ch,
                    control=123,
                    value=0,
                )
            )
            self._to_synth(
                mido.Message(
                    "control_change",
                    channel=ch,
                    control=64,
                    value=0,
                )
            )
