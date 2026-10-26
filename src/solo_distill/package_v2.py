"""Explicit offline V2 public registries; no V1 transport or model activation."""
from __future__ import annotations

import copy
import json
from typing import Any

from .syntax_v2 import PARSER_VERSION, body_text, extract_units, identity

TABLES = ("files", "units", "owners", "sources", "conditions", "role_proofs",
          "facts", "relations", "obligations")
ROLES = ("entry", "assembly", "state", "persistence", "boundary")
LEVELS = ("unknown", "sourced_inference", "direct_static")


def contains(parent: dict, child: dict) -> bool:
    route, nested = parent["syntax_path"], child["syntax_path"]
    return (nested[:len(route)] == route and parent["raw_start"] <= child["raw_start"]
            and child["raw_end"] <= parent["raw_end"])


def build_package(project_id: str, snapshot_id: str, raw_files: dict[str, bytes],
                  selected: list[str], *, scenario: str = "A") -> dict[str, Any]:
    parsed = {path: extract_units(path, raw) for path, raw in sorted(raw_files.items())}
    available = {unit["id"]: (path, index) for path, value in parsed.items()
                 for index, unit in enumerate(value["units"])}
    if not selected or len(set(selected)) != len(selected) or any(key not in available for key in selected):
        raise ValueError("invalid_selected_units")
    wanted = set(selected)
    # Condition origin units must have complete bodies too, before any cost accounting.
    pending = list(wanted)
    while pending:
        key = pending.pop()
        path, index = available[key]
        value = parsed[path]
        for condition in value["units"][index]["conditions"]:
            origin = value["units"][value["conditions"][condition]["unit"]]["id"]
            if origin not in wanted:
                wanted.add(origin)
                pending.append(origin)
    owners = [(path, index) for key in sorted(wanted) for path, index in [available[key]]]
    owners = [(path, index) for path, index in owners if not any(
        other_path == path and other_index != index and contains(
            parsed[path]["units"][other_index], parsed[path]["units"][index])
        for other_path, other_index in owners)]
    owners.sort(key=lambda pair: (pair[0], parsed[pair[0]]["units"][pair[1]]["raw_start"]))
    package = {"contract_version": 2, "kind": "knowledge_package", "parser_version": PARSER_VERSION,
               "project_id": project_id, "snapshot_id": snapshot_id, "scenario": scenario,
               "status": "complete", **{table: [] for table in TABLES}, "failures": [],
               "coverage": {"scan_files": len(raw_files), "body_units": 0, "semantic_supported": 0}}
    file_map, unit_map, owner_map = {}, {}, {}
    for path, value in parsed.items():
        file_map[path] = len(package["files"])
        package["files"].append(value["file"])
        for index, unit in enumerate(value["units"]):
            if any(owner_path == path and contains(value["units"][owner_index], unit)
                   for owner_path, owner_index in owners):
                unit_map[path, index] = len(package["units"])
                package["units"].append({**unit, "file": file_map[path], "conditions": []})
    for path, index in owners:
        unit = parsed[path]["units"][index]
        text = body_text(raw_files[path], parsed[path]["file"], unit)
        owner_map[path, index] = len(package["owners"])
        package["owners"].append({"id": identity("owner-v2", unit["id"]),
            "unit": unit_map[path, index], "file": file_map[path], "raw_start": unit["raw_start"],
            "raw_end": unit["raw_end"], "body_bytes": len(text.encode("utf-8")), "complete": True})
    condition_map = {}
    needed = {(path, condition) for path, index in unit_map
              for condition in parsed[path]["units"][index]["conditions"]}
    needed |= {(path, index) for path, value in parsed.items()
               for index, condition in enumerate(value["conditions"])
               if (path, condition["unit"]) in unit_map}
    for path, index in sorted(needed):
        item = parsed[path]["conditions"][index]
        condition_map[path, index] = len(package["conditions"])
        package["conditions"].append({**item, "unit": unit_map[path, item["unit"]],
                                      "source": unit_map[path, item["unit"]]})
    for (path, index), public_index in unit_map.items():
        unit = parsed[path]["units"][index]
        active = [condition_map[path, condition] for condition in unit["conditions"]]
        package["units"][public_index]["conditions"] = active
        owner = next(owner_map[owner_path, owner_index] for owner_path, owner_index in owners
                     if owner_path == path and contains(parsed[path]["units"][owner_index], unit))
        package["sources"].append({"id": identity("source-v2", unit["id"]), "file": file_map[path],
            "unit": public_index, "owner": owner, "conditions": active})
    package["coverage"]["body_units"] = len(package["owners"])
    validate_package(package, raw_files)
    return package


def derive_status(package: dict) -> str:
    if any(item["kind"] == "failed" for item in package["failures"]):
        return "failed"
    if package["failures"] or any(item["required"] and item["state"] == "blocked"
                                  for item in package["obligations"]):
        return "blocked"
    if not package["sources"]:
        return "blocked"
    if any(item["required"] and item["state"] != "satisfied" for item in package["obligations"]):
        return "partial"
    return "complete"


def proof_satisfies(package: dict, obligation: dict) -> bool:
    for support in obligation["supports"]:
        item = package[support["table"]][support["index"]]
        if (item["level"] != "unknown" and LEVELS.index(item["level"]) >= LEVELS.index(obligation["evidence"])
                and item["sources"] and (obligation["role"] is None or
                support["table"] == "role_proofs" and item["role"] == obligation["role"])):
            return True
    return False


def finalize(package: dict) -> dict:
    result = copy.deepcopy(package)
    for obligation in result["obligations"]:
        if proof_satisfies(result, obligation):
            obligation.update(state="satisfied", reason=None)
        elif obligation["state"] == "satisfied":
            obligation.update(state="unknown", reason="insufficient_evidence")
    result["status"] = derive_status(result)
    result["coverage"]["semantic_supported"] = sum(
        item["state"] == "satisfied" and item["evidence"] == "sourced_inference"
        for item in result["obligations"])
    return result


def validate_package(package: Any, raw_files: dict[str, bytes]) -> None:
    def require(test: bool, reason: str) -> None:
        if not test:
            raise ValueError(reason)

    def shape(item: Any, keys: set[str]) -> None:
        require(type(item) is dict and set(item) == keys, "invalid_object_fields")

    def string(value: Any) -> None:
        require(type(value) is str and bool(value) and len(value.encode("utf-8")) <= 4096, "invalid_string")

    def ref(value: Any, table: str, nullable: bool = False) -> None:
        require(nullable and value is None or type(value) is int and 0 <= value < len(package[table]),
                "invalid_reference:" + table)

    def refs(values: Any, table: str) -> None:
        require(type(values) is list, "references_not_list")
        for value in values:
            ref(value, table)
        require(len(values) == len(set(values)), "duplicate_reference")

    def level(value: Any) -> None:
        require(type(value) is str and value in LEVELS, "invalid_evidence_level")

    shape(package, set(TABLES) | {"contract_version", "kind", "parser_version", "project_id",
        "snapshot_id", "scenario", "status", "failures", "coverage"})
    require(type(package["contract_version"]) is int and package["contract_version"] == 2, "unsupported_version")
    require(package["kind"] == "knowledge_package" and package["parser_version"] == PARSER_VERSION, "unsupported_parser")
    string(package["project_id"])
    string(package["snapshot_id"])
    require(type(package["scenario"]) is str and package["scenario"] in {"A", "B"}, "invalid_scenario")
    identities = set()
    for table in TABLES:
        require(type(package[table]) is list, "table_not_list")
        for item in package[table]:
            require(type(item) is dict and "id" in item, "missing_identity")
            string(item["id"])
            require(item["id"] not in identities, "duplicate_identity")
            identities.add(item["id"])
    parsed = {}
    for index, file in enumerate(package["files"]):
        shape(file, {"id", "path", "sha256", "encoding", "parse_status"})
        string(file["path"])
        require(file["path"] in raw_files, "unauthorized_file")
        value = extract_units(file["path"], raw_files[file["path"]])
        require(file == value["file"], "file_identity_conflict")
        require(file["path"] not in parsed, "duplicate_file")
        parsed[file["path"]] = value
    unit_keys = {"id", "kind", "ast_type", "syntax_path", "qualified_name", "start", "end",
                 "char_start", "char_end", "raw_start", "raw_end", "conditions", "file"}
    actual_units = []
    for unit in package["units"]:
        shape(unit, unit_keys)
        ref(unit["file"], "files")
        refs(unit["conditions"], "conditions")
        for key in ("start", "end", "char_start", "char_end"):
            require(type(unit[key]) is list and len(unit[key]) == 2 and
                    all(type(item) is int for item in unit[key]), "invalid_position")
        require(all(type(unit[key]) is int for key in ("raw_start", "raw_end")), "invalid_byte_position")
        require(type(unit["syntax_path"]) is list and all(type(part) in {str, int} for part in unit["syntax_path"]), "invalid_syntax_path")
        file = package["files"][unit["file"]]
        expected = next((item for item in parsed[file["path"]]["units"] if item["id"] == unit["id"]), None)
        require(expected is not None and all(unit[key] == expected[key] for key in unit_keys - {"file", "conditions"}), "unit_identity_conflict")
        actual_units.append(expected)
    for owner in package["owners"]:
        shape(owner, {"id", "unit", "file", "raw_start", "raw_end", "body_bytes", "complete"})
        ref(owner["unit"], "units")
        ref(owner["file"], "files")
        unit = package["units"][owner["unit"]]
        file = package["files"][owner["file"]]
        require(owner["file"] == unit["file"] and owner["complete"] is True, "incomplete_owner")
        require(all(type(owner[key]) is int for key in ("raw_start", "raw_end", "body_bytes")), "invalid_owner_cost")
        require(owner["raw_start"] == unit["raw_start"] and owner["raw_end"] == unit["raw_end"] and
                owner["body_bytes"] == len(body_text(raw_files[file["path"]], file, unit).encode("utf-8")) and
                owner["id"] == identity("owner-v2", unit["id"]), "owner_identity_conflict")
    require(len({item["unit"] for item in package["owners"]}) == len(package["owners"]), "duplicate_owner")
    require(len(package["sources"]) == len(package["units"]), "missing_unit_sources")
    for index, source in enumerate(package["sources"]):
        shape(source, {"id", "file", "unit", "owner", "conditions"})
        ref(source["file"], "files")
        ref(source["unit"], "units")
        ref(source["owner"], "owners")
        refs(source["conditions"], "conditions")
        unit = package["units"][source["unit"]]
        owner = package["owners"][source["owner"]]
        require(source["unit"] == index and source["file"] == unit["file"] == owner["file"] and
                source["conditions"] == unit["conditions"] and contains(package["units"][owner["unit"]], unit) and
                source["id"] == identity("source-v2", unit["id"]), "source_or_alias_conflict")
        file = package["files"][unit["file"]]
        expected_conditions = [parsed[file["path"]]["conditions"][value]["id"] for value in actual_units[index]["conditions"]]
        require([package["conditions"][value]["id"] for value in unit["conditions"]] == expected_conditions, "condition_closure_conflict")
    for item in package["conditions"]:
        shape(item, {"id", "syntax_path", "field", "branch", "scope", "unit", "source"})
        ref(item["unit"], "units")
        ref(item["source"], "sources")
        unit = package["units"][item["unit"]]
        file = package["files"][unit["file"]]
        expected = next((value for value in parsed[file["path"]]["conditions"] if value["id"] == item["id"]), None)
        require(expected is not None and all(item[key] == expected[key] for key in {"id", "syntax_path", "field", "branch", "scope"}) and
                package["sources"][item["source"]]["unit"] == item["unit"] and
                parsed[file["path"]]["units"][expected["unit"]]["id"] == unit["id"], "forged_condition")
    for item in package["role_proofs"]:
        shape(item, {"id", "unit", "role", "rule", "level", "sources"})
        ref(item["unit"], "units")
        require(type(item["role"]) is str and item["role"] in ROLES, "invalid_role")
        string(item["rule"])
        level(item["level"])
        refs(item["sources"], "sources")
        require(all(package["sources"][value]["unit"] == item["unit"] for value in item["sources"]), "role_source_conflict")
        # Direct role claims are accepted only when recomputed from syntax by local rules.
        if item["level"] == "direct_static":
            from .evidence_v2 import syntax_roles
            unit = package["units"][item["unit"]]
            file = package["files"][unit["file"]]
            require((item["role"], item["rule"]) in syntax_roles(raw_files[file["path"]], unit), "forged_role_proof")
    for item in package["facts"]:
        shape(item, {"id", "text", "level", "sources", "relations", "conditions"})
        string(item["text"])
        level(item["level"])
        require(item["level"] != "direct_static", "model_fact_promoted")
        for table in ("sources", "relations", "conditions"):
            refs(item[table], table)
    for item in package["relations"]:
        shape(item, {"id", "from_unit", "to_unit", "layer", "direction", "conditions", "sources", "reason", "site"})
        shape(item["site"], {"syntax_path", "start", "end", "expression"})
        require(type(item["site"]["syntax_path"]) is list and all(type(part) in {str, int}
                for part in item["site"]["syntax_path"]), "invalid_relation_syntax_path")
        for key in ("start", "end"):
            require(type(item["site"][key]) is list and len(item["site"][key]) == 2
                    and all(type(part) is int for part in item["site"][key]), "invalid_relation_position")
        string(item["site"]["expression"])
        ref(item["from_unit"], "units")
        ref(item["to_unit"], "units", True)
        require(type(item["layer"]) is str and item["layer"] in {"call", "import", "inheritance"}, "invalid_relation_layer")
        require(item["direction"] == "caller_to_dependency", "invalid_relation_direction")
        refs(item["conditions"], "conditions")
        refs(item["sources"], "sources")
        require(bool(item["sources"]), "unsourced_relation")
        if item["to_unit"] is None:
            string(item["reason"])
        else:
            require(item["reason"] is None, "known_relation_with_unknown_reason")
    if package["relations"]:
        from .graph_v2 import extract_relations
        expected_relations = {item["id"]: item for item in extract_relations(package, raw_files)}
        require(all(item == expected_relations.get(item["id"]) for item in package["relations"]),
                "relation_binding_or_site_conflict")
    for item in package["obligations"]:
        shape(item, {"id", "required", "evidence", "role", "state", "supports", "reason"})
        require(type(item["required"]) is bool, "invalid_required")
        level(item["evidence"])
        require(item["role"] is None or type(item["role"]) is str and item["role"] in ROLES, "invalid_obligation_role")
        require(type(item["state"]) is str and item["state"] in {"satisfied", "unknown", "unread", "blocked"}, "invalid_obligation_state")
        require(type(item["supports"]) is list, "invalid_supports")
        supports = set()
        for support in item["supports"]:
            shape(support, {"table", "index"})
            require(type(support["table"]) is str and support["table"] in {"facts", "role_proofs"}, "invalid_support_type")
            ref(support["index"], support["table"])
            key = support["table"], support["index"]
            require(key not in supports, "duplicate_support")
            supports.add(key)
        require((item["state"] == "satisfied") == proof_satisfies(package, item), "obligation_level_conflict")
        if item["state"] == "satisfied":
            require(item["reason"] is None, "satisfied_with_failure_reason")
        else:
            string(item["reason"])
    require(type(package["failures"]) is list, "invalid_failures")
    for item in package["failures"]:
        shape(item, {"kind", "reason"})
        require(type(item["kind"]) is str and item["kind"] in {"blocked", "failed"}, "invalid_failure_kind")
        string(item["reason"])
    shape(package["coverage"], {"scan_files", "body_units", "semantic_supported"})
    require(all(type(value) is int and value >= 0 for value in package["coverage"].values()), "invalid_coverage")
    require(package["coverage"]["scan_files"] == len(raw_files) and package["coverage"]["body_units"] == len(package["owners"]) and
            package["coverage"]["semantic_supported"] == sum(item["state"] == "satisfied" and item["evidence"] == "sourced_inference" for item in package["obligations"]), "coverage_conflict")
    require(type(package["status"]) is str and package["status"] == derive_status(package), "model_declared_status_conflict")


def decode_package(raw: bytes, raw_files: dict[str, bytes]) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value
    def reject(value):
        raise ValueError("nonfinite_json")
    package = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject)
    if type(package) is not dict or type(package.get("contract_version")) is not int:
        raise ValueError("invalid_version")
    if package["contract_version"] == 2:
        validate_package(package, raw_files)
    elif package["contract_version"] != 1:
        raise ValueError("unsupported_version")
    # V1 is returned unchanged to its existing validator; no V2 reinterpretation.
    return package
