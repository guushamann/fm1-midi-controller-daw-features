from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NoteEvent:
    channel: int
    note: int
    velocity: int
    is_on: bool


@dataclass(frozen=True)
class PadEvent:
    pad_index: int
    velocity: int
    pressed: bool


@dataclass(frozen=True)
class EncoderEvent:
    bank: str
    encoder_index: int
    value: int


@dataclass(frozen=True)
class TransportEvent:
    action: Literal["play", "stop", "record"]


@dataclass(frozen=True)
class EncoderBankChange:
    bank: str


InternalEvent = NoteEvent | PadEvent | EncoderEvent | TransportEvent | EncoderBankChange
