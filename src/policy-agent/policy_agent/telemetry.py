from __future__ import annotations

import logging
import os
from threading import Lock

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from policy_agent.config import Settings

_LOGGER_NAME = "llmops_demo"
_AGENT_FRAMEWORK_LOGGER_NAME = "agent_framework"
_CONTENT_CAPTURE_ENV_VARS = (
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
    "ENABLE_SENSITIVE_DATA",
    "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED",
)
_CONFIGURE_LOCK = Lock()
_configured = False


def configure_content_logging(settings: Settings) -> None:
    capture_value = "true" if settings.capture_content else "false"
    for name in _CONTENT_CAPTURE_ENV_VARS:
        os.environ[name] = capture_value
    level = logging.INFO if settings.capture_content else logging.WARNING
    logging.getLogger(_AGENT_FRAMEWORK_LOGGER_NAME).setLevel(level)


def configure_telemetry(settings: Settings) -> None:
    global _configured
    with _CONFIGURE_LOCK:
        if _configured:
            configure_content_logging(settings)
            return

        if settings.applicationinsights_connection_string:
            configure_azure_monitor(
                connection_string=settings.applicationinsights_connection_string,
                enable_live_metrics=False,
                logger_name=_LOGGER_NAME,
                resource=Resource.create(
                    {
                        "service.name": settings.application_name,
                        "service.version": settings.service_version,
                    }
                ),
            )
        elif trace.get_tracer_provider().__class__.__name__ == "ProxyTracerProvider":
            trace.set_tracer_provider(
                TracerProvider(
                    resource=Resource.create(
                        {
                            "service.name": settings.application_name,
                            "service.version": settings.service_version,
                        }
                    )
                )
            )

        logging.getLogger(_LOGGER_NAME).setLevel(logging.INFO)
        configure_content_logging(settings)
        _configured = True


def get_tracer():
    return trace.get_tracer("llmops_demo.policy_agent")


def current_trace_id() -> str:
    span_context = trace.get_current_span().get_span_context()
    return f"{span_context.trace_id:032x}"
