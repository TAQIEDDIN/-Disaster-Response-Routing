import unittest
from unittest.mock import patch

import networkx as nx

from backend import routing_engine


def make_graph():
    graph = nx.MultiDiGraph()
    graph.add_node(1, y=36.0, x=36.0)
    graph.add_node(2, y=36.0, x=36.01)
    graph.add_node(3, y=36.0, x=36.02)
    graph.add_node(4, y=36.01, x=36.01)

    graph.add_edge(1, 2, key=0, osmid=11, length=100.0, highway="residential", maxspeed="70")
    graph.add_edge(2, 3, key=0, osmid=12, length=100.0, highway="residential", maxspeed="70")
    graph.add_edge(1, 4, key=0, osmid=13, length=250.0, highway="residential", maxspeed="30", road_status="open")
    graph.add_edge(4, 3, key=0, osmid=14, length=250.0, highway="residential", maxspeed="30", road_status="open")
    return graph


def calculate(graph, mode="balanced", road_statuses=None):
    with patch.object(routing_engine, "G_REGION", graph), patch.object(
        routing_engine.ox.distance, "nearest_nodes", side_effect=[1, 3]
    ):
        return routing_engine.calculate_route(
            36.0, 36.0, 36.0, 36.02,
            36.0, 36.0, 36.0, 36.02,
            [], mode=mode, road_statuses=road_statuses,
        )


class SmartRoutingTests(unittest.TestCase):
    def test_verified_blocked_report_maps_only_to_nearby_osm_way(self):
        class GraphStub:
            graph = {"crs": "epsg:4326"}

            def get_edge_data(self, u, v, key):
                return {"osmid": [701, 702]}

        with patch.object(routing_engine.ox.distance, "nearest_edges", return_value=([(1, 2, 0)], [0.0001])):
            statuses = routing_engine.verified_blocked_road_override(GraphStub(), 36.2, 36.1)
        self.assertEqual(statuses, [
            {"osmid": "701", "status": "blocked"},
            {"osmid": "702", "status": "blocked"},
        ])

        with patch.object(routing_engine.ox.distance, "nearest_edges", return_value=([(1, 2, 0)], [0.002])):
            statuses = routing_engine.verified_blocked_road_override(GraphStub(), 36.2, 36.1)
        self.assertEqual(statuses, [])

    def test_verified_road_report_override_changes_the_selected_route(self):
        graph = make_graph()
        with patch.object(routing_engine.ox.distance, "nearest_edges", return_value=([(1, 2, 0)], [0.0001])):
            statuses = routing_engine.verified_blocked_road_override(graph, 36.0, 36.005)
        result = calculate(graph, road_statuses=statuses)
        self.assertNotIn([36.0, 36.01], result["primary_route"])
        self.assertIn([36.01, 36.01], result["primary_route"])

    def test_confirmed_blocked_osm_way_is_never_used(self):
        result = calculate(make_graph(), road_statuses=[{"osmid": 11, "status": "blocked"}])
        self.assertIn([36.01, 36.01], result["primary_route"])
        self.assertEqual(result["routing_mode"], "balanced")

    def test_no_unweighted_fallback_through_blocked_roads(self):
        statuses = [
            {"osmid": 11, "status": "blocked"},
            {"osmid": 13, "status": "blocked"},
        ]
        with self.assertRaisesRegex(RuntimeError, "kapalı yollar çıkarıldıktan sonra"):
            calculate(make_graph(), road_statuses=statuses)

    def test_fastest_and_lowest_risk_can_choose_different_routes(self):
        graph = make_graph()
        fastest = calculate(graph, mode="fastest")
        lowest_risk = calculate(graph, mode="lowest_risk")
        self.assertNotIn([36.01, 36.01], fastest["primary_route"])
        self.assertIn([36.01, 36.01], lowest_risk["primary_route"])

    def test_restricted_road_contributes_risk_but_is_not_marked_blocked(self):
        graph = make_graph()
        graph[1][2][0]["road_status"] = "restricted"
        graph[2][3][0]["road_status"] = "restricted"
        fastest = calculate(graph, mode="fastest")
        self.assertNotIn([36.01, 36.01], fastest["primary_route"])
        self.assertEqual(fastest["primary_metrics"]["risk_score"], 35.0)

    def test_metrics_include_time_risk_and_unknown_segments(self):
        result = calculate(make_graph(), mode="fastest")
        metrics = result["primary_metrics"]
        self.assertAlmostEqual(metrics["distance_m"], 200.0, delta=0.1)
        self.assertGreater(metrics["estimated_time_s"], 0)
        self.assertGreater(metrics["risk_score"], 0)
        self.assertEqual(len(metrics["unknown_segments"]), 2)

    def test_road_status_and_mode_validation(self):
        with self.assertRaisesRegex(ValueError, "Unsupported routing mode"):
            calculate(make_graph(), mode="unsafe")
        with self.assertRaisesRegex(ValueError, "Unknown road status"):
            calculate(make_graph(), road_statuses=[{"osmid": 11, "status": "maybe"}])


if __name__ == "__main__":
    unittest.main()
