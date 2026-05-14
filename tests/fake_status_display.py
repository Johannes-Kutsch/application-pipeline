from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _Call:
    method: str
    name: str
    kwargs: dict[str, object] = field(default_factory=dict)


class FakeStatusDisplay:
    """Recording fake for StatusDisplay — captures all calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[_Call] = []
        self.stopped: bool = False

    def register(
        self, name: str, *, order: int, phase: str = "starting", body: str = ""
    ) -> None:
        self.calls.append(
            _Call("register", name, {"order": order, "phase": phase, "body": body})
        )

    def update_phase(self, name: str, *, phase: str) -> None:
        self.calls.append(_Call("update_phase", name, {"phase": phase}))

    def update_body(self, name: str, *, body: str) -> None:
        self.calls.append(_Call("update_body", name, {"body": body}))

    def remove(self, name: str) -> None:
        self.calls.append(_Call("remove", name))

    def print(self, *, caller: str, message: str) -> None:
        self.calls.append(_Call("print", caller, {"message": message}))

    def stop(self) -> None:
        self.stopped = True
        self.calls.append(_Call("stop", ""))

    def registered_names(self) -> list[str]:
        return [c.name for c in self.calls if c.method == "register"]

    def body_updates_for(self, name: str) -> list[str]:
        return [
            str(c.kwargs["body"])
            for c in self.calls
            if c.method == "update_body" and c.name == name
        ]
