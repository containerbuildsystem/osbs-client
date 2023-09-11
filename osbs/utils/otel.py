"""
Copyright (c) 2023 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from opentelemetry import trace
from opentelemetry.trace import format_trace_id, format_span_id


def get_current_traceparent():
    tracecontext = trace.get_current_span().get_span_context()
    traceparent = (f'00-{format_trace_id(tracecontext.trace_id)}-'
                   f'{format_span_id(tracecontext.span_id)}-01')
    return traceparent
