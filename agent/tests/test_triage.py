from __future__ import annotations

import unittest

from agent.triage import triage


class TriageTests(unittest.TestCase):
    def test_ranks_telemetry_signals_and_fingerprints_without_incident_lookup(self) -> None:
        telemetry = {
            "service_red": {
                "ok": True,
                "data": {
                    "services": [
                        {
                            "service": "checkout",
                            "error_rate": {
                                "current": 0.35,
                                "baseline": 0.01,
                                "delta": 0.34,
                                "delta_percent": 3400,
                            },
                            "request_rate": {
                                "current": 1.0,
                                "baseline": 1.0,
                                "delta": 0,
                                "delta_percent": 0,
                            },
                            "p95_latency_ms": {
                                "current": 400,
                                "baseline": 100,
                                "delta": 300,
                                "delta_percent": 300,
                            },
                        },
                        {
                            "service": "payment",
                            "error_rate": {
                                "current": 0.0,
                                "baseline": 0.0,
                                "delta": 0.0,
                                "delta_percent": None,
                            },
                            "request_rate": {
                                "current": 1.0,
                                "baseline": 1.0,
                                "delta": 0,
                                "delta_percent": 0,
                            },
                            "p95_latency_ms": {
                                "current": 100,
                                "baseline": 100,
                                "delta": 0,
                                "delta_percent": 0,
                            },
                        },
                    ]
                },
            },
            "traces": {
                "ok": True,
                "data": {
                    "traces": [
                        {
                            "trace_id": "trace-a",
                            "deepest_error_span": {
                                "service": "checkout",
                                "operation": "PlaceOrder",
                                "error_message": "database error",
                            },
                        }
                    ]
                },
            },
        }
        result = triage("checkout requests are failing", telemetry)
        self.assertEqual(result["suspects"][0]["service"], "checkout")
        self.assertEqual(
            result["suspects"][0]["signals"],
            ["deepest_error_span", "error_rate_increased", "high_error_rate", "latency_increased"],
        )
        self.assertEqual(len(result["suspects"]), 1)
        self.assertEqual(
            result["fingerprint"],
            triage("a completely different incident description", telemetry)["fingerprint"],
        )
        self.assertNotIn("flagd", repr(result).casefold())

    def test_down_unhealthy_and_restarted_containers_rank_highest(self) -> None:
        result = triage(
            "service is failing",
            {
                "container_health": {
                    "ok": True,
                    "data": {
                        "containers": [
                            {
                                "name": "checkout",
                                "compose_project": "opentelemetry-demo",
                                "state": "exited",
                                "health": "unhealthy",
                                "restart_count": 2,
                                "oom_killed": True,
                            }
                        ]
                    },
                }
            },
        )
        suspect = result["suspects"][0]
        self.assertEqual(suspect["service"], "checkout")
        self.assertIn("container_not_running", suspect["signals"])
        self.assertIn("container_unhealthy", suspect["signals"])
        self.assertIn("restart_count_increased", suspect["signals"])
        self.assertIn("oom_killed", suspect["signals"])

    def test_ignores_unrelated_compose_projects(self) -> None:
        result = triage(
            "checkout is failing",
            {
                "container_health": {
                    "containers": [
                        {
                            "name": "checkout",
                            "compose_project": "opentelemetry-demo",
                            "state": "running",
                        },
                        {
                            "name": "developerhub_db",
                            "compose_project": "developerhub",
                            "state": "exited",
                        },
                    ]
                }
            },
        )
        self.assertEqual(result["suspects"], [])
        self.assertEqual(
            result["ignored_containers"],
            [{"service": "developerhub_db", "compose_project": "developerhub"}],
        )

    def test_resource_rules_detect_cpu_and_monotonic_memory_growth(self) -> None:
        result = triage(
            "latency increase",
            {
                "resource_metrics": {
                    "ok": True,
                    "data": {
                        "cpu_percent": [
                            {"metric": {"container_name": "checkout"}, "last": 91.0}
                        ],
                        "memory_bytes": [
                            {
                                "metric": {"container_name": "checkout"},
                                "values": [[1, 100], [2, 108], [3, 115]],
                            },
                            {
                                "metric": {"container_name": "payment"},
                                "values": [[1, 100], [2, 130], [3, 120]],
                            },
                        ],
                    },
                }
            },
        )
        self.assertEqual(result["suspects"][0]["service"], "checkout")
        self.assertIn("high_cpu", result["suspects"][0]["signals"])
        self.assertIn("memory_increasing", result["suspects"][0]["signals"])
        self.assertNotIn("payment", [suspect["service"] for suspect in result["suspects"]])

    def test_trace_network_gap_identifies_both_ends(self) -> None:
        result = triage(
            "requests are slow",
            {
                "traces": {
                    "ok": True,
                    "data": {
                        "traces": [
                            {
                                "trace_id": "network-trace",
                                "edges": [
                                    {
                                        "from": {"service": "frontend"},
                                        "to": {"service": "checkout"},
                                        "client_duration_ms": 250,
                                        "server_duration_ms": 100,
                                        "network_gap_ms": 150,
                                    }
                                ],
                            }
                        ]
                    },
                }
            },
        )
        self.assertEqual(
            {suspect["service"] for suspect in result["suspects"]},
            {"checkout", "frontend"},
        )
        self.assertTrue(
            all("network_latency_gap" in suspect["signals"] for suspect in result["suspects"])
        )

    def test_find_error_trace_output_adds_error_and_connection_signals(self) -> None:
        result = triage(
            "service is failing",
            {
                "traces": {
                    "ok": True,
                    "data": {
                        "traces": [
                            {
                                "trace_id": "failure",
                                "error_span": {
                                    "service": "product-catalog",
                                    "operation": "GetProducts",
                                    "message": "connection refused",
                                },
                            }
                        ]
                    },
                }
            },
        )
        suspect = result["suspects"][0]
        self.assertEqual(suspect["service"], "product-catalog")
        self.assertIn("error_span_observed", suspect["signals"])
        self.assertIn("connection_failure", suspect["signals"])

    def test_cache_logs_and_deploy_history_add_suspects(self) -> None:
        result = triage(
            "cart requests failing",
            {
                "logs": {
                    "cart": {
                        "ok": True,
                        "data": {
                            "service": "cart",
                            "lines": [
                                {"line": "ERROR connection refused to valkey-cart"}
                            ],
                        },
                    }
                },
                "deploy_history": {
                    "ok": True,
                    "data": {
                        "events": [
                            {
                                "kind": "restarted",
                                "container": "cart",
                                "timestamp": "2026-10-24T02:00:00Z",
                            }
                        ]
                    },
                },
            },
        )
        suspects = {suspect["service"]: suspect for suspect in result["suspects"]}
        self.assertIn("valkey-cart", suspects)
        self.assertIn("cache_connection_failure", suspects["valkey-cart"]["signals"])
        self.assertIn("recent_deployment", suspects["cart"]["signals"])

    def test_global_traffic_pattern_requires_multiple_latency_signals(self) -> None:
        services = []
        for service, current, baseline, p95_current in (
            ("frontend-proxy", 12.0, 3.0, 240.0),
            ("checkout", 8.0, 5.0, 180.0),
            ("payment", 6.0, 4.0, 150.0),
        ):
            services.append(
                {
                    "service": service,
                    "request_rate": {
                        "current": current,
                        "baseline": baseline,
                        "delta": current - baseline,
                    },
                    "error_rate": {"current": 0.0, "baseline": 0.0, "delta": 0.0},
                    "p95_latency_ms": {
                        "current": p95_current,
                        "baseline": 100.0,
                        "delta_percent": (p95_current - 100.0),
                    },
                }
            )
        result = triage("slow requests", {"service_red": {"services": services}})
        suspects = {suspect["service"]: suspect for suspect in result["suspects"]}
        self.assertIn("traffic_spike", suspects["frontend-proxy"]["signals"])
        self.assertIn("latency_increased_across_services", suspects["payment"]["signals"])

    def test_failed_sources_are_reported_and_do_not_generate_signals(self) -> None:
        result = triage(
            "anything",
            {
                "service_red": {
                    "ok": False,
                    "summary": "Prometheus unavailable",
                    "data": {},
                    "source": "Prometheus",
                },
                "container_health": {
                    "ok": True,
                    "data": {"containers": [{"name": "flagd", "state": "exited"}]},
                },
            },
        )
        self.assertEqual(result["suspects"], [])
        self.assertEqual(result["unavailable_sources"], ["service_red: Prometheus unavailable"])

    def test_invalid_input_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            triage("", {})
        with self.assertRaises(ValueError):
            triage("incident", [])


if __name__ == "__main__":
    unittest.main()
