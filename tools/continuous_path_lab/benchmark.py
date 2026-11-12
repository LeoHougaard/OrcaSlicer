#!/usr/bin/env python3
"""Batch benchmark for continuous path lab algorithms."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


LAB_DIR = Path(__file__).resolve().parent
if str(LAB_DIR) not in sys.path:
    sys.path.insert(0, str(LAB_DIR))

import planner  # type: ignore  # noqa: E402


def metric(result: dict[str, Any], name: str, default: float = 0.0) -> float:
    value = result.get("metrics", {}).get(name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def shape_score(result: dict[str, Any], coverage_target: float, spacing_target: int) -> tuple[float, ...]:
    coverage = metric(result, "coverageRatio")
    spacing = metric(result, "spacingViolations")
    crossings = metric(result, "selfIntersections")
    containment = metric(result, "containmentViolations")
    extrusion = metric(result, "extrusionViolationCount")
    missed = metric(result, "missedContourCount")
    overlap = metric(result, "internalOverlapRatio")
    underfill = metric(result, "underfillRatio")
    return (
        max(0.0, coverage_target - coverage),
        max(0.0, spacing - spacing_target) / 1000.0,
        extrusion,
        crossings,
        containment,
        missed,
        overlap,
        underfill,
    )


def run_case(shape: str, algorithm: str, args: argparse.Namespace) -> dict[str, Any]:
    options = {
        "algorithm": algorithm,
        "grid": args.grid,
        "coverageGrid": args.coverage_grid,
        "lineWidth": args.line_width,
        "spacing": args.spacing,
        "coverageThreshold": args.coverage_target,
        "overlapThreshold": args.overlap_target,
        "spacingTolerance": args.spacing_tolerance,
        "maxLevels": args.max_levels,
        "retryAttempts": args.retry_attempts,
    }
    started = time.perf_counter()
    result = planner.plan_model(planner.available_shapes()[shape], options)
    wall_time = time.perf_counter() - started
    result["benchmark"] = {
        "shape": shape,
        "algorithm": algorithm,
        "wallSeconds": wall_time,
        "score": list(shape_score(result, args.coverage_target, args.spacing_target)),
    }
    return result


def print_table(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    rows = sorted(
        results,
        key=lambda result: tuple(result["benchmark"]["score"]),
        reverse=True,
    )
    print(
        "shape                 ok  cov    under  overlap spacing cross bead turn contain miss roots contours gaps insert safe largestUF  sec"
    )
    for result in rows:
        metrics = result.get("metrics", {})
        print(
            f"{result['benchmark']['shape']:<20s} "
            f"{str(bool(result.get('ok'))):<5s} "
            f"{metric(result, 'coverageRatio'):.3f}  "
            f"{metric(result, 'underfillRatio'):.3f}  "
            f"{metric(result, 'internalOverlapRatio'):.3f}   "
            f"{int(metric(result, 'spacingViolations')):7d} "
            f"{int(metric(result, 'selfIntersections')):5d} "
            f"{int(metric(result, 'beadOverlapViolations')):4d} "
            f"{int(metric(result, 'turnbackViolations')):4d} "
            f"{int(metric(result, 'containmentViolations')):7d} "
            f"{int(metric(result, 'missedContourCount')):4d} "
            f"{int(metric(result, 'treeRoots')):5d} "
            f"{int(metric(result, 'contourCount')):8d} "
            f"{int(metric(result, 'residualGapContours')):4d} "
            f"{int(metric(result, 'residualGapSpirals')):6d} "
            f"{int(metric(result, 'safeUnderfillDetours')):4d} "
            f"{int(metric(result, 'largestUnderfillComponent')):9d} "
            f"{result['benchmark']['wallSeconds']:5.1f}"
        )

    passed = [result for result in results if bool(result.get("ok"))]
    print()
    print(
        f"passed {len(passed)}/{len(results)} "
        f"(coverage >= {args.coverage_target:.3f}, spacing warnings <= {args.spacing_target}, bead violations = 0)"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--algorithm", default="contour_tree_v5")
    parser.add_argument("--grid", type=int, default=80)
    parser.add_argument("--coverage-grid", type=int, default=70)
    parser.add_argument("--line-width", type=float, default=1.2)
    parser.add_argument("--spacing", type=float, default=1.2)
    parser.add_argument("--coverage-target", type=float, default=0.99)
    parser.add_argument("--overlap-target", type=float, default=0.02)
    parser.add_argument("--spacing-target", type=int, default=3)
    parser.add_argument("--spacing-tolerance", type=float, default=0.25)
    parser.add_argument("--max-levels", type=int, default=256)
    parser.add_argument("--retry-attempts", type=int, default=4)
    parser.add_argument("--shape", action="append", default=[])
    parser.add_argument("--json", dest="json_path", default="")
    args = parser.parse_args()

    shapes = args.shape or sorted(planner.available_shapes())
    results = [run_case(shape, args.algorithm, args) for shape in shapes]
    print_table(results, args)

    if args.json_path:
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # The planner owns the complete contract (coverage, overlap, topology,
    # missed contours, spacing, containment, and physical extrusion checks).
    # Duplicating only a subset here previously reported unsafe results as
    # benchmark passes.
    failed = [result for result in results if not bool(result.get("ok"))]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
