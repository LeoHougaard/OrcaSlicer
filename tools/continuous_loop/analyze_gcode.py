#!/usr/bin/env python3
"""Analyze OrcaSlicer continuous-filament G-code and render layer previews."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from statistics import median
from typing import Any


WORD_RE = re.compile(r"([A-Za-z])([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
EPS = 1e-6


def parse_words(line: str) -> dict[str, float]:
    return {key.upper(): float(value) for key, value in WORD_RE.findall(line)}


def split_comment(line: str) -> tuple[str, str]:
    if ";" not in line:
        return line.strip(), ""
    code, comment = line.split(";", 1)
    return code.strip(), comment.strip()


def angle_degrees(a: tuple[float, float], b: tuple[float, float]) -> float:
    la = math.hypot(a[0], a[1])
    lb = math.hypot(b[0], b[1])
    if la <= EPS or lb <= EPS:
        return 0.0
    dot = max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (la * lb)))
    return math.degrees(math.acos(dot))


def is_layer_marker(comment: str) -> bool:
    text = comment.lower()
    return (
        "layer_change" in text
        or text.startswith("layer:")
        or text.startswith("layer ")
        or " layer num" in text
        or " layer_num" in text
    )


def classify_segments(gcode_path: Path, sharp_turn_degrees: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    x = y = z = e = 0.0
    absolute_xyz = True
    relative_e = False
    layer_index = 0
    flow_started = False
    body_active = False
    shutdown_active = False
    previous_xy_vector: tuple[float, float] | None = None
    segments: list[dict[str, Any]] = []
    positive_e_per_mm: list[float] = []
    z_monotonic_violations = 0
    max_z_drop = 0.0

    for line_no, raw_line in enumerate(gcode_path.read_text(errors="replace").splitlines(), start=1):
        code, comment = split_comment(raw_line)
        comment_lc = comment.lower()
        if "stop printing object" in comment_lc:
            body_active = False
            shutdown_active = True
            previous_xy_vector = None
        elif comment_lc.startswith("executable_block_end"):
            body_active = False
            shutdown_active = False
        if is_layer_marker(comment):
            layer_index += 1
            previous_xy_vector = None

        if not code:
            continue

        cmd = code.split(None, 1)[0].upper()
        words = parse_words(code)

        if cmd == "G90":
            absolute_xyz = True
            continue
        if cmd == "G91":
            absolute_xyz = False
            continue
        if cmd == "M82":
            relative_e = False
            continue
        if cmd == "M83":
            relative_e = True
            continue
        if cmd == "G92":
            if "X" in words:
                x = words["X"]
            if "Y" in words:
                y = words["Y"]
            if "Z" in words:
                z = words["Z"]
            if "E" in words:
                e = words["E"]
            continue
        if cmd not in {"G0", "G1"}:
            continue

        old_x, old_y, old_z, old_e = x, y, z, e
        new_x = old_x
        new_y = old_y
        new_z = old_z
        new_e = old_e

        if "X" in words:
            new_x = old_x + words["X"] if not absolute_xyz else words["X"]
        if "Y" in words:
            new_y = old_y + words["Y"] if not absolute_xyz else words["Y"]
        if "Z" in words:
            new_z = old_z + words["Z"] if not absolute_xyz else words["Z"]
        if "E" in words:
            new_e = old_e + words["E"] if relative_e else words["E"]

        dx = new_x - old_x
        dy = new_y - old_y
        dz = new_z - old_z
        xy_len = math.hypot(dx, dy)
        e_delta = new_e - old_e
        has_xy = "X" in words or "Y" in words
        has_e = "E" in words
        has_z = "Z" in words

        if new_z < z - EPS:
            z_monotonic_violations += 1
            max_z_drop = max(max_z_drop, z - new_z)

        kind = "other"
        if has_xy and xy_len > EPS and e_delta > EPS:
            kind = "extrude"
            flow_started = True
            body_active = True
            positive_e_per_mm.append(e_delta / xy_len)
        elif has_xy and xy_len > EPS:
            kind = "travel"
        elif has_e and e_delta < -EPS:
            kind = "retract"
        elif has_e and e_delta > EPS:
            kind = "unretract"
        elif has_z:
            kind = "z"

        turn_angle = 0.0
        sharp_turn = False
        current_vector = (dx, dy)
        if has_xy and xy_len > EPS and previous_xy_vector is not None:
            turn_angle = angle_degrees(previous_xy_vector, current_vector)
            sharp_turn = turn_angle >= sharp_turn_degrees
        if has_xy and xy_len > EPS:
            previous_xy_vector = current_vector

        segments.append(
            {
                "line": line_no,
                "layer": layer_index,
                "cmd": cmd,
                "kind": kind,
                "from": [old_x, old_y, old_z],
                "to": [new_x, new_y, new_z],
                "xy_len": xy_len,
                "e_delta": e_delta,
                "e_per_mm": e_delta / xy_len if xy_len > EPS else 0.0,
                "flow_started_before": flow_started and kind != "extrude",
                "phase": "shutdown" if shutdown_active else "body" if body_active or flow_started else "startup",
                "turn_angle": turn_angle,
                "sharp_turn": sharp_turn,
                "comment": comment,
            }
        )

        x, y, z, e = new_x, new_y, new_z, new_e

    nominal_e_per_mm = median(positive_e_per_mm) if positive_e_per_mm else 0.0
    connector_cutoff = nominal_e_per_mm * 0.55 if nominal_e_per_mm > EPS else 0.0
    for segment in segments:
        segment["connector"] = (
            segment["kind"] == "extrude"
            and connector_cutoff > EPS
            and 0.0 < segment["e_per_mm"] <= connector_cutoff
        )

    return segments, {
        "nominal_e_per_mm": nominal_e_per_mm,
        "connector_cutoff_e_per_mm": connector_cutoff,
        "z_monotonic_violations": z_monotonic_violations,
        "max_z_drop": max_z_drop,
    }


def summarize(segments: list[dict[str, Any]], derived: dict[str, Any]) -> dict[str, Any]:
    layers: dict[int, dict[str, Any]] = {}
    flow_seen_by_layer: dict[int, bool] = {}
    path_open_by_layer: dict[int, bool] = {}

    for segment in segments:
        layer = int(segment["layer"])
        item = layers.setdefault(
            layer,
            {
                "layer": layer,
                "extrusion_segments": 0,
                "connector_segments": 0,
                "travel_segments": 0,
                "unwanted_travels_after_flow": 0,
                "retractions": 0,
                "unretractions": 0,
                "sharp_turns": 0,
                "path_fragments": 0,
                "extrusion_mm": 0.0,
                "travel_mm": 0.0,
                "connector_mm": 0.0,
                "z_min": None,
                "z_max": None,
            },
        )

        z_values = [segment["from"][2], segment["to"][2]]
        item["z_min"] = min(z for z in z_values if z is not None) if item["z_min"] is None else min(item["z_min"], *z_values)
        item["z_max"] = max(z for z in z_values if z is not None) if item["z_max"] is None else max(item["z_max"], *z_values)

        kind = segment["kind"]
        if kind == "extrude":
            item["extrusion_segments"] += 1
            item["extrusion_mm"] += segment["xy_len"]
            if segment["connector"]:
                item["connector_segments"] += 1
                item["connector_mm"] += segment["xy_len"]
            if not path_open_by_layer.get(layer, False):
                item["path_fragments"] += 1
            path_open_by_layer[layer] = True
            flow_seen_by_layer[layer] = True
        elif kind == "travel":
            item["travel_segments"] += 1
            item["travel_mm"] += segment["xy_len"]
            if flow_seen_by_layer.get(layer, False):
                item["unwanted_travels_after_flow"] += 1
            path_open_by_layer[layer] = False
        elif kind == "retract":
            item["retractions"] += 1
            path_open_by_layer[layer] = False
        elif kind == "unretract":
            item["unretractions"] += 1

        if segment["sharp_turn"]:
            item["sharp_turns"] += 1

    layer_list = sorted(layers.values(), key=lambda item: item["layer"])
    total_extrusion_mm = sum(item["extrusion_mm"] for item in layer_list)
    total_connector_mm = sum(item["connector_mm"] for item in layer_list)
    total_travel_mm = sum(item["travel_mm"] for item in layer_list)
    total_unwanted_travels = sum(item["unwanted_travels_after_flow"] for item in layer_list)
    total_retractions = sum(item["retractions"] for item in layer_list)
    body_unwanted_travels = sum(1 for segment in segments if segment["phase"] == "body" and segment["kind"] == "travel")
    body_retractions = sum(1 for segment in segments if segment["phase"] == "body" and segment["kind"] == "retract")
    shutdown_retractions = sum(1 for segment in segments if segment["phase"] == "shutdown" and segment["kind"] == "retract")
    total_sharp_turns = sum(item["sharp_turns"] for item in layer_list)
    total_path_fragments = sum(item["path_fragments"] for item in layer_list)
    connector_ratio = total_connector_mm / total_extrusion_mm if total_extrusion_mm > EPS else 0.0

    worst_layer = None
    if layer_list:
        worst_layer = max(
            layer_list,
            key=lambda item: (
                item["unwanted_travels_after_flow"] * 50
                + item["retractions"] * 50
                + item["sharp_turns"]
                + item["path_fragments"] * 5
            ),
        )["layer"]

    return {
        "summary": {
            "layers": len(layer_list),
            "segments": len(segments),
            "extrusion_mm": round(total_extrusion_mm, 5),
            "connector_mm": round(total_connector_mm, 5),
            "connector_path_ratio": round(connector_ratio, 6),
            "travel_mm": round(total_travel_mm, 5),
            "unwanted_travels_after_flow": total_unwanted_travels,
            "retractions": total_retractions,
            "body_unwanted_travels": body_unwanted_travels,
            "body_retractions": body_retractions,
            "shutdown_retractions": shutdown_retractions,
            "sharp_turns": total_sharp_turns,
            "path_fragments": total_path_fragments,
            "nominal_e_per_mm": round(derived["nominal_e_per_mm"], 7),
            "connector_cutoff_e_per_mm": round(derived["connector_cutoff_e_per_mm"], 7),
            "z_monotonic_violations": derived["z_monotonic_violations"],
            "max_z_drop": round(derived["max_z_drop"], 6),
            "worst_layer": worst_layer,
            "acceptance": {
                "no_body_unwanted_travels": body_unwanted_travels == 0,
                "no_body_retractions": body_retractions == 0,
                "z_monotonic": derived["z_monotonic_violations"] == 0,
                "has_extrusion": total_extrusion_mm > EPS,
            },
        },
        "layers": layer_list,
    }


def svg_color(segment: dict[str, Any]) -> tuple[str, str]:
    if segment["kind"] == "extrude" and segment["connector"]:
        return "#1f9d55", ""
    if segment["kind"] == "extrude":
        return "#2563eb", ""
    if segment["kind"] == "travel":
        return "#888888", " stroke-dasharray=\"3 3\""
    if segment["kind"] == "retract":
        return "#dc2626", ""
    return "#9ca3af", ""


def render_layer_svg(layer: int, segments: list[dict[str, Any]], out_path: Path) -> None:
    layer_segments = [
        segment
        for segment in segments
        if segment["layer"] == layer and segment["kind"] in {"extrude", "travel"}
    ]
    if not layer_segments:
        out_path.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"400\" height=\"120\"><text x=\"10\" y=\"30\">No XY moves</text></svg>\n")
        return

    xs = [point[0] for segment in layer_segments for point in (segment["from"], segment["to"])]
    ys = [point[1] for segment in layer_segments for point in (segment["from"], segment["to"])]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = 8.0
    scale = 6.0
    width = max(160.0, (max_x - min_x) * scale + pad * 2)
    height = max(160.0, (max_y - min_y) * scale + pad * 2)

    def tx(x: float) -> float:
        return (x - min_x) * scale + pad

    def ty(y: float) -> float:
        return height - ((y - min_y) * scale + pad)

    lines = [
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width:.1f}\" height=\"{height:.1f}\" viewBox=\"0 0 {width:.1f} {height:.1f}\">",
        "<rect width=\"100%\" height=\"100%\" fill=\"#ffffff\"/>",
        f"<text x=\"8\" y=\"16\" font-size=\"12\" fill=\"#111827\">Layer {layer}</text>",
    ]
    for segment in layer_segments:
        color, dash = svg_color(segment)
        x1, y1, _ = segment["from"]
        x2, y2, _ = segment["to"]
        stroke_width = "1.8" if segment["kind"] == "extrude" else "1.0"
        lines.append(
            f"<line x1=\"{tx(x1):.2f}\" y1=\"{ty(y1):.2f}\" x2=\"{tx(x2):.2f}\" y2=\"{ty(y2):.2f}\" "
            f"stroke=\"{color}\" stroke-width=\"{stroke_width}\" stroke-linecap=\"round\"{dash}/>"
        )
    lines.append("</svg>")
    out_path.write_text("\n".join(lines) + "\n")


def select_preview_layers(metrics: dict[str, Any], max_layers: int) -> list[int]:
    layers = [item["layer"] for item in metrics["layers"]]
    if not layers:
        return []
    selected = {layers[0], layers[len(layers) // 2], layers[-1]}
    worst_layer = metrics["summary"].get("worst_layer")
    if worst_layer is not None:
        selected.add(int(worst_layer))
    return sorted(selected)[:max_layers]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcode", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--sharp-turn-degrees", type=float, default=120.0)
    parser.add_argument("--max-preview-layers", type=int, default=4)
    args = parser.parse_args()

    if not args.gcode.exists():
        raise SystemExit(f"G-code file not found: {args.gcode}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    segments, derived = classify_segments(args.gcode, args.sharp_turn_degrees)
    metrics = summarize(segments, derived)
    metrics["source"] = str(args.gcode)
    metrics["thresholds"] = {"sharp_turn_degrees": args.sharp_turn_degrees}

    metrics_path = args.out_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")

    for layer in select_preview_layers(metrics, args.max_preview_layers):
        render_layer_svg(layer, segments, args.out_dir / f"layer_{layer:04d}.svg")

    print(json.dumps(metrics["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
