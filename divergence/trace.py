# divergence/trace.py
"""OTel GenAI tracing (semconv v1.44.0), exported as OTLP-JSON to a local file.
No collector, no network. One file per agent run."""

import json, os
from contextlib import contextmanager

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor
from opentelemetry.sdk.resources import Resource

RUNS_DIR = os.environ.get("RUNS_DIR", "runs")
SEMCONV_VERSION = "1.44.0"


# ---------- OTLP-JSON encoding ----------

def _attr_value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}          # int64 is a string in OTLP-JSON
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_attr_value(x) for x in v]}}
    return {"stringValue": str(v)}


def _attrs(d):
    return [{"key": k, "value": _attr_value(v)} for k, v in (d or {}).items()]


def _encode_span(s: ReadableSpan):
    ctx = s.get_span_context()
    out = {
        "traceId": format(ctx.trace_id, "032x"),
        "spanId": format(ctx.span_id, "016x"),
        "name": s.name,
        "kind": int(s.kind.value),
        "startTimeUnixNano": str(s.start_time),
        "endTimeUnixNano": str(s.end_time),
        "attributes": _attrs(dict(s.attributes or {})),
        "events": [
            {"timeUnixNano": str(e.timestamp),
             "name": e.name,
             "attributes": _attrs(dict(e.attributes or {}))}
            for e in s.events
        ],
        "status": {"code": int(s.status.status_code.value)},
    }
    if s.status.description:
        out["status"]["message"] = s.status.description
    if s.parent is not None:
        out["parentSpanId"] = format(s.parent.span_id, "016x")
    return out


class FileExporter(SpanExporter):
    """Collects spans in memory, writes one OTLP-JSON doc per run on flush."""

    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def flush_to(self, path, resource_attrs):
        doc = {"resourceSpans": [{
            "resource": {"attributes": _attrs(resource_attrs)},
            "scopeSpans": [{
                "scope": {"name": "crucible.divergence", "version": "0.1.0"},
                "schemaUrl": f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}",
                "spans": [_encode_span(s) for s in self.spans],
            }],
        }]}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(doc, f, indent=2)
        self.spans = []
        return path

    def shutdown(self):
        pass


# ---------- message schema (v1.44 gen_ai.input/output.messages) ----------

def _to_spec_messages(messages):
    """OpenAI-shaped messages -> the spec's ChatMessage schema.
    Roles: system | user | assistant | tool. Parts: text | tool_call | tool_call_response."""
    out = []
    for m in messages:
        role, parts = m.get("role"), []
        if role == "tool":
            parts.append({
                "type": "tool_call_response",
                "id": m.get("tool_call_id", ""),
                "response": m.get("content", ""),
            })
        else:
            if m.get("content"):
                parts.append({"type": "text", "content": m["content"]})
            for tc in (m.get("tool_calls") or []):
                args = tc.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                parts.append({
                    "type": "tool_call",
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "arguments": args or {},
                })
        out.append({"role": role, "parts": parts})
    return out


# ---------- run scope ----------

_exporter = None
_tracer = None


def _init():
    global _exporter, _tracer
    if _tracer is None:
        _exporter = FileExporter()
        provider = TracerProvider(resource=Resource.create({"service.name": "divergence"}))
        provider.add_span_processor(SimpleSpanProcessor(_exporter))
        otel_trace.set_tracer_provider(provider)
        _tracer = otel_trace.get_tracer("crucible.divergence")
    return _tracer


@contextmanager
def run(task_id, run_index, model, tool_schemas=None, agent_name="patent-research"):
    """One agent run = one trace = one file. Span kind INTERNAL (same process)."""
    tracer = _init()
    run_id = f"{task_id}__{model.replace('/', '-')}__{run_index:03d}"

    attrs = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.provider.name": "groq",
        "gen_ai.agent.name": agent_name,
        "gen_ai.request.model": model,
        "divergence.task_id": task_id,
        "divergence.run_index": run_index,
        "divergence.run_id": run_id,
    }
    if tool_schemas:
        # what the agent COULD have called - lets the viewer spot "never used an available tool"
        attrs["gen_ai.tool.definitions"] = json.dumps(tool_schemas)

    # with tracer.start_as_current_span(f"invoke_agent {agent_name}", attributes=attrs) as span:
    with tracer.start_as_current_span(f"invoke_agent {agent_name}", attributes=attrs,end_on_exit=False) as span:
        try:
            yield span
        finally:
            path = os.path.join(RUNS_DIR, task_id, f"{run_id}.json")
            span.end()
            _exporter.flush_to(path, {
                "service.name": "divergence",
                "divergence.run_id": run_id,
            })
            print(f"  trace -> {path}")


@contextmanager
def chat(model, messages, temperature=None):
    tracer = _init()
    attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "groq",
        "gen_ai.request.model": model,
        "gen_ai.input.messages": json.dumps(_to_spec_messages(messages)),
    }
    if temperature is not None:
        attrs["gen_ai.request.temperature"] = float(temperature)
    with tracer.start_as_current_span(f"chat {model}", attributes=attrs) as span:
        yield span

def record_chat_result(span, result):
    span.set_attribute("gen_ai.response.model", result["model"])
    span.set_attribute("gen_ai.response.finish_reasons", [result["finish_reason"]])
    span.set_attribute("gen_ai.usage.input_tokens", result["usage"]["input_tokens"])
    span.set_attribute("gen_ai.usage.output_tokens", result["usage"]["output_tokens"])

    msg = {"role": "assistant", "parts": [], "finish_reason": result["finish_reason"]}
    if result["content"]:
        msg["parts"].append({"type": "text", "content": result["content"]})
    for tc in result["tool_calls"]:
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {"_raw": tc["arguments"]}
        msg["parts"].append({"type": "tool_call", "id": tc["id"],
                             "name": tc["name"], "arguments": args})
    span.set_attribute("gen_ai.output.messages", json.dumps([msg]))


@contextmanager
def execute_tool(name, call_id, arguments):
    tracer = _init()
    with tracer.start_as_current_span(
        f"execute_tool {name}",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "gen_ai.tool.call.id": call_id or "",
            "gen_ai.tool.call.arguments": json.dumps(arguments),
        },
    ) as span:
        yield span


def record_tool_result(span, result):
    n = len(result) if isinstance(result, list) else 1
    empty = (isinstance(result, list) and n == 0) or (
        isinstance(result, dict) and result.get("error") is not None)
    span.set_attribute("gen_ai.tool.call.result", json.dumps(result)[:4000])
    # ours, not theirs - the silent-failure detector
    span.set_attribute("divergence.result_count", n)
    span.set_attribute("divergence.result_empty", bool(empty))