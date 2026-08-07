from __future__ import annotations

import urllib.parse
from typing import final

from harness.session.opencode_contract import (
    CapabilityState,
    ChildrenResult,
    Completeness,
    Compatibility,
    JsonValue,
    parse_trace_contract,
)
from .opencode_trace_bundle import (
    UNSUPPORTED_STATUSES,
    bundle_text,
    bundle_with_raw_evidence,
    capture_errors,
    identity_errors,
)
from .opencode_trace_models import (
    DocumentRetrieval,
    EndpointCapture,
    HealthRetrieval,
    SessionGraphRetrieval,
    TraceCapabilityState,
    TraceHttp,
)

__all__ = [
    "DocumentRetrieval",
    "EndpointCapture",
    "HealthRetrieval",
    "OpenCodeTraceClient",
    "SessionGraphRetrieval",
    "TraceCapabilityState",
    "TraceHttp",
]


@final
class OpenCodeTraceClient:
    def __init__(self, http: TraceHttp) -> None:
        self._http = http

    def get_health(self) -> HealthRetrieval:
        capture = self._get("/global/health")
        body = capture.body if isinstance(capture.body, dict) else None
        version_value = body.get("version") if body is not None else None
        server_version = version_value if isinstance(version_value, str) else ""
        healthy = body is not None and body.get("healthy") is True
        if capture.status == 200:
            capability = (
                CapabilityState.SUPPORTED
                if healthy and server_version
                else CapabilityState.UNKNOWN
            )
        else:
            capability = capture.capability
        return HealthRetrieval(capability, server_version, healthy, capture)

    def get_document(self) -> DocumentRetrieval:
        capture = self._get("/doc")
        body = capture.body if isinstance(capture.body, dict) else None
        paths = body.get("paths") if body is not None else None
        if capture.status == 200:
            capability = (
                CapabilityState.SUPPORTED
                if isinstance(paths, dict)
                else CapabilityState.UNKNOWN
            )
        else:
            capability = capture.capability
        return DocumentRetrieval(capability, capture)

    def get_full_messages(self, session_id: str) -> EndpointCapture:
        encoded = urllib.parse.quote(session_id, safe="")
        return self._get(f"/session/{encoded}/message")

    def get_session_info(self, session_id: str) -> EndpointCapture:
        encoded = urllib.parse.quote(session_id, safe="")
        return self._get(f"/session/{encoded}")

    def get_immediate_children(self, session_id: str) -> EndpointCapture:
        encoded = urllib.parse.quote(session_id, safe="")
        return self._get(f"/session/{encoded}/children")

    def list_sessions(self) -> EndpointCapture:
        return self._get("/session")

    def retrieve_session_graph(self, session_id: str) -> SessionGraphRetrieval:
        health = self.get_health()
        document = self.get_document()
        messages = self.get_full_messages(session_id)
        children = self.get_immediate_children(session_id)
        captures = {
            "health": health.capture,
            "doc": document.capture,
            "messages": messages,
            "children": children,
        }
        contract = parse_trace_contract(bundle_text(captures))
        errors = capture_errors(captures)
        fallback = None
        fallback_capture = None
        if children.status in UNSUPPORTED_STATUSES:
            fallback, fallback_capture, fallback_errors = self._fallback_children(
                session_id,
                captures,
            )
            errors.extend(fallback_errors)
        errors.extend(identity_errors(session_id, contract))
        if (
            contract.compatibility is Compatibility.INCOMPATIBLE
            and not errors
        ):
            errors.append("malformed_contract")
        state = self._state(contract.compatibility, contract.completeness, errors)
        return SessionGraphRetrieval(
            state,
            contract,
            fallback,
            fallback_capture,
            tuple(errors),
        )

    def _get(self, path: str) -> EndpointCapture:
        raw = self._http("GET", path)
        status_value = raw.get("status")
        status = (
            status_value
            if isinstance(status_value, int) and not isinstance(status_value, bool)
            else None
        )
        body: JsonValue = raw.get("data")
        if "data" not in raw:
            body = raw.get("details")
        headers_value = raw.get("headers")
        headers = headers_value if isinstance(headers_value, dict) else {}
        raw_body_value = raw.get("raw_body")
        raw_body = raw_body_value if isinstance(raw_body_value, str) else None
        capability = self._endpoint_capability(status)
        error_value = raw.get("error") or raw.get("details")
        error = error_value if isinstance(error_value, str) else None
        return EndpointCapture(
            capability,
            status,
            body,
            headers,
            raw_body,
            error,
            raw,
        )

    @staticmethod
    def _endpoint_capability(status: int | None) -> CapabilityState:
        if status == 200:
            return CapabilityState.SUPPORTED
        if status in UNSUPPORTED_STATUSES:
            return CapabilityState.UNSUPPORTED
        return CapabilityState.UNKNOWN

    def _fallback_children(
        self,
        session_id: str,
        captures: dict[str, EndpointCapture],
    ) -> tuple[ChildrenResult | None, EndpointCapture, list[str]]:
        listing = self.list_sessions()
        if listing.status in UNSUPPORTED_STATUSES:
            return None, listing, []
        if listing.status != 200 or not isinstance(listing.body, list):
            suffix = (
                f"http_{listing.status}"
                if listing.status is not None
                else "transport_error"
            )
            return None, listing, [f"session_list:{suffix}"]
        if any(key.casefold() == "x-next-cursor" for key in listing.headers):
            return None, listing, []
        if listing.raw_body:
            raw_evidence_contract = parse_trace_contract(
                bundle_with_raw_evidence(
                    captures,
                    "sessionList",
                    listing,
                )
            )
            if raw_evidence_contract.compatibility is Compatibility.INCOMPATIBLE:
                return None, listing, ["session_list:malformed_capture"]
        filtered: list[JsonValue] = [
            item
            for item in listing.body
            if isinstance(item, dict) and item.get("parentID") == session_id
        ]
        fallback_captures = dict(captures)
        fallback_captures["children"] = EndpointCapture(
            CapabilityState.SUPPORTED,
            200,
            filtered,
            {},
            None,
            None,
            listing.raw,
        )
        fallback_contract = parse_trace_contract(bundle_text(fallback_captures))
        if fallback_contract.children.completeness is Completeness.INCOMPATIBLE:
            return None, listing, ["session_list:malformed_children"]
        return fallback_contract.children, listing, []

    @staticmethod
    def _state(
        compatibility: Compatibility,
        completeness: Completeness,
        errors: list[str],
    ) -> TraceCapabilityState:
        if errors:
            return TraceCapabilityState.ERROR
        if compatibility is Compatibility.INCOMPATIBLE:
            return TraceCapabilityState.ERROR
        if completeness is Completeness.COMPLETE:
            return TraceCapabilityState.COMPATIBLE
        return TraceCapabilityState.PARTIAL
