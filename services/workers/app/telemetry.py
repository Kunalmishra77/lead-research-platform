"""OpenTelemetry tracing: exported only when OTEL_EXPORTER_OTLP_ENDPOINT is set, no-op otherwise."""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_tracing(endpoint: str | None, environment: str) -> TracerProvider | None:
    if not endpoint:
        return None
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": "leadforge-workers", "deployment.environment": environment}
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    return provider
