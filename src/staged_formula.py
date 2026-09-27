"""Deterministic, evidence-only implementation of the supplied three-stage formula.

This module does not fetch data, invent disease annotations, or establish drug
synergy. Callers must supply explicitly classified disease targets and an
explicit disease-core pathway set. All returned objects are JSON compatible.
An input error blocks scoring instead of silently assigning an optimistic value.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import json
import math
from numbers import Real
from typing import Any
import unicodedata


FORMULA_VERSION = "image-staged-v1"
TARGET_WEIGHTS = {"core": 5, "important": 2, "normal": 1}
SIGNIFICANCE_THRESHOLD = 0.01


class ScoringInputError(ValueError):
    """Required evidence is missing, ambiguous, or invalid."""


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoringInputError(f"{label} must be a non-empty string")
    return unicodedata.normalize("NFC", value.strip())


def _names(values: Iterable[str] | None, label: str) -> set[str]:
    if values is None or isinstance(values, (str, bytes)):
        raise ScoringInputError(f"{label} must be an explicitly supplied collection")
    return {_name(value, label) for value in values}


def _finite(value: Any, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ScoringInputError(f"{label} must be a finite number, not {value!r}")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ScoringInputError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ScoringInputError(f"{label} must be a finite number")
    if minimum is not None and number < minimum:
        raise ScoringInputError(f"{label} must be >= {minimum}")
    return number


def _audit_record(record: dict[str, Any]) -> dict[str, Any]:
    """Reject non-JSON/non-finite extras supplied by caller evidence dictionaries."""
    try:
        json.dumps(record, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScoringInputError("Audit evidence must contain only finite JSON-compatible values") from exc
    return record


def canonical_pair_key(herb_a: str, herb_b: str) -> str:
    """Stable unordered pair identity; same-herb combinations are invalid."""
    names = sorted((_name(herb_a, "herb_a"), _name(herb_b, "herb_b")))
    if names[0] == names[1]:
        raise ScoringInputError("A two-herb pair requires two different herbs")
    if any("__" in name for name in names):
        raise ScoringInputError("Herb names cannot contain the pair separator '__'")
    return "__".join(names)


def score_initial(
    herb_a: str,
    herb_b: str,
    targets_a: Iterable[str],
    targets_b: Iterable[str],
    target_grades: Mapping[str, str],
    core_targets: Iterable[str] | None,
) -> dict[str, Any]:
    """S1 = weighted common targets * (1 + 0.5 * complement).

    Target inputs must already be drug/disease intersections. Grades are exactly
    ``core``, ``important`` or ``normal``. They must cover the entire union;
    missing annotations are never treated as normal. The independent explicit
    core list must agree with grades for every target in this pair.
    """
    pair_key = canonical_pair_key(herb_a, herb_b)
    a = _names(targets_a, "targets_a")
    b = _names(targets_b, "targets_b")
    if not a or not b:
        raise ScoringInputError("Empty single-herb target sets cannot be ranked")
    core = _names(core_targets, "core_targets")
    if not core:
        raise ScoringInputError("core_targets must be an explicit non-empty list")
    if not isinstance(target_grades, Mapping):
        raise ScoringInputError("target_grades must explicitly classify every target")
    grades: dict[str, str] = {}
    for gene, grade in target_grades.items():
        gene = _name(gene, "target grade gene")
        if gene in grades and grades[gene] != grade:
            raise ScoringInputError(f"Conflicting normalized target grades: {gene}")
        grades[gene] = grade
    union = a | b
    common = a & b
    missing = sorted(union - grades.keys())
    if missing:
        raise ScoringInputError("Missing target grades: " + ", ".join(missing))
    for gene in sorted(union):
        grade = grades[gene]
        if not isinstance(grade, str) or grade not in TARGET_WEIGHTS:
            raise ScoringInputError(f"Invalid target grade for {gene}: {grade!r}")
        if (grade == "core") != (gene in core):
            raise ScoringInputError(f"Core list and target grade disagree for {gene}")
    weighted_common = sum(TARGET_WEIGHTS[grades[gene]] for gene in common)
    complement = _finite((len(union) - len(common)) / min(len(a), len(b)), "complement")
    score = _finite(weighted_common * (1 + 0.5 * complement), "score_initial")
    # Canonicalize A/B as well as the key, so reversed inputs have identical output.
    names = (_name(herb_a, "herb_a"), _name(herb_b, "herb_b"))
    if names[0] > names[1]:
        names = (names[1], names[0])
        a, b = b, a
    common_details = [
        {"gene": gene, "grade": grades[gene], "weight": TARGET_WEIGHTS[grades[gene]]}
        for gene in sorted(common)
    ]
    return {
        "formula_version": FORMULA_VERSION,
        "pair_key": pair_key,
        "herb_a": names[0],
        "herb_b": names[1],
        "targets_a": sorted(a),
        "targets_b": sorted(b),
        "union_targets": sorted(union),
        "common_targets": sorted(common),
        "n_a": len(a),
        "n_b": len(b),
        "n_union": len(union),
        "n_inter": len(common),
        "common_target_weights": common_details,
        "weighted_common": weighted_common,
        "complement": complement,
        "core_common_targets": sorted(common & core),
        "core_common_count": len(common & core),
        "score_initial": score,
        "initial_formula": "weighted_common * (1 + 0.5 * complement)",
    }


def ppi_induced_summary(
    targets: Iterable[str], edges: Iterable[Mapping[str, Any]] | None
) -> dict[str, Any]:
    """Average actual edges in an induced graph, never zero-fill absent edges.

    Edge schema: ``{"source": gene, "target": gene, "score": number}``.
    All scores must use the same caller-selected scale. Undirected duplicates
    with identical scores count once; conflicting duplicates are rejected.
    Self-edges and edges with outside endpoints are excluded and counted.
    No edges gives ``mean_score: None``, not zero. Pass only evidence satisfying
    the agreed STRING threshold; this helper does not silently choose a threshold.
    """
    nodes = _names(targets, "PPI targets")
    if not nodes:
        raise ScoringInputError("PPI targets must be non-empty")
    if edges is None:
        raise ScoringInputError("PPI edges are missing")
    unique: dict[tuple[str, str], float] = {}
    outside_count = self_count = duplicate_count = received_count = 0
    for edge in edges:
        received_count += 1
        if not isinstance(edge, Mapping):
            raise ScoringInputError("Each PPI edge must be a source/target/score mapping")
        source = _name(edge.get("source"), "PPI edge source")
        target = _name(edge.get("target"), "PPI edge target")
        value = _finite(edge.get("score"), "PPI edge score", minimum=0)
        if source not in nodes or target not in nodes:
            outside_count += 1
            continue
        if source == target:
            self_count += 1
            continue
        key = tuple(sorted((source, target)))
        if key in unique:
            if unique[key] != value:
                raise ScoringInputError(f"Conflicting duplicate PPI edge: {key!r}")
            duplicate_count += 1
            continue
        unique[key] = value
    # Divide before summing to avoid avoidable overflow on large finite scores.
    mean = _finite(math.fsum(score / len(unique) for score in unique.values()), "mean_score") if unique else None
    return {
        "targets": sorted(nodes),
        "node_count": len(nodes),
        "edge_count": len(unique),
        "mean_score": mean,
        "received_edge_count": received_count,
        "excluded_outside_edges": outside_count,
        "excluded_self_edges": self_count,
        "deduplicated_edges": duplicate_count,
        "edges": [
            {"source": key[0], "target": key[1], "score": score}
            for key, score in sorted(unique.items())
        ],
        "mean_definition": "mean of observed unique induced edges; missing edges not zero-filled",
    }


def score_ppi(
    initial: Mapping[str, Any],
    mean_pair: float | None,
    mean_a: float | None,
    mean_b: float | None,
    *,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """S2 = S1 * (1 + 0.8 * (pair/max(A,B)-1)) + 10 * core_common.

    Means should come from ``ppi_induced_summary`` (or equivalent documented
    real-edge means). Missing or zero single-herb baselines block scoring.
    Negative network gains are retained, without clipping.
    """
    s1 = _finite(initial.get("score_initial"), "score_initial", minimum=0)
    pair = _finite(mean_pair, "mean_pair", minimum=0)
    a = _finite(mean_a, "mean_a", minimum=0)
    b = _finite(mean_b, "mean_b", minimum=0)
    if a == 0 or b == 0:
        raise ScoringInputError("Single-herb PPI baselines must be non-zero; cannot impute zero")
    count = initial.get("core_common_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ScoringInputError("core_common_count must be an explicit non-negative integer")
    gain = _finite(pair / max(a, b) - 1, "network_gain")
    score = _finite(s1 * (1 + 0.8 * gain) + 10 * count, "score_ppi")
    result = dict(initial)
    result.update({
        "ppi_mean_pair": pair,
        "ppi_mean_a": a,
        "ppi_mean_b": b,
        "ppi_baseline_max": max(a, b),
        "network_gain": gain,
        "core_common_bonus": 10 * count,
        "score_ppi": score,
        "ppi_formula": "score_initial * (1 + 0.8 * network_gain) + 10 * core_common_count",
    })
    if evidence is not None:
        result["ppi_evidence"] = dict(evidence)
    return _audit_record(result)


def _significant_pvalues(values: Mapping[str, float] | None, label: str) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise ScoringInputError(f"{label} must be an explicit pathway-to-P-value mapping")
    significant: dict[str, float] = {}
    seen: dict[str, float] = {}
    for path, raw_p in values.items():
        name = _name(path, f"{label} pathway")
        p = _finite(raw_p, f"{label}[{name}] P-value", minimum=0)
        if p == 0 or p > 1:
            raise ScoringInputError(f"{label}[{name}] P-value must be in (0, 1]")
        if name in seen and seen[name] != p:
            raise ScoringInputError(f"Duplicate normalized pathway with conflicting P-values: {name}")
        seen[name] = p
        if p < SIGNIFICANCE_THRESHOLD:
            significant[name] = p
    return significant


def score_enrichment(
    ppi: Mapping[str, Any],
    a_pvalues: Mapping[str, float] | None,
    b_pvalues: Mapping[str, float] | None,
    pair_pvalues: Mapping[str, float] | None,
    core_pathways: Iterable[str] | None,
) -> dict[str, Any]:
    """S3 = S2 * (1 + .3 * shared core paths) * (1 + .2 * exclusive).

    Single-herb significant sets use strict P < .01 and the explicit disease-core
    pathway list. Exclusive means symmetric difference (not union). Significant
    pair-core -log10(P) mean is retained only as evidence, never multiplied into
    the score. Empty supplied result mappings mean no significant results;
    missing mappings mean absent evidence and are rejected.
    """
    s2 = _finite(ppi.get("score_ppi"), "score_ppi")
    core = _names(core_pathways, "core_pathways")
    if not core:
        raise ScoringInputError("core_pathways must be an explicit non-empty disease-core list")
    a_sig = _significant_pvalues(a_pvalues, "a_pvalues")
    b_sig = _significant_pvalues(b_pvalues, "b_pvalues")
    pair_sig = _significant_pvalues(pair_pvalues, "pair_pvalues")
    a = set(a_sig) & core
    b = set(b_sig) & core
    pair = set(pair_sig) & core
    shared = a & b
    exclusive = a ^ b
    log_evidence = [
        {"pathway": path, "p_value": pair_sig[path], "minus_log10_p": -math.log10(pair_sig[path])}
        for path in sorted(pair)
    ]
    average = (
        _finite(math.fsum(row["minus_log10_p"] / len(log_evidence) for row in log_evidence), "pair_core_mean_minus_log10_p")
        if log_evidence else None
    )
    score = _finite(s2 * (1 + 0.3 * len(shared)) * (1 + 0.2 * len(exclusive)), "score_enrichment")
    result = dict(ppi)
    result.update({
        "pathway_p_threshold": SIGNIFICANCE_THRESHOLD,
        "pathway_p_comparison": "strictly_less_than",
        "core_pathways_a": sorted(a),
        "core_pathways_b": sorted(b),
        "shared_core_pathways": sorted(shared),
        "exclusive_core_pathways": sorted(exclusive),
        "shared_core_pathway_count": len(shared),
        "exclusive_core_pathway_count": len(exclusive),
        "pair_significant_core_pathways": log_evidence,
        "pair_core_mean_minus_log10_p": average,
        "pair_significance_used_in_score": False,
        "score_enrichment": score,
        "enrichment_formula": "score_ppi * (1 + 0.3 * shared_core_pathway_count) * (1 + 0.2 * exclusive_core_pathway_count)",
    })
    return _audit_record(result)


def rank_stage(
    rows: Sequence[Mapping[str, Any]],
    score_field: str,
    limit: int,
    *,
    eligible_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Descending numeric rank with stable pair-key tie breaking and exact caps.

    Cutoff ties never expand the quota. All rows tied across the selection
    boundary are flagged, whether selected or rejected. Fewer than ``limit``
    valid rows remain fewer; no synthetic candidates are created. Supplying an
    eligibility set prevents candidates eliminated in a previous round returning.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ScoringInputError("Ranking limit must be a positive integer")
    eligible = _names(eligible_keys, "eligible_keys") if eligible_keys is not None else None
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    excluded: list[str] = []
    for row in rows:
        key = _name(row.get("pair_key"), "pair_key")
        if key in seen:
            raise ScoringInputError(f"Duplicate pair in ranking: {key}")
        seen.add(key)
        if eligible is not None and key not in eligible:
            excluded.append(key)
            continue
        a, b = row.get("herb_a"), row.get("herb_b")
        if a is not None or b is not None:
            if canonical_pair_key(a, b) != key:
                raise ScoringInputError(f"Non-canonical pair_key: {key}")
        else:
            components = key.split("__")
            if len(components) != 2 or canonical_pair_key(*components) != key:
                raise ScoringInputError(f"Non-canonical pair_key: {key}")
        value = _finite(row.get(score_field), score_field)
        item = dict(row)
        item[score_field] = value
        candidates.append(_audit_record(item))
    if eligible is not None and eligible - seen:
        raise ScoringInputError("Missing eligible pair results: " + ", ".join(sorted(eligible - seen)))
    candidates.sort(key=lambda row: (-row[score_field], row["pair_key"]))
    selected_count = min(limit, len(candidates))
    cutoff_score = candidates[selected_count - 1][score_field] if selected_count else None
    boundary_tie = bool(
        selected_count and len(candidates) > selected_count
        and candidates[selected_count][score_field] == cutoff_score
    )
    for index, row in enumerate(candidates, 1):
        row["rank"] = index
        row["selected"] = index <= limit
        row["cutoff_tie"] = boundary_tie and row[score_field] == cutoff_score
    return {
        "score_field": score_field,
        "limit": limit,
        "eligible_count": len(candidates),
        "selected_count": selected_count,
        "cutoff_score": cutoff_score,
        "cutoff_tie": boundary_tie,
        "tie_breaker": "canonical pair_key ascending; ties never expand quota",
        "excluded_ineligible_keys": sorted(excluded),
        "selected_keys": [row["pair_key"] for row in candidates[:limit]],
        "ranked_rows": candidates,
    }


def run_staged_funnel(
    initial_rows: Sequence[Mapping[str, Any]],
    ppi_scorer: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    enrichment_scorer: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    *,
    limits: tuple[int, int, int] = (30, 15, 10),
) -> dict[str, Any]:
    """Run strict S1 -> Top30 -> S2 -> Top15 -> S3 -> Top10 -> Top3.

    Only advancing pairs invoke the next scorer. Errors propagate so missing
    evidence does not masquerade as a zero score or a completed round. Top3 is
    merely the docking candidate list; no docking success is claimed.
    """
    if len(limits) != 3 or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in limits):
        raise ScoringInputError("limits must contain three positive integers")
    if not limits[0] >= limits[1] >= limits[2]:
        raise ScoringInputError("Stage limits must be non-increasing")
    initial = rank_stage(initial_rows, "score_initial", limits[0])

    def advance(stage: Mapping[str, Any], scorer: Callable) -> list[dict[str, Any]]:
        output = []
        for row in stage["ranked_rows"]:
            if not row["selected"]:
                continue
            scored = dict(scorer(dict(row)))
            if scored.get("pair_key") != row["pair_key"]:
                raise ScoringInputError("A stage scorer cannot change the pair identity")
            output.append(scored)
        return output

    ppi_rows = advance(initial, ppi_scorer)
    ppi = rank_stage(ppi_rows, "score_ppi", limits[1], eligible_keys=initial["selected_keys"])
    enrichment_rows = advance(ppi, enrichment_scorer)
    enrichment = rank_stage(enrichment_rows, "score_enrichment", limits[2], eligible_keys=ppi["selected_keys"])
    top10 = [dict(row) for row in enrichment["ranked_rows"] if row["selected"]]
    top3 = [dict(row, docking_status="not_run_by_this_module") for row in top10[:3]]
    return {
        "formula_version": FORMULA_VERSION,
        "status": "scoring_complete_not_experimental_validation",
        "stage_initial": initial,
        "stage_ppi": ppi,
        "stage_enrichment": enrichment,
        "top10": top10,
        "top3_docking_candidates": top3,
        "interpretation": "Computational research potential only; not proof of 1+1>2 efficacy",
    }
