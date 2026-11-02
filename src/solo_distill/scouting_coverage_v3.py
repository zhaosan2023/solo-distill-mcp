"""显式新选择版本的通用问题义务与可调用正文语法判据。

不读取参考答案、锚点或独立实现。只描述静态语法，不证明运行、
异常可达性、业务限额有效性或人工审核；旧选择不调用本规则。
"""
from __future__ import annotations

import ast

COVERAGE_VERSION = "question-scout-v2"
_CALLABLE = (ast.FunctionDef, ast.AsyncFunctionDef)
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_GENERATION = {"generate", "generation", "generator", "answer"}


def _body_nodes(body, tick):
    """按正文顺序遍历，嵌套定义属于另一可调用所有者。"""
    pending = list(reversed(body))
    while pending:
        tick()
        node = pending.pop()
        if isinstance(node, _NESTED):
            continue
        yield node
        pending.extend(reversed(tuple(ast.iter_child_nodes(node))))


def coverage_roles(node, tokenize, tick):
    """生成、限额、生成异常及解析分派的公开语法见证。

    生成函数自身名称须命中生成词，正文须实际调用生成或完成词。
    限额比较须同时具有输入族和限额族标识符；异常须有处理器且
    try 主体含生成调用。解析函数须有至少两个比较分支及两个
    return-call。字符串、注释和嵌套定义不提供这些新增义务。
    """
    if not isinstance(node, _CALLABLE):
        return set()
    nodes = tuple(_body_nodes(node.body, tick))

    def is_generation_call(item):
        return (isinstance(item, ast.Call)
                and bool(tokenize(ast.unparse(item.func))
                         & (_GENERATION | {"completion", "complete"})))

    roles = set()
    if tokenize(node.name) & _GENERATION and any(is_generation_call(item) for item in nodes):
        roles.add("generation_entry")
        for item in nodes:
            tick()
            if isinstance(item, ast.Compare):
                identifiers = []
                for child in ast.walk(item):
                    tick()
                    if isinstance(child, ast.Name):
                        identifiers.append(child.id)
                    elif isinstance(child, ast.Attribute):
                        identifiers.append(child.attr)
                words = tokenize(" ".join(identifiers))
                if (words & {"token", "tokens", "context", "input"}
                        and words & {"limit", "budget", "max", "available"}):
                    roles.add("generation_input_limit")
            if isinstance(item, (ast.Try, ast.TryStar)) and item.handlers:
                if any(is_generation_call(child) for child in _body_nodes(item.body, tick)):
                    roles.add("generation_failure")
    if tokenize(node.name) & {"parse", "parser", "parsing"}:
        branch_count = sum(isinstance(item, ast.If) and isinstance(item.test, ast.Compare)
                           for item in nodes)
        return_call_count = sum(isinstance(item, ast.Return) and isinstance(item.value, ast.Call)
                                for item in nodes)
        if branch_count >= 2 and return_call_count >= 2:
            roles.add("parser_dispatch")
    tick()
    return roles


def coverage_duties(terms):
    """仅维度问题中的词族启用新增子义务，不使用维度标识符。"""
    duties = []
    if terms & _GENERATION:
        duties.append("generation_entry")
        if terms & {"limit", "limits", "budget", "context", "input", "token", "tokens"}:
            duties.append("generation_input_limit")
        if terms & {"failure", "error", "errors", "exception", "exceptions"}:
            duties.append("generation_failure")
    if (terms & {"parse", "parser", "parsing"}
            and terms & {"support", "route", "routing", "dispatch", "language", "languages", "boundaries"}):
        duties.append("parser_dispatch")
    return duties
