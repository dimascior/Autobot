"""Mixed Project Work Graph tests."""

import unittest

import support

from autobot.project_graph import (
    DEPENDENCY_KINDS,
    WorkNode,
    Dependency,
    ProjectGraph,
)
from autobot.work_profile import VALID_PROFILES
from autobot.execution_reach import EXECUTION_REACH_POLICIES


class DependencyKindsTests(unittest.TestCase):

    def test_kinds_defined(self):
        self.assertEqual(DEPENDENCY_KINDS,
                         frozenset({"ACCEPTED_ARTIFACT", "ACCEPTED_DECISION",
                                    "REVIEW_OF", "COMPLETION"}))


class WorkNodeTests(unittest.TestCase):

    def test_node_creation(self):
        node = WorkNode("n1", "ENGINEERING",
                        execution_profile="luna-local/v1",
                        description="Build it")
        self.assertEqual(node.node_id, "n1")
        self.assertEqual(node.work_profile, "ENGINEERING")
        self.assertEqual(node.status, "PENDING")

    def test_node_to_dict(self):
        node = WorkNode("n2", "RESEARCH", role="SPECIALIST")
        d = node.to_dict()
        self.assertEqual(d["node_id"], "n2")
        self.assertEqual(d["work_profile"], "RESEARCH")
        self.assertEqual(d["role"], "SPECIALIST")


class DependencyTests(unittest.TestCase):

    def test_valid_dependency(self):
        dep = Dependency("src", "tgt", "ACCEPTED_ARTIFACT")
        self.assertEqual(dep.source, "src")
        self.assertEqual(dep.target, "tgt")
        self.assertEqual(dep.kind, "ACCEPTED_ARTIFACT")

    def test_invalid_kind_raises(self):
        with self.assertRaises(ValueError):
            Dependency("a", "b", "UNKNOWN")


class ProjectGraphBasicTests(unittest.TestCase):

    def test_empty_graph(self):
        g = ProjectGraph("obj-1")
        self.assertEqual(len(g.nodes), 0)
        self.assertEqual(g.edges, [])
        self.assertTrue(g.is_complete())

    def test_add_node(self):
        g = ProjectGraph("obj-1")
        n = g.add_node(WorkNode("n1", "ENGINEERING"))
        self.assertEqual(len(g.nodes), 1)
        self.assertEqual(n.node_id, "n1")

    def test_duplicate_node_raises(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("n1", "ENGINEERING"))
        with self.assertRaises(ValueError):
            g.add_node(WorkNode("n1", "RESEARCH"))

    def test_add_dependency(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("n1", "RESEARCH"))
        g.add_node(WorkNode("n2", "ENGINEERING"))
        dep = g.add_dependency("n1", "n2")
        self.assertEqual(dep.source, "n1")
        self.assertEqual(dep.target, "n2")
        self.assertIn("n1", g.nodes["n2"].predecessors)

    def test_dependency_unknown_node_raises(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("n1", "ENGINEERING"))
        with self.assertRaises(ValueError):
            g.add_dependency("n1", "nonexistent")


class TopologicalOrderTests(unittest.TestCase):

    def test_linear_chain(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("c", "GENERAL_OPERATION"))
        g.add_node(WorkNode("b", "ENGINEERING"))
        g.add_node(WorkNode("a", "RESEARCH"))
        g.add_dependency("a", "b")
        g.add_dependency("b", "c")
        order = g.topological_order()
        self.assertEqual(order.index("a"), 0)
        self.assertTrue(order.index("a") < order.index("b"))
        self.assertTrue(order.index("b") < order.index("c"))

    def test_diamond(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("root", "RESEARCH"))
        g.add_node(WorkNode("left", "ENGINEERING"))
        g.add_node(WorkNode("right", "ENGINEERING"))
        g.add_node(WorkNode("join", "GENERAL_OPERATION"))
        g.add_dependency("root", "left")
        g.add_dependency("root", "right")
        g.add_dependency("left", "join")
        g.add_dependency("right", "join")
        order = g.topological_order()
        self.assertTrue(order.index("root") < order.index("left"))
        self.assertTrue(order.index("root") < order.index("right"))
        self.assertTrue(order.index("left") < order.index("join"))
        self.assertTrue(order.index("right") < order.index("join"))


class ReadyNodesTests(unittest.TestCase):

    def test_initial_roots_ready(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("a", "RESEARCH"))
        g.add_node(WorkNode("b", "ENGINEERING"))
        g.add_dependency("a", "b")
        ready = g.ready_nodes()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].node_id, "a")

    def test_completing_predecessor_unlocks(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("a", "RESEARCH"))
        g.add_node(WorkNode("b", "ENGINEERING"))
        g.add_dependency("a", "b")
        g.mark_completed("a")
        ready = g.ready_nodes()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].node_id, "b")


class CompletionTests(unittest.TestCase):

    def test_incomplete_until_all_done(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("a", "RESEARCH"))
        g.add_node(WorkNode("b", "ENGINEERING"))
        g.mark_completed("a")
        self.assertFalse(g.is_complete())

    def test_complete_when_all_done(self):
        g = ProjectGraph("obj-1")
        g.add_node(WorkNode("a", "RESEARCH"))
        g.add_node(WorkNode("b", "ENGINEERING"))
        g.mark_completed("a")
        g.mark_completed("b")
        self.assertTrue(g.is_complete())


class MixedProjectTests(unittest.TestCase):
    """The canonical mixed project pattern from the work order."""

    def test_mixed_project_graph(self):
        g = ProjectGraph("obj-mixed", "Research + Engineering + Ops")
        g.add_node(WorkNode("research", "RESEARCH",
                            execution_profile="luna-research/v1",
                            role="SPECIALIST"))
        g.add_node(WorkNode("investigation", "ENGINEERING",
                            execution_profile="luna-local/v1",
                            role="SPECIALIST"))
        g.add_node(WorkNode("decision", "ENGINEERING",
                            execution_profile="opus-engineer/v1",
                            role="ENGINEER"))
        g.add_node(WorkNode("implementation", "ENGINEERING",
                            execution_profile="luna-local/v1",
                            role="SPECIALIST"))
        g.add_node(WorkNode("report", "GENERAL_OPERATION",
                            execution_profile="luna-resource/v1",
                            role="SPECIALIST"))
        g.add_node(WorkNode("review", "ENGINEERING",
                            execution_profile="luna-local/v1",
                            role="REVIEWER"))
        g.add_dependency("research", "decision", "ACCEPTED_ARTIFACT")
        g.add_dependency("investigation", "decision", "ACCEPTED_ARTIFACT")
        g.add_dependency("decision", "implementation", "ACCEPTED_DECISION")
        g.add_dependency("implementation", "review", "REVIEW_OF")
        g.add_dependency("implementation", "report", "ACCEPTED_ARTIFACT")
        g.add_dependency("review", "report", "COMPLETION")

        self.assertEqual(g.work_profiles_used(),
                         {"RESEARCH", "ENGINEERING", "GENERAL_OPERATION"})
        eps = g.execution_profiles_used()
        self.assertIn("luna-research/v1", eps)
        self.assertIn("luna-local/v1", eps)
        self.assertIn("opus-engineer/v1", eps)
        self.assertIn("luna-resource/v1", eps)
        for ep in eps:
            self.assertIn(ep, EXECUTION_REACH_POLICIES)
        order = g.topological_order()
        self.assertTrue(order.index("research") < order.index("decision"))
        self.assertTrue(order.index("decision") < order.index("implementation"))
        self.assertTrue(order.index("implementation") < order.index("review"))

    def test_mixed_project_completion_flow(self):
        g = ProjectGraph("obj-flow")
        g.add_node(WorkNode("r", "RESEARCH"))
        g.add_node(WorkNode("e", "ENGINEERING"))
        g.add_node(WorkNode("o", "GENERAL_OPERATION"))
        g.add_dependency("r", "e")
        g.add_dependency("e", "o")
        self.assertEqual(len(g.ready_nodes()), 1)
        g.mark_completed("r")
        self.assertEqual(len(g.ready_nodes()), 1)
        self.assertEqual(g.ready_nodes()[0].node_id, "e")
        g.mark_completed("e")
        self.assertEqual(len(g.ready_nodes()), 1)
        self.assertEqual(g.ready_nodes()[0].node_id, "o")
        g.mark_completed("o")
        self.assertTrue(g.is_complete())


class GraphSerializationTests(unittest.TestCase):

    def test_to_dict(self):
        g = ProjectGraph("obj-ser", "test")
        g.add_node(WorkNode("n1", "ENGINEERING"))
        d = g.to_dict()
        self.assertEqual(d["objective_id"], "obj-ser")
        self.assertIn("n1", d["nodes"])
        self.assertEqual(d["nodes"]["n1"]["work_profile"], "ENGINEERING")


if __name__ == "__main__":
    unittest.main()
