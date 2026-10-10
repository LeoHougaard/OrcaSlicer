#!/usr/bin/env python3
"""Input parsing helpers for the continuous path lab."""

from __future__ import annotations

import base64
import json
import math
import re
import struct
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence


FERMAT_DIR = Path(__file__).resolve().parents[1] / "continuous_fermat"
if str(FERMAT_DIR) not in sys.path:
    sys.path.insert(0, str(FERMAT_DIR))

import fermat_layer as cf  # type: ignore  # noqa: E402


Point = tuple[float, float]
Point3 = tuple[float, float, float]
EPS = 1e-9


class InputError(ValueError):
    pass


def decode_file_payload(payload: dict[str, Any]) -> tuple[str, bytes]:
    name = str(payload.get("fileName") or payload.get("name") or "uploaded")
    data_b64 = payload.get("dataBase64")
    if not isinstance(data_b64, str) or not data_b64:
        raise InputError("No file data was provided.")
    if "," in data_b64 and data_b64.lstrip().startswith("data:"):
        data_b64 = data_b64.split(",", 1)[1]
    try:
        return name, base64.b64decode(data_b64)
    except Exception as exc:
        raise InputError(f"Could not decode file data: {exc}") from exc


def strip_closed_duplicate(points: Sequence[Point]) -> list[Point]:
    out = [(float(x), float(y)) for x, y in points]
    if len(out) > 1 and cf.dist(out[0], out[-1]) <= 1e-7:
        out.pop()
    return out


def signed_area(points: Sequence[Point]) -> float:
    return cf.signed_area(points)


def centroid(points: Sequence[Point]) -> Point:
    return cf.polygon_centroid(points)


def normalize_model(name: str, outer: Sequence[Point], holes: Sequence[Sequence[Point]] | None = None) -> cf.PolygonModel:
    outer_points = strip_closed_duplicate(outer)
    if len(outer_points) < 3:
        raise InputError("Outer polygon has fewer than three points.")
    if signed_area(outer_points) < 0:
        outer_points.reverse()

    hole_points: list[list[Point]] = []
    for hole in holes or []:
        h = strip_closed_duplicate(hole)
        if len(h) < 3:
            continue
        if signed_area(h) > 0:
            h.reverse()
        hole_points.append(h)

    return cf.PolygonModel(name, outer_points, hole_points)


def classify_loops(name: str, loops: Sequence[Sequence[Point]]) -> tuple[cf.PolygonModel, list[str]]:
    clean = [strip_closed_duplicate(loop) for loop in loops if len(strip_closed_duplicate(loop)) >= 3]
    if not clean:
        raise InputError("No closed polygon loops were found.")

    clean.sort(key=lambda loop: abs(signed_area(loop)), reverse=True)
    outer = clean[0]
    diagnostics: list[str] = []
    holes: list[list[Point]] = []
    ignored = 0
    for loop in clean[1:]:
        c = centroid(loop)
        if cf.point_in_polygon(c, outer):
            holes.append(loop)
        else:
            ignored += 1
    if ignored:
        diagnostics.append(f"Ignored {ignored} disconnected loop(s); strict continuous mode requires one island.")

    return normalize_model(name, outer, holes), diagnostics


def model_from_json_payload(payload: Any) -> cf.PolygonModel:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise InputError("Polygon JSON must be an object.")
    outer = payload.get("outer")
    if not isinstance(outer, list):
        raise InputError("Polygon JSON requires an 'outer' point list.")
    holes = payload.get("holes") or []
    return normalize_model(str(payload.get("name") or "json_polygon"), outer, holes)


def parse_dxf_pairs(text: str) -> list[tuple[str, str]]:
    lines = [line.rstrip("\r\n") for line in text.splitlines()]
    pairs: list[tuple[str, str]] = []
    idx = 0
    while idx + 1 < len(lines):
        pairs.append((lines[idx].strip(), lines[idx + 1].strip()))
        idx += 2
    return pairs


def loops_from_line_segments(segments: Sequence[tuple[Point, Point]], quant: float) -> list[list[Point]]:
    def key(p: Point) -> tuple[int, int]:
        return (int(round(p[0] / quant)), int(round(p[1] / quant)))

    unused = set(range(len(segments)))
    adjacency: dict[tuple[int, int], list[int]] = {}
    for idx, (a, b) in enumerate(segments):
        adjacency.setdefault(key(a), []).append(idx)
        adjacency.setdefault(key(b), []).append(idx)

    loops: list[list[Point]] = []
    while unused:
        start_idx = min(unused)
        unused.remove(start_idx)
        a, b = segments[start_idx]
        start_key = key(a)
        current_key = key(b)
        path = [a, b]
        guard = 0
        while current_key != start_key and guard <= len(segments) + 2:
            guard += 1
            next_idx = next((idx for idx in adjacency.get(current_key, []) if idx in unused), None)
            if next_idx is None:
                break
            unused.remove(next_idx)
            p, q = segments[next_idx]
            nxt = q if key(p) == current_key else p
            path.append(nxt)
            current_key = key(nxt)
        if len(path) >= 4 and current_key == start_key:
            loops.append(strip_closed_duplicate(path))
    return loops


def model_from_dxf(data: bytes, name: str) -> tuple[cf.PolygonModel, list[str]]:
    text = data.decode("utf-8", errors="replace")
    pairs = parse_dxf_pairs(text)
    loops: list[list[Point]] = []
    segments: list[tuple[Point, Point]] = []
    idx = 0

    while idx < len(pairs):
        code, value = pairs[idx]
        if code != "0":
            idx += 1
            continue

        entity = value.upper()
        idx += 1
        body: list[tuple[str, str]] = []
        while idx < len(pairs) and pairs[idx][0] != "0":
            body.append(pairs[idx])
            idx += 1

        if entity == "LWPOLYLINE":
            points: list[Point] = []
            flag = 0
            pending_x: float | None = None
            for c, v in body:
                if c == "70":
                    try:
                        flag = int(float(v))
                    except ValueError:
                        flag = 0
                elif c == "10":
                    pending_x = float(v)
                elif c == "20" and pending_x is not None:
                    points.append((pending_x, float(v)))
                    pending_x = None
            if len(points) >= 3 and (flag & 1):
                loops.append(points)
            elif len(points) >= 2:
                segments.extend(zip(points, points[1:]))

        elif entity == "POLYLINE":
            points: list[Point] = []
            closed = False
            while idx < len(pairs):
                c, v = pairs[idx]
                if c == "0" and v.upper() == "VERTEX":
                    idx += 1
                    vx: float | None = None
                    vy: float | None = None
                    while idx < len(pairs) and pairs[idx][0] != "0":
                        vc, vv = pairs[idx]
                        if vc == "10":
                            vx = float(vv)
                        elif vc == "20":
                            vy = float(vv)
                        idx += 1
                    if vx is not None and vy is not None:
                        points.append((vx, vy))
                    continue
                if c == "0" and v.upper() == "SEQEND":
                    idx += 1
                    break
                if c == "70":
                    try:
                        closed = bool(int(float(v)) & 1)
                    except ValueError:
                        closed = False
                idx += 1
            if len(points) >= 3 and (closed or cf.dist(points[0], points[-1]) <= 1e-7):
                loops.append(points)
            elif len(points) >= 2:
                segments.extend(zip(points, points[1:]))

        elif entity == "LINE":
            values: dict[str, float] = {}
            for c, v in body:
                if c in {"10", "20", "11", "21"}:
                    values[c] = float(v)
            if {"10", "20", "11", "21"} <= set(values):
                segments.append(((values["10"], values["20"]), (values["11"], values["21"])))

    if segments:
        extent = max((max(abs(x), abs(y)) for seg in segments for x, y in seg), default=100.0)
        loops.extend(loops_from_line_segments(segments, quant=max(extent * 1e-7, 1e-5)))

    return classify_loops(Path(name).stem or "dxf_layer", loops)


def is_binary_stl(data: bytes) -> bool:
    if len(data) < 84:
        return False
    tri_count = struct.unpack_from("<I", data, 80)[0]
    expected = 84 + tri_count * 50
    if expected == len(data):
        return True
    return not data[:5].lower() == b"solid"


def triangles_from_stl(data: bytes) -> list[tuple[Point3, Point3, Point3]]:
    if is_binary_stl(data):
        if len(data) < 84:
            raise InputError("Binary STL is too short.")
        tri_count = struct.unpack_from("<I", data, 80)[0]
        triangles: list[tuple[Point3, Point3, Point3]] = []
        offset = 84
        for _ in range(tri_count):
            if offset + 50 > len(data):
                break
            vals = struct.unpack_from("<12fH", data, offset)
            triangles.append((vals[3:6], vals[6:9], vals[9:12]))  # type: ignore[arg-type]
            offset += 50
        return triangles

    text = data.decode("utf-8", errors="replace")
    vertices = [
        (float(x), float(y), float(z))
        for x, y, z in re.findall(
            r"vertex\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
            text,
        )
    ]
    return [(vertices[i], vertices[i + 1], vertices[i + 2]) for i in range(0, len(vertices) - 2, 3)]


def inspect_stl(data: bytes) -> dict[str, Any]:
    triangles = triangles_from_stl(data)
    if not triangles:
        raise InputError("No triangles were found in the STL.")
    xs = [p[0] for tri in triangles for p in tri]
    ys = [p[1] for tri in triangles for p in tri]
    zs = [p[2] for tri in triangles for p in tri]
    return {
        "type": "stl",
        "triangles": len(triangles),
        "bounds": [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)],
        "zMin": min(zs),
        "zMax": max(zs),
    }


def interpolate3(a: Point3, b: Point3, z: float) -> Point | None:
    za = a[2]
    zb = b[2]
    if abs(za - zb) <= EPS:
        return None
    if (z < min(za, zb) - EPS) or (z > max(za, zb) + EPS):
        return None
    t = (z - za) / (zb - za)
    if t < -EPS or t > 1.0 + EPS:
        return None
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def model_from_stl_slice(data: bytes, name: str, z: float) -> tuple[cf.PolygonModel, list[str]]:
    triangles = triangles_from_stl(data)
    if not triangles:
        raise InputError("No triangles were found in the STL.")

    segments: list[tuple[Point, Point]] = []
    for tri in triangles:
        points: list[Point] = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            p = interpolate3(a, b, z)
            if p is not None and all(cf.dist(p, existing) > 1e-6 for existing in points):
                points.append(p)
        if len(points) == 2 and cf.dist(points[0], points[1]) > 1e-7:
            segments.append((points[0], points[1]))

    if not segments:
        raise InputError(f"The STL has no cross-section at Z={z:.4f}.")

    xs = [x for seg in segments for x, _ in seg]
    ys = [y for seg in segments for _, y in seg]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    loops = cf.assemble_loops(segments, quant=max(span * 1e-6, 1e-5), min_length=span * 1e-5)
    if not loops:
        loops = loops_from_line_segments(segments, quant=max(span * 1e-6, 1e-5))
    model, diagnostics = classify_loops(f"{Path(name).stem}_z{z:.3f}", loops)
    diagnostics.append(f"Sliced STL at Z={z:.4f}; extracted {len(loops)} closed loop(s).")
    return model, diagnostics


def load_model_from_request(payload: dict[str, Any]) -> tuple[cf.PolygonModel, list[str], dict[str, Any]]:
    source = str(payload.get("source") or "builtin")
    diagnostics: list[str] = []
    meta: dict[str, Any] = {"source": source}

    if source == "builtin":
        shape = str(payload.get("shape") or "rectangle")
        shapes = cf.available_shapes()
        if shape not in shapes:
            raise InputError(f"Unknown built-in shape '{shape}'.")
        return shapes[shape], diagnostics, {"source": source, "shape": shape}

    if source == "polygon":
        return model_from_json_payload(payload.get("polygon")), diagnostics, {"source": source}

    if source == "file":
        name, data = decode_file_payload(payload)
        suffix = Path(name).suffix.lower()
        meta.update({"fileName": name, "bytes": len(data)})
        if suffix == ".dxf":
            model, diagnostics = model_from_dxf(data, name)
            return model, diagnostics, meta
        if suffix == ".stl":
            info = inspect_stl(data)
            layer_height = float(payload.get("layerHeight") or 0.2)
            layer_index = int(payload.get("layerIndex") or 0)
            z = payload.get("z")
            slice_z = float(z) if z is not None else info["zMin"] + layer_height * max(0, layer_index)
            slice_z = min(max(slice_z, info["zMin"]), info["zMax"])
            model, diagnostics = model_from_stl_slice(data, name, slice_z)
            meta.update(info)
            meta.update({"sliceZ": slice_z, "layerHeight": layer_height, "layerIndex": layer_index})
            return model, diagnostics, meta
        raise InputError("Supported file types are DXF and STL.")

    raise InputError(f"Unknown source '{source}'.")

