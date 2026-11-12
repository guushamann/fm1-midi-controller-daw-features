from __future__ import annotations

from typing import TYPE_CHECKING

import mido

if TYPE_CHECKING:
    from bridge.events import InternalEvent


class BaseControllerDriver:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.name = config.get("name", "Generic Controller")

    def on_connect(self, daw_out_port: mido.Output | None) -> None:
        pass

    def on_disconnect(self, daw_out_port: mido.Output | None) -> None:
        pass

    def parse_daw_message(self, msg: mido.Message) -> InternalEvent | None:
        return None

    def update_feedback(
        self,
        daw_out_port: mido.Output | None,
        state: dict,
    ) -> None:
        pass

    def send_led_diagnostic(self, daw_out_port: mido.Output | None) -> None:
        pass
