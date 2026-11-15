"""real_bytes - the local-only real-boundary evidence reader (TI-1 WP3b).

The 274 runtime tests drive a fake transport; the A1 chain was calibrated
(C4-M2, U-12) against real provider records that live only on the
operator's machine. This module READS those retained records in place -
nothing here is a fixture, no provider or prompt byte is ever written into
the repository, and the committed ``real_bytes_manifest.json`` holds
sha256 identities and class tokens ONLY: no call id, no file name, no
size, no text (TI-1 section 7c: the default is a manifest of sha256
identities; committing real bytes needs the operator's explicit approval
per file). The names and headers the arm works with are read from the
live records at test time and never leave the machine.

Locations (the live_g2.py convention):
  AUTOSWARM_HOME               default E:\\Users\\dimas\\autoswarm-home
                               (the retained ``calls/<call_id>/task.json``)
  AUTOBOT_PROMPT_EVIDENCE_DIR  default E:\\Users\\dimas\\luna-v12
                               (the prompt files the Luna runs were given)

The run/skip/fail decision is explicit and operator-controlled:
  AUTOBOT_REAL_BYTES unset     the arm runs when the evidence is present
                               and SKIPS (visibly) when it is absent;
  AUTOBOT_REAL_BYTES=require   absence is a FAILURE - set on a host that
                               holds the evidence, so the arm can never
                               silently skip there.

Operator use - regenerate the manifest from the live records (written
with LF line ends whatever the shell):
    python tests/autobot/real_bytes.py tests/autobot/real_bytes_manifest.json

Stdlib only; imports nothing from the production packages.
"""

import hashlib
import json
import os
import re
import sys

DEFAULT_AUTOSWARM_HOME = r"E:\Users\dimas\autoswarm-home"
DEFAULT_PROMPT_DIR = r"E:\Users\dimas\luna-v12"
MARKER_ENV = "AUTOBOT_REAL_BYTES"
MANIFEST_SCHEMA = "autobot-ti1-real-bytes-manifest/v1"
MANIFEST_NAME = "real_bytes_manifest.json"

CLASS_FILE = "file-identical"     # prompt bytes == a retained prompt file
CLASS_COMPOSED = "ps51-composed"  # BOM + KEY=<call id> header + body
CLASS_INLINE = "inline"           # given on the command line, no file

_HEADER_RE = re.compile(
    "^\ufeff([A-Z_]+_CALL_ID)=(call_[0-9a-f]{32})\n")


def locations(environ=None):
    """(calls directory, prompt directory) for this process."""
    environ = os.environ if environ is None else environ
    home = environ.get("AUTOSWARM_HOME", DEFAULT_AUTOSWARM_HOME)
    prompts = environ.get("AUTOBOT_PROMPT_EVIDENCE_DIR", DEFAULT_PROMPT_DIR)
    return os.path.join(home, "calls"), prompts


def decide(present, marker):
    """The whole run/skip/fail policy as one pure function."""
    if present:
        return "run"
    if marker == "require":
        return "fail"
    return "skip"


def sha256_text(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_prompt_files(prompt_dir):
    """basename -> exact bytes of every retained ``*.txt`` prompt file."""
    files = {}
    if not os.path.isdir(prompt_dir):
        return files
    for name in sorted(os.listdir(prompt_dir)):
        path = os.path.join(prompt_dir, name)
        if name.endswith(".txt") and os.path.isfile(path):
            with open(path, "rb") as handle:
                files[name] = handle.read()
    return files


def load_task_records(calls_dir):
    """call_id -> {"bytes": the exact task.json bytes, "prompt": str} for
    every retained provider task record that carries a prompt string."""
    records = {}
    if not os.path.isdir(calls_dir):
        return records
    for name in sorted(os.listdir(calls_dir)):
        path = os.path.join(calls_dir, name, "task.json")
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as handle:
            data = handle.read()
        try:
            record = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("prompt"),
                                                   str):
            records[name] = {"bytes": data, "prompt": record["prompt"],
                             "call_id": record.get("call_id")}
    return records


def classify(prompt, prompt_files):
    """One recorded prompt against the retained prompt files: the LOCAL
    detail the arm works with on the operator's machine (file names, the
    composed header's key and call id). None of the names is committed -
    ``manifest_entry`` keeps the identities only."""
    encoded = prompt.encode("utf-8")
    for name in sorted(prompt_files):
        if prompt_files[name] == encoded:
            return {"class": CLASS_FILE, "input_file": name,
                    "input_sha256": sha256_text(prompt_files[name])}
    header = _HEADER_RE.match(prompt)
    if header is not None:
        entry = {"class": CLASS_COMPOSED, "header_key": header.group(1),
                 "header_call_id": header.group(2)}
        body = prompt[header.end():].encode("utf-8")
        for name in sorted(prompt_files):
            if prompt_files[name] + b"\n" == body:
                entry["body_file"] = name
                entry["body_sha256"] = sha256_text(prompt_files[name])
        return entry
    return {"class": CLASS_INLINE}


IDENTITY_MEMBERS = ("input_sha256", "body_sha256")


def manifest_entry(record_bytes, prompt, prompt_files):
    """The COMMITTED projection of one record: the sha256 identity of the
    exact task-record bytes, the sha256 identity of the recorded prompt,
    the class token, and the identity of the retained file the prompt was
    read or composed from, when there is one. Nothing else."""
    local = classify(prompt, prompt_files)
    entry = {"task_record_sha256": sha256_text(record_bytes),
             "prompt_sha256": sha256_text(prompt.encode("utf-8")),
             "class": local["class"]}
    for member in IDENTITY_MEMBERS:
        if member in local:
            entry[member] = local[member]
    return entry


def build_manifest(calls_dir, prompt_dir):
    """The identity manifest of the live evidence: sha256 identities and
    class tokens, ordered by record identity."""
    prompt_files = load_prompt_files(prompt_dir)
    records = load_task_records(calls_dir)
    entries = sorted(
        (manifest_entry(records[name]["bytes"], records[name]["prompt"],
                        prompt_files) for name in records),
        key=lambda entry: entry["task_record_sha256"])
    named = set(entry[member] for entry in entries
                for member in IDENTITY_MEMBERS if member in entry)
    return {
        "schema": MANIFEST_SCHEMA,
        "note": ("sha256 identities of provider records retained on the"
                 " operator's machine; no provider or prompt bytes, names"
                 " or sizes are committed (TI-1 section 7c)"),
        "prompt_files": sorted(named),
        "task_records": entries,
    }


def manifest_text(manifest):
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def main(argv):
    calls_dir, prompt_dir = locations()
    if not (os.path.isdir(calls_dir) and os.path.isdir(prompt_dir)):
        sys.stderr.write("real_bytes: the retained evidence is not on this"
                         " machine (%s, %s)\n" % (calls_dir, prompt_dir))
        return 2
    text = manifest_text(build_manifest(calls_dir, prompt_dir))
    if len(argv) == 2:
        with open(argv[1], "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
