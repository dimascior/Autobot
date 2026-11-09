"""The D-43 authoring-profile validator: a deterministic, stdlib-only
implementation of the JSON Schema 2020-12 subset the Chapter 1 schemas use.

Supported keywords: type, properties, required, additionalProperties
(false or a schema), patternProperties, propertyNames, minProperties,
enum, const, pattern (search semantics, as the standard defines),
items (single schema), minItems, maxItems, minimum, maximum, oneOf,
$defs and local $ref ("#/$defs/...").

Any other keyword in a schema is a profile violation and raises, so a
schema cannot silently rely on something this validator ignores.
"""

import re

_ALLOWED_KEYWORDS = {
    "$schema", "$id", "$defs", "$ref", "title", "description",
    "type", "properties", "required", "additionalProperties",
    "patternProperties", "propertyNames", "minProperties",
    "enum", "const", "pattern", "items", "minItems", "maxItems",
    "minimum", "maximum", "oneOf",
}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


class SchemaProfileError(ValueError):
    """The schema itself steps outside the authoring profile."""


class Issue:
    __slots__ = ("code", "path", "message")

    def __init__(self, code, path, message):
        self.code = code
        self.path = path
        self.message = message

    def as_dict(self):
        return {"code": self.code, "path": self.path, "message": self.message}

    def __repr__(self):
        return "%s at %s: %s" % (self.code, self.path or "$", self.message)


def check_profile(schema):
    """Raise SchemaProfileError if the schema uses anything outside the profile."""
    def walk(node, where):
        if isinstance(node, dict):
            for key, val in node.items():
                if where == "schema" and key not in _ALLOWED_KEYWORDS:
                    raise SchemaProfileError(
                        "keyword %r outside the authoring profile" % key
                    )
                if key in ("properties", "patternProperties", "$defs"):
                    for sub in val.values():
                        walk(sub, "schema")
                elif key in ("items", "additionalProperties", "propertyNames"):
                    if isinstance(val, dict):
                        walk(val, "schema")
                elif key == "oneOf":
                    for sub in val:
                        walk(sub, "schema")
    walk(schema, "schema")


def _resolve_ref(ref, root):
    if not ref.startswith("#/"):
        raise SchemaProfileError("non-local $ref %r" % ref)
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise SchemaProfileError("unresolvable $ref %r" % ref)
        node = node[part]
    return node


def validate(value, schema, root=None, path="$", issues=None):
    """Validate ``value`` against ``schema``; returns a list of Issues."""
    if issues is None:
        issues = []
    if root is None:
        root = schema

    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root)
        merged = dict(target)
        for key, val in schema.items():
            if key != "$ref":
                merged[key] = val
        return validate(value, merged, root, path, issues)

    stype = schema.get("type")
    if stype is not None:
        checker = _TYPES.get(stype)
        if checker is None:
            raise SchemaProfileError("type %r outside the profile" % stype)
        if not checker(value):
            issues.append(Issue("ERR_TYPE", path, "expected %s" % stype))
            return issues

    if "const" in schema and value != schema["const"]:
        issues.append(Issue("ERR_CONST", path, "expected %r" % (schema["const"],)))
    if "enum" in schema and value not in schema["enum"]:
        issues.append(Issue("ERR_ENUM", path, "value %r not in enum" % (value,)))

    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            issues.append(Issue("ERR_PATTERN", path, "does not match %s" % schema["pattern"]))

    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(Issue("ERR_MINIMUM", path, "below minimum %d" % schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            issues.append(Issue("ERR_MAXIMUM", path, "above maximum %d" % schema["maximum"]))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            issues.append(Issue("ERR_MIN_ITEMS", path, "fewer than %d items" % schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            issues.append(Issue("ERR_MAX_ITEMS", path, "more than %d items" % schema["maxItems"]))
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], root, "%s[%d]" % (path, i), issues)

    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            issues.append(Issue("ERR_MIN_PROPERTIES", path, "fewer than %d properties" % schema["minProperties"]))
        props = schema.get("properties", {})
        pattern_props = schema.get("patternProperties", {})
        for req in schema.get("required", []):
            if req not in value:
                issues.append(Issue("ERR_MISSING_FIELD", path, "missing required %r" % req))
        if "propertyNames" in schema:
            for key in value:
                validate(key, schema["propertyNames"], root, "%s.<name:%s>" % (path, key), issues)
        for key, val in value.items():
            sub_path = "%s.%s" % (path, key)
            matched = False
            if key in props:
                matched = True
                validate(val, props[key], root, sub_path, issues)
            for pat, sub in pattern_props.items():
                if re.search(pat, key) is not None:
                    matched = True
                    validate(val, sub, root, sub_path, issues)
            if not matched:
                extra = schema.get("additionalProperties")
                if extra is False:
                    issues.append(Issue("ERR_UNKNOWN_FIELD", sub_path, "unknown field (D-36)"))
                elif isinstance(extra, dict):
                    validate(val, extra, root, sub_path, issues)
                elif extra is None and (props or pattern_props):
                    # Chapter 1 schemas always state additionalProperties
                    # explicitly; reaching here is a schema defect.
                    issues.append(Issue("ERR_UNKNOWN_FIELD", sub_path, "unknown field (schema left additionalProperties unstated)"))

    if "oneOf" in schema:
        matches = 0
        branch_issues = []
        for i, sub in enumerate(schema["oneOf"]):
            trial = validate(value, sub, root, path, [])
            if not trial:
                matches += 1
            else:
                branch_issues.append((i, trial))
        if matches != 1:
            issues.append(Issue(
                "ERR_ONE_OF", path,
                "%d of %d oneOf branches matched" % (matches, len(schema["oneOf"])),
            ))

    return issues
