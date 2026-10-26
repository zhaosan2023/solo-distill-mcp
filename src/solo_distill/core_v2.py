"""Explicit, in-memory V2 closure slicing and authenticated immutable cores."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from .package_v2 import TABLES, finalize, validate_package

RENDER_VERSION = "offline-core-v2-1"
LIMITS = {"A": 22016, "B": 12288}
INSTRUCTIONS = b"Use authenticated sources; static syntax is not runtime or semantic proof.\n"


class CoreBlocked(ValueError):
    pass


def canonical(value) -> bytes:
    """Our restricted JSON domain, not a general cross-language standard."""
    def check(item, depth=0):
        if depth > 64:
            raise ValueError("json_depth")
        if item is None or type(item) in (bool, int, str):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
        else:
            raise ValueError("restricted_json_type")
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(namespace, value):
    return hashlib.sha256(namespace.encode() + b":" + canonical(value)).hexdigest()


def input_identity(raw_files):
    return digest("input-v2", [[path, hashlib.sha256(raw).hexdigest()]
                               for path, raw in sorted(raw_files.items())])


def canonical_package(package):
    """Stable registry order with reference remapping; condition nesting is retained."""
    value = copy.deepcopy(package)
    orders = {table: sorted(range(len(value[table])), key=lambda i: value[table][i]["id"])
              for table in TABLES}
    # Public sources and units share positional correspondence in the E1 contract.
    orders["sources"] = sorted(range(len(value["sources"])),
                               key=lambda i: value["units"][value["sources"][i]["unit"]]["id"])
    maps = {table: {old: new for new, old in enumerate(order)} for table, order in orders.items()}
    for table, order in orders.items():
        value[table] = [value[table][old] for old in order]

    def one(item, key, table):
        if item[key] is not None:
            item[key] = maps[table][item[key]]

    def many(item, key, table, ordered=False):
        item[key] = [maps[table][index] for index in item[key]]
        if not ordered:
            item[key].sort()

    for item in value["units"]:
        one(item, "file", "files")
        many(item, "conditions", "conditions", True)
    for item in value["owners"]:
        one(item, "unit", "units")
        one(item, "file", "files")
    for item in value["sources"]:
        for key, table in (("unit", "units"), ("file", "files"), ("owner", "owners")):
            one(item, key, table)
        many(item, "conditions", "conditions", True)
    for item in value["conditions"]:
        one(item, "unit", "units")
        one(item, "source", "sources")
    for item in value["role_proofs"]:
        one(item, "unit", "units")
        many(item, "sources", "sources")
    for item in value["facts"]:
        for table in ("sources", "relations", "conditions"):
            many(item, table, table)
    for item in value["relations"]:
        one(item, "from_unit", "units")
        one(item, "to_unit", "units")
        many(item, "sources", "sources")
        many(item, "conditions", "conditions", True)
    for item in value["obligations"]:
        for support in item["supports"]:
            support["index"] = maps[support["table"]][support["index"]]
        item["supports"].sort(key=lambda support: (support["table"], support["index"]))
    value["failures"].sort(key=lambda item: (item["kind"], item["reason"]))
    # E2 relation identities include positional condition references. Re-derive
    # that coordinate-dependent identity after table remapping, not the syntax.
    for relation in value["relations"]:
        origin = value["units"][relation["from_unit"]]
        active = list(origin["conditions"])
        route = relation["site"]["syntax_path"]
        for index, condition in enumerate(value["conditions"]):
            unit = value["units"][condition["unit"]]
            branch = condition["syntax_path"] + [condition["field"]]
            if unit["file"] == origin["file"] and route[:len(branch)] == branch and index not in active:
                active.append(index)
        relation["conditions"] = active
        target = None if relation["to_unit"] is None else value["units"][relation["to_unit"]]["id"]
        relation["id"] = digest("relation-v2", [origin["id"], relation["site"],
            relation["layer"], target, active, relation["reason"]])
    order = sorted(range(len(value["relations"])), key=lambda i: value["relations"][i]["id"])
    relation_map = {old: new for new, old in enumerate(order)}
    value["relations"] = [value["relations"][i] for i in order]
    for fact in value["facts"]:
        fact["relations"] = sorted(relation_map[i] for i in fact["relations"])
    return value


def slice_package(package, raw_files, roots):
    """Transitive typed closure. Complete owners pull all authenticated child aliases."""
    validate_package(package, raw_files)
    by_id = {table: {item["id"]: i for i, item in enumerate(package[table])} for table in TABLES}
    keep = {table: set() for table in TABLES}
    pending = []

    def add(table, index):
        if index is not None and index not in keep[table]:
            keep[table].add(index)
            pending.append((table, index))

    for table, identity in roots:
        if table not in TABLES or identity not in by_id[table]:
            raise ValueError("unknown_slice_root")
        add(table, by_id[table][identity])
    # The file inventory and all required obligations are mandatory, never a denominator trick.
    for i in range(len(package["files"])):
        add("files", i)
    for i, item in enumerate(package["obligations"]):
        if item["required"]:
            add("obligations", i)
    unit_source = {item["unit"]: i for i, item in enumerate(package["sources"])}
    while pending:
        table, index = pending.pop()
        item = package[table][index]
        if table == "units":
            add("files", item["file"])
            add("sources", unit_source[index])
            for condition in item["conditions"]:
                add("conditions", condition)
        elif table == "sources":
            add("units", item["unit"])
            add("owners", item["owner"])
            for condition in item["conditions"]:
                add("conditions", condition)
        elif table == "owners":
            parent = package["units"][item["unit"]]
            for i, unit in enumerate(package["units"]):
                if unit["file"] == item["file"] and parent["raw_start"] <= unit["raw_start"] and unit["raw_end"] <= parent["raw_end"] and unit["syntax_path"][:len(parent["syntax_path"])] == parent["syntax_path"]:
                    add("units", i)
            for i, condition in enumerate(package["conditions"]):
                if condition["unit"] in keep["units"]:
                    add("conditions", i)
        elif table == "conditions":
            add("units", item["unit"])
            add("sources", item["source"])
        elif table == "role_proofs":
            add("units", item["unit"])
            for source in item["sources"]:
                add("sources", source)
        elif table == "facts":
            for target in ("sources", "relations", "conditions"):
                for reference in item[target]:
                    add(target, reference)
        elif table == "relations":
            add("units", item["from_unit"])
            add("units", item["to_unit"])
            for target in ("sources", "conditions"):
                for reference in item[target]:
                    add(target, reference)
        elif table == "obligations":
            for support in item["supports"]:
                add(support["table"], support["index"])
    # Use canonical remapping on a temporary index-preserving selection.
    result = copy.deepcopy(package)
    maps = {table: {old: new for new, old in enumerate(sorted(indices))}
            for table, indices in keep.items()}
    for table in TABLES:
        result[table] = [copy.deepcopy(package[table][i]) for i in sorted(keep[table])]
    def ref(item, key, table):
        if item[key] is not None:
            item[key] = maps[table][item[key]]
    def refs(item, key, table):
        item[key] = [maps[table][i] for i in item[key]]
    for item in result["units"]:
        ref(item, "file", "files")
        refs(item, "conditions", "conditions")
    for item in result["owners"]:
        ref(item, "unit", "units")
        ref(item, "file", "files")
    for item in result["sources"]:
        for key, target in (("unit", "units"), ("file", "files"), ("owner", "owners")):
            ref(item, key, target)
        refs(item, "conditions", "conditions")
    for item in result["conditions"]:
        ref(item, "unit", "units")
        ref(item, "source", "sources")
    for item in result["role_proofs"]:
        ref(item, "unit", "units")
        refs(item, "sources", "sources")
    for item in result["facts"]:
        for target in ("sources", "relations", "conditions"):
            refs(item, target, target)
    for item in result["relations"]:
        ref(item, "from_unit", "units")
        ref(item, "to_unit", "units")
        refs(item, "sources", "sources")
        refs(item, "conditions", "conditions")
    for item in result["obligations"]:
        for support in item["supports"]:
            support["index"] = maps[support["table"]][support["index"]]
    result["coverage"]["body_units"] = len(result["owners"])
    result = canonical_package(finalize(result))
    validate_package(result, raw_files)
    return result


@dataclass(frozen=True)
class SealedCore:
    payload: bytes

    @property
    def document(self):
        # A detached view cannot mutate the sealed bytes.
        return json.loads(self.payload)

    @property
    def prefix(self):
        return INSTRUCTIONS + self.payload + b"\n"

    @property
    def core_id(self):
        return self.document["core_id"]


def make_seal(package, raw_files, policy_id):
    if type(policy_id) is not str or not policy_id or len(policy_id.encode()) > 128:
        raise ValueError("invalid_policy_identity")
    # Canonicalization is a coordinate transform, never a forgery repair path.
    validate_package(package, raw_files)
    package = canonical_package(package)
    validate_package(package, raw_files)
    document = {"render_version": RENDER_VERSION, "input_id": input_identity(raw_files),
                "policy_id": policy_id, "scope": sorted(raw_files), "package": package,
                "wire_limit": LIMITS[package["scenario"]]}
    document["core_id"] = digest("core-v2", document)
    return SealedCore(canonical(document))


def inspect_seal(core):
    if type(core) is not SealedCore or type(core.payload) is not bytes:
        raise ValueError("invalid_seal")
    value = core.document
    if set(value) != {"render_version", "input_id", "policy_id", "scope", "package", "wire_limit", "core_id"} or value["render_version"] != RENDER_VERSION or canonical(value) != core.payload:
        raise ValueError("invalid_core_fields_or_encoding")
    unsigned = {key: item for key, item in value.items() if key != "core_id"}
    if value["core_id"] != digest("core-v2", unsigned) or type(value["wire_limit"]) is not int or value["wire_limit"] != LIMITS[value["package"]["scenario"]]:
        raise ValueError("core_identity_conflict")
    return value


def authenticate_seal(core, raw_files, *, policy_id=None):
    value = inspect_seal(core)
    if value["input_id"] != input_identity(raw_files) or value["scope"] != sorted(raw_files):
        raise ValueError("stale_core_input")
    if policy_id is not None and value["policy_id"] != policy_id:
        raise ValueError("stale_core_policy")
    validate_package(value["package"], raw_files)
    if canonical_package(value["package"]) != value["package"]:
        raise ValueError("noncanonical_registry_order")
    return value


def form_core(package, raw_files, *, required_sources=None, optional_groups=(), policy_id="lifecycle-v2-1"):
    """Reserve the worst admitted ID and observations BEFORE stable optional selection."""
    from .wire_v2 import reserved_size
    validate_package(package, raw_files)
    if required_sources is None:
        required_sources = [item["id"] for item in package["sources"]]
    if len(set(required_sources)) != len(required_sources):
        raise ValueError("duplicate_required_source")
    roots = [("sources", identity) for identity in required_sources]
    groups = []
    seen = set()
    for group_id, references in optional_groups:
        if type(group_id) is not str or not group_id or group_id in seen:
            raise ValueError("invalid_optional_group")
        seen.add(group_id)
        groups.append((group_id, list(references)))
    # Validate even omitted groups; malformed optional data is not silently repaired.
    for _, references in groups:
        slice_package(package, raw_files, roots + references)
    selected = slice_package(package, raw_files, roots)
    if not selected["sources"]:
        raise CoreBlocked("minimum_required_sources_missing")
    core = make_seal(selected, raw_files, policy_id)
    if reserved_size(core) > LIMITS[package["scenario"]]:
        raise CoreBlocked("minimum_required_core_over_budget")
    for _, references in sorted(groups):
        candidate = slice_package(package, raw_files, roots + references)
        sealed = make_seal(candidate, raw_files, policy_id)
        if reserved_size(sealed) <= LIMITS[package["scenario"]]:
            roots += references
            core = sealed
    return core
