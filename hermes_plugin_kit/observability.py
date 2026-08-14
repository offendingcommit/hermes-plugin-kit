"""Privacy-safe, local structured observability for Hermes plugins."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "ObservabilityEvent",
    "credential_identity_hash",
    "log_observability_event",
    "new_correlation_id",
]

_SCHEMA = "hermes.plugin.observability.v1"
_MAX_FIELD_CHARS = 256
_MAX_ERROR_CHARS = 512
_MAX_ATTRIBUTE_CHARS = 1024
_MAX_EVENT_CHARS = 16384
_MAX_ATTRIBUTES = 24
_MAX_COLLECTION_ITEMS = 24
_MAX_DEPTH = 4
_SECRET_KEY_HINTS = (
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+\-/=]+"),
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|password|passwd|secret|token)"
        r"\s*[:=]\s*([^\s,;]+)"
    ),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z_-]{16,}\b"),
)
_FAILED_STATUSES = frozenset(
    {"denied", "error", "failed", "failure", "rejected", "timeout"}
)


def new_correlation_id() -> str:
    """Return an opaque identifier suitable for correlating plugin events."""
    return uuid.uuid4().hex


def credential_identity_hash(secret: str | bytes) -> str:
    """Return a non-secret, stable identity for comparing credential wiring.

    The secret is used only as hash input and is never retained or logged. The
    result is intentionally an identity signal, not a credential validator.
    """
    if isinstance(secret, str):
        encoded = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        encoded = secret
    else:
        raise TypeError("secret must be str or bytes")
    if not encoded:
        raise ValueError("secret must not be empty")
    digest = hashlib.sha256(b"hermes-plugin-kit:credential-identity:v1\0" + encoded)
    return f"sha256:{digest.hexdigest()[:16]}"


def _redact_text(value: Any, *, limit: int = _MAX_FIELD_CHARS) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    for pattern in _SECRET_TEXT_PATTERNS:
        if pattern.pattern.lower().startswith("(?i)\\b(bearer)"):
            text = pattern.sub(r"\1 ***", text)
        elif pattern.groups >= 2:
            text = pattern.sub(r"\1=***", text)
        else:
            text = pattern.sub("***", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_secret_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    return any(hint in normalized for hint in _SECRET_KEY_HINTS)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ATTRIBUTES:
                safe["<truncated>"] = len(value) - _MAX_ATTRIBUTES
                break
            clean_key = _redact_text(key, limit=64)
            safe[clean_key] = (
                "***" if _is_secret_key(key) else _safe_value(item, depth=depth + 1)
            )
        return safe
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        safe_items = [
            _safe_value(item, depth=depth + 1)
            for item in items[:_MAX_COLLECTION_ITEMS]
        ]
        if len(items) > _MAX_COLLECTION_ITEMS:
            safe_items.append(f"<{len(items) - _MAX_COLLECTION_ITEMS} more>")
        return safe_items
    return _redact_text(value)


def _safe_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    safe = _safe_value(attributes)
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) <= _MAX_ATTRIBUTE_CHARS:
        return safe
    return {
        "_truncated": True,
        "preview": encoded[: _MAX_ATTRIBUTE_CHARS - 48] + "…",
    }


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    """One versioned plugin lifecycle event containing only safe metadata."""

    plugin: str
    event: str
    correlation_id: str = field(default_factory=new_correlation_id)
    persona: str | None = None
    lane: str | None = None
    tool: str | None = None
    request_id: str | None = None
    fingerprint: str | None = None
    provider: str | None = None
    model: str | None = None
    credential_ref: str | None = None
    credential_hash: str | None = None
    stage: str | None = None
    status: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    elapsed_ms: float | None = None
    retry_classification: str | None = None
    artifact_outcome: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.plugin or "").strip():
            raise ValueError("plugin must be a non-empty string")
        if not str(self.event or "").strip():
            raise ValueError("event must be a non-empty string")
        if not str(self.correlation_id or "").strip():
            raise ValueError("correlation_id must be a non-empty string")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("http_status must be between 100 and 599")
        if self.elapsed_ms is not None and self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must not be negative")
        if not isinstance(self.attributes, Mapping):
            raise TypeError("attributes must be a mapping")

    def as_dict(self) -> dict[str, Any]:
        """Return the stable v1 JSON shape after redaction and bounding."""
        values: tuple[tuple[str, Any], ...] = (
            ("schema", _SCHEMA),
            ("event", self.event),
            ("correlation_id", self.correlation_id),
            ("plugin", self.plugin),
            ("persona", self.persona),
            ("lane", self.lane),
            ("tool", self.tool),
            ("request_id", self.request_id),
            ("fingerprint", self.fingerprint),
            ("provider", self.provider),
            ("model", self.model),
            ("credential_ref", self.credential_ref),
            ("credential_hash", self.credential_hash),
            ("stage", self.stage),
            ("status", self.status),
            ("http_status", self.http_status),
            ("error_code", self.error_code),
            ("error_message", self.error_message),
            ("elapsed_ms", self.elapsed_ms),
            ("retry_classification", self.retry_classification),
            ("artifact_outcome", self.artifact_outcome),
        )
        result: dict[str, Any] = {}
        for key, value in values:
            if value is None:
                continue
            if isinstance(value, str):
                limit = _MAX_ERROR_CHARS if key == "error_message" else _MAX_FIELD_CHARS
                result[key] = _redact_text(value, limit=limit)
            elif key == "elapsed_ms":
                result[key] = round(float(value), 2)
            else:
                result[key] = value
        if self.attributes:
            result["attributes"] = _safe_attributes(self.attributes)
        return result


def log_observability_event(
    logger: logging.Logger,
    event: ObservabilityEvent,
    *,
    level: int | None = None,
) -> dict[str, Any]:
    """Emit one compact local JSON receipt and return its serialized mapping."""
    if not isinstance(logger, logging.Logger):
        raise TypeError("logger must be a logging.Logger")
    if not isinstance(event, ObservabilityEvent):
        raise TypeError("event must be an ObservabilityEvent")
    payload = event.as_dict()
    if level is None:
        level = (
            logging.WARNING
            if str(payload.get("status", "")).lower() in _FAILED_STATUSES
            else logging.INFO
        )
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), default=str
    )
    if len(encoded) > _MAX_EVENT_CHARS:
        payload = {
            key: payload[key]
            for key in ("schema", "event", "correlation_id", "plugin", "status")
            if key in payload
        }
        payload["truncated"] = True
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    logger.log(level, "hermes_plugin_observability %s", encoded)
    return payload
