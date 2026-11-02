"""独立候选解析预期；不导入生产解析器、恢复器或网关，不修补输出。"""
from __future__ import annotations

import json


class SyntaxRejection(ValueError):
    reason = "invalid_candidate_json_syntax"

    def __init__(self, output, error):
        super().__init__(self.reason)
        self.output = output
        self.details = {
            "error_type": "JSONDecodeError", "message": error.msg,
            "position": error.pos, "line": error.lineno, "column": error.colno,
            "parsed_text_characters": len(error.doc),
        }


def parse_candidate(output):
    """仅 JSON 文法失败类型化；围栏、重复键及非有限数保持硬拒绝。"""
    if type(output) is not str or not output.strip():
        raise ValueError("nonempty_candidate_text_required")
    body = output.strip()
    if body.startswith("```"):
        rows = body.splitlines()
        if len(rows) < 2 or rows[0] not in ("```", "```json") or rows[-1] != "```":
            raise ValueError("incomplete_or_unknown_fence")
        body = "\n".join(rows[1:-1])

    def unique_pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("non_finite_json")

    try:
        return json.loads(body, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise SyntaxRejection(output, error) from error
