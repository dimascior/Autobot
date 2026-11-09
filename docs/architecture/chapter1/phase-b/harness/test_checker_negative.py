"""Planted defects against check_chapter1 itself: each mutation of a copy of
the workspace must make the named check fail (the established negative-test
pattern from Chapter 0 and Phase A)."""

import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import check_chapter1  # noqa: E402


def _find_repo():
    for parent in [ROOT] + list(ROOT.parents):
        if (parent / "docs" / "architecture" / "chapter0" / "registries" / "invariants.json").exists():
            return parent
    env = Path("C:/Users/dimas/swarm-coordinator")
    if (env / "docs" / "architecture" / "chapter0" / "registries" / "invariants.json").exists():
        return env
    raise RuntimeError("repository root not found for checker negatives")


REPO = _find_repo()


def _copy_workspace(tmp):
    dst = Path(tmp) / "ch1"
    for sub in ("registries", "schemas", "handoff"):
        src = ROOT / sub
        if src.exists():
            shutil.copytree(src, dst / sub)
    (dst / "vectors" / "golden").mkdir(parents=True)
    shutil.copy2(ROOT / "vectors" / "golden" / "golden_digests.json",
                 dst / "vectors" / "golden" / "golden_digests.json")
    for doc in ("CHAPTER1_SPECIFICATION.md", "DECISION_PACKAGE.md"):
        shutil.copy2(ROOT / doc, dst / doc)
    return dst


def _edit_json(path, fn):
    payload = json.loads(path.read_text(encoding="utf-8"))
    fn(payload)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


# (name, mutate(dst), check that must fail)
def _defects():
    cases = []

    cases.append(("drop_decision", lambda d: _edit_json(
        d / "registries" / "decisions.json",
        lambda p: p["decisions"].pop(26)), "decisions.count_and_ids"))

    cases.append(("swap_candidate_order", lambda d: _edit_json(
        d / "registries" / "decisions.json",
        lambda p: (p["decisions"].__setitem__(0, dict(p["decisions"][0], candidate="dc.multi-kind")),
                   p["decisions"].__setitem__(1, dict(p["decisions"][1], candidate="dc.schema-scope")))),
        "decisions.candidate_order"))

    cases.append(("wrong_resolves", lambda d: _edit_json(
        d / "registries" / "decisions.json",
        lambda p: p["decisions"][3].__setitem__("resolves", ["U-99"])), "decisions.resolves_match"))

    cases.append(("wrong_status", lambda d: _edit_json(
        d / "registries" / "decisions.json",
        lambda p: p["decisions"][30].__setitem__("status", "PROPOSED")), "decisions.statuses"))

    cases.append(("ghost_basis", lambda d: _edit_json(
        d / "registries" / "decisions.json",
        lambda p: p["decisions"][5]["basis"].append("INV-99")), "decisions.basis_ids_exist"))

    cases.append(("drop_effect_enum_value", lambda d: _edit_json(
        d / "registries" / "enums.json",
        lambda p: next(e for e in p["enums"] if e["name"] == "effect_id")["values"].pop()),
        "enums.ch0_consistency"))

    cases.append(("unregister_schema_enum", lambda d: _edit_json(
        d / "registries" / "enums.json",
        lambda p: next(e for e in p["enums"] if e["name"] == "edge_kind")["values"].append("SOFT")),
        "enums.schema_consistency"))

    cases.append(("identifier_prefix_drift", lambda d: _edit_json(
        d / "registries" / "identifiers.json",
        lambda p: next(i for i in p["identifiers"] if i.get("prefix") == "tc").__setitem__("prefix", "tk")),
        "identifiers.grammar_sync"))

    cases.append(("ghost_parity_test", lambda d: _edit_json(
        d / "registries" / "semantic_rules.json",
        lambda p: p["rules"][4].__setitem__("parity_test", "test_semantic_rules.test_sem_missing")),
        "semantic_rules.tests_exist"))

    cases.append(("drop_vector", lambda d: _edit_json(
        d / "registries" / "vectors_resolution.json",
        lambda p: p["vectors"].pop(16)), "vectors.ids_match_phase_a"))

    cases.append(("tamper_golden", lambda d: _edit_json(
        d / "vectors" / "golden" / "golden_digests.json",
        lambda p: p["V-A"].__setitem__("record_hash", "sha256:" + "0" * 64 + "x")),
        "vectors.goldens_consistent"))

    def _drop_preimage_key(p):
        preimage = json.loads(p["V-A"]["canonical_requirement_preimage"])
        preimage.pop("objective")
        p["V-A"]["canonical_requirement_preimage"] = json.dumps(
            preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    cases.append(("preimage_key_drift", lambda d: _edit_json(
        d / "vectors" / "golden" / "golden_digests.json", _drop_preimage_key),
        "identity.tiers_match"))

    cases.append(("drop_traceability_row", lambda d: _edit_json(
        d / "registries" / "traceability.json",
        lambda p: p["rows"].pop(70)), "traceability.covers_76"))

    cases.append(("wrong_owner", lambda d: _edit_json(
        d / "registries" / "traceability.json",
        lambda p: next(r for r in p["rows"] if r["id"] == "INV-05").__setitem__("owner", "ch4")),
        "traceability.covers_76"))

    cases.append(("drop_required_object", lambda d: _edit_json(
        d / "registries" / "representation.json",
        lambda p: next(o for o in p["objects"] if o["id"] == "obj-18").__setitem__("ch1_scope", "form_only")),
        "schemas.profile_and_coverage"))

    cases.append(("storage_drift", lambda d: _edit_json(
        d / "registries" / "representation.json",
        lambda p: next(o for o in p["objects"] if o["id"] == "obj-08").__setitem__("storage", "content_store")),
        "representation.covers_42"))

    cases.append(("extra_conditional_object", lambda d: _edit_json(
        d / "registries" / "representation.json",
        lambda p: next(o for o in p["objects"] if o["id"] == "obj-15").__setitem__("conditional_bindings", True)),
        "representation.covers_42"))

    cases.append(("draft_handoff_bad_predecessor", lambda d: (
        (d / "handoff").mkdir(exist_ok=True),
        (d / "handoff" / "ch01-phase-b.draft.json").write_text(json.dumps({
            "predecessor": {"handoff": "ch00-amendment-01.json", "sha256": "0" * 64}
        }), encoding="utf-8"))[-1], "handoff.chain"))

    cases.append(("spec_counts_broken", lambda d: (d / "CHAPTER1_SPECIFICATION.md").write_text(
        "# stub\n", encoding="utf-8"), "docs.present_and_counted"))

    return cases


class TestCheckerNegatives(unittest.TestCase):
    def test_control_copy_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = _copy_workspace(tmp)
            with redirect_stdout(io.StringIO()):
                code = check_chapter1.run(dst, REPO, quiet=True)
            self.assertEqual(code, 0, "an unmodified copy must pass as a control")

    def test_planted_defects_caught(self):
        for name, mutate, expected_check in _defects():
            with tempfile.TemporaryDirectory() as tmp:
                dst = _copy_workspace(tmp)
                mutate(dst)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = check_chapter1.run(dst, REPO, quiet=True)
                output = buf.getvalue()
                self.assertNotEqual(code, 0, "%s: checker passed a planted defect" % name)
                self.assertIn("FAIL %s" % expected_check, output,
                              "%s: expected %s to fail, got:\n%s" % (name, expected_check, output))


if __name__ == "__main__":
    unittest.main()
