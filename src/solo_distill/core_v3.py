"""Schema 3.0 不可变结构封印；与任务证据及旧 V2 路径分离。

摘要检查只证明规范结构与身份绑定，不能代替可信源码认证。
公开认证须显式提供完整源码、项目、快照及包外核心清单；
不接受包自报已认证，不执行来源，不读取参考答案或访问网关。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .macro_v3 import canonical, identity
from .package_v3 import PACKAGE_KEYS, validate_package
from .snapshot_v2 import snapshot_scope

RENDER_VERSION = "offline-core-v3-1"
POLICY = "adaptive-backpressure-v1"
WIRE_LIMIT = 22016
CORE_KEYS = {"render_version", "input_id", "policy_id", "scope", "selection_id",
             "package", "wire_limit", "core_id"}


def _source_path(path):
    return (type(path) is str and path.endswith(".py") and "\\" not in path
            and not any(part in {"", ".", ".."} for part in path.split("/")))


def _sha256(value):
    return (type(value) is str and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def input_identity(raw_files):
    """绑定实际完整字节范围；路径无效或空范围不能形成可信身份。"""
    if (type(raw_files) is not dict or not raw_files
            or any(not _source_path(path) or type(raw) is not bytes
                   for path, raw in raw_files.items())):
        raise ValueError("v3_invalid_trusted_source_scope")
    return identity("input-v3", [[path, hashlib.sha256(raw).hexdigest()]
                                 for path, raw in sorted(raw_files.items())])


@dataclass(frozen=True)
class SealedCore:
    payload: bytes
    wire_encoding: str = "json"

    @property
    def document(self):
        return _load_payload(self.payload)

    @property
    def core_id(self):
        return self.document["core_id"]


def _load_payload(raw):
    if type(raw) is not bytes:
        raise ValueError("v3_core_bytes_required")

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("v3_core_duplicate_json_key")
            value[key] = item
        return value

    def reject(value):
        raise ValueError("v3_core_noninteger_json_number")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_float=reject, parse_constant=reject)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("v3_core_invalid_json") from error


def inspect_seal(core):
    """检查规范封印及跨层身份，不宣称完成源码或独立选择认证。"""
    if (type(core) is not SealedCore or type(core.payload) is not bytes
            or type(core.wire_encoding) is not str or core.wire_encoding != "json"):
        raise ValueError("v3_invalid_core_or_encoding")
    value = core.document
    if (type(value) is not dict or set(value) != CORE_KEYS
            or value["render_version"] != RENDER_VERSION
            or value["policy_id"] != POLICY
            or type(value["wire_limit"]) is not int or value["wire_limit"] != WIRE_LIMIT
            or not _sha256(value["input_id"]) or not _sha256(value["core_id"])
            or type(value["selection_id"]) is not str or not value["selection_id"]
            or canonical(value) != core.payload):
        raise ValueError("v3_core_fields_or_canonical_encoding")
    unsigned = {key: item for key, item in value.items() if key != "core_id"}
    if value["core_id"] != identity("core-v3", unsigned):
        raise ValueError("v3_core_digest_conflict")
    package = value["package"]
    if (type(package) is not dict or set(package) != PACKAGE_KEYS
            or type(package["contract_version"]) is not int
            or package["contract_version"] != 3 or package["kind"] != "knowledge_package"
            or package["scenario"] != "A" or package["status"] != "complete"
            or package["failures"] != []
            or any(type(package[key]) is not str or not package[key]
                   for key in ("project_id", "snapshot_id"))):
        raise ValueError("v3_core_package_version_or_binding")
    macro = package["macro"]
    local = package["local_closures"]
    if (type(macro) is not dict or type(macro.get("schema_version")) is not int
            or macro["schema_version"] != 3
            or any(macro.get(key) != package[key] for key in ("project_id", "snapshot_id"))
            or type(local) is not dict or local.get("selection_id") != value["selection_id"]):
        raise ValueError("v3_core_cross_layer_identity_conflict")
    compatibility = local.get("compatibility_package")
    if (type(compatibility) is not dict
            or type(compatibility.get("contract_version")) is not int
            or compatibility["contract_version"] != 2
            or compatibility.get("kind") != "knowledge_package"
            or compatibility.get("scenario") != "A"
            or any(compatibility.get(key) != package[key]
                   for key in ("project_id", "snapshot_id"))):
        raise ValueError("v3_core_local_compatibility_conflict")
    scope = value["scope"]
    files = macro.get("files")
    if (type(scope) is not list or not scope or any(not _source_path(path) for path in scope)
            or scope != sorted(set(scope)) or type(files) is not list
            or len(files) != len(scope)
            or any(type(row) is not list or len(row) != 5
                   or not _source_path(row[1]) or not _sha256(row[2]) for row in files)
            or [row[1] for row in files] != scope):
        raise ValueError("v3_core_scope_or_macro_files_conflict")
    if value["input_id"] != identity("input-v3", [[row[1], row[2]] for row in files]):
        raise ValueError("v3_core_input_identity_conflict")
    return value


@snapshot_scope
def make_seal(package, raw_files, *, project_id, snapshot_id,
              required_core_unit_ids, selection_id, policy_id=POLICY,
              wire_encoding="json", tick=None):
    """先从可信输入认证原包，再封印；不通过重新构建修复伪造输入。"""
    tick = tick if tick is not None else (lambda: None)
    tick()
    if (type(policy_id) is not str or policy_id != POLICY
            or type(wire_encoding) is not str or wire_encoding != "json"):
        raise ValueError("v3_core_unapproved_policy_or_encoding")
    validate_package(package, raw_files, project_id=project_id,
        snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
        selection_id=selection_id, tick=tick)
    document = {"render_version": RENDER_VERSION, "input_id": input_identity(raw_files),
                "policy_id": policy_id, "scope": sorted(raw_files),
                "selection_id": selection_id, "package": package, "wire_limit": WIRE_LIMIT}
    document["core_id"] = identity("core-v3", document)
    core = SealedCore(canonical(document), wire_encoding)
    inspect_seal(core)
    tick()
    return core


@snapshot_scope
def authenticate_seal(core, raw_files, *, project_id, snapshot_id,
                      required_core_unit_ids, selection_id, tick=None):
    """完整来源和外部核心复算；摘要一致本身不是可信输入认证。"""
    tick = tick if tick is not None else (lambda: None)
    tick()
    value = inspect_seal(core)
    if (value["input_id"] != input_identity(raw_files)
            or value["scope"] != sorted(raw_files) or value["selection_id"] != selection_id):
        raise ValueError("v3_stale_core_input_or_selection")
    validate_package(value["package"], raw_files, project_id=project_id,
        snapshot_id=snapshot_id, required_core_unit_ids=required_core_unit_ids,
        selection_id=selection_id, tick=tick)
    tick()
    return value
