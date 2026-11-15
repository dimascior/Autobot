"""TI-1 WP4: the mutation matrix, enforced.

``mutation_matrix.json`` ties every refusal the runtime suite proves to a
registry row: invariant(s) -> seam -> planted defect -> the named refusal
(error class and pinned marker text) -> the test that plants it -> the
control that passes without the plant. This module is the registry's
checker (the chapter-1 shape: a registry is only as good as the tool that
holds it to the sources):

- census, both directions: every refusal assertion in tests/autobot (an
  ``assertRaises`` of any authority-core LedgerError subclass) is
  referenced by at least one row, and every row names a site that exists
  with exactly the error class it claims - an unregistered new refusal
  assertion, or a row drifting from its test, fails here; and because the
  census names a site by ONE class, a refusal asserted as a tuple of
  classes or as the LedgerError base (which no row could carry) is itself
  a failure;
- every referenced test and control resolves through the unittest loader,
  and the test really exercises its site;
- every marker is PINNED: it must be a string constant asserted against
  the caught exception's text (``assertIn(marker, str(ctx.exception))``,
  an ``assertRaisesRegex`` pattern, or the constant a test passes to a
  helper that performs that assertion) - or, for a refusal a seam RETURNS
  rather than raises, the first argument of an ``assertIn`` in the test.
  Comments and docstrings are not in the AST: a marker that appears only
  in prose is rejected;
- every target invariant has at least one row whose marker is pinned, and
  every invariant id a row names exists in a frozen registry.

Scope, stated: these are PLANTED-DEFECT proofs (corrupt an input, a row or
a byte; the seam must refuse). Classical code-mutation testing - mutating
the implementation and watching tests fail - is deliberately out of scope
for this milestone.
"""

import ast
import json
import re
import unittest
from pathlib import Path

import support

HERE = Path(__file__).resolve().parent
MATRIX_PATH = HERE / "mutation_matrix.json"
MATRIX_SCHEMA = "autobot-ti1-mutation-matrix/v1"

# models.py's LedgerError family: "every authority-core refusal".
LEDGER_REFUSALS = (
    "IntegrityHalt", "ConflictError", "StaleFenceError", "AuthorityLost",
    "AdmissionError", "JournalBroken", "LeaseUnavailable",
    "PermitExhaustedError", "SchemaTooNew",
)
# The six classes the TI-1 audit counted (219 sites at C4-M2).
AUDITED_REFUSALS = LEDGER_REFUSALS[:6]
RETURNED = "returned-refusal"

ROW_KEYS = ("id", "invariants", "seam", "planted_defect", "expected_error",
            "marker", "test", "site", "control")

TARGET_INVARIANTS = (
    "INV-03", "INV-06", "INV-07", "INV-13", "INV-17", "INV-18", "INV-44",
    "INV-49", "INV-53", "INV-58", "INV-66", "INV-68", "INV-72",
    "SEM-15", "SEM-16", "D-52", "D-53", "D-56", "D-C3-2", "F-C4-1",
    "F-C4-2",
    # every registered chapter 2-4 runtime proof whose statement names a
    # refusal (chapter0 invariants.json "tests")
    "CH2-reserve-requires-frozen-record", "CH2-failpoint-matrix",
    "CH2-single-current-attempt", "CH2-stale-fence-rejected",
    "CH2-instance-lease", "CH2-permit-race-two-reservers-one-use",
    "CH2-permit-counters", "CH2-reservation-lease",
    "CH3-artifact-provenance-single-attempt",
    "CH3-provenance-from-witnesses", "CH3-integrity-failure-blocks",
    "CH3-input-rehash", "CH3-integrity-classes",
    "CH4-llm-output-never-pass", "CH4-mandatory-assertions-required",
    "CH4-verification-binding", "CH4-evidence-not-reused",
    "CH4-a1-transform-chain", "CH4-role-transform-check",
    "CH4-artifact-retention",
)
# Registered proofs that assert a positive property and name no refusal;
# listed so the split above is a checked statement, not an omission.
NON_REFUSAL_PROOFS = (
    "CH2-attempt-contract-captures-source-state",
    "CH2-record-immutable-in-sequence", "CH2-receipt-storage",
    "CH3-mtime-never-selects-artifact", "CH3-index-lag-diagnostic",
)


# ------------------------------------------------------------ the census

def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _own_nodes(function):
    """Every node inside ``function`` that is not inside a nested def or
    class (lambdas belong to their enclosing function)."""
    stack = [node for node in function.body
             if not isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.ClassDef)):
                stack.append(child)


def _refusal_call(node):
    """(error name, regex or None) when ``node`` is an assertRaises /
    assertRaisesRegex call naming a ledger refusal; else None."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in (
            "assertRaises", "assertRaisesRegex"):
        return None
    error = _dotted(node.args[0]).split(".")[-1]
    if error not in LEDGER_REFUSALS:
        return None
    regex = None
    if node.func.attr == "assertRaisesRegex" and len(node.args) > 1 \
            and isinstance(node.args[1], ast.Constant) \
            and isinstance(node.args[1].value, str):
        regex = node.args[1].value
    return error, regex


def _opaque_refusal(node):
    """True for an assertRaises the census cannot name as ONE ledger
    class: a tuple of classes that includes a ledger refusal, or the
    LedgerError base itself. Such an assertion proves a refusal that no
    row could carry, so it must not exist (the adversarial hole in a
    by-name census)."""
    if not isinstance(node, ast.Call) or not node.args:
        return False
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in (
            "assertRaises", "assertRaisesRegex"):
        return False
    first = node.args[0]
    names = [first] if not isinstance(first, ast.Tuple) else list(first.elts)
    family = LEDGER_REFUSALS + ("LedgerError",)
    named = [_dotted(name).split(".")[-1] for name in names]
    if isinstance(first, ast.Tuple):
        return any(name in family for name in named)
    return named[0] == "LedgerError"


def _exception_text_of(node, name):
    """True when ``node`` reads ``<name>.exception`` (any wrapping)."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Attribute) and inner.attr == "exception" \
                and isinstance(inner.value, ast.Name) \
                and inner.value.id == name:
            return True
    return False


class ModuleCensus(object):
    """One test module's functions and refusal sites."""

    def __init__(self, module_name, source=None):
        self.module = module_name
        if source is None:
            source = (HERE / (module_name + ".py")).read_text(
                encoding="utf-8")
        self.tree = ast.parse(source)
        self.functions = {}  # qualname -> FunctionDef
        self.sites = {}      # site id -> facts
        self.opaque = []     # (qualname, line): refusals no row could name
        self._walk(self.tree, [])

    def _walk(self, node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self._walk(child, scope + [child.name])
            elif isinstance(child, ast.FunctionDef):
                qualname = ".".join(scope + [child.name])
                self.functions[qualname] = child
                self._sites(qualname, child)
                self._walk(child, scope + [child.name])
            else:
                self._walk(child, scope)

    def _sites(self, qualname, function):
        parameters = set(arg.arg for arg in function.args.args
                         + function.args.kwonlyargs)
        found = []
        bound = set()
        for node in _own_nodes(function):
            if isinstance(node, ast.With):
                for item in node.items:
                    refusal = _refusal_call(item.context_expr)
                    if refusal is None:
                        continue
                    bound.add(id(item.context_expr))
                    name = item.optional_vars.id if isinstance(
                        item.optional_vars, ast.Name) else None
                    found.append([node.lineno, node.col_offset,
                                  getattr(node, "end_lineno", node.lineno),
                                  refusal, name])
        for node in _own_nodes(function):
            refusal = _refusal_call(node)
            if refusal is not None and id(node) not in bound:
                found.append([node.lineno, node.col_offset, node.lineno,
                              refusal, None])
            if _opaque_refusal(node):
                self.opaque.append((qualname, node.lineno))
        found.sort(key=lambda site: (site[0], site[1]))
        asserts = sorted(
            (node for node in _own_nodes(function)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "assertIn" and len(node.args) >= 2),
            key=lambda node: node.lineno)
        for index, (line, _col, end, refusal, name) in enumerate(found):
            markers, marker_parameters = [], []
            if refusal[1] is not None:
                markers.append(refusal[1])
            if name is not None:
                # this binding lives until the next site rebinds the name
                horizon = min(
                    [other[0] for other in found[index + 1:]
                     if other[4] == name] or [10 ** 9])
                for call in asserts:
                    if not (end < call.lineno < horizon):
                        continue
                    if not _exception_text_of(call.args[1], name):
                        continue
                    first = call.args[0]
                    if isinstance(first, ast.Constant) and isinstance(
                            first.value, str):
                        markers.append(first.value)
                    elif isinstance(first, ast.Name) \
                            and first.id in parameters:
                        marker_parameters.append(first.id)
            self.sites["%s.%s#%d" % (self.module, qualname, index + 1)] = {
                "error": refusal[0], "function": qualname, "line": line,
                "markers": markers, "marker_parameters": marker_parameters,
            }

    def constants_passed_to(self, function_qualname, helper_name):
        """String constants a function passes (positionally or by
        keyword) in calls to ``helper_name``."""
        function = self.functions[function_qualname]
        found = set()
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if _dotted(node.func).split(".")[-1] != helper_name:
                continue
            values = list(node.args) + [kw.value for kw in node.keywords]
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(
                        value.value, str):
                    found.add(value.value)
        return found

    def calls(self, function_qualname, helper_name):
        function = self.functions[function_qualname]
        return any(isinstance(node, ast.Call)
                   and _dotted(node.func).split(".")[-1] == helper_name
                   for node in ast.walk(function))

    def assert_in_constants(self, function_qualname):
        """First-argument string constants of every assertIn in a test
        (the pin for a refusal a seam RETURNS)."""
        function = self.functions[function_qualname]
        found = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "assertIn" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                found.add(node.args[0].value)
        return found


def test_modules():
    return sorted(path.stem for path in HERE.glob("test_*.py"))


def full_census():
    return dict((name, ModuleCensus(name)) for name in test_modules())


def split_test_id(test_id):
    module, _, qualname = test_id.partition(".")
    return module, qualname


# ----------------------------------------------------------- id registry

def _collect_ids(node, into):
    """Every string a registry stores under an ``id`` key, at any depth
    (invariants and their registered proofs, semantic rules, decisions,
    transitions, capabilities, objects)."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            identifier = current.get("id")
            if isinstance(identifier, str):
                into.add(identifier)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


# The id families a row may cite: invariants, semantic rules, decisions,
# registered proofs, transitions, capabilities, objects, findings. (The
# registries also store states, principals and effects under "id"; those
# are not rules and are not citable.)
CITABLE = re.compile(r"^(INV|SEM|D|CH[0-9]+|t|cap|obj|F)-")


def known_ids():
    """The vocabulary a row may cite: every rule-family id a chapter
    registry defines, plus the findings (F-...) a chapter document
    actually names - findings live in chapter prose, not in a registry."""
    docs = support.REPO / "docs" / "architecture"
    ids = set()
    for path in sorted(docs.glob("chapter*/registries/*.json")):
        _collect_ids(support.load_json(path), ids)
    prose = "\n".join(path.read_text(encoding="utf-8")
                      for path in sorted(docs.glob("chapter*/*.md")))
    ids.update(re.findall(r"\bF-[A-Z0-9]+-\d+\b", prose))
    return set(identifier for identifier in ids
               if CITABLE.match(identifier))


class MutationMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix = support.load_json(MATRIX_PATH)
        cls.rows = cls.matrix["rows"]
        cls.census = full_census()
        cls.sites = {}
        for module in cls.census.values():
            cls.sites.update(module.sites)

    def test_registry_shape(self):
        self.assertEqual(self.matrix["schema"], MATRIX_SCHEMA)
        self.assertEqual(tuple(self.matrix["target_invariants"]),
                         TARGET_INVARIANTS)
        identifiers = [row["id"] for row in self.rows]
        self.assertEqual(identifiers,
                         ["MM-%03d" % (index + 1)
                          for index in range(len(self.rows))])
        for row in self.rows:
            self.assertEqual(tuple(sorted(row)), tuple(sorted(ROW_KEYS)),
                             row["id"])
            self.assertTrue(row["invariants"], row["id"])
            self.assertEqual(row["invariants"], sorted(row["invariants"]),
                             row["id"])
            for member in ("seam", "planted_defect", "test", "control"):
                self.assertTrue(
                    isinstance(row[member], str) and row[member].strip(),
                    "%s lacks %s" % (row["id"], member))
            self.assertIn(row["expected_error"],
                          LEDGER_REFUSALS + (RETURNED,), row["id"])
            self.assertEqual(row["site"] is None,
                             row["expected_error"] == RETURNED, row["id"])

    def test_every_refusal_assertion_is_registered(self):
        """The census, source -> registry: no refusal assertion in the
        suite is outside the matrix."""
        referenced = set(row["site"] for row in self.rows
                         if row["site"] is not None)
        unregistered = sorted(set(self.sites) - referenced)
        self.assertEqual(unregistered, [],
                         "refusal assertions with no matrix row")
        audited = [site for site, facts in self.sites.items()
                   if facts["error"] in AUDITED_REFUSALS]
        # the C4-M2 audit counted 219 in these six classes; TI-1 only adds
        self.assertGreaterEqual(len(audited), 219)

    def test_no_refusal_assertion_escapes_the_census(self):
        """The census names sites by ONE error class. A refusal asserted
        as a tuple of classes, or as the LedgerError base, would be a
        proven refusal no row could carry - so none may exist."""
        opaque = sorted(
            "%s.%s line %d" % (module.module, qualname, line)
            for module in self.census.values()
            for qualname, line in module.opaque)
        self.assertEqual(opaque, [],
                         "name exactly one ledger class per refusal"
                         " assertion")

    def test_every_row_names_a_real_site_with_its_error(self):
        """The census, registry -> source."""
        for row in self.rows:
            if row["site"] is None:
                continue
            self.assertIn(row["site"], self.sites, row["id"])
            self.assertEqual(self.sites[row["site"]]["error"],
                             row["expected_error"], row["id"])

    def test_every_test_and_control_exists(self):
        """unittest loader introspection: each id resolves to exactly one
        test method."""
        loader = unittest.TestLoader()
        seen = {}
        for row in self.rows:
            for member in ("test", "control"):
                name = row[member]
                if name not in seen:
                    seen[name] = loader.loadTestsFromName(
                        name).countTestCases()
                self.assertEqual(seen[name], 1,
                                 "%s %s %r does not resolve"
                                 % (row["id"], member, name))
                self.assertIn(".test_", name, row["id"])

    def test_every_test_exercises_its_site(self):
        for row in self.rows:
            if row["site"] is None:
                continue
            module_name, test_qualname = split_test_id(row["test"])
            site = self.sites[row["site"]]
            self.assertTrue(row["site"].startswith(module_name + "."),
                            "%s: site and test are in different modules"
                            % row["id"])
            function = site["function"]
            if function == test_qualname or function.startswith(
                    test_qualname + "."):
                continue
            helper = function.split(".")[-1]
            self.assertTrue(
                self.census[module_name].calls(test_qualname, helper),
                "%s: test %s never calls the helper %s that holds its site"
                % (row["id"], row["test"], helper))

    def test_every_marker_is_pinned_by_an_assertion(self):
        """A marker in a comment or a docstring is not a pin: it must be
        asserted against the refusal's text."""
        for row in self.rows:
            marker = row["marker"]
            if marker is None:
                continue
            self.assertTrue(isinstance(marker, str) and marker, row["id"])
            module_name, test_qualname = split_test_id(row["test"])
            census = self.census[module_name]
            if row["site"] is None:
                self.assertIn(
                    marker, census.assert_in_constants(test_qualname),
                    "%s: %r is not the first argument of an assertIn in"
                    " %s" % (row["id"], marker, row["test"]))
                continue
            site = self.sites[row["site"]]
            if marker in site["markers"]:
                continue
            helper = site["function"].split(".")[-1]
            self.assertTrue(
                site["marker_parameters"]
                and marker in census.constants_passed_to(test_qualname,
                                                         helper),
                "%s: %r is not asserted against the exception at %s"
                % (row["id"], marker, row["site"]))

    def test_every_target_invariant_has_a_pinned_row(self):
        pinned = set()
        named = set()
        for row in self.rows:
            named.update(row["invariants"])
            if row["marker"] is not None:
                pinned.update(row["invariants"])
        for target in TARGET_INVARIANTS:
            self.assertIn(target, named, "%s has no row at all" % target)
            self.assertIn(target, pinned,
                          "%s has no row with a pinned marker" % target)

    def test_target_list_covers_the_registered_chapter_proofs(self):
        """The CH2/CH3/CH4 half of the target list IS the registry's
        runtime-proof list, split into refusal and non-refusal proofs."""
        registered = set()
        for invariant in support.load_json(
                support.CH0_REGISTRIES / "invariants.json")["invariants"]:
            for test in invariant.get("tests", []):
                if test.get("kind") == "runtime" and test.get(
                        "chapter") in (2, 3, 4):
                    registered.add(test["id"])
        targeted = set(target for target in TARGET_INVARIANTS
                       if target.startswith("CH"))
        self.assertEqual(targeted | set(NON_REFUSAL_PROOFS), registered)
        self.assertEqual(targeted & set(NON_REFUSAL_PROOFS), set())

    def test_every_cited_id_exists(self):
        """Every id a row cites is defined by a chapter registry (or, for
        a finding, named by a chapter document) - a typo or an invented
        rule fails here."""
        known = known_ids()
        self.assertGreater(len(known), 300)  # the vocabulary really loaded
        unknown = sorted(
            "%s cites %s" % (row["id"], identifier)
            for row in self.rows for identifier in row["invariants"]
            if identifier not in known)
        self.assertEqual(unknown, [])
        self.assertNotIn("INV-999", known)   # and it is not "anything"
        self.assertNotIn("you", known)       # principals are not rules

    def test_the_checker_rejects_prose_markers_and_drift(self):
        """The checker tested like a checker (control first): over a
        specimen source its census finds the constant asserted on the
        exception - and finds NEITHER the marker that exists only in a
        comment or docstring, NOR one asserted against a different
        binding, NOR one asserted before the refusal."""
        specimen = "\n".join((
            "class Specimen:",
            "    def test_one(self):",
            "        '''docstring-only marker'''",
            "        self.assertIn('asserted too early', str(early.exception))",
            "        with self.assertRaises(ConflictError) as caught:",
            "            # comment-only marker",
            "            seam()",
            "        self.assertIn('asserted marker', str(caught.exception))",
            "        self.assertIn('other binding', str(other.exception))",
            "        with self.assertRaises(IntegrityHalt) as caught:",
            "            seam()",
            "        self.assertIn('second marker', str(caught.exception))",
            "        with self.assertRaises(ValueError):",
            "            seam()",
            "    def helper(self, marker):",
            "        with self.assertRaises(AdmissionError) as caught:",
            "            seam()",
            "        self.assertIn(marker, str(caught.exception))",
            "    def test_two(self):",
            "        self.helper('passed marker')",
            "        self.assertIn('returned marker', refusal)",
            "    def test_three(self):",
            "        with self.assertRaises((ConflictError, ValueError)):",
            "            seam()",
            "        self.assertRaises(LedgerError, seam)",
            "        with self.assertRaises((KeyError, ValueError)):",
            "            seam()",
            ""))
        census = ModuleCensus("specimen", source=specimen)
        self.assertEqual(sorted(census.sites), [
            "specimen.Specimen.helper#1", "specimen.Specimen.test_one#1",
            "specimen.Specimen.test_one#2"])  # ValueError is no refusal
        # a tuple naming a ledger class and the LedgerError base are
        # caught as refusals no row could name; a non-ledger tuple is not
        self.assertEqual(sorted(census.opaque),
                         [("Specimen.test_three", 23),
                          ("Specimen.test_three", 25)])
        first = census.sites["specimen.Specimen.test_one#1"]
        self.assertEqual((first["error"], first["markers"]),
                         ("ConflictError", ["asserted marker"]))
        second = census.sites["specimen.Specimen.test_one#2"]
        self.assertEqual((second["error"], second["markers"]),
                         ("IntegrityHalt", ["second marker"]))
        helper = census.sites["specimen.Specimen.helper#1"]
        self.assertEqual((helper["markers"], helper["marker_parameters"]),
                         ([], ["marker"]))
        self.assertEqual(
            census.constants_passed_to("Specimen.test_two", "helper"),
            set(["passed marker"]))
        self.assertTrue(census.calls("Specimen.test_two", "helper"))
        self.assertFalse(census.calls("Specimen.test_one", "helper"))
        returned = census.assert_in_constants("Specimen.test_two")
        self.assertEqual(returned, set(["returned marker"]))
        everything = repr(census.sites) + repr(returned)
        for prose in ("docstring-only marker", "comment-only marker",
                      "asserted too early", "other binding"):
            self.assertNotIn(prose, everything)


if __name__ == "__main__":
    unittest.main()
