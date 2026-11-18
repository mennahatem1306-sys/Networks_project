"""Flat application objects shared with the transport and HTTP adapters."""

from dataclasses import dataclass, field
import json

from .config import integer


@dataclass(frozen=True)
class Outcome:
    success: bool
    status: str
    error_code: str | None = None
    reason: str | None = None
    data: dict = field(default_factory=dict)
    outbound: tuple[dict, ...] = ()

    def as_dict(self):
        return {
            "success": self.success, "status": self.status, "error_code": self.error_code,
            "reason": self.reason, "data": self.data, "outbound": list(self.outbound),
        }


def ok(status, *, outbound=(), **data):
    return Outcome(True, status, data=data, outbound=tuple(outbound))


def reject(code, reason):
    return Outcome(False, "REJECTED", code, reason)


def flat_message(value):
    return (isinstance(value, dict) and all(isinstance(key, str) and type(item) in (str, int, bool)
                                            for key, item in value.items())
            and type(value.get("schema_version")) is int and value["schema_version"] == 1
            and isinstance(value.get("msg_type"), str))


def command(kind, cmd_id, session_id, **fields):
    return dict(schema_version=1, msg_type=kind, device_id=0, session_id=session_id,
                cmd_id=cmd_id, **fields)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def matching_ids(message, source, fields):
    return all(integer(message.get(key)) and message[key] == source[key] for key in fields)
