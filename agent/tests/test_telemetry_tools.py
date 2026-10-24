from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from agent.backends.offline import OfflineBackend
from agent.backends.live import LiveBackend
from agent.tools.docker_tools import container_health, deploy_history
from agent.tools.jaeger import find_error_traces, get_trace, list_services
from agent.tools.logs import get_logs
from agent.tools.prom import container_resources, prom_query, service_red
from agent.tools.sanitize import redact


def prom_matrix(service: str, values: list[list[float | str]]) -> dict:
    return {
        "resultType": "matrix",
        "result": [{"metric": {"service_name": service}, "values": values}],
    }


class TelemetryToolsTests(unittest.TestCase):
    def test_redact_removes_nested_flag_fields_and_log_lines(self) -> None:
        original = {
            "spans": [
                {
                    "tags": [
                        {"key": "http.status_code", "value": 500},
                        {"key": "feature_flag.variant", "value": "secret"},
                    ],
                    "logs": ["normal error", "Feature Flag adManualGc enabled"],
                }
            ],
            "message": "OpenFeature event",
        }
        clean, removed = redact(original)
        self.assertEqual(removed, 3)
        self.assertEqual(clean["spans"][0]["tags"], [{"key": "http.status_code", "value": 500}])
        self.assertEqual(clean["spans"][0]["logs"], ["normal error"])
        self.assertNotIn("message", clean)

    def test_redact_drops_flagd_metric_series(self) -> None:
        clean, removed = redact(
            {
                "series": [
                    {"metric": {"container_name": "flagd"}, "values": [[1, "5"]]},
                    {"metric": {"container_name": "checkout"}, "values": [[1, "2"]]},
                ]
            }
        )
        self.assertEqual(removed, 1)
        self.assertEqual(len(clean["series"]), 1)
        self.assertEqual(clean["series"][0]["metric"]["container_name"], "checkout")

    def test_prom_query_summarizes_range_series(self) -> None:
        backend = OfflineBackend(
            {
                "prometheus": {
                    "range_queries": {
                        "up": prom_matrix("checkout", [[1, "2"], [2, "4"], [3, "3"]])
                    }
                }
            }
        )
        result = prom_query("up", backend=backend)
        self.assertTrue(result["ok"])
        series = result["data"]["series"][0]
        self.assertEqual((series["min"], series["max"], series["last"]), (2.0, 4.0, 3.0))

    def test_service_red_compares_recent_window_with_baseline(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        timestamps = [[now - 720, "1"], [now - 500, "1"], [now - 120, "3"], [now - 20, "3"]]
        backend = OfflineBackend(
            {
                "prometheus": {
                    "range_queries": {
                        "sum by (service_name) (rate(traces_span_metrics_calls_total[5m]))": prom_matrix(
                            "checkout", timestamps
                        ),
                        'sum by (service_name) (rate(traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"}[5m]))': prom_matrix(
                            "checkout", timestamps
                        ),
                        "histogram_quantile(0.95, sum by (service_name, le) (rate(traces_span_metrics_duration_milliseconds_bucket[5m])))": prom_matrix(
                            "checkout", timestamps
                        ),
                    }
                }
            }
        )
        result = service_red(backend=backend)
        self.assertTrue(result["ok"], result["summary"])
        self.assertEqual(result["data"]["services"][0]["service"], "checkout")
        self.assertGreater(result["data"]["services"][0]["request_rate"]["delta"], 0)

    def test_container_resources_discovers_container_metrics(self) -> None:
        backend = OfflineBackend(
            {
                "prometheus": {
                    "metric_names": [
                        "container_cpu_usage_seconds_total",
                        "container_memory_working_set_bytes",
                    ],
                    "range_queries": {
                        "rate(container_cpu_usage_seconds_total[1m]) * 100": prom_matrix(
                            "checkout", [[1, "20"], [2, "25"]]
                        ),
                        "container_memory_working_set_bytes": prom_matrix(
                            "checkout", [[1, "1024"], [2, "2048"]]
                        ),
                    },
                }
            }
        )
        result = container_resources(backend=backend)
        self.assertTrue(result["ok"], result["summary"])
        self.assertEqual(result["data"]["cpu_percent"][0]["last"], 25.0)
        self.assertEqual(result["data"]["memory_bytes"][0]["last"], 2048.0)

    def test_container_resources_supports_otel_docker_stats_metric_names(self) -> None:
        backend = OfflineBackend(
            {
                "prometheus": {
                    "metric_names": [
                        "container_cpu_usage_nanoseconds_total",
                        "container_memory_usage_total_bytes",
                    ],
                    "range_queries": {
                        "rate(container_cpu_usage_nanoseconds_total[1m]) / 10000000": prom_matrix(
                            "checkout", [[1, "2"]]
                        ),
                        "container_memory_usage_total_bytes": prom_matrix(
                            "checkout", [[1, "4096"]]
                        ),
                    },
                }
            }
        )
        result = container_resources(backend=backend)
        self.assertTrue(result["ok"], result["summary"])
        self.assertEqual(result["data"]["cpu_percent"][0]["last"], 2.0)
        self.assertEqual(result["data"]["memory_bytes"][0]["last"], 4096.0)

    def test_jaeger_tools_sanitize_and_summarize(self) -> None:
        trace = {
            "traceID": "t1",
            "processes": {
                "p1": {"tags": [{"key": "service.name", "value": "checkout"}]},
                "p2": {"tags": [{"key": "service.name", "value": "payment"}]},
            },
            "spans": [
                {
                    "spanID": "s1",
                    "operationName": "checkout",
                    "processID": "p1",
                    "startTime": 1,
                    "duration": 2000,
                    "references": [],
                    "tags": [{"key": "span.kind", "value": "client"}],
                },
                {
                    "spanID": "s2",
                    "operationName": "charge",
                    "processID": "p2",
                    "startTime": 2,
                    "duration": 1000,
                    "references": [{"refType": "CHILD_OF", "spanID": "s1"}],
                    "tags": [
                        {"key": "span.kind", "value": "server"},
                        {"key": "error", "value": True},
                        {"key": "feature_flag.secret", "value": "x"},
                    ],
                },
            ],
        }
        backend = OfflineBackend(
            {
                "jaeger": {
                    "services": ["checkout", "flagd", "payment"],
                    "traces": {"t1": trace},
                    "traces_by_service": {"checkout": [trace]},
                }
            }
        )
        services = list_services(backend)
        self.assertEqual(services["data"]["services"], ["checkout", "payment"])
        errors = find_error_traces("checkout", backend=backend)
        self.assertTrue(errors["ok"], errors["summary"])
        self.assertEqual(errors["data"]["traces"][0]["error_span"]["service"], "payment")
        detailed = get_trace("t1", backend=backend)
        self.assertTrue(detailed["ok"], detailed["summary"])
        self.assertEqual(detailed["data"]["deepest_error_spans"][0]["operation"], "charge")
        self.assertEqual(detailed["data"]["edges"][0]["network_gap_ms"], 1.0)

    def test_live_backend_normalizes_jaeger_v3_otlp_trace_and_redacts_flags(self) -> None:
        backend = LiveBackend()
        payload = {
            "result": {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {"key": "service.name", "value": {"stringValue": "checkout"}}
                            ]
                        },
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "t2",
                                        "spanId": "root",
                                        "name": "checkout",
                                        "kind": 3,
                                        "startTimeUnixNano": "1000000",
                                        "endTimeUnixNano": "3000000",
                                        "status": {"code": 0},
                                    },
                                    {
                                        "traceId": "t2",
                                        "spanId": "child",
                                        "parentSpanId": "root",
                                        "name": "charge",
                                        "kind": 2,
                                        "startTimeUnixNano": "1100000",
                                        "endTimeUnixNano": "2100000",
                                        "status": {"code": 2, "message": "payment failed"},
                                        "attributes": [
                                            {
                                                "key": "feature_flag.variant",
                                                "value": {"stringValue": "private"},
                                            }
                                        ],
                                        "events": [
                                            {
                                                "name": "feature_flag",
                                                "attributes": [
                                                    {
                                                        "key": "feature_flag.key",
                                                        "value": {"stringValue": "private"},
                                                    }
                                                ],
                                            }
                                        ],
                                    },
                                ]
                            }
                        ],
                    }
                ]
            }
        }
        with patch.object(backend, "_get_json", return_value=payload):
            result = get_trace("t2", backend=backend)
        self.assertTrue(result["ok"], result["summary"])
        self.assertEqual(result["data"]["deepest_error_span"]["service"], "checkout")
        self.assertEqual(result["data"]["deepest_error_span"]["error_message"], "payment failed")
        self.assertEqual(result["data"]["duration_ms"], 2.0)
        self.assertNotIn("feature_flag", json.dumps(result).casefold())

    def test_offline_backend_normalizes_jaeger_v3_trace_fixtures(self) -> None:
        fixture = {
            "result": {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {"key": "service.name", "value": {"stringValue": "checkout"}}
                            ]
                        },
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "spanId": "s1",
                                        "name": "checkout",
                                        "kind": 2,
                                        "startTimeUnixNano": "1000000",
                                        "endTimeUnixNano": "2000000",
                                        "status": {"code": 2, "message": "failure"},
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        }
        backend = OfflineBackend({"jaeger": {"traces": {"t3": fixture}}})
        self.assertEqual(backend.jaeger_trace("t3")["spans"][0]["spanID"], "s1")
        errors = find_error_traces("checkout", backend=backend)
        self.assertTrue(errors["ok"], errors["summary"])
        self.assertEqual(errors["data"]["traces"][0]["trace_id"], "t3")

    def test_logs_dedupe_and_deny_flagd(self) -> None:
        backend = OfflineBackend(
            {
                "docker": {
                    "containers": [{"ID": "c1", "Names": "checkout", "Image": "checkout:latest"}],
                    "logs": {
                        "c1": [
                            "2026-10-24T01:00:00Z ERROR request failed",
                            "2026-10-24T01:00:00Z ERROR request failed",
                            "Feature Flag adManualGc enabled",
                        ]
                    },
                }
            }
        )
        result = get_logs("checkout", level="error", backend=backend)
        self.assertTrue(result["ok"], result["summary"])
        self.assertEqual(result["data"]["lines"][0]["count"], 2)
        denied = get_logs("flagd", backend=backend)
        self.assertFalse(denied["ok"])

    def test_container_health_and_deploy_history(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        backend = OfflineBackend(
            {
                "docker": {
                    "containers": [{"ID": "c1", "Names": "checkout"}],
                    "inspect": {
                        "c1": {
                            "Id": "c1",
                            "Name": "/checkout",
                            "Created": now,
                            "RestartCount": 1,
                            "Config": {"Image": "checkout:v1"},
                            "State": {
                                "Status": "running",
                                "StartedAt": now,
                                "FinishedAt": "0001-01-01T00:00:00Z",
                                "OOMKilled": False,
                                "ExitCode": 0,
                                "Health": {"Status": "healthy"},
                            },
                        }
                    },
                }
            }
        )
        health = container_health(backend)
        self.assertTrue(health["ok"], health["summary"])
        self.assertEqual(health["data"]["containers"][0]["restart_count"], 1)
        history = deploy_history(backend=backend)
        self.assertTrue(history["ok"], history["summary"])
        self.assertTrue(any(event["kind"] == "restarted" for event in history["data"]["events"]))

    def test_backend_errors_are_returned_in_tool_result(self) -> None:
        result = prom_query("up", backend=OfflineBackend({}))
        self.assertFalse(result["ok"])
        self.assertEqual(set(result), {"ok", "summary", "data", "source"})


if __name__ == "__main__":
    unittest.main()
