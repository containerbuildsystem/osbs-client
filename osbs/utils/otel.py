"""
Copyright (c) 2023 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import logging
import os
from typing import Optional

from opentelemetry import trace
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.trace import format_trace_id, format_span_id
from otel_extensions import TelemetryOptions, init_telemetry_provider

from osbs.constants import OTEL_SERVICE_NAME

logger = logging.getLogger(__name__)


def init_otel(otel_url: Optional[str], traceparent: Optional[str]):
    logger.info("Initializing otel with traceparent %s", traceparent)
    span_exporter = ''
    otel_protocol = 'http/protobuf'
    if not otel_url:
        otel_protocol = 'custom'
        span_exporter = '"opentelemetry.sdk.trace.export.ConsoleSpanExporter"'

    if traceparent:
        os.environ['TRACEPARENT'] = traceparent
    otel_options = TelemetryOptions(
        OTEL_SERVICE_NAME=OTEL_SERVICE_NAME,
        OTEL_EXPORTER_CUSTOM_SPAN_EXPORTER_TYPE=span_exporter,
        OTEL_EXPORTER_OTLP_ENDPOINT=otel_url,
        OTEL_EXPORTER_OTLP_PROTOCOL=otel_protocol,
    )
    init_telemetry_provider(otel_options)
    if 'TRACEPARENT' in os.environ:
        del os.environ['TRACEPARENT']
    RequestsInstrumentor().instrument()
    logger.info("Initialization complete")


def get_current_traceparent():
    tracecontext = trace.get_current_span().get_span_context()
    traceparent = (f'00-{format_trace_id(tracecontext.trace_id)}-'
                   f'{format_span_id(tracecontext.span_id)}-01')
    logger.info("current traceparent is %s", traceparent)
    none_traceparent = '00-00000000000000000000000000000000-0000000000000000-01'
    if traceparent == none_traceparent:
        return None
    return traceparent
