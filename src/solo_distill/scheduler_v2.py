"""Bounded deterministic lifecycle selection. No model, I/O or wall-clock ranking.

Mandatory alternatives reserve their whole dependency closure before optional
fill. Each complete syntax owner is charged once; replacements commit atomically.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Iterable

ALGORITHM_VERSION = "lifecycle-v2-1"
MAX_STATES = 4096
ROLE_ORDER = ("entry", "assembly", "state", "persistence", "boundary")
PIPELINES = ("skeleton", "context", "exception")


@dataclass(frozen=True)
class Candidate:
    id: str
    path: str
    syntax_path: tuple[str | int, ...]
    raw_start: int
    raw_end: int
    body_bytes: int
    pipeline: str
    roles: tuple[str, ...] = ("boundary",)
    signal: int = 0
    dependencies: tuple[str, ...] = ()

    def __post_init__(self):
        if (type(self.id) is not str or not self.id or type(self.path) is not str
                or not self.path or self.path.startswith("/") or "\\" in self.path
                or any(part in {"", ".", ".."} for part in self.path.split("/"))
                or type(self.syntax_path) is not tuple or not self.syntax_path
                or any(type(part) not in {str, int} for part in self.syntax_path)
                or any(type(value) is not int for value in (self.raw_start, self.raw_end, self.body_bytes, self.signal))
                or not 0 <= self.raw_start < self.raw_end or self.body_bytes <= 0 or self.signal < 0
                or self.pipeline not in PIPELINES or not self.roles
                or len(set(self.roles)) != len(self.roles) or any(role not in ROLE_ORDER for role in self.roles)
                or type(self.dependencies) is not tuple or any(type(key) is not str for key in self.dependencies)
                or len(set(self.dependencies)) != len(self.dependencies)):
            raise ValueError("invalid_candidate")
        if self.pipeline == "skeleton" and not any(role != "boundary" for role in self.roles):
            raise ValueError("auxiliary_cannot_be_skeleton")

    def contains(self, other: Candidate) -> bool:
        return (self.path == other.path and other.syntax_path[:len(self.syntax_path)] == self.syntax_path
                and self.raw_start <= other.raw_start and other.raw_end <= self.raw_end)


@dataclass(frozen=True)
class Group:
    id: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    status: str
    reason: str | None
    owners: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()
    required_owners: tuple[str, ...] = ()
    chosen_groups: tuple[tuple[str, str], ...] = ()
    borrowed: int = 0
    counts: tuple[int, int, int] = (0, 0, 0)
    body_bytes: int = 0
    expanded_states: int = 0
    rejected_reasons: tuple[str, ...] = ()
    proof_scope: str = "none"
    optional_omissions: tuple[str, ...] = ()


class SearchStopped(Exception):
    pass


class Scheduler:
    def __init__(self, candidates: Iterable[Candidate], *, max_states: int = MAX_STATES,
                 body_budget: int = 98304, stopped: Callable[[], bool] | None = None):
        values = list(candidates)
        if len({item.id for item in values}) != len(values):
            raise ValueError("duplicate_candidate")
        if type(max_states) is not int or not 1 <= max_states <= MAX_STATES:
            raise ValueError("invalid_search_budget")
        if type(body_budget) is not int or not 1 <= body_budget <= 98304:
            raise ValueError("invalid_body_budget")
        self.candidates = {item.id: item for item in values}
        if any(key not in self.candidates for item in values for key in item.dependencies):
            raise ValueError("dangling_dependency")
        self.max_states, self.body_budget = max_states, body_budget
        self.stopped = stopped or (lambda: False)
        self.current: Selection | None = None

    def closure(self, keys: Iterable[str]) -> set[str]:
        selected, pending = set(), list(keys)
        while pending:
            key = pending.pop()
            if key not in self.candidates:
                raise ValueError("candidate_outside_snapshot")
            if key not in selected:
                selected.add(key)
                pending.extend(self.candidates[key].dependencies)
        return selected

    def merge(self, keys: Iterable[str]) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
        keys = self.closure(keys)
        # A containing selected parent owns true descendants, never a whole module.
        owners = [key for key in keys if not any(other != key and self.candidates[other].contains(self.candidates[key])
                                                  for other in keys)]
        owners.sort(key=self.position)
        aliases = tuple((key, next(owner for owner in owners if self.candidates[owner].contains(self.candidates[key])))
                        for key in sorted(keys))
        return tuple(owners), aliases

    def position(self, key: str):
        item = self.candidates[key]
        return item.path, item.raw_start, item.raw_end, item.id

    def accounting(self, keys: Iterable[str], required_keys: set[str]):
        owners, aliases = self.merge(keys)
        required = tuple(owner for owner in owners if any(key in required_keys and target == owner for key, target in aliases))
        counts = Counter(self.candidates[key].pipeline for key in owners)
        reserved = Counter(self.candidates[key].pipeline for key in required)
        cost = sum(self.candidates[key].body_bytes for key in owners)
        return owners, aliases, required, counts, reserved, cost

    def mandatory_fit(self, keys: set[str]):
        owners, aliases, required, counts, reserved, cost = self.accounting(keys, keys)
        borrowed = max(0, reserved["skeleton"] - 16)
        if (reserved["context"] > 12 or reserved["exception"] > 4
                or borrowed > 12 - reserved["context"]):
            return None, "policy_rejected"
        if len(owners) > 32 or cost > self.body_budget:
            return None, "capacity_proven"
        return (owners, aliases, required, counts, reserved, cost, borrowed), None

    def select(self, obligations: dict[str, tuple[Group, ...]], *,
               optional: Iterable[str] = (), adjacency: dict[str, set[str]] | None = None) -> Selection:
        """Return no partial mandatory selection on failure; preserve current state."""
        if type(obligations) is not dict:
            raise ValueError("invalid_obligations")
        group_ids = set()
        for obligation, alternatives in obligations.items():
            if type(obligation) is not str or not obligation or type(alternatives) is not tuple or not alternatives:
                raise ValueError("invalid_alternatives")
            for group in alternatives:
                if (not isinstance(group, Group) or not group.id or group.id in group_ids
                        or type(group.candidates) is not tuple or not group.candidates
                        or len(set(group.candidates)) != len(group.candidates)):
                    raise ValueError("invalid_group")
                group_ids.add(group.id)
                self.closure(group.candidates)
        optional = tuple(optional)
        self.closure(optional)
        adjacency = adjacency or {}
        if any(key not in self.candidates or type(neighbors) is not set or not neighbors <= self.candidates.keys()
               for key, neighbors in adjacency.items()):
            raise ValueError("invalid_determined_adjacency")
        order = sorted(obligations)
        expanded, exhausted, reasons = 0, False, set()
        found = None

        def search(index: int, keys: set[str], chosen: tuple[tuple[str, str], ...]):
            nonlocal expanded, exhausted, found
            if self.stopped():
                raise SearchStopped
            if expanded >= self.max_states:
                exhausted = True
                return False
            expanded += 1
            if index == len(order):
                fit, reason = self.mandatory_fit(keys)
                if fit is not None:
                    found = keys, chosen, fit
                    return True
                reasons.add(reason)
                return False
            obligation = order[index]
            for group in sorted(obligations[obligation], key=lambda item: item.id):
                if search(index + 1, keys | self.closure(group.candidates), chosen + ((obligation, group.id),)):
                    return True
                if exhausted:
                    return False
            return False

        try:
            search(0, set(), ())
        except SearchStopped:
            return Selection("blocked", "deadline_or_cancelled", expanded_states=expanded)
        if found is None:
            reason = "search_exhausted" if exhausted else next(iter(reasons)) if len(reasons) == 1 else "no_policy_feasible_combination"
            return Selection("blocked", reason, expanded_states=expanded,
                             rejected_reasons=tuple(sorted(reasons)), proof_scope="none" if exhausted else "enumerated_alternatives")
        keys, chosen, fit = found
        owners, aliases, required, counts, reserved, cost, borrowed = fit
        required_keys = set(keys)
        original_reserved = reserved.copy()
        limits = {"skeleton": 16 + borrowed, "context": 12 - borrowed, "exception": 4}
        omitted = []

        def fill(queue):
            nonlocal keys, owners, aliases, required, counts, reserved, cost
            for key in queue:
                if self.stopped():
                    raise SearchStopped
                if key in keys:
                    continue
                proposed = keys | self.closure([key])
                new_owners, new_aliases, new_required, new_counts, new_reserved, new_cost = self.accounting(proposed, required_keys)
                # Optional ancestor replacement cannot create a new mandatory loan.
                optional_skeleton = sum(self.candidates[value].pipeline == "skeleton" for value in new_owners if value not in new_required)
                per_file = Counter(self.candidates[value].path for value in new_owners
                                   if value not in new_required and self.candidates[value].pipeline == "context")
                if (len(new_owners) > 32 or new_cost > self.body_budget
                        or any(new_counts[p] > limits[p] for p in PIPELINES)
                        or max(0, new_reserved["skeleton"] - 16) > borrowed
                        or optional_skeleton > max(0, 16 - original_reserved["skeleton"])
                        or any(value > 2 for value in per_file.values())):
                    omitted.append(key)
                    continue
                keys, owners, aliases, required, counts, reserved, cost = proposed, new_owners, new_aliases, new_required, new_counts, new_reserved, new_cost

        try:
            # Fixed role round-robin; center degree breaks ties only within a role/signal.
            skeleton = [key for key in optional if self.candidates[key].pipeline == "skeleton"]
            fill(self.rank_round_robin(skeleton, adjacency))
            frozen_seeds = tuple(key for key in owners if self.candidates[key].pipeline == "skeleton")
            neighbors = {key for seed in frozen_seeds for key in adjacency.get(seed, set())}
            context = {key for key in optional if self.candidates[key].pipeline != "skeleton"}
            context |= {key for key in neighbors if self.candidates[key].pipeline != "skeleton"}
            fill(self.rank_round_robin(context, adjacency))
        except SearchStopped:
            return Selection("blocked", "deadline_or_cancelled", expanded_states=expanded)
        if not owners:
            return Selection("blocked", "zero_body_hits", expanded_states=expanded)
        result = Selection("selected", None, owners, aliases, required, chosen, borrowed,
                           tuple(counts[p] for p in PIPELINES), cost, expanded,
                           tuple(sorted(reasons)), "one_feasible_combination", tuple(omitted))
        self.current = result
        return result

    def rank_round_robin(self, keys: Iterable[str], adjacency: dict[str, set[str]]) -> tuple[str, ...]:
        queues = defaultdict(list)
        for key in set(keys):
            item = self.candidates[key]
            role = min(item.roles, key=ROLE_ORDER.index)
            degree = len(adjacency.get(key, set()) - {key})
            queues[role].append(key)
        for role, values in queues.items():
            values.sort(key=lambda key: (-self.candidates[key].signal,
                -Fraction(len(adjacency.get(key, set()) - {key}), len(adjacency.get(key, set()) - {key}) + 8), self.position(key)))
        result = []
        while any(queues.values()):
            for role in ROLE_ORDER:
                if queues[role]:
                    result.append(queues[role].pop(0))
        return tuple(result)


def candidates_from_package(package: dict, raw_files: dict[str, bytes]) -> tuple[Candidate, ...]:
    """Derive pipeline and condition dependencies from authenticated syntax only."""
    from .evidence_v2 import syntax_roles
    from .package_v2 import validate_package
    from .syntax_v2 import body_text
    validate_package(package, raw_files)
    result = []
    for unit in package["units"]:
        file = package["files"][unit["file"]]
        roles = tuple(role for role, _ in syntax_roles(raw_files[file["path"]], unit))
        pipeline = ("exception" if unit["ast_type"] in {"Try", "TryStar", "Raise"} else
                    "skeleton" if any(role != "boundary" for role in roles) else "context")
        dependencies = {package["units"][package["conditions"][index]["unit"]]["id"] for index in unit["conditions"]}
        result.append(Candidate(unit["id"], file["path"], tuple(unit["syntax_path"]), unit["raw_start"], unit["raw_end"],
            len(body_text(raw_files[file["path"]], file, unit).encode("utf-8")), pipeline, roles,
            len([role for role in roles if role != "boundary"]), tuple(sorted(dependencies - {unit["id"]}))))
    return tuple(result)
