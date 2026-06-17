"""OpenTelemetry tracing setup.

Tracer is always available — exports go to console by default and to OTLP HTTP
when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (e.g. http://jaeger:4318/v1/traces or
the Tempo endpoint of your choice). Set `OTEL_TRACING=0` to disable entirely.

Spans we emit (in chat.py and nim.py):
  • chat.turn          — wraps gen() per user message
  • chat.node.<name>   — plan / act / reflect
  • nim.chat           — every NIM completion (streaming or single)
  • tool.exec          — every tool call dispatched to the sidecar
  • subagent           — _run_subagent end-to-end
"""
from __future__ import annotations

import os
from contextlib import nullcontext

_TRACER = None


def init_tracing(app=None) -> None:
    """Initialise the global tracer once. Idempotent."""
    global _TRACER
    if _TRACER is not None:
        return
    if os.environ.get("OTEL_TRACING", "1") in ("0", "false", "False", ""):
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor,
        )
    except ImportError:
        # opentelemetry packages missing — silently no-op so dev installs without
        # OTel still work.
        print("[telemetry] opentelemetry not installed; tracing disabled")
        return

    resource = Resource.create({SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "atelier-backend")})
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            traces_endpoint = endpoint.rstrip("/") + "/v1/traces" if not endpoint.endswith("/v1/traces") else endpoint
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint)))
            print(f"[telemetry] exporting OTLP traces to {traces_endpoint}")
        except Exception as e:  # noqa: BLE001
            print(f"[telemetry] OTLP exporter init failed ({e}); falling back to console")
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif os.environ.get("OTEL_CONSOLE", "0") in ("1", "true", "True"):
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        print("[telemetry] exporting traces to console (OTEL_CONSOLE=1)")
    # else: no exporter; spans are created but discarded. Cheap and harmless.

    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("atelier")

    # Auto-instrument FastAPI + httpx if the user opted in.
    if os.environ.get("OTEL_AUTO_INSTRUMENT", "1") in ("1", "true", "True"):
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            if app is not None:
                FastAPIInstrumentor.instrument_app(app)
        except Exception as e:  # noqa: BLE001
            print(f"[telemetry] fastapi instrumentor unavailable: {e}")
        # httpx auto-instrumentation is opt-in (OTEL_INSTRUMENT_HTTPX=1). The
        # instrumentor patches httpx.AsyncClient.__init__ in a way that breaks
        # authlib's OAuth2Client subclass — Google OAuth login fails with
        # `TypeError: super(type, obj): obj must be an instance or subtype of type`
        # whenever authlib opens a client to fetch the OIDC metadata.
        if os.environ.get("OTEL_INSTRUMENT_HTTPX", "0") in ("1", "true", "True"):
            try:
                from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
                HTTPXClientInstrumentor().instrument()
            except Exception as e:  # noqa: BLE001
                print(f"[telemetry] httpx instrumentor unavailable: {e}")


def span(name: str, **attrs):
    """Context manager that opens a span if tracing is active, else a no-op."""
    if _TRACER is None:
        return nullcontext()
    cm = _TRACER.start_as_current_span(name)
    if attrs:
        # Wrap to set attributes after entering. Use a lightweight shim.
        return _AttrSpan(cm, attrs)
    return cm


class _AttrSpan:
    """Tiny wrapper that sets attributes on the span on enter."""

    def __init__(self, cm, attrs):
        self._cm = cm
        self._attrs = attrs

    def __enter__(self):
        sp = self._cm.__enter__()
        try:
            for k, v in self._attrs.items():
                if v is None:
                    continue
                if isinstance(v, (str, bool, int, float)):
                    sp.set_attribute(k, v)
                else:
                    sp.set_attribute(k, str(v)[:512])
        except Exception:  # noqa: BLE001 — never let telemetry kill the request
            pass
        return sp

    def __exit__(self, exc_type, exc, tb):
        return self._cm.__exit__(exc_type, exc, tb)
