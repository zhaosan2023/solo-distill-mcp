"""定位已保存在线产物的三项锚点缺口；不重发，不修改冻结选择或门禁。

独立30秒前台窗口，只读认证资产及获准来源；独占保存诊断证据。
宏观符号存在、源码存在与实际POST包含完整正文分别记录，不互相替代。
"""
from __future__ import annotations

import ast
import json
import sys

import mainline_a_schema_v3_minimal_acceptance_20261102 as acceptance


def main():
    support = acceptance.support
    support.configure_minimal()
    guard = support.Guard(30)
    live = acceptance.binding()
    consumed = acceptance.load(support.PARENT + "/" + acceptance.PREFIX + ".consume.receipt.json")
    acceptance.require(consumed["binding"] == live["binding"], "diagnostic_binding")
    publication = acceptance.strict_json(acceptance.checked_asset(
        consumed["assets"]["publication"]).decode("utf-8"))
    post_raw = acceptance.checked_asset(live["assets"]["post"])
    post = acceptance.strict_json(post_raw.decode("utf-8"))
    payload = acceptance.strict_json(post["messages"][1]["content"])
    frozen = acceptance.load(support.PARENT + "/preflight-1/frozen-source-and-selection.json")
    raw_files = support.current_source(frozen, guard)
    anchor_raw = support.read("tests/fastcode_acceptance.json")
    anchors = acceptance.strict_json(anchor_raw.decode("utf-8"))
    package = publication["core"]["package"]
    syntax = package["local_closures"]["compatibility_package"]
    _, checks = acceptance.locate_anchors(syntax, anchors)
    details = []
    for check in checks:
        if check["body_coverage"]:
            continue
        guard.check()
        path, symbol = check["path"], check["symbol"]
        text = raw_files[path].decode("utf-8")
        lines = text.splitlines(keepends=True)
        definitions = []

        def walk(node, names):
            guard.check()
            defining = isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            names = names + (node.name,) if defining else names
            if defining and ".".join(names) == symbol:
                definitions.append({"start": node.lineno, "end": node.end_lineno,
                    "kind": type(node).__name__,
                    "covers_anchor": node.lineno <= check["lines"][0]
                        and node.end_lineno >= check["lines"][1]})
            for child in ast.iter_child_nodes(node):
                walk(child, names)

        walk(ast.parse(text, filename=path), ())
        macro = package["macro"]
        macro_matches = [{"symbol_id": row[0], "kind": row[3], "start": row[7], "end": row[8]}
            for row in macro["symbols"]
            if macro["files"][row[1]][1] == path and row[4] == symbol]
        sent = [{"symbol": item["symbol"], "source_id": item["source_id"],
                 "alias_source_ids": item["alias_source_ids"],
                 "body_utf8_bytes": len(item["text"].encode("utf-8"))}
                for item in payload["sources"] if item["path"] == path]
        details.append({"id": check["id"], "path": path, "symbol": symbol,
            "anchor_lines": check["lines"], "local_body_coverage": False,
            "source_file_sha256": support.digest(raw_files[path]),
            "source_definitions": definitions, "macro_symbol_matches": macro_matches,
            "actual_post_sources_in_file": sent,
            "selected_local_file": any(item["path"] == path for item in syntax["files"]),
            "source_excerpt": "".join(lines[check["lines"][0] - 1:check["lines"][1]])})
    acceptance.require(support.current_source(frozen, guard) == raw_files
        and acceptance.binding() == live
        and support.read("tests/fastcode_acceptance.json") == anchor_raw,
        "diagnostic_inputs_changed")
    result = {"status": "blocked_anchor_gap_diagnosed_not_gate_pass",
        "execution_instance": support.INSTANCE, "request_id": support.REQUEST_ID,
        "binding": live["binding"], "post_sha256": support.digest(post_raw),
        "anchor_sha256": support.digest(anchor_raw), "required_anchors": len(checks),
        "local_anchors_located": sum(item["body_coverage"] for item in checks),
        "missing": details, "human_review": "not_proven",
        "online_replays": 0, "production_changes": 0,
        "adr_promotions": 0, "archive_operations": 0,
        "limitation": "诊断源码与宏观符号不补写已发送正文，也不证明语义通过。",
        "elapsed_seconds": support.time.monotonic() - guard.started}
    asset = support.save("phase5-minimal-anchor-gap-20261102T025148Z.receipt.json", result, guard)
    print(json.dumps({"status": result["status"], "receipt": asset,
        "missing": [{key: item[key] for key in ("id", "path", "symbol", "anchor_lines",
            "source_definitions", "macro_symbol_matches", "actual_post_sources_in_file",
            "selected_local_file")} for item in details],
        "elapsed_seconds": support.time.monotonic() - guard.started}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "blocked", "error": type(error).__name__,
            "reason": str(error)[:240]}, ensure_ascii=False), file=sys.stderr, flush=True)
        raise SystemExit(2)
