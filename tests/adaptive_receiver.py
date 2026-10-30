"""Independent offline adaptive receiver; no production imports or source execution."""
from __future__ import annotations

import ast
import base64
import hashlib

from v2_consumer import core_check, dump, load

POLICY = "adaptive-backpressure-v1"
METHOD = "notifications/solo_distill/chunk-v1"
BINDING = {"project_id", "snapshot_id", "input_id", "core_id", "policy_id",
           "contract_version", "encoding", "wire_encoding"}


def reject(condition, reason):
    if condition:
        raise ValueError(reason)


def envelope(raw, request_id):
    value = load(raw)
    reject(type(value) is not dict or set(value) != {"jsonrpc", "id", "result"}
           or value["jsonrpc"] != "2.0" or type(value["id"]) is not type(request_id)
           or value["id"] != request_id, "response_identity")
    result = value["result"]
    reject(type(result) is not dict or set(result) != {"content", "isError"}
           or type(result["isError"]) is not bool or type(result["content"]) is not list
           or len(result["content"]) != 1, "response_result")
    text = result["content"][0]
    reject(type(text) is not dict or set(text) != {"type", "text"}
           or text["type"] != "text" or type(text["text"]) is not str, "response_text")
    return load(text["text"]), result["isError"]


def receive(raw, files, capabilities, *, request_id, project_id, expected_definitions,
            expected_calls):
    """Bound actual wire, reassemble notifications, then independently verify source."""
    c = capabilities
    reject(c["policy"] != POLICY or c["authority"] != "caller_explicit", "capability")
    reject(type(raw) is not bytes or not raw.endswith(b"\n")
           or len(raw) > c["max_transfer_bytes"], "transfer_limit")
    lines = raw.splitlines(keepends=True)
    reject(not lines or len(lines) > c["max_chunks"] + 1
           or any(not line.endswith(b"\n") or len(line) > c["max_frame_bytes"]
                  for line in lines), "frame_limit")
    manifest = None
    if len(lines) > 1:
        reject(not c["chunking"], "chunking_not_negotiated")
        report, is_error = envelope(lines[-1], request_id)
        reject(set(report) != {"contract_version", "kind", "status", "manifest"}
               or type(report["contract_version"]) is not int
               or report["contract_version"] != 2 or report["kind"] != "chunk_manifest"
               or is_error != (report["status"] in {"blocked", "failed"}), "manifest")
        manifest = report["manifest"]
        reject(set(manifest) != BINDING | {"count", "decoded_bytes", "sha256"}
               or type(manifest["count"]) is not int or manifest["count"] != len(lines) - 1
               or manifest["policy_id"] != POLICY or manifest["contract_version"] != 2
               or manifest["encoding"] != "base64-jsonrpc-wire-v1"
               or manifest["wire_encoding"] != "json"
               or type(manifest["decoded_bytes"]) is not int
               or not 0 < manifest["decoded_bytes"] <= c["max_decoded_bytes"], "manifest_binding")
        pieces = []
        for index, line in enumerate(lines[:-1]):
            notification = load(line)
            reject(set(notification) != {"jsonrpc", "method", "params"}
                   or notification["jsonrpc"] != "2.0" or notification["method"] != METHOD,
                   "notification")
            p = notification["params"]
            reject(set(p) != BINDING | {"count", "index", "chunk_bytes", "chunk_sha256", "data"}
                   or any(type(p[k]) is not type(manifest[k]) or p[k] != manifest[k]
                          for k in BINDING)
                   or type(p["index"]) is not int or p["index"] != index
                   or type(p["count"]) is not int or p["count"] != manifest["count"], "piece_binding")
            part = base64.b64decode(p["data"].encode("ascii"), validate=True)
            reject(base64.b64encode(part).decode("ascii") != p["data"]
                   or type(p["chunk_bytes"]) is not int or p["chunk_bytes"] != len(part)
                   or hashlib.sha256(part).hexdigest() != p["chunk_sha256"], "piece_digest")
            pieces.append(part)
        raw = b"".join(pieces)
        reject(len(raw) != manifest["decoded_bytes"]
               or hashlib.sha256(raw).hexdigest() != manifest["sha256"], "whole_digest")
    reject(len(raw) > c["max_decoded_bytes"] or not raw.endswith(b"\n")
           or b"\n" in raw[:-1], "decoded_boundary")
    report, is_error = envelope(raw, request_id)
    reject(set(report) != {"contract_version", "kind", "status", "core", "observations"}
           or report["contract_version"] != 2 or report["kind"] != "sealed_knowledge"
           or type(report["observations"]) is not dict or len(dump(report["observations"])) > 512,
           "publication")
    core = report["core"]
    package = core_check(core, files, project_id, POLICY)
    reject(package["scenario"] != "A" or report["status"] != package["status"]
           or is_error != (package["status"] in {"blocked", "failed"}), "status")
    if manifest is not None:
        expected = {"project_id": package["project_id"], "snapshot_id": package["snapshot_id"],
                    "input_id": core["input_id"], "core_id": core["core_id"], "policy_id": POLICY}
        reject(any(manifest[k] != v for k, v in expected.items()), "manifest_core_binding")
    definitions = {u["qualified_name"] for u in package["units"] if u["kind"] == "definition"}
    reject(not expected_definitions or definitions != set(expected_definitions), "definition_denominator")
    calls = {r["site"]["expression"] for r in package["relations"]}
    reject(not expected_calls or not set(expected_calls) <= calls, "call_denominator")
    # Independently enumerate every owned call/import site, including repeated
    # expressions and unresolved targets; production graph code is not an oracle.
    expected_sites = set()
    for file_index, file in enumerate(package["files"]):
        routes = {tuple(u["syntax_path"]) for u in package["units"]
                  if u["file"] == file_index}
        pending = [(ast.parse(files[file["path"]], feature_version=(3, 12)), (), False)]
        while pending:
            node, route, owned = pending.pop()
            owned = owned or route in routes
            if owned and isinstance(node, (ast.Call, ast.Import, ast.ImportFrom)):
                expected_sites.add((file_index, route,
                                    "call" if isinstance(node, ast.Call) else "import"))
            for field, value in ast.iter_fields(node):
                for index, child in (enumerate(value) if isinstance(value, list)
                                     else [(None, value)]):
                    if isinstance(child, ast.AST):
                        pending.append((child, route + (field,) +
                                        (() if index is None else (index,)), owned))
    actual_sites = {(package["units"][r["from_unit"]]["file"],
                     tuple(r["site"]["syntax_path"]), r["layer"])
                    for r in package["relations"]}
    reject(actual_sites != expected_sites or len(package["relations"]) != len(expected_sites),
           "relationship_site_denominator")
    return package
