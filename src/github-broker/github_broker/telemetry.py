from __future__ import annotations

import logging
import os
from threading import Lock

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from github_broker.config import BrokerSettings

_CONFIGURE_LOCK = Lock()
_configured = False
_CONTENT_CAPTURE_ENV_VARS = (
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
    "ENABLE_SENSITIVE_DATA",
    "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED",
)


def configure_telemetry(settings: BrokerSettings) -> None:
    global _configured
    with _CONFIGURE_LOCK:
        for name in _CONTENT_CAPTURE_ENV_VARS:
            os.environ[name] = "false"
        if _configured:
            return
        resource = Resource.create(
            {
                "service.name": settings.application_name,
                "service.version": settings.service_version,
            }
        )
        if settings.applicationinsights_connection_string:
            configure_azure_monitor(
                connection_string=settings.applicationinsights_connection_string,
                enable_live_metrics=False,
                logger_name="llmops_github_broker",
                resource=resource,
            )
        elif trace.get_tracer_provider().__class__.__name__ == "ProxyTracerProvider":
            trace.set_tracer_provider(TracerProvider(resource=resource))
        logging.getLogger("llmops_github_broker").setLevel(logging.INFO)
        _configured = True
