from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import mido

from bridge.drivers.base import BaseControllerDriver

if TYPE_CHECKING:
    from bridge.events import InternalEvent


class LaunchkeyMK3Driver(BaseControllerDriver):
    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.handshake = config["handshake"]
        self.encoder_bank_cc = config.get("encoder_bank_cc")
        self.encoder_bank_channel = config.get("encoder_bank_channel", 16)
        self.encoder_banks = config.get("encoder_banks", {})
        self.pads = config["pads"]
        self.encoders = config["encoders"]
        self.transport = config["transport"]
        self.led = config["led"]
        self.current_bank = "device"

    def _send_bytes(self, port: mido.Output | None, data: list[int]) -> None:
        if port is None:
            return
        try:
            msg = mido.Message.from_bytes(bytes(data))
            port.send(msg)
        except Exception:
            pass

    def on_connect(self, daw_out_port: mido.Output | None) -> None:
        self._send_bytes(daw_out_port, self.handshake["connect"])

    def on_disconnect(self, daw_out_port: mido.Output | None) -> None:
        self._send_bytes(daw_out_port, self.handshake["disconnect"])

    def parse_daw_message(self, msg: mido.Message) -> InternalEvent | None:
        from bridge.events import (
            EncoderBankChange,
            EncoderEvent,
            PadEvent,
            TransportEvent,
        )

        encoder_ch = self.encoders["channel"] - 1
        transport_ch = self.transport["channel"] - 1
        bank_ch = self.encoder_bank_channel - 1

        if msg.is_cc():
            if (
                self.encoder_bank_cc is not None
                and msg.channel == bank_ch
                and msg.control == self.encoder_bank_cc
                and msg.value in self.encoder_banks
            ):
                bank = self.encoder_banks[msg.value]
                self.current_bank = bank
                return EncoderBankChange(bank=bank)

            if msg.channel == encoder_ch:
                for idx, cc in enumerate(self.encoders["ccs"]):
                    if msg.control == cc:
                        return EncoderEvent(
                            bank=self.current_bank,
                            encoder_index=idx,
                            value=msg.value,
                        )

            if msg.channel == transport_ch:
                transport_map = {
                    self.transport["play"]: "play",
                    self.transport["stop"]: "stop",
                    self.transport["record"]: "record",
                }
                action = transport_map.get(msg.control)
                if action:
                    return TransportEvent(action=action)

        if msg.type in ("note_on", "note_off"):
            base_top = self.pads["base_note_top"]
            base_bottom = self.pads["base_note_bottom"]
            ch = self.pads["channel"] - 1
            if msg.channel == ch:
                if base_top <= msg.note < base_top + 8:
                    pad_index = msg.note - base_top
                    pressed = msg.type == "note_on" and msg.velocity > 0
                    return PadEvent(
                        pad_index=pad_index,
                        velocity=msg.velocity if pressed else 0,
                        pressed=pressed,
                    )
                if base_bottom <= msg.note < base_bottom + 8:
                    pad_index = 8 + (msg.note - base_bottom)
                    pressed = msg.type == "note_on" and msg.velocity > 0
                    return PadEvent(
                        pad_index=pad_index,
                        velocity=msg.velocity if pressed else 0,
                        pressed=pressed,
                    )

        return None

    def update_feedback(
        self,
        daw_out_port: mido.Output | None,
        state: dict,
    ) -> None:
        if daw_out_port is None:
            return

        colors = self.led["colors"]
        active_track = state.get("active_track", 1)
        mutes = state.get("mutes", {})
        patterns = state.get("patterns", {})

        for i in range(8):
            note = self.pads["base_note_top"] + i
            track_id = i + 1
            muted = mutes.get(track_id, False)
            color = colors["dim_red"] if muted else colors["bright_green"]
            daw_out_port.send(
                mido.Message(
                    "note_on",
                    channel=self.led["static_channel"] - 1,
                    note=note,
                    velocity=color,
                )
            )

        for i in range(4):
            note = self.pads["base_note_bottom"] + i
            track_id = i + 1
            color = colors["bright_cyan"] if active_track == track_id else colors["dim_blue"]
            daw_out_port.send(
                mido.Message(
                    "note_on",
                    channel=self.led["static_channel"] - 1,
                    note=note,
                    velocity=color,
                )
            )

        for i in range(4):
            note = self.pads["base_note_bottom"] + 4 + i
            pattern_id = i + 1
            active = patterns.get(pattern_id, False)
            color = colors["amber"] if active else colors["off"]
            daw_out_port.send(
                mido.Message(
                    "note_on",
                    channel=self.led["static_channel"] - 1,
                    note=note,
                    velocity=color,
                )
            )

    async def send_led_diagnostic(self, daw_out_port: mido.Output | None) -> None:
        if daw_out_port is None:
            return
        palette = list(self.led["colors"].values())
        for color in palette:
            for i in range(16):
                note = (
                    self.pads["base_note_top"] + i
                    if i < 8
                    else self.pads["base_note_bottom"] + (i - 8)
                )
                daw_out_port.send(
                    mido.Message(
                        "note_on",
                        channel=self.led["static_channel"] - 1,
                        note=note,
                        velocity=color,
                    )
                )
            await asyncio.sleep(0.1)
        self.update_feedback(daw_out_port, {})
