#!/usr/bin/env python3
"""One-layer connected Fermat prototype for strict continuous slicing.

This is intentionally standalone and dependency-free. It is not the production
OrcaSlicer implementation. The goal is to make the geometry easy to debug:

* polygon signed-distance field
* marching-squares offset contours
* one continuous extrusion polyline
* JSON metrics and SVG preview output

The production slicer should replace the sampled SDF/marching-squares contour
generator with Orca's Clipper offset geometry, while keeping the same invariants:
one path, exact endpoint continuity, and no travel-like gaps.
"""

from __future__ import annotations

import argparse
import struct
import heapq
import json
import math
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


Point = tuple[float, float]
EPS = 1e-9

def add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def mul(a: Point, s: float) -> Point:
    return (a[0] * s, a[1] * s)


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def dist2(a: Point, b: Point) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def dist(a: Point, b: Point) -> float:
    return math.sqrt(dist2(a, b))


def lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ab = sub(b, a)
    denom = dot(ab, ab)
    if denom <= EPS:
        return dist(p, a)
    t = max(0.0, min(1.0, dot(sub(p, a), ab) / denom))
    q = lerp(a, b, t)
    return dist(p, q)


def orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(a: Point, b: Point, p: Point, eps: float = 1e-7) -> bool:
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
        and abs(orient(a, b, p)) <= eps
    )


def segments_intersect(a: Point, b: Point, c: Point, d: Point, eps: float = 1e-7) -> bool:
    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)

    if ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and (
        (o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)
    ):
        return True

    return on_segment(a, b, c, eps) or on_segment(a, b, d, eps) or on_segment(c, d, a, eps) or on_segment(c, d, b, eps)


def segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(
        point_segment_distance(a, c, d),
        point_segment_distance(b, c, d),
        point_segment_distance(c, a, b),
        point_segment_distance(d, a, b),
    )


def point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_at_y = (xj - xi) * (y - yi) / (yj - yi + EPS) + xi
            if x < x_at_y:
                inside = not inside
        j = i
    return inside


def signed_area(poly: Sequence[Point]) -> float:
    area = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        area += p[0] * q[1] - q[0] * p[1]
    return 0.5 * area


def polygon_centroid(poly: Sequence[Point]) -> Point:
    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        cross = p[0] * q[1] - q[0] * p[1]
        area2 += cross
        cx += (p[0] + q[0]) * cross
        cy += (p[1] + q[1]) * cross
    if abs(area2) <= EPS:
        return (
            sum(p[0] for p in poly) / max(1, len(poly)),
            sum(p[1] for p in poly) / max(1, len(poly)),
        )
    return (cx / (3.0 * area2), cy / (3.0 * area2))


def polyline_length(points: Sequence[Point], closed: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points) - 1):
        total += dist(points[i], points[i + 1])
    if closed:
        total += dist(points[-1], points[0])
    return total


def point_at_closed_fraction(points: Sequence[Point], fraction: float) -> Point:
    if not points:
        return (0.0, 0.0)
    if len(points) == 1:
        return points[0]
    fraction = fraction % 1.0
    total = polyline_length(points, closed=True)
    if total <= EPS:
        return points[0]
    target = total * fraction
    walked = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        length = dist(p, q)
        if walked + length >= target:
            return lerp(p, q, (target - walked) / max(length, EPS))
        walked += length
    return points[-1]


def distance_to_loop(p: Point, loop: Sequence[Point]) -> float:
    best = float("inf")
    for i, a in enumerate(loop):
        b = loop[(i + 1) % len(loop)]
        best = min(best, point_segment_distance(p, a, b))
    return best


def nearest_index(points: Sequence[Point], p: Point) -> int:
    best_idx = 0
    best_dist = float("inf")
    for i, q in enumerate(points):
        d = dist2(p, q)
        if d < best_dist:
            best_idx = i
            best_dist = d
    return best_idx


def circle_polygon(radius: float, segments: int, center: Point = (0.0, 0.0)) -> list[Point]:
    cx, cy = center
    return [
        (cx + math.cos(2.0 * math.pi * i / segments) * radius, cy + math.sin(2.0 * math.pi * i / segments) * radius)
        for i in range(segments)
    ]


def star_polygon(outer_radius: float, inner_radius: float, points: int) -> list[Point]:
    out: list[Point] = []
    for i in range(points * 2):
        r = outer_radius if i % 2 == 0 else inner_radius
        a = -math.pi / 2.0 + math.pi * i / points
        out.append((math.cos(a) * r, math.sin(a) * r))
    return out


def loop_bounds(points: Sequence[Point], margin: float = 0.0) -> tuple[float, float, float, float]:
    return (
        min(p[0] for p in points) - margin,
        min(p[1] for p in points) - margin,
        max(p[0] for p in points) + margin,
        max(p[1] for p in points) + margin,
    )


@dataclass(frozen=True)
class PolygonModel:
    name: str
    outer: list[Point]
    holes: list[list[Point]]

    def bounds(self, margin: float = 0.0) -> tuple[float, float, float, float]:
        pts = list(self.outer)
        for hole in self.holes:
            pts.extend(hole)
        min_x = min(p[0] for p in pts) - margin
        min_y = min(p[1] for p in pts) - margin
        max_x = max(p[0] for p in pts) + margin
        max_y = max(p[1] for p in pts) + margin
        return (min_x, min_y, max_x, max_y)

    def contains(self, p: Point) -> bool:
        return point_in_polygon(p, self.outer) and not any(point_in_polygon(p, hole) for hole in self.holes)

    def sdf(self, p: Point) -> float:
        boundary_distance = distance_to_loop(p, self.outer)
        for hole in self.holes:
            boundary_distance = min(boundary_distance, distance_to_loop(p, hole))
        return boundary_distance if self.contains(p) else -boundary_distance


def available_shapes() -> dict[str, PolygonModel]:
    return {
        "rectangle": PolygonModel(
            "rectangle",
            [(-45.0, -28.0), (45.0, -28.0), (45.0, 28.0), (-45.0, 28.0)],
            [],
        ),
        "dumbbell": PolygonModel(
            "dumbbell",
            [
                (-52.0, -25.0),
                (-20.0, -25.0),
                (-20.0, -8.0),
                (20.0, -8.0),
                (20.0, -25.0),
                (52.0, -25.0),
                (52.0, 25.0),
                (20.0, 25.0),
                (20.0, 8.0),
                (-20.0, 8.0),
                (-20.0, 25.0),
                (-52.0, 25.0),
            ],
            [],
        ),
        "c_shape": PolygonModel(
            "c_shape",
            [
                (-48.0, -32.0),
                (48.0, -32.0),
                (48.0, -16.0),
                (-18.0, -16.0),
                (-18.0, 16.0),
                (48.0, 16.0),
                (48.0, 32.0),
                (-48.0, 32.0),
            ],
            [],
        ),
        "annulus": PolygonModel(
            "annulus",
            circle_polygon(42.0, 160),
            [circle_polygon(16.0, 96)],
        ),
        "square_hole": PolygonModel(
            "square_hole",
            [(-45.0, -36.0), (45.0, -36.0), (45.0, 36.0), (-45.0, 36.0)],
            [[(-12.0, -12.0), (-12.0, 12.0), (12.0, 12.0), (12.0, -12.0)]],
        ),
        "two_holes": PolygonModel(
            "two_holes",
            [(-56.0, -34.0), (56.0, -34.0), (56.0, 34.0), (-56.0, 34.0)],
            [circle_polygon(10.0, 64, (-21.0, 0.0)), circle_polygon(10.0, 64, (21.0, 0.0))],
        ),
        "thin_tab": PolygonModel(
            "thin_tab",
            [
                (-42.0, -24.0),
                (18.0, -24.0),
                (18.0, -11.0),
                (45.0, -11.0),
                (45.0, 11.0),
                (18.0, 11.0),
                (18.0, 24.0),
                (-42.0, 24.0),
            ],
            [],
        ),
        "sharp_corner": PolygonModel(
            "sharp_corner",
            [(-42.0, -25.0), (20.0, -25.0), (44.0, 0.0), (20.0, 25.0), (-42.0, 25.0)],
            [],
        ),
        "narrow_boundary_slot": PolygonModel(
            "narrow_boundary_slot",
            [(-46.0, -30.0), (46.0, -30.0), (46.0, 30.0), (-46.0, 30.0)],
            [[(-10.0, -10.0), (-10.0, 10.0), (10.0, 10.0), (10.0, -10.0)]],
        ),
        "star": PolygonModel("star", star_polygon(45.0, 22.0, 7), []),
    }


@dataclass
class SDFGrid:
    model: PolygonModel
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    nx: int
    ny: int
    values: list[float]

    @property
    def dx(self) -> float:
        return (self.max_x - self.min_x) / self.nx

    @property
    def dy(self) -> float:
        return (self.max_y - self.min_y) / self.ny

    @property
    def step(self) -> float:
        return max(self.dx, self.dy)

    def value(self, i: int, j: int) -> float:
        return self.values[j * (self.nx + 1) + i]

    def point(self, i: int, j: int) -> Point:
        return (self.min_x + i * self.dx, self.min_y + j * self.dy)

    def sample(self, p: Point) -> float:
        fx = (p[0] - self.min_x) / max(self.dx, EPS)
        fy = (p[1] - self.min_y) / max(self.dy, EPS)
        fx = max(0.0, min(float(self.nx), fx))
        fy = max(0.0, min(float(self.ny), fy))
        i = min(self.nx - 1, max(0, int(math.floor(fx))))
        j = min(self.ny - 1, max(0, int(math.floor(fy))))
        tx = fx - i
        ty = fy - j
        v00 = self.value(i, j)
        v10 = self.value(i + 1, j)
        v11 = self.value(i + 1, j + 1)
        v01 = self.value(i, j + 1)
        return (v00 * (1.0 - tx) + v10 * tx) * (1.0 - ty) + (v01 * (1.0 - tx) + v11 * tx) * ty

    def max_sdf(self) -> float:
        return max(self.values)


def build_sdf_grid(model: PolygonModel, grid_cells: int, margin: float) -> SDFGrid:
    min_x, min_y, max_x, max_y = model.bounds(margin)
    width = max_x - min_x
    height = max_y - min_y
    if width >= height:
        nx = max(24, grid_cells)
        ny = max(24, int(round(grid_cells * height / width)))
    else:
        ny = max(24, grid_cells)
        nx = max(24, int(round(grid_cells * width / height)))

    values: list[float] = []
    for j in range(ny + 1):
        y = min_y + j * height / ny
        for i in range(nx + 1):
            x = min_x + i * width / nx
            values.append(model.sdf((x, y)))

    return SDFGrid(model, min_x, min_y, max_x, max_y, nx, ny, values)


EDGE_CORNERS = {
    0: (0, 1),  # bottom
    1: (1, 2),  # right
    2: (2, 3),  # top
    3: (3, 0),  # left
}

CASE_SEGMENTS: dict[int, list[tuple[int, int]]] = {
    0: [],
    1: [(3, 0)],
    2: [(0, 1)],
    3: [(3, 1)],
    4: [(1, 2)],
    6: [(0, 2)],
    7: [(3, 2)],
    8: [(2, 3)],
    9: [(0, 2)],
    11: [(1, 2)],
    12: [(1, 3)],
    13: [(0, 1)],
    14: [(3, 0)],
    15: [],
}


@dataclass
class ContourLoop:
    level_index: int
    offset: float
    points: list[Point]
    area: float
    length: float
    centroid: Point
    closed: bool = True
    line_width: float = 0.0
    speed_multiplier: float = 1.0


@dataclass(frozen=True)
class ScanlineSpan:
    fixed_index: int
    fixed: float
    low: float
    high: float
    vertical: bool
    cell_id: int = -1

    @property
    def length(self) -> float:
        return self.high - self.low

    @property
    def center(self) -> Point:
        if self.vertical:
            return (self.fixed, 0.5 * (self.low + self.high))
        return (0.5 * (self.low + self.high), self.fixed)

    def with_cell(self, cell_id: int) -> "ScanlineSpan":
        return ScanlineSpan(self.fixed_index, self.fixed, self.low, self.high, self.vertical, cell_id)

    def segment(self, reverse: bool) -> list[Point]:
        a = (self.fixed, self.low) if self.vertical else (self.low, self.fixed)
        b = (self.fixed, self.high) if self.vertical else (self.high, self.fixed)
        return [b, a] if reverse else [a, b]


def interpolate_edge(corners: Sequence[Point], values: Sequence[float], level: float, edge: int) -> Point:
    a_idx, b_idx = EDGE_CORNERS[edge]
    va = values[a_idx]
    vb = values[b_idx]
    if abs(vb - va) <= EPS:
        t = 0.5
    else:
        t = (level - va) / (vb - va)
    t = max(0.0, min(1.0, t))
    return lerp(corners[a_idx], corners[b_idx], t)


def marching_cell_segments(grid: SDFGrid, i: int, j: int, level: float) -> list[tuple[Point, Point]]:
    corners = [
        grid.point(i, j),
        grid.point(i + 1, j),
        grid.point(i + 1, j + 1),
        grid.point(i, j + 1),
    ]
    values = [
        grid.value(i, j),
        grid.value(i + 1, j),
        grid.value(i + 1, j + 1),
        grid.value(i, j + 1),
    ]

    case = 0
    for bit, value in enumerate(values):
        if value >= level:
            case |= 1 << bit

    if case == 5:
        center = sum(values) / 4.0
        pairs = [(3, 2), (0, 1)] if center >= level else [(3, 0), (1, 2)]
    elif case == 10:
        center = sum(values) / 4.0
        pairs = [(0, 3), (1, 2)] if center >= level else [(0, 1), (2, 3)]
    else:
        pairs = CASE_SEGMENTS.get(case, [])

    return [(interpolate_edge(corners, values, level, a), interpolate_edge(corners, values, level, b)) for a, b in pairs]


def assemble_loops(segments: list[tuple[Point, Point]], quant: float, min_length: float) -> list[list[Point]]:
    def key(p: Point) -> tuple[int, int]:
        return (int(round(p[0] / quant)), int(round(p[1] / quant)))

    adjacency: dict[tuple[int, int], list[int]] = {}
    for idx, (a, b) in enumerate(segments):
        adjacency.setdefault(key(a), []).append(idx)
        adjacency.setdefault(key(b), []).append(idx)

    used = [False] * len(segments)
    loops: list[list[Point]] = []

    for start_idx in range(len(segments)):
        if used[start_idx]:
            continue

        a, b = segments[start_idx]
        used[start_idx] = True
        start_key = key(a)
        current_key = key(b)
        current_point = b
        path = [a, b]

        guard = 0
        while current_key != start_key and guard < len(segments) + 4:
            guard += 1
            next_idx = None
            for candidate in adjacency.get(current_key, []):
                if not used[candidate]:
                    next_idx = candidate
                    break
            if next_idx is None:
                break

            p, q = segments[next_idx]
            used[next_idx] = True
            if key(p) == current_key:
                current_point = q
            else:
                current_point = p
            path.append(current_point)
            current_key = key(current_point)

        if len(path) >= 4 and dist(path[-1], path[0]) <= quant * 4.0:
            if dist(path[-1], path[0]) > EPS:
                path[-1] = path[0]
            open_loop = path[:-1]
            length = polyline_length(open_loop, closed=True)
            if length >= min_length and abs(signed_area(open_loop)) >= quant * quant:
                loops.append(open_loop)

    return loops


def resample_closed_loop(points: Sequence[Point], target_step: float) -> list[Point]:
    length = polyline_length(points, closed=True)
    if length <= EPS:
        return list(points)

    sample_count = max(16, int(math.ceil(length / max(target_step, EPS))))
    out: list[Point] = []
    segment_lengths: list[float] = []
    for i, p in enumerate(points):
        segment_lengths.append(dist(p, points[(i + 1) % len(points)]))

    seg_idx = 0
    seg_start_distance = 0.0
    for sample_idx in range(sample_count):
        target = length * sample_idx / sample_count
        while seg_start_distance + segment_lengths[seg_idx] < target and seg_idx < len(points) - 1:
            seg_start_distance += segment_lengths[seg_idx]
            seg_idx += 1
        seg_len = max(segment_lengths[seg_idx], EPS)
        t = (target - seg_start_distance) / seg_len
        out.append(lerp(points[seg_idx], points[(seg_idx + 1) % len(points)], t))

    return out


def densify_closed_loop_preserving_vertices(points: Sequence[Point], max_step: float) -> list[Point]:
    if len(points) < 2:
        return list(points)

    collinear_tolerance = max(max_step * 0.002, EPS)
    simplified: list[Point] = []
    for i, point in enumerate(points):
        prev = points[(i - 1) % len(points)]
        nxt = points[(i + 1) % len(points)]
        if dist(prev, nxt) > EPS and point_segment_distance(point, prev, nxt) <= collinear_tolerance:
            continue
        simplified.append(point)
    source = simplified if len(simplified) >= 3 else list(points)

    out: list[Point] = []
    for i, a in enumerate(source):
        b = source[(i + 1) % len(source)]
        append_point(out, a)
        parts = max(1, int(math.ceil(dist(a, b) / max(max_step, EPS))))
        for part in range(1, parts):
            append_point(out, lerp(a, b, part / parts))

    return out


def extract_contours(
    grid: SDFGrid,
    level_index: int,
    offset: float,
    spacing: float,
    preserve_vertices: bool,
) -> list[ContourLoop]:
    segments: list[tuple[Point, Point]] = []
    for j in range(grid.ny):
        for i in range(grid.nx):
            segments.extend(marching_cell_segments(grid, i, j, offset))

    raw_loops = assemble_loops(segments, quant=grid.step * 0.01, min_length=spacing * 2.5)
    loops: list[ContourLoop] = []
    for raw in raw_loops:
        points = (
            densify_closed_loop_preserving_vertices(raw, max_step=max(spacing * 0.25, grid.step * 0.75))
            if preserve_vertices
            else resample_closed_loop(raw, target_step=max(spacing * 0.65, grid.step * 0.75))
        )
        area = signed_area(points)
        if abs(area) < spacing * spacing:
            continue
        length = polyline_length(points, closed=True)
        loops.append(
            ContourLoop(
                level_index=level_index,
                offset=offset,
                points=points if area >= 0.0 else list(reversed(points)),
                area=abs(area),
                length=length,
                centroid=polygon_centroid(points),
            )
        )

    loops.sort(key=lambda loop: loop.area, reverse=True)
    return loops


def generate_offset_contours(
    grid: SDFGrid,
    line_width: float,
    spacing: float,
    max_levels: int,
) -> list[ContourLoop]:
    contours: list[ContourLoop] = []
    max_sdf = grid.max_sdf()
    first_offset = line_width * 0.5
    if max_sdf < first_offset:
        return contours

    offset = first_offset
    level_index = 0
    precise_wall_levels = 3
    while offset <= max_sdf - spacing * 0.15 and level_index < max_levels:
        loops = extract_contours(
            grid,
            level_index,
            offset,
            spacing,
            preserve_vertices=level_index < precise_wall_levels,
        )
        contours.extend(loops)
        offset += spacing
        level_index += 1

    return contours


def append_point(path: list[Point], p: Point) -> None:
    if not path or dist(path[-1], p) > EPS:
        path.append(p)


def append_points(path: list[Point], points: Iterable[Point]) -> None:
    for p in points:
        append_point(path, p)


def closed_arc(points: Sequence[Point], start: int, end: int, direction: int) -> list[Point]:
    if not points:
        return []
    n = len(points)
    out = [points[start % n]]
    idx = start % n
    guard = 0
    while idx != end % n and guard <= n + 2:
        idx = (idx + direction) % n
        out.append(points[idx])
        guard += 1
    return out


def almost_full_loop(points: Sequence[Point], start: int, direction: int, gap_points: int) -> list[Point]:
    n = len(points)
    gap_points = max(1, min(gap_points, max(1, n // 3)))
    end = (start - direction * gap_points) % n
    return closed_arc(points, start, end, direction)


def full_loop(points: Sequence[Point], start: int, direction: int) -> list[Point]:
    n = len(points)
    out = [points[start % n]]
    idx = start % n
    for _ in range(n):
        idx = (idx + direction) % n
        out.append(points[idx])
    return out


def loop_arc_between_params(points: Sequence[Point], start: float, end: float, direction: int) -> list[Point]:
    if not points:
        return []

    n = len(points)
    start %= n
    end %= n
    out = [loop_point_at_index(points, start)]

    if direction >= 0:
        i = math.ceil(start) % n
        if abs(float(i) - start) <= 1e-6:
            i = (i + 1) % n
        guard = 0
        while subset_cycle_param(start, end, float(i), False, False) and guard <= n + 2:
            append_point(out, points[i])
            i = (i + 1) % n
            guard += 1
    else:
        i = math.floor(start) % n
        if abs(float(i) - start) <= 1e-6:
            i = (i + n - 1) % n
        guard = 0
        while subset_cycle_param(end, start, float(i), False, False) and guard <= n + 2:
            append_point(out, points[i])
            i = (i + n - 1) % n
            guard += 1

    append_loop_param(out, points, end)
    return out


def loops_by_level(contours: Sequence[ContourLoop]) -> dict[int, list[ContourLoop]]:
    grouped: dict[int, list[ContourLoop]] = {}
    for loop in contours:
        grouped.setdefault(loop.level_index, []).append(loop)
    return grouped


def filter_printable_contours(contours: Sequence[ContourLoop], spacing: float) -> list[ContourLoop]:
    min_area = spacing * spacing * 8.0
    min_length = spacing * 5.0
    return [loop for loop in contours if loop.area >= min_area and loop.length >= min_length]


def centroid_spread(loops: Sequence[ContourLoop]) -> float:
    spread = 0.0
    for i, a in enumerate(loops):
        for b in loops[i + 1 :]:
            spread = max(spread, dist(a.centroid, b.centroid))
    return spread


def filter_topology_stable_levels(contours: Sequence[ContourLoop], spacing: float) -> list[ContourLoop]:
    grouped = loops_by_level(contours)
    if not grouped:
        return []

    first_level = min(grouped)
    initial_count = len(grouped[first_level])
    initial_spread = centroid_spread(grouped[first_level])
    max_allowed_spread_jump = spacing * 8.0
    stable: list[ContourLoop] = []

    for level in sorted(grouped):
        loops = grouped[level]
        spread = centroid_spread(loops)
        excessive_fragmentation = len(loops) > max(initial_count * 3, initial_count + 4)
        medial_split = level != first_level and len(loops) <= initial_count and spread > initial_spread + max_allowed_spread_jump
        if excessive_fragmentation or medial_split:
            break
        stable.extend(loops)

    return stable


def sampled_loop_gap(a: ContourLoop, b: ContourLoop, max_samples: int = 128) -> float:
    """Estimate the minimum distance between two closed contours."""

    best = float("inf")
    stride_a = max(1, len(a.points) // max_samples)
    stride_b = max(1, len(b.points) // max_samples)

    for p in a.points[::stride_a]:
        best = min(best, distance_to_loop(p, b.points))
    for p in b.points[::stride_b]:
        best = min(best, distance_to_loop(p, a.points))

    return best


def filter_ring_medial_overlap(contours: Sequence[ContourLoop], spacing: float) -> list[ContourLoop]:
    """Drop one-hole ring levels whose opposing fronts are closer than spacing.

    Marching squares may emit one extra contour pair near the medial axis of a
    ring. Those two centerlines are less than one line spacing apart, so no
    Fermat ordering can route both without violating the equal-spacing check.
    """

    grouped = loops_by_level(contours)
    stable: list[ContourLoop] = []

    for level in sorted(grouped):
        loops = grouped[level]
        if len(loops) == 2:
            outer, inner = sorted(loops, key=lambda loop: loop.area, reverse=True)
            if sampled_loop_gap(outer, inner) < spacing * 0.98:
                break
        stable.extend(loops)

    return stable if stable else list(contours)


def open_polyline_distance(p: Point, points: Sequence[Point]) -> float:
    if not points:
        return float("inf")
    if len(points) == 1:
        return dist(p, points[0])
    return min(point_segment_distance(p, a, b) for a, b in zip(points, points[1:]))


def contour_distance(p: Point, contour: ContourLoop) -> float:
    return distance_to_loop(p, contour.points) if contour.closed else open_polyline_distance(p, contour.points)


def principal_terminal_axes(points: Sequence[Point]) -> tuple[Point, Point, Point, float, float, float, float] | None:
    if len(points) < 4:
        return None

    center = (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )
    xx = 0.0
    xy = 0.0
    yy = 0.0
    for p in points:
        dx = p[0] - center[0]
        dy = p[1] - center[1]
        xx += dx * dx
        xy += dx * dy
        yy += dy * dy

    if xx + yy <= EPS:
        return None

    angle = 0.5 * math.atan2(2.0 * xy, xx - yy)
    major = (math.cos(angle), math.sin(angle))
    minor = (-major[1], major[0])

    major_values = [dot(sub(p, center), major) for p in points]
    minor_values = [dot(sub(p, center), minor) for p in points]
    major_span = max(major_values) - min(major_values)
    minor_span = max(minor_values) - min(minor_values)
    if minor_span > major_span:
        major, minor = minor, major
        major_values, minor_values = minor_values, major_values
        major_span, minor_span = minor_span, major_span

    return center, major, minor, min(major_values), max(major_values), min(minor_values), max(minor_values)


def add_terminal_medial_gap_fill(
    contours: Sequence[ContourLoop],
    line_width: float,
    spacing: float,
) -> tuple[list[ContourLoop], list[str]]:
    """Redistribute a terminal band over two safely connected wider passes.

    A final elongated loop already contributes two opposing passes.  An earlier
    iteration inserted a third medial pass, but odd-pass parity forced a long
    retrace when both CFS ports were on the same end of the band.  Widen and
    move the existing two passes instead; this preserves a simple open loop and
    fills the same total band width without another branch.
    """

    grouped = loops_by_level(contours)
    if not grouped:
        return list(contours), []

    last_level = max(grouped)
    terminal_level = grouped[last_level]
    if len(terminal_level) != 1:
        return list(contours), []

    terminal = terminal_level[0]
    if not terminal.closed or len(terminal.points) < 4:
        return list(contours), []

    axes = principal_terminal_axes(terminal.points)
    if axes is None:
        return list(contours), []

    center, major, minor, min_major, max_major, min_minor, max_minor = axes
    major_span = max_major - min_major
    minor_span = max_minor - min_minor
    if major_span < spacing * 6.0:
        return list(contours), []
    if not (line_width * 1.02 < minor_span < line_width * 2.05):
        return list(contours), []

    adaptive_width = (minor_span + line_width) / 2.0
    if not (line_width * 1.01 <= adaptive_width < line_width * 1.55):
        return list(contours), []

    mid_minor = 0.5 * (min_minor + max_minor)

    def remap_terminal_point(p: Point) -> Point:
        rel = sub(p, center)
        major_coord = dot(rel, major)
        minor_coord = dot(rel, minor)
        side = 1.0 if minor_coord >= mid_minor else -1.0
        return add(center, add(mul(major, major_coord), mul(minor, mid_minor + side * adaptive_width * 0.5)))

    adjusted_points = [remap_terminal_point(p) for p in terminal.points]
    adjusted_area = abs(signed_area(adjusted_points))
    adjusted = ContourLoop(
        level_index=terminal.level_index,
        offset=terminal.offset,
        points=adjusted_points if signed_area(adjusted_points) >= 0.0 else list(reversed(adjusted_points)),
        area=adjusted_area,
        length=polyline_length(adjusted_points, closed=True),
        centroid=polygon_centroid(adjusted_points),
        closed=True,
        line_width=adaptive_width,
        speed_multiplier=adaptive_width / line_width,
    )

    out: list[ContourLoop] = []
    for loop in contours:
        if loop is terminal:
            out.append(adjusted)
        else:
            out.append(loop)
    out.sort(key=lambda loop: (loop.level_index, -loop.area))

    return out, [
        f"Terminal band {minor_span:.3f} mm was redistributed into two {adaptive_width:.3f} mm beads "
        f"(cross-section factor approximately {adaptive_width / line_width:.3f})."
    ]


def build_even_odd_fermat_chain(level_loops: Sequence[ContourLoop], anchor: Point, spacing: float) -> list[Point]:
    """Build a boundary-to-boundary Fermat-like contour spiral for one-loop levels.

    Even contour levels form the inward branch. Odd contour levels form the
    outward branch. This mirrors the useful CFS property that the path can start
    and end near the boundary instead of stopping at the medial axis.
    """

    path: list[Point] = []
    even = [i for i in range(len(level_loops)) if i % 2 == 0]
    odd = [i for i in range(len(level_loops)) if i % 2 == 1]

    direction = 1
    for level_pos, loop_idx in enumerate(even):
        loop = level_loops[loop_idx]
        start = nearest_index(loop.points, anchor if not path else path[-1])
        gap = max(2, int(round(len(loop.points) * 0.025)))
        segment = almost_full_loop(loop.points, start, direction, gap)
        append_points(path, segment)
        direction *= -1

    if odd:
        direction *= -1
        for loop_idx in reversed(odd):
            loop = level_loops[loop_idx]
            start = nearest_index(loop.points, path[-1] if path else anchor)
            gap = max(2, int(round(len(loop.points) * 0.025)))
            segment = almost_full_loop(loop.points, start, direction, gap)
            append_points(path, segment)
            direction *= -1

    return path


def terminal_medial_band_path(
    terminal: ContourLoop,
    medial: ContourLoop,
    start_hint: Point,
    end_hint: Point,
) -> list[Point]:
    if terminal.line_width <= EPS or len(medial.points) < 2:
        return []

    a = medial.points[0]
    b = medial.points[-1]
    axis_length = dist(a, b)
    if axis_length <= EPS:
        return []

    major = ((b[0] - a[0]) / axis_length, (b[1] - a[1]) / axis_length)
    minor = (-major[1], major[0])
    center = lerp(a, b, 0.5)
    if terminal.points:
        farthest = max(terminal.points, key=lambda p: abs(dot(sub(p, center), minor)))
        if dot(sub(farthest, center), minor) < 0.0:
            minor = (-minor[0], -minor[1])

    sides = [a, b]

    def side_point(side: int, level: float) -> Point:
        return add(sides[side], mul(minor, level * terminal.line_width))

    candidates: list[tuple[float, list[Point]]] = []
    for start_side in (0, 1):
        for levels in ((1.0, 0.0, -1.0), (-1.0, 0.0, 1.0)):
            current_side = start_side
            path: list[Point] = []
            for idx, level in enumerate(levels):
                next_side = 1 - current_side
                append_point(path, side_point(current_side, level))
                append_point(path, side_point(next_side, level))
                if idx + 1 < len(levels):
                    append_point(path, side_point(next_side, levels[idx + 1]))
                current_side = next_side
            score = dist(start_hint, path[0]) + dist(path[-1], end_hint)
            candidates.append((score, path))

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1] if candidates else []


def seam_gap_points(loop: ContourLoop, spacing: float) -> int:
    if not loop.points:
        return 1
    average_step = max(loop.length / len(loop.points), EPS)
    return max(2, min(max(2, len(loop.points) // 4), int(math.ceil(spacing * 1.15 / average_step))))


def seam_gap_points_for_width(loop: ContourLoop, spacing: float, width_factor: float) -> int:
    if not loop.points:
        return 1
    average_step = max(loop.length / len(loop.points), EPS)
    return max(2, min(max(2, len(loop.points) // 3), int(math.ceil(spacing * width_factor / average_step))))


def build_open_contour_spiral(
    model: PolygonModel,
    grid: SDFGrid,
    ordered_loops: Sequence[ContourLoop],
    anchor: Point,
    line_width: float,
    spacing: float,
) -> tuple[list[Point], int]:
    """Build a non-crossing contour-parallel spiral by opening every loop.

    A closed contour loop is not printable as part of a single no-travel path
    without either retracing or crossing into the next loop. This routine leaves
    one seam gap in each contour and connects through that gap to the next
    contour. For simply nested contours this creates an ordinary contour spiral.
    """

    path: list[Point] = []
    current_point = anchor
    direction = 1
    unsafe_connectors = 0
    required_clearance = line_width * 0.5 - spacing * 0.15

    for loop_idx, loop in enumerate(ordered_loops):
        if not loop.points:
            continue

        if path:
            start_idx, _, safe = sampled_connection_to_loop(
                model,
                current_point,
                loop,
                required_clearance=required_clearance,
                spacing=spacing,
            )
            start_point = loop.points[start_idx]
            if not safe:
                connector = grid_astar_connector(grid, current_point, start_point, required_clearance=required_clearance)
                if connector is None:
                    unsafe_connectors += 1
                else:
                    append_points(path, connector)
        else:
            start_idx = nearest_index(loop.points, current_point)

        gap = seam_gap_points(loop, spacing)
        append_points(path, almost_full_loop(loop.points, start_idx, direction, gap))
        current_point = path[-1]

    return path, unsafe_connectors


def build_fixed_seam_open_contour_spiral(
    model: PolygonModel,
    grid: SDFGrid,
    ordered_loops: Sequence[ContourLoop],
    seam_target: Point,
    line_width: float,
    spacing: float,
) -> tuple[list[Point], int]:
    path: list[Point] = []
    unsafe_connectors = 0
    direction = 1
    required_clearance = line_width * 0.5 - spacing * 0.15

    for loop in ordered_loops:
        entry_idx = nearest_index(loop.points, seam_target)
        entry = loop.points[entry_idx]
        if path and not segment_is_inside(model, path[-1], entry, required_clearance, spacing * 0.5):
            connector = grid_astar_connector(grid, path[-1], entry, required_clearance=required_clearance)
            if connector is None:
                unsafe_connectors += 1
            else:
                append_points(path, connector)
        append_points(path, almost_full_loop(loop.points, entry_idx, direction, seam_gap_points_for_width(loop, spacing, 2.5)))

    return path, unsafe_connectors


def build_two_slot_contour_weave(
    model: PolygonModel,
    grid: SDFGrid,
    ordered_loops: Sequence[ContourLoop],
    start_anchor: Point,
    exit_anchor: Point,
    line_width: float,
    spacing: float,
) -> tuple[list[Point], int]:
    """Build a closed cyclic spiral from contour arcs.

    Every contour is split at two boundary-controlled slots. The inward pass
    consumes alternating arcs, then the outward pass consumes the complementary
    arcs. This produces a cyclic path whose first and last point are on the
    outer contour, so a later layer-to-layer solver can rotate/cut the cycle.
    """

    if not ordered_loops:
        return [], 1

    slots: list[tuple[int, int, int, int]] = []
    for loop in ordered_loops:
        a = nearest_index(loop.points, start_anchor)
        b = nearest_index(loop.points, exit_anchor)
        if abs(a - b) < max(3, len(loop.points) // 20):
            b = (a + len(loop.points) // 2) % len(loop.points)
        gap = seam_gap_points_for_width(loop, spacing, 0.65)
        slots.append(((a + gap) % len(loop.points), (b - gap) % len(loop.points), (a - gap) % len(loop.points), (b + gap) % len(loop.points)))

    path: list[Point] = []
    unsafe_connectors = 0
    required_clearance = line_width * 0.5 - spacing * 0.15
    direction = 1
    current_slot = 0

    def connect_to(point: Point) -> None:
        nonlocal unsafe_connectors
        if not path:
            append_point(path, point)
            return
        if dist(path[-1], point) <= EPS:
            return
        if segment_is_inside(model, path[-1], point, required_clearance, spacing * 0.5):
            append_point(path, point)
            return
        connector = grid_astar_connector(grid, path[-1], point, required_clearance=required_clearance)
        if connector is None:
            unsafe_connectors += 1
        else:
            append_points(path, connector)

    for i, loop in enumerate(ordered_loops):
        inward_slots = (slots[i][0], slots[i][1])
        start_idx = inward_slots[current_slot]
        end_idx = inward_slots[1 - current_slot]
        connect_to(loop.points[start_idx])
        append_points(path, closed_arc(loop.points, start_idx, end_idx, direction))
        current_slot = 1 - current_slot

    for i in reversed(range(len(ordered_loops))):
        loop = ordered_loops[i]
        outward_slots = (slots[i][2], slots[i][3])
        start_idx = outward_slots[current_slot]
        end_idx = outward_slots[1 - current_slot]
        connect_to(loop.points[start_idx])
        append_points(path, closed_arc(loop.points, start_idx, end_idx, direction))
        current_slot = 1 - current_slot

    return path, unsafe_connectors


def ordered_loops_for_spiral(contours: Sequence[ContourLoop]) -> list[ContourLoop]:
    grouped = loops_by_level(contours)
    if not grouped:
        return []

    first_count = len(grouped[min(grouped)])
    if first_count == 1:
        ordered: list[ContourLoop] = []
        for level in sorted(grouped):
            loops = grouped[level]
            if len(loops) == 1:
                ordered.append(loops[0])
            else:
                ordered.extend(sorted(loops, key=lambda loop: (loop.centroid[0], loop.centroid[1])))
        return ordered

    if first_count == 2 and all(len(grouped[level]) == 2 for level in grouped):
        outer_family: list[ContourLoop] = []
        hole_family: list[ContourLoop] = []
        for level in sorted(grouped):
            larger, smaller = sorted(grouped[level], key=lambda loop: loop.area, reverse=True)
            outer_family.append(larger)
            hole_family.append(smaller)
        return outer_family + list(reversed(hole_family))

    ordered = []
    for level in sorted(grouped):
        ordered.extend(sorted(grouped[level], key=lambda loop: -loop.area))
    return ordered


def subset_cycle_param(left: float, right: float, query: float, close_left: bool = False, close_right: bool = False) -> bool:
    if abs(query - left) <= 1e-9:
        return close_left
    if abs(query - right) <= 1e-9:
        return close_right
    if abs(left - right) <= 1e-9:
        return False
    if left < right:
        return left < query < right
    return query > left or query < right


def loop_point_at_index(points: Sequence[Point], index: float) -> Point:
    n = len(points)
    if n == 0:
        return (0.0, 0.0)
    index %= n
    base = math.floor(index)
    alpha = index - base
    return lerp(points[base % n], points[(base + 1) % n], alpha)


def loop_segment_length(points: Sequence[Point], index: int) -> float:
    n = len(points)
    return dist(points[index % n], points[(index + 1) % n])


def loop_nearest_index(points: Sequence[Point], p: Point) -> float:
    if not points:
        return 0.0

    best_idx = 0.0
    best_dist = float("inf")
    n = len(points)
    for i, a in enumerate(points):
        b = points[(i + 1) % n]
        ab = sub(b, a)
        denom = dot(ab, ab)
        t = 0.0 if denom <= EPS else max(0.0, min(1.0, dot(sub(p, a), ab) / denom))
        q = lerp(a, b, t)
        d = dist2(p, q)
        if d < best_dist:
            best_dist = d
            best_idx = i + t
    return best_idx % n


def loop_furthest_vertex_index(points: Sequence[Point], p: Point) -> float:
    best_idx = 0
    best_dist = -1.0
    for i, q in enumerate(points):
        d = dist2(p, q)
        if d > best_dist:
            best_dist = d
            best_idx = i
    return float(best_idx)


def loop_forward_by_distance(points: Sequence[Point], index: float, distance_along: float) -> float:
    if distance_along < 0.0:
        return loop_back_by_distance(points, index, -distance_along)

    n = len(points)
    index %= n
    p = loop_point_at_index(points, index)
    ceil_idx = math.ceil(index) % n
    walked = dist(p, points[ceil_idx])
    if walked >= distance_along and walked > EPS:
        alpha = distance_along / walked
        return (math.ceil(index) * alpha + index * (1.0 - alpha)) % n

    i = ceil_idx
    for _ in range(n + 2):
        segment = loop_segment_length(points, i)
        if walked + segment <= distance_along:
            walked += segment
            i = (i + 1) % n
        else:
            alpha = (distance_along - walked) / max(segment, EPS)
            return (i + alpha) % n
    return index


def loop_back_by_distance(points: Sequence[Point], index: float, distance_along: float) -> float:
    if distance_along < 0.0:
        return loop_forward_by_distance(points, index, -distance_along)

    n = len(points)
    index %= n
    p = loop_point_at_index(points, index)
    floor_idx = math.floor(index)
    walked = dist(p, points[floor_idx])
    if walked >= distance_along and walked > EPS:
        alpha = distance_along / walked
        return (floor_idx * alpha + index * (1.0 - alpha)) % n

    i = (floor_idx + n - 1) % n
    for _ in range(n + 2):
        segment = loop_segment_length(points, i)
        if walked + segment <= distance_along:
            walked += segment
            i = (i + n - 1) % n
        else:
            alpha = (distance_along - walked) / max(segment, EPS)
            return (i + 1.0 - alpha) % n
    return index


def loop_length_between(points: Sequence[Point], start: float, end: float) -> float:
    n = len(points)
    start %= n
    end %= n
    start_floor = math.floor(start)
    end_floor = math.floor(end)
    if start_floor == end_floor and end > start:
        return (end - start) * loop_segment_length(points, start_floor)

    length = (1.0 - start + start_floor) * loop_segment_length(points, start_floor)
    length += (end - end_floor) * loop_segment_length(points, end_floor)
    i = (start_floor + 1) % n
    while i != end_floor:
        length += loop_segment_length(points, i)
        i = (i + 1) % n
    return length


def append_loop_param(path: list[Point], points: Sequence[Point], index: float) -> None:
    append_point(path, loop_point_at_index(points, index))


def append_loop_vertices_forward(
    path: list[Point],
    points: Sequence[Point],
    start_index: int,
    stop_index: int,
    guard_extra: int = 2,
) -> None:
    n = len(points)
    i = start_index % n
    guard = 0
    while i != stop_index % n and guard <= n + guard_extra:
        append_point(path, points[i])
        i = (i + 1) % n
        guard += 1


def append_loop_vertices_backward_until(
    path: list[Point],
    points: Sequence[Point],
    start_index: int,
    left: float,
    right: float,
) -> None:
    n = len(points)
    i = start_index % n
    guard = 0
    while subset_cycle_param(left, float(i), right, False, False) and guard <= n + 2:
        append_point(path, points[i])
        i = (i + n - 1) % n
        guard += 1


def build_single_minimum_connected_fermat(
    ordered_loops: Sequence[ContourLoop],
    start_anchor: Point,
    spacing: float,
    port_spacing: float | None = None,
    exit_anchor: Point | None = None,
    preserve_medial_pockets: bool = True,
) -> tuple[list[Point], int]:
    """Build a boundary-to-boundary connected Fermat spiral for one contour chain.

    This is a direct contour-index version of the CFS single-minimum connector:
    each contour owns an in point and an out point. The in branch walks inward,
    the out branch walks inward in parallel, and the final path appends the out
    branch in reverse. The result starts and ends on the outer contour without
    the slot self-touches produced by the earlier two-slot weave.
    """

    loops = [loop for loop in ordered_loops if len(loop.points) >= (4 if loop.closed else 2)]
    if not loops:
        return [], 0
    port_spacing = spacing if port_spacing is None else port_spacing
    if len(loops) == 1:
        start = loop_nearest_index(loops[0].points, start_anchor)
        return full_loop(loops[0].points, int(round(start)), 1), 0

    # Keep the small medial contours for concave pocket shapes. One-hole rings
    # are different: opposing fronts can become closer than one line spacing,
    # so their final medial caps are not printable without spacing violations.
    if not preserve_medial_pockets:
        while len(loops) > 2 and loops[-1].length < spacing * 20.0:
            loops.pop()

    in_index = loop_nearest_index(loops[0].points, start_anchor)
    out_index = loop_nearest_index(loops[0].points, exit_anchor) if exit_anchor is not None else in_index
    out_forward_in = True
    in_run = False
    first_circle = True
    in_branch: list[Point] = []
    out_branch: list[Point] = []
    loop_index = 0

    while True:
        loop = loops[loop_index]
        points = loop.points
        n = len(points)
        if (
            loop.closed
            and loop_index + 1 < len(loops)
            and not loops[loop_index + 1].closed
            and in_branch
            and out_branch
        ):
            band_path = terminal_medial_band_path(loop, loops[loop_index + 1], in_branch[-1], out_branch[-1])
            if band_path:
                append_points(in_branch, band_path)
                break

        if not loop.closed:
            if len(points) < 2:
                break
            if not in_branch or not out_branch:
                return [points[0], points[-1]], 0

            a = points[0]
            b = points[-1]
            forward_score = dist(in_branch[-1], a) + dist(out_branch[-1], b)
            reverse_score = dist(in_branch[-1], b) + dist(out_branch[-1], a)
            if forward_score <= reverse_score:
                start_point, end_point = a, b
            else:
                start_point, end_point = b, a

            append_point(in_branch, start_point)
            append_point(out_branch, end_point)
            append_point(in_branch, end_point)
            break

        active_port_spacing = port_spacing
        circle_small = loop.length < active_port_spacing * 2.0

        if circle_small:
            near_index = (0.5 * (in_index + out_index)) % n
            near = loop_point_at_index(points, near_index)
            far_index = loop_furthest_vertex_index(points, near)
            far = loop_point_at_index(points, far_index)

            in_index = float(math.ceil(near_index) % n)

            def small_score(idx: float) -> float:
                p = points[int(idx) % n]
                return min(dist(near, p), dist(far, p))

            best = small_score(in_index)
            i = (int(in_index) + 1) % n
            while subset_cycle_param(near_index, far_index, float(i)):
                score = small_score(float(i))
                if score > best:
                    best = score
                    in_index = float(i)
                i = (i + 1) % n

            out_index = float(math.ceil(far_index) % n)
            best = small_score(out_index)
            i = (int(out_index) + 1) % n
            while subset_cycle_param(far_index, near_index, float(i)):
                score = small_score(float(i))
                if score > best:
                    best = score
                    out_index = float(i)
                i = (i + 1) % n

            out_forward_in = loop_length_between(points, in_index, out_index) <= loop_length_between(points, out_index, in_index)
        else:
            if abs(in_index - out_index) < 1e-6:
                if first_circle:
                    out_forward = loop_forward_by_distance(points, in_index, active_port_spacing)
                    out_backward = loop_back_by_distance(points, in_index, active_port_spacing)
                    next_loop = loops[min(loop_index + 1, len(loops) - 1)]
                    if distance_to_loop(loop_point_at_index(points, out_forward), next_loop.points) < distance_to_loop(
                        loop_point_at_index(points, out_backward), next_loop.points
                    ):
                        out_index = out_backward
                        out_forward_in = False
                    else:
                        out_index = out_forward
                        out_forward_in = True
                else:
                    in0 = loop_point_at_index(points, in_index)
                    out_forward = loop_forward_by_distance(points, in_index, active_port_spacing)
                    out_backward = loop_back_by_distance(points, in_index, active_port_spacing)
                    p_forward = loop_point_at_index(points, out_forward)
                    p_backward = loop_point_at_index(points, out_backward)
                    prev_in = in_branch[-1]
                    prev_out = out_branch[-1]

                    forward_score = min(dist(in0, prev_in) + dist(p_forward, prev_out), dist(p_forward, prev_in) + dist(in0, prev_out))
                    backward_score = min(
                        dist(in0, prev_in) + dist(p_backward, prev_out), dist(p_backward, prev_in) + dist(in0, prev_out)
                    )
                    if forward_score < backward_score:
                        if dist(in0, prev_in) + dist(p_forward, prev_out) <= dist(p_forward, prev_in) + dist(in0, prev_out):
                            out_index = out_forward
                            out_forward_in = True
                        else:
                            out_index = in_index
                            in_index = out_forward
                            out_forward_in = False
                    else:
                        if dist(in0, prev_in) + dist(p_backward, prev_out) <= dist(p_backward, prev_in) + dist(in0, prev_out):
                            out_index = out_backward
                            out_forward_in = False
                        else:
                            out_index = in_index
                            in_index = out_backward
                            out_forward_in = True
            else:
                length_io = loop_length_between(points, in_index, out_index)
                length_oi = loop_length_between(points, out_index, in_index)
                out_forward_in = length_io <= length_oi
                arc_length = min(length_io, length_oi)
                if first_circle:
                    if arc_length < active_port_spacing:
                        if out_forward_in:
                            out_index = loop_forward_by_distance(points, in_index, active_port_spacing)
                        else:
                            out_index = loop_back_by_distance(points, in_index, active_port_spacing)
                    out_forward_in = loop_length_between(points, in_index, out_index) <= loop_length_between(
                        points, out_index, in_index
                    )
                    append_loop_param(in_branch, points, in_index)
                    append_loop_param(out_branch, points, out_index)
                    if loop_index + 1 < len(loops):
                        if out_forward_in:
                            in_back = loop_back_by_distance(points, in_index, active_port_spacing)
                            i = math.ceil(out_index) % n
                            guard = 0
                            while subset_cycle_param(float(i), in_index, in_back, False, False) and guard <= n + 2:
                                append_point(out_branch, points[i])
                                i = (i + 1) % n
                                guard += 1
                            append_loop_param(out_branch, points, in_back)
                        else:
                            in_far = loop_forward_by_distance(points, in_index, active_port_spacing)
                            i = math.floor(out_index) % n
                            append_loop_vertices_backward_until(out_branch, points, i, in_index, in_far)
                            append_loop_param(out_branch, points, in_far)

                        in_run = True
                        loop_index += 1
                        child_points = loops[loop_index].points
                        in_index = loop_nearest_index(child_points, in_branch[-1])
                        out_index = loop_nearest_index(child_points, out_branch[-1])
                        first_circle = False
                        continue
                in0 = loop_point_at_index(points, in_index)
                out0 = loop_point_at_index(points, out_index)

                if out_forward_in:
                    in1_index = loop_forward_by_distance(points, in_index, arc_length - active_port_spacing)
                    out1_index = loop_back_by_distance(points, out_index, arc_length - active_port_spacing)
                else:
                    in1_index = loop_back_by_distance(points, in_index, arc_length - active_port_spacing)
                    out1_index = loop_forward_by_distance(points, out_index, arc_length - active_port_spacing)

                in1 = loop_point_at_index(points, in1_index)
                out1 = loop_point_at_index(points, out1_index)
                prev_in = in_branch[-1]
                prev_out = out_branch[-1]

                keep_in_score = min(dist(in0, prev_in) + dist(out1, prev_out), dist(in0, prev_out) + dist(out1, prev_in))
                keep_out_score = min(dist(in1, prev_in) + dist(out0, prev_out), dist(in1, prev_out) + dist(out0, prev_in))
                if keep_in_score <= keep_out_score:
                    if dist(in0, prev_in) + dist(out1, prev_out) < dist(in0, prev_out) + dist(out1, prev_in):
                        out_index = out1_index
                    else:
                        out_index = in_index
                        in_index = out1_index
                        out_forward_in = not out_forward_in
                else:
                    if dist(in1, prev_in) + dist(out0, prev_out) < dist(in1, prev_out) + dist(out0, prev_in):
                        in_index = in1_index
                    else:
                        in_index = out_index
                        out_index = in1_index
                        out_forward_in = not out_forward_in

        if not first_circle and segments_intersect(
            loop_point_at_index(points, in_index),
            in_branch[-1],
            loop_point_at_index(points, out_index),
            out_branch[-1],
        ):
            in_index, out_index = out_index, in_index
            out_forward_in = not out_forward_in

        append_loop_param(in_branch, points, in_index)
        append_loop_param(out_branch, points, out_index)

        if loop_index + 1 < len(loops):
            if in_run:
                if out_forward_in:
                    out_far = loop_forward_by_distance(points, out_index, active_port_spacing)
                    i = math.floor(in_index) % n
                    append_loop_vertices_backward_until(in_branch, points, i, out_index, out_far)
                    append_loop_param(in_branch, points, out_far)
                else:
                    out_back = loop_back_by_distance(points, out_index, active_port_spacing)
                    i = math.ceil(in_index) % n
                    guard = 0
                    while subset_cycle_param(float(i), out_index, out_back, False, False) and guard <= n + 2:
                        append_point(in_branch, points[i])
                        i = (i + 1) % n
                        guard += 1
                    append_loop_param(in_branch, points, out_back)
            else:
                if out_forward_in:
                    in_back = loop_back_by_distance(points, in_index, active_port_spacing)
                    i = math.ceil(out_index) % n
                    guard = 0
                    while subset_cycle_param(float(i), in_index, in_back, False, False) and guard <= n + 2:
                        append_point(out_branch, points[i])
                        i = (i + 1) % n
                        guard += 1
                    append_loop_param(out_branch, points, in_back)
                else:
                    in_far = loop_forward_by_distance(points, in_index, active_port_spacing)
                    i = math.floor(out_index) % n
                    append_loop_vertices_backward_until(out_branch, points, i, in_index, in_far)
                    append_loop_param(out_branch, points, in_far)

            in_run = not in_run
            loop_index += 1
            child_points = loops[loop_index].points
            in_index = loop_nearest_index(child_points, in_branch[-1])
            out_index = loop_nearest_index(child_points, out_branch[-1])
        else:
            prev_in = in_branch[-1]
            prev_out = out_branch[-1]
            medial_index = max(range(n), key=lambda i: min(dist(points[i], prev_in), dist(points[i], prev_out)))
            if subset_cycle_param(in_index, out_index, float(medial_index), True, False):
                append_loop_vertices_forward(in_branch, points, math.ceil(in_index) % n, math.floor(out_index) % n)
            else:
                append_loop_vertices_forward(out_branch, points, math.ceil(out_index) % n, math.floor(in_index) % n)
            break

        first_circle = False

    for p in reversed(out_branch):
        append_point(in_branch, p)
    cleaned: list[Point] = []
    append_points(cleaned, in_branch)
    return cleaned, 0


def project_open_polyline(points: Sequence[Point], p: Point) -> tuple[float, Point, float]:
    if len(points) < 2:
        q = points[0] if points else (0.0, 0.0)
        return 0.0, q, dist(p, q)

    best_index = 0.0
    best_point = points[0]
    best_distance2 = float("inf")
    for i, (a, b) in enumerate(zip(points, points[1:])):
        ab = sub(b, a)
        denom = dot(ab, ab)
        t = 0.0 if denom <= EPS else max(0.0, min(1.0, dot(sub(p, a), ab) / denom))
        q = lerp(a, b, t)
        d = dist2(p, q)
        if d < best_distance2:
            best_distance2 = d
            best_index = i + t
            best_point = q
    return best_index, best_point, math.sqrt(best_distance2)


def project_open_polyline_with_min_sdf(
    points: Sequence[Point],
    p: Point,
    grid: SDFGrid,
    min_sdf: float,
) -> tuple[float, Point, float]:
    if len(points) < 2:
        q = points[0] if points else (0.0, 0.0)
        return 0.0, q, dist(p, q)

    best_index = 0.0
    best_point = points[0]
    best_distance2 = float("inf")
    found = False
    for i, (a, b) in enumerate(zip(points, points[1:])):
        midpoint = lerp(a, b, 0.5)
        if grid.sample(midpoint) < min_sdf:
            continue
        ab = sub(b, a)
        denom = dot(ab, ab)
        t = 0.0 if denom <= EPS else max(0.0, min(1.0, dot(sub(p, a), ab) / denom))
        q = lerp(a, b, t)
        d = dist2(p, q)
        if d < best_distance2:
            found = True
            best_distance2 = d
            best_index = i + t
            best_point = q

    return (best_index, best_point, math.sqrt(best_distance2)) if found else project_open_polyline(points, p)


def cut_open_polyline(points: Sequence[Point], index: float) -> tuple[list[Point], list[Point], Point]:
    if not points:
        return [], [], (0.0, 0.0)
    if len(points) == 1:
        return [points[0]], [points[0]], points[0]

    index = max(0.0, min(index, len(points) - 1.0))
    base = min(math.floor(index), len(points) - 2)
    alpha = index - base
    cut = lerp(points[base], points[base + 1], alpha)
    before = list(points[: base + 1])
    append_point(before, cut)
    after = [cut]
    after.extend(points[base + 1 :])
    return before, after, cut


def merge_child_spiral(parent_path: Sequence[Point], child_path: Sequence[Point], spacing: float) -> list[Point]:
    if not parent_path:
        return list(child_path)
    if not child_path:
        return list(parent_path)

    start_index, _, _ = project_open_polyline(parent_path, child_path[0])
    end_index, _, _ = project_open_polyline(parent_path, child_path[-1])
    child = list(child_path)
    if start_index > end_index:
        child.reverse()
        start_index, _, _ = project_open_polyline(parent_path, child[0])
        end_index, _, _ = project_open_polyline(parent_path, child[-1])

    parent_start, _, _ = cut_open_polyline(parent_path, start_index)
    _, parent_end, _ = cut_open_polyline(parent_path, end_index)
    merged: list[Point] = []
    append_points(merged, parent_start)
    append_points(merged, child)
    append_points(merged, parent_end)
    return simplify_polyline(merged, tolerance=spacing * 0.04)


def closed_path_arc_between_params(path: Sequence[Point], start: float, end: float) -> list[Point]:
    if len(path) < 2:
        return list(path)

    base = list(path[:-1]) if dist(path[0], path[-1]) <= EPS else list(path)
    if len(base) < 2:
        return base

    n = len(base)
    start %= n
    end %= n
    out = [loop_point_at_index(base, start)]
    i = math.ceil(start) % n
    if abs(float(i) - start) <= 1e-6:
        i = (i + 1) % n
    guard = 0
    while subset_cycle_param(start, end, float(i), False, False) and guard <= n + 2:
        append_point(out, base[i])
        i = (i + 1) % n
        guard += 1
    append_loop_param(out, base, end)
    return out


def rotate_closed_path_to_anchor(path: Sequence[Point], anchor: Point) -> list[Point]:
    if len(path) < 3:
        return list(path)
    base = list(path[:-1]) if dist(path[0], path[-1]) <= EPS else list(path)
    if len(base) < 2:
        return list(path)
    start = nearest_index(base, anchor)
    rotated = base[start:] + base[:start]
    append_point(rotated, rotated[0])
    return rotated


def rotate_closed_path_to_loop(path: Sequence[Point], loop: Sequence[Point]) -> list[Point]:
    if len(path) < 3 or not loop:
        return list(path)
    base = list(path[:-1]) if dist(path[0], path[-1]) <= EPS else list(path)
    if len(base) < 2:
        return list(path)
    start = min(range(len(base)), key=lambda idx: distance_to_loop(base[idx], loop))
    rotated = base[start:] + base[:start]
    append_point(rotated, rotated[0])
    return rotated


def merge_child_into_closed_parent(
    parent_path: Sequence[Point],
    child_path: Sequence[Point],
    anchor: Point,
    grid: SDFGrid | None = None,
    parent_min_sdf: float | None = None,
) -> list[list[Point]]:
    if len(parent_path) < 3 or len(child_path) < 2:
        return []

    base = list(parent_path[:-1]) if dist(parent_path[0], parent_path[-1]) <= EPS else list(parent_path)
    parent_size = len(base)
    if parent_size < 2:
        return []

    if grid is not None and parent_min_sdf is not None:
        start_index, _, _ = project_open_polyline_with_min_sdf(parent_path, child_path[0], grid, parent_min_sdf)
        end_index, _, _ = project_open_polyline_with_min_sdf(parent_path, child_path[-1], grid, parent_min_sdf)
    else:
        start_index, _, _ = project_open_polyline(parent_path, child_path[0])
        end_index, _, _ = project_open_polyline(parent_path, child_path[-1])
    s = start_index % parent_size
    e = end_index % parent_size
    s_point = loop_point_at_index(base, s)
    e_point = loop_point_at_index(base, e)

    candidates: list[list[Point]] = []
    variants = [
        (s_point, e_point, s, e, list(child_path)),
        (e_point, s_point, e, s, list(reversed(child_path))),
    ]
    for first_port, second_port, first_index, second_index, child in variants:
        kept_arc = closed_path_arc_between_params(parent_path, second_index, first_index)

        candidate: list[Point] = []
        append_point(candidate, first_port)
        append_points(candidate, child)
        append_point(candidate, second_port)
        append_points(candidate, kept_arc[1:])
        append_point(candidate, candidate[0])
        candidates.append(rotate_closed_path_to_anchor(candidate, anchor))

        if grid is None or parent_min_sdf is None:
            continue

        connector_in = grid_astar_connector(grid, first_port, child[0], required_clearance=parent_min_sdf)
        connector_out = grid_astar_connector(grid, child[-1], second_port, required_clearance=parent_min_sdf)
        if connector_in is None or connector_out is None:
            continue

        routed: list[Point] = []
        append_points(routed, connector_in)
        append_points(routed, child[1:])
        append_points(routed, connector_out)
        append_points(routed, kept_arc[1:])
        append_point(routed, routed[0])
        candidates.append(rotate_closed_path_to_anchor(routed, anchor))

    return candidates


def complete_outer_boundary_cycle(
    path: Sequence[Point],
    outer_loop: ContourLoop,
    spacing: float,
    spacing_tolerance: float,
) -> list[Point]:
    """Append the missing outer-contour arc so the CFS can be cut anywhere.

    The CFS routines produce a boundary-to-boundary curve. For layer-to-layer
    continuity we need the layer path to behave as a closed cycle with only a
    tiny boundary cut gap. Try both possible outer-boundary arcs and keep the
    one that preserves the non-crossing/equal-spacing validator best.
    """

    if len(path) < 2 or len(outer_loop.points) < 4:
        return list(path)

    if dist(path[0], path[-1]) <= EPS:
        return list(path)

    start_index = loop_nearest_index(outer_loop.points, path[0])
    end_index = loop_nearest_index(outer_loop.points, path[-1])
    candidates: list[tuple[tuple[int, int, float, float], list[Point]]] = []

    for direction in (1, -1):
        if direction > 0:
            stop_index = start_index
        else:
            stop_index = start_index

        arc = loop_arc_between_params(outer_loop.points, end_index, stop_index, direction)
        if len(arc) < 2:
            continue
        arc[0] = path[-1]
        arc[-1] = path[0]

        candidate: list[Point] = []
        append_points(candidate, path)
        append_points(candidate, arc[1:])
        if dist(candidate[0], candidate[-1]) > EPS:
            continue

        crossings, close_pairs, min_spacing = path_pair_metrics(
            candidate,
            spacing=spacing,
            spacing_tolerance=spacing_tolerance,
        )
        arc_length = polyline_length(arc, closed=False)
        candidates.append(((crossings, close_pairs, -min_spacing, arc_length), candidate))

    if not candidates:
        return list(path)

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def open_path_prefix_lengths(path: Sequence[Point]) -> list[float]:
    prefix = [0.0]
    for a, b in zip(path, path[1:]):
        prefix.append(prefix[-1] + dist(a, b))
    return prefix


def open_path_point_at_index(path: Sequence[Point], index: float) -> Point:
    if not path:
        return (0.0, 0.0)
    if len(path) == 1:
        return path[0]
    index = max(0.0, min(index, len(path) - 1.0))
    base = min(math.floor(index), len(path) - 2)
    return lerp(path[base], path[base + 1], index - base)


def open_path_length_at_index(path: Sequence[Point], prefix: Sequence[float], index: float) -> float:
    if len(path) < 2:
        return 0.0
    index = max(0.0, min(index, len(path) - 1.0))
    base = min(math.floor(index), len(path) - 2)
    return prefix[base] + (index - base) * dist(path[base], path[base + 1])


def open_path_index_at_length(path: Sequence[Point], prefix: Sequence[float], target: float) -> float:
    if len(path) < 2:
        return 0.0

    target = max(0.0, min(target, prefix[-1]))
    lo = 0
    hi = len(prefix) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if prefix[mid] < target:
            lo = mid + 1
        else:
            hi = mid

    idx = max(0, lo - 1)
    segment_length = max(dist(path[idx], path[idx + 1]), EPS)
    return idx + (target - prefix[idx]) / segment_length


def count_containment_violations(model: PolygonModel, path: Sequence[Point], spacing: float) -> int:
    violations = 0
    outside_tolerance = -spacing * 0.05
    for a, b in zip(path, path[1:]):
        length = dist(a, b)
        samples = max(2, int(math.ceil(length / max(spacing * 0.25, EPS))))
        for i in range(samples + 1):
            if model.sdf(lerp(a, b, i / samples)) < outside_tolerance:
                violations += 1
    return violations


def loop_uncovered_fraction(loop: ContourLoop, path: Sequence[Point], spacing: float) -> float:
    if not path:
        return 1.0

    stride = max(1, len(loop.points) // 32)
    sampled = loop.points[::stride]
    if not sampled:
        return 0.0

    uncovered = 0
    for p in sampled:
        _, _, distance = project_open_polyline(path, p)
        if distance > spacing * 0.55:
            uncovered += 1
    return uncovered / len(sampled)


def uncovered_pocket_groups(contours: Sequence[ContourLoop], path: Sequence[Point], spacing: float) -> list[list[ContourLoop]]:
    uncovered = [
        loop
        for loop in contours
        if loop.level_index > 0 and loop.area < spacing * spacing * 90.0 and loop_uncovered_fraction(loop, path, spacing) > 0.65
    ]

    groups: list[list[ContourLoop]] = []
    for loop in sorted(uncovered, key=lambda item: -item.area):
        for group in groups:
            if dist(loop.centroid, group[0].centroid) < spacing * 7.0:
                group.append(loop)
                break
        else:
            groups.append([loop])
    return groups


def unique_port_candidates(candidates: Sequence[tuple[float, float]], limit: int) -> list[float]:
    selected: list[float] = []
    for _, index in sorted(candidates, key=lambda item: item[0]):
        if all(abs(index - existing) > 2.0 for existing in selected):
            selected.append(index)
            if len(selected) >= limit:
                break
    return selected


def try_insert_pocket_group(
    model: PolygonModel,
    parent_path: Sequence[Point],
    group: Sequence[ContourLoop],
    spacing: float,
    spacing_tolerance: float,
) -> list[Point] | None:
    if len(parent_path) < 4 or not group:
        return None

    prefix = open_path_prefix_lengths(parent_path)
    centroid = (
        sum(loop.centroid[0] for loop in group) / len(group),
        sum(loop.centroid[1] for loop in group) / len(group),
    )
    port_candidates: list[tuple[float, float]] = []
    index, _, distance = project_open_polyline(parent_path, centroid)
    port_candidates.append((distance, index))
    for loop in group:
        stride = max(1, len(loop.points) // 16)
        for p in loop.points[::stride]:
            index, _, distance = project_open_polyline(parent_path, p)
            port_candidates.append((distance, index))

    ordered = ordered_loops_for_spiral(group)
    best: tuple[tuple[int, int, int, float], list[Point]] | None = None
    for center_index in unique_port_candidates(port_candidates, limit=4):
        center_index = max(3.0, min(center_index, len(parent_path) - 4.0))
        center_length = open_path_length_at_index(parent_path, prefix, center_index)

        for half_width in (spacing * 0.60, spacing * 1.00, spacing * 1.35, spacing * 2.25, spacing * 3.25):
            left_index = open_path_index_at_length(parent_path, prefix, center_length - half_width)
            right_index = open_path_index_at_length(parent_path, prefix, center_length + half_width)

            left_port = open_path_point_at_index(parent_path, left_index)
            right_port = open_path_point_at_index(parent_path, right_index)
            if dist(left_port, right_port) < spacing * 0.5:
                continue
            for start_anchor, exit_anchor in ((left_port, right_port), (right_port, left_port)):
                child_path, _ = build_single_minimum_connected_fermat(
                    ordered,
                    start_anchor=start_anchor,
                    spacing=spacing,
                    port_spacing=spacing,
                    exit_anchor=exit_anchor,
                    preserve_medial_pockets=True,
                )
                for candidate_child in (child_path, list(reversed(child_path))):
                    before, _, _ = cut_open_polyline(parent_path, left_index)
                    _, after, _ = cut_open_polyline(parent_path, right_index)
                    candidate: list[Point] = []
                    append_points(candidate, before)
                    append_points(candidate, candidate_child)
                    append_points(candidate, after)

                    crossings, close_pairs, min_spacing = path_pair_metrics(
                        candidate,
                        spacing=spacing,
                        spacing_tolerance=spacing_tolerance,
                    )
                    containment = 0
                    if crossings == 0 and close_pairs == 0:
                        containment = count_containment_violations(model, candidate, spacing)

                    score = (crossings, close_pairs, containment, -min_spacing)
                    if best is None or score < best[0]:
                        best = (score, candidate)
                    if crossings == 0 and close_pairs == 0 and containment == 0:
                        return candidate

    if best is not None and best[0][0] == 0 and best[0][1] == 0 and best[0][2] == 0:
        return best[1]
    return None


def insert_uncovered_pocket_spirals(
    model: PolygonModel,
    contours: Sequence[ContourLoop],
    path: Sequence[Point],
    spacing: float,
    spacing_tolerance: float,
) -> tuple[list[Point], int]:
    current = list(path)
    inserted = 0
    for group in uncovered_pocket_groups(contours, current, spacing):
        candidate = try_insert_pocket_group(
            model,
            current,
            group,
            spacing=spacing,
            spacing_tolerance=spacing_tolerance,
        )
        if candidate is not None:
            current = candidate
            inserted += 1
    return current, inserted


class PathDistanceIndex:
    def __init__(self, path: Sequence[Point], bin_size: float) -> None:
        self.bin_size = max(bin_size, EPS)
        self.segments = list(zip(path, path[1:]))
        self.bins: dict[tuple[int, int], list[int]] = {}
        for idx, (a, b) in enumerate(self.segments):
            min_x = min(a[0], b[0])
            min_y = min(a[1], b[1])
            max_x = max(a[0], b[0])
            max_y = max(a[1], b[1])
            bx0, by0 = self.key((min_x, min_y))
            bx1, by1 = self.key((max_x, max_y))
            for by in range(by0, by1 + 1):
                for bx in range(bx0, bx1 + 1):
                    self.bins.setdefault((bx, by), []).append(idx)

    def key(self, p: Point) -> tuple[int, int]:
        return (math.floor(p[0] / self.bin_size), math.floor(p[1] / self.bin_size))

    def distance(self, p: Point, max_search: float) -> float:
        if not self.segments:
            return float("inf")

        bx, by = self.key(p)
        ring_count = max(1, int(math.ceil(max_search / self.bin_size)) + 1)
        best = float("inf")
        checked: set[int] = set()
        for ring in range(ring_count + 1):
            for cy in range(by - ring, by + ring + 1):
                for cx in range(bx - ring, bx + ring + 1):
                    if ring > 0 and bx - ring < cx < bx + ring and by - ring < cy < by + ring:
                        continue
                    for idx in self.bins.get((cx, cy), []):
                        if idx in checked:
                            continue
                        checked.add(idx)
                        a, b = self.segments[idx]
                        best = min(best, point_segment_distance(p, a, b))
            if best <= max(0.0, (ring - 1) * self.bin_size):
                break
        return best if best <= max_search else float("inf")


def build_residual_gap_grid(
    model: PolygonModel,
    source_grid: SDFGrid,
    path: Sequence[Point],
    line_width: float,
    spacing: float,
    exact_first_spacing: bool,
) -> SDFGrid:
    index = PathDistanceIndex(path, bin_size=max(spacing * 2.0, line_width))
    if exact_first_spacing:
        # The residual boundary is offset so the first generated residual
        # centerline lands exactly one nominal spacing from the existing path.
        extrusion_radius = max(spacing - line_width * 0.5, EPS)
    else:
        extrusion_radius = line_width * 0.5 + spacing * 0.10
    max_search = max(spacing * 5.0, line_width * 4.0)
    values: list[float] = []
    for j in range(source_grid.ny + 1):
        for i in range(source_grid.nx + 1):
            p = source_grid.point(i, j)
            model_sdf = source_grid.value(i, j)
            if model_sdf <= 0.0:
                values.append(model_sdf)
                continue
            path_distance = index.distance(p, max_search)
            stroke_sdf = max_search if path_distance == float("inf") else path_distance - extrusion_radius
            values.append(min(model_sdf, stroke_sdf))

    return SDFGrid(
        model=model,
        min_x=source_grid.min_x,
        min_y=source_grid.min_y,
        max_x=source_grid.max_x,
        max_y=source_grid.max_y,
        nx=source_grid.nx,
        ny=source_grid.ny,
        values=values,
    )


def residual_gap_groups(contours: Sequence[ContourLoop]) -> list[list[ContourLoop]]:
    if not contours:
        return []

    first_level = min(loop.level_index for loop in contours)
    seeds = [loop for loop in contours if loop.level_index == first_level]
    if len(seeds) <= 1:
        return [list(contours)]

    groups: list[list[ContourLoop]] = [[] for _ in seeds]
    for loop in contours:
        seed_index = min(range(len(seeds)), key=lambda idx: dist2(loop.centroid, seeds[idx].centroid))
        groups[seed_index].append(loop)

    return [group for group in groups if group]


def insert_residual_gap_spirals(
    model: PolygonModel,
    source_grid: SDFGrid,
    path: Sequence[Point],
    line_width: float,
    spacing: float,
    max_levels: int,
    spacing_tolerance: float,
) -> tuple[list[Point], int, int]:
    residual_cells = max(48, min(120, max(source_grid.nx, source_grid.ny)))
    residual_source_grid = build_sdf_grid(model, grid_cells=residual_cells, margin=line_width * 2.0)
    exact_first_spacing = len(model.holes) >= 2
    residual_grid = build_residual_gap_grid(model, residual_source_grid, path, line_width, spacing, exact_first_spacing)
    residual_spacing = spacing if exact_first_spacing else spacing * 1.25
    residual_contours = filter_printable_contours(
        generate_offset_contours(
            residual_grid,
            line_width=line_width,
            spacing=residual_spacing,
            max_levels=max(1, min(max_levels, 64)),
        ),
        spacing=residual_spacing,
    )
    residual_contours = [
        loop
        for loop in residual_contours
        if loop_uncovered_fraction(loop, path, spacing) > 0.65
    ]
    if not residual_contours:
        return list(path), 0, 0

    current = list(path)
    inserted = 0
    groups = sorted(residual_gap_groups(residual_contours), key=lambda group: -sum(loop.area for loop in group))
    for group in groups[:8]:
        candidate = try_insert_pocket_group(
            model,
            current,
            group,
            spacing=spacing,
            spacing_tolerance=spacing_tolerance,
        )
        if candidate is None:
            candidate = try_merge_gap_group(
                model,
                current,
                group,
                spacing=spacing,
                spacing_tolerance=spacing_tolerance,
            )
        if candidate is not None:
            current = candidate
            inserted += 1

    return current, inserted, len(residual_contours)


def try_merge_gap_group(
    model: PolygonModel,
    parent_path: Sequence[Point],
    group: Sequence[ContourLoop],
    spacing: float,
    spacing_tolerance: float,
) -> list[Point] | None:
    if len(parent_path) < 4 or not group:
        return None

    ordered = ordered_loops_for_spiral(group)
    if not ordered:
        return None

    first_loop = ordered[0]
    anchor_candidates: list[Point] = []
    stride = max(1, len(first_loop.points) // 24)
    sampled = first_loop.points[::stride]
    sampled.sort(key=lambda p: project_open_polyline(parent_path, p)[2])
    anchor_candidates.extend(sampled[:4])
    for fraction in (0.0, 0.25, 0.5, 0.75):
        anchor_candidates.append(point_at_closed_fraction(first_loop.points, fraction))

    best: tuple[tuple[int, int, int, float], list[Point]] | None = None
    for start_anchor in anchor_candidates:
        for exit_fraction in (0.5, 0.25, 0.75):
            exit_anchor = point_at_closed_fraction(first_loop.points, exit_fraction)
            child_path, _ = build_single_minimum_connected_fermat(
                ordered,
                start_anchor=start_anchor,
                spacing=spacing,
                port_spacing=spacing,
                exit_anchor=exit_anchor,
                preserve_medial_pockets=True,
            )
            for candidate_child in (child_path, list(reversed(child_path))):
                candidate = merge_child_spiral(parent_path, candidate_child, spacing)
                crossings, close_pairs, min_spacing = path_pair_metrics(
                    candidate,
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                )
                containment = 0
                if crossings == 0:
                    containment = count_containment_violations(model, candidate, spacing)
                score = (crossings, close_pairs, containment, -min_spacing)
                if best is None or score < best[0]:
                    best = (score, candidate)
                if crossings == 0 and close_pairs == 0 and containment == 0:
                    return candidate

    if best is not None and best[0][0] == 0 and best[0][1] == 0 and best[0][2] == 0:
        return best[1]
    return None


def build_branch_connected_fermat(
    contours: Sequence[ContourLoop],
    start_anchor: Point,
    spacing: float,
) -> tuple[list[Point], int]:
    grouped = loops_by_level(contours)
    levels = sorted(grouped)
    split_level = next((level for level in levels if len(grouped[level]) > 1), None)
    if split_level is None:
        return build_single_minimum_connected_fermat(
            ordered_loops_for_spiral(contours),
            start_anchor=start_anchor,
            spacing=spacing,
            port_spacing=spacing,
        )

    trunk = [grouped[level][0] for level in levels if level < split_level and len(grouped[level]) == 1]
    if not trunk:
        return build_single_minimum_connected_fermat(
            ordered_loops_for_spiral(contours),
            start_anchor=start_anchor,
            spacing=spacing,
            port_spacing=spacing,
        )

    parent_path, unsafe = build_single_minimum_connected_fermat(
        trunk,
        start_anchor=start_anchor,
        spacing=spacing,
        port_spacing=spacing,
    )

    chains = [[loop] for loop in sorted(grouped[split_level], key=lambda loop: (loop.centroid[0], loop.centroid[1]))]
    for level in (level for level in levels if level > split_level):
        remaining = list(grouped[level])
        used: set[int] = set()
        for chain in chains:
            last = chain[-1]
            best: tuple[float, int, ContourLoop] | None = None
            for idx, loop in enumerate(remaining):
                if idx in used:
                    continue
                d = dist2(last.centroid, loop.centroid)
                if best is None or d < best[0]:
                    best = (d, idx, loop)
            if best is not None:
                used.add(best[1])
                chain.append(best[2])

    for chain in chains:
        stride = max(1, len(chain[0].points) // 48)
        anchor = min((p for p in chain[0].points[::stride]), key=lambda p: project_open_polyline(parent_path, p)[2])
        child_path, child_unsafe = build_single_minimum_connected_fermat(
            chain,
            start_anchor=anchor,
            spacing=spacing,
            port_spacing=spacing,
        )
        unsafe += child_unsafe
        parent_path = merge_child_spiral(parent_path, child_path, spacing)

    return parent_path, unsafe


def build_horizontal_capsule_loop(
    left_center: Point,
    right_center: Point,
    radius: float,
    level_index: int,
    offset: float,
    segments: int = 64,
) -> ContourLoop:
    cy = (left_center[1] + right_center[1]) * 0.5
    left_x = left_center[0]
    right_x = right_center[0]
    half_segments = max(8, segments // 2)
    points: list[Point] = [(left_x, cy + radius), (right_x, cy + radius)]

    for i in range(1, half_segments + 1):
        angle = math.pi * 0.5 - math.pi * i / half_segments
        points.append((right_x + math.cos(angle) * radius, cy + math.sin(angle) * radius))

    points.append((left_x, cy - radius))

    for i in range(1, half_segments + 1):
        angle = -math.pi * 0.5 - math.pi * i / half_segments
        points.append((left_x + math.cos(angle) * radius, cy + math.sin(angle) * radius))

    if signed_area(points) < 0.0:
        points.reverse()
    return ContourLoop(
        level_index=level_index,
        offset=offset,
        points=points,
        area=abs(signed_area(points)),
        length=polyline_length(points, closed=True),
        centroid=polygon_centroid(points),
    )


def build_merged_hole_connected_fermat(
    model: PolygonModel,
    contours: Sequence[ContourLoop],
    start_anchor: Point,
    line_width: float,
    spacing: float,
) -> tuple[list[Point], int]:
    """Conservative multi-hole fallback.

    A strict no-travel CFS cannot always preserve independent hole islands
    without a multi-slot contour tree. For the prototype, merge multiple holes
    into one synthetic capsule cavity and fill the printable area around that
    cavity. This intentionally leaves the bridge material between holes
    unprinted, which is acceptable for this continuous mode when topology would
    otherwise force crossings.
    """

    grouped = loops_by_level(contours)
    outer: list[ContourLoop] = []
    min_outer_area = spacing * spacing * 20.0
    for level in sorted(grouped):
        loops = sorted(grouped[level], key=lambda loop: loop.area, reverse=True)
        if loops and loops[0].area > min_outer_area:
            outer.append(loops[0])

    if not outer or len(model.holes) < 2:
        return build_single_minimum_connected_fermat(
            ordered_loops_for_spiral(contours),
            start_anchor=start_anchor,
            spacing=spacing,
            port_spacing=spacing,
        )

    # Keep only the large outer-family contours; later medial fragments are
    # children of the hole topology and are intentionally replaced by the
    # conservative capsule cavity.
    stable_outer: list[ContourLoop] = []
    previous_area = None
    for loop in outer:
        if previous_area is not None and loop.area < previous_area * 0.2:
            break
        stable_outer.append(loop)
        previous_area = loop.area
    outer = stable_outer

    centers = sorted((polygon_centroid(hole) for hole in model.holes), key=lambda p: p[0])
    left_center = centers[0]
    right_center = centers[-1]
    base_radius = 0.0
    for hole, center in zip(model.holes, centers):
        base_radius = max(base_radius, max(dist(p, center) for p in hole))
    base_radius += line_width * 0.5

    _, min_y, _, max_y = model.bounds(margin=0.0)
    center_y = (left_center[1] + right_center[1]) * 0.5
    max_radius = min(center_y - min_y, max_y - center_y) - line_width

    inner: list[ContourLoop] = []
    for level_index in range(len(outer)):
        radius = base_radius + level_index * spacing
        if radius > max_radius:
            break
        inner.append(
            build_horizontal_capsule_loop(
                left_center,
                right_center,
                radius,
                level_index=level_index,
                offset=line_width * 0.5 + level_index * spacing,
            )
        )

    if not inner:
        return build_single_minimum_connected_fermat(
            ordered_loops_for_spiral(contours),
            start_anchor=start_anchor,
            spacing=spacing,
            port_spacing=spacing,
        )

    ordered = outer[: len(inner)] + list(reversed(inner))
    candidate_anchors = [start_anchor]
    for fraction in (0.04, 0.06, 0.10, 0.25, 0.50, 0.75):
        candidate_anchors.append(point_at_closed_fraction(ordered[0].points, fraction))

    best: tuple[tuple[int, int, float], list[Point], int] | None = None
    for anchor in candidate_anchors:
        candidate_path, unsafe = build_single_minimum_connected_fermat(
            ordered,
            start_anchor=anchor,
            spacing=spacing,
            port_spacing=spacing,
        )
        crossings, close_pairs, min_spacing = path_pair_metrics(
            candidate_path,
            spacing=spacing,
            spacing_tolerance=0.25,
        )
        score = (crossings, close_pairs, -min_spacing)
        if best is None or score < best[0]:
            best = (score, candidate_path, unsafe)
        if crossings == 0 and close_pairs == 0:
            return candidate_path, unsafe

    assert best is not None
    return best[1], best[2]


def close_path_to_cycle(
    model: PolygonModel,
    grid: SDFGrid,
    path: list[Point],
    line_width: float,
    spacing: float,
) -> int:
    if len(path) < 2:
        return 1
    if dist(path[-1], path[0]) <= EPS:
        return 0

    required_clearance = line_width * 0.5 - spacing * 0.15
    if segment_is_inside(model, path[-1], path[0], required_clearance, spacing * 0.5):
        append_point(path, path[0])
        return 0

    connector = grid_astar_connector(grid, path[-1], path[0], required_clearance=required_clearance)
    if connector is None:
        return 1
    append_points(path, connector)
    if dist(path[-1], path[0]) > EPS:
        append_point(path, path[0])
    return 0


def segment_is_inside(model: PolygonModel, a: Point, b: Point, required_clearance: float, step: float) -> bool:
    length = dist(a, b)
    samples = max(2, int(math.ceil(length / max(step, EPS))))
    for i in range(samples + 1):
        p = lerp(a, b, i / samples)
        if model.sdf(p) < required_clearance:
            return False
    return True


def sampled_connection_to_loop(
    model: PolygonModel,
    start: Point,
    loop: ContourLoop,
    required_clearance: float,
    spacing: float,
) -> tuple[int, float, bool]:
    stride = max(1, len(loop.points) // 96)
    candidates: list[tuple[float, int]] = []
    for idx in range(0, len(loop.points), stride):
        p = loop.points[idx]
        candidates.append((dist(start, p), idx))

    candidates.sort(key=lambda item: item[0])
    for candidate_dist, idx in candidates:
        if segment_is_inside(model, start, loop.points[idx], required_clearance, spacing * 0.7):
            return idx, candidate_dist, True
    best_dist, best_idx = candidates[0]
    return best_idx, best_dist, False


def nearest_valid_grid_node(grid: SDFGrid, p: Point, required_clearance: float) -> tuple[int, int] | None:
    ix = int(round((p[0] - grid.min_x) / max(grid.dx, EPS)))
    iy = int(round((p[1] - grid.min_y) / max(grid.dy, EPS)))
    ix = max(0, min(grid.nx, ix))
    iy = max(0, min(grid.ny, iy))

    best: tuple[float, int, int] | None = None
    max_radius = max(grid.nx, grid.ny)
    for radius in range(max_radius + 1):
        found_at_radius = False
        for y in range(max(0, iy - radius), min(grid.ny, iy + radius) + 1):
            for x in range(max(0, ix - radius), min(grid.nx, ix + radius) + 1):
                if abs(x - ix) != radius and abs(y - iy) != radius:
                    continue
                if grid.value(x, y) >= required_clearance:
                    d = dist2(p, grid.point(x, y))
                    if best is None or d < best[0]:
                        best = (d, x, y)
                        found_at_radius = True
        if found_at_radius:
            break
    if best is None:
        return None
    return (best[1], best[2])


def grid_astar_connector(grid: SDFGrid, start: Point, goal: Point, required_clearance: float) -> list[Point] | None:
    start_node = nearest_valid_grid_node(grid, start, required_clearance)
    goal_node = nearest_valid_grid_node(grid, goal, required_clearance)
    if start_node is None or goal_node is None:
        return None
    if start_node == goal_node:
        return [start, goal]

    def heuristic(node: tuple[int, int]) -> float:
        return dist(grid.point(node[0], node[1]), grid.point(goal_node[0], goal_node[1]))

    neighbors = [
        (-1, -1, math.sqrt(2.0)),
        (0, -1, 1.0),
        (1, -1, math.sqrt(2.0)),
        (-1, 0, 1.0),
        (1, 0, 1.0),
        (-1, 1, math.sqrt(2.0)),
        (0, 1, 1.0),
        (1, 1, math.sqrt(2.0)),
    ]

    open_heap: list[tuple[float, tuple[int, int]]] = [(heuristic(start_node), start_node)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {start_node: 0.0}
    closed: set[tuple[int, int]] = set()

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current == goal_node:
            nodes = [current]
            while nodes[-1] in came_from:
                nodes.append(came_from[nodes[-1]])
            nodes.reverse()
            points = [start]
            points.extend(grid.point(x, y) for x, y in nodes)
            points.append(goal)
            return simplify_polyline(points, tolerance=grid.step * 0.35)

        closed.add(current)
        cx, cy = current
        for ox, oy, step_cost in neighbors:
            nx = cx + ox
            ny = cy + oy
            if nx < 0 or nx > grid.nx or ny < 0 or ny > grid.ny:
                continue
            if grid.value(nx, ny) < required_clearance:
                continue
            neighbor = (nx, ny)
            tentative = g_score[current] + step_cost * grid.step
            if tentative < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative
                heapq.heappush(open_heap, (tentative + heuristic(neighbor), neighbor))

    return None


def connector_clear_of_path(
    existing_path: Sequence[Point],
    connector: Sequence[Point],
    spacing: float,
    spacing_tolerance: float,
    skip_tail: int = 10,
) -> bool:
    if len(existing_path) < 2 or len(connector) < 2:
        return True

    min_allowed = spacing * (1.0 - spacing_tolerance)
    existing_segments = list(zip(existing_path, existing_path[1:]))
    connector_segments = list(zip(connector, connector[1:]))
    compare_until = max(0, len(existing_segments) - skip_tail)

    for ca, cb in connector_segments:
        for ea, eb in existing_segments[:compare_until]:
            d = segment_distance(ca, cb, ea, eb)
            if d <= 1e-7 or d < min_allowed:
                return False
    return True


def choose_self_avoiding_entry(
    model: PolygonModel,
    path: Sequence[Point],
    current_point: Point,
    loop: ContourLoop,
    line_width: float,
    spacing: float,
    spacing_tolerance: float,
) -> tuple[int, float, bool]:
    required_clearance = line_width * 0.5 - spacing * 0.15
    stride = max(1, len(loop.points) // 80)
    candidates: list[tuple[float, int]] = [(dist(current_point, loop.points[idx]), idx) for idx in range(0, len(loop.points), stride)]
    candidates.sort(key=lambda item: item[0])

    for connector_length, idx in candidates:
        p = loop.points[idx]
        if not segment_is_inside(model, current_point, p, required_clearance, spacing * 0.7):
            continue
        if connector_clear_of_path(path, [current_point, p], spacing, spacing_tolerance):
            return idx, connector_length, True

    best_length, best_idx = candidates[0]
    return best_idx, best_length, False


def build_self_avoiding_open_spiral(
    model: PolygonModel,
    grid: SDFGrid,
    contours: Sequence[ContourLoop],
    anchor: Point,
    line_width: float,
    spacing: float,
    spacing_tolerance: float,
) -> tuple[list[Point], int]:
    if not contours:
        return [], 0

    remaining = set(range(len(contours)))
    start_idx = max(remaining, key=lambda idx: contours[idx].area)
    path: list[Point] = []
    unsafe_connectors = 0
    current_point = anchor
    direction = 1
    required_clearance = line_width * 0.5 - spacing * 0.15
    last_area = contours[start_idx].area

    while remaining:
        if not path:
            loop_idx = start_idx
            loop = contours[loop_idx]
            entry_idx = nearest_index(loop.points, current_point)
            safe = True
        else:
            best: tuple[float, int, int, bool] | None = None
            candidate_order = sorted(
                remaining,
                key=lambda idx: (
                    dist2(current_point, contours[idx].centroid),
                    abs(math.sqrt(max(contours[idx].area, 0.0)) - math.sqrt(max(last_area, 0.0))),
                ),
            )[:10]
            for candidate_idx in candidate_order:
                loop = contours[candidate_idx]
                entry_idx, connector_length, safe = choose_self_avoiding_entry(
                    model,
                    path,
                    current_point,
                    loop,
                    line_width=line_width,
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                )
                if not safe:
                    continue
                area_penalty = abs(math.sqrt(max(loop.area, 0.0)) - math.sqrt(max(last_area, 0.0))) * 0.35
                level_penalty = abs(loop.level_index - contours[start_idx].level_index) * spacing * 0.02
                score = connector_length + area_penalty + level_penalty
                if best is None or score < best[0]:
                    best = (score, candidate_idx, entry_idx, safe)

            if best is None:
                loop_idx = min(remaining, key=lambda idx: dist2(current_point, contours[idx].centroid))
                loop = contours[loop_idx]
                entry_idx, _, safe = sampled_connection_to_loop(
                    model,
                    current_point,
                    loop,
                    required_clearance=required_clearance,
                    spacing=spacing,
                )
                safe = safe and connector_clear_of_path(
                    path,
                    [current_point, loop.points[entry_idx]],
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                )
            else:
                _, loop_idx, entry_idx, safe = best
                loop = contours[loop_idx]

            entry_point = loop.points[entry_idx]
            if not safe:
                connector = grid_astar_connector(grid, current_point, entry_point, required_clearance=required_clearance)
                if connector is not None and connector_clear_of_path(path, connector, spacing, spacing_tolerance):
                    append_points(path, connector)
                else:
                    unsafe_connectors += 1

        gap = seam_gap_points(loop, spacing)
        append_points(path, almost_full_loop(loop.points, entry_idx, direction, gap))
        current_point = path[-1]
        last_area = loop.area
        remaining.remove(loop_idx)

    return path, unsafe_connectors


def scanline_intervals(
    model: PolygonModel,
    fixed: float,
    variable_min: float,
    variable_max: float,
    line_width: float,
    sample_step: float,
    vertical: bool,
) -> list[tuple[float, float]]:
    required = line_width * 0.5
    samples = max(8, int(math.ceil((variable_max - variable_min) / max(sample_step, EPS))))
    inside = []
    for i in range(samples + 1):
        v = variable_min + (variable_max - variable_min) * i / samples
        p = (fixed, v) if vertical else (v, fixed)
        inside.append(model.sdf(p) >= required)

    intervals: list[tuple[float, float]] = []
    start: float | None = None
    for i, is_inside in enumerate(inside):
        v = variable_min + (variable_max - variable_min) * i / samples
        if is_inside and start is None:
            start = v
        elif not is_inside and start is not None:
            prev = variable_min + (variable_max - variable_min) * (i - 1) / samples
            if prev - start >= line_width:
                intervals.append((start, prev))
            start = None
    if start is not None:
        if variable_max - start >= line_width:
            intervals.append((start, variable_max))
    return intervals


def grid_scanline_intervals(
    grid: SDFGrid,
    fixed: float,
    variable_min: float,
    variable_max: float,
    line_width: float,
    sample_step: float,
    vertical: bool,
    required_sdf: float | None = None,
    min_interval_length: float | None = None,
) -> list[tuple[float, float]]:
    required = line_width * 0.5 if required_sdf is None else required_sdf
    min_length = line_width if min_interval_length is None else min_interval_length
    samples = max(8, int(math.ceil((variable_max - variable_min) / max(sample_step, EPS))))
    values: list[tuple[float, float, bool]] = []
    for i in range(samples + 1):
        v = variable_min + (variable_max - variable_min) * i / samples
        p = (fixed, v) if vertical else (v, fixed)
        sdf = grid.sample(p)
        values.append((v, sdf, sdf >= required))

    def refined_crossing(left: tuple[float, float, bool], right: tuple[float, float, bool]) -> float:
        lo = left[0]
        hi = right[0]
        lo_inside = left[2]
        for _ in range(10):
            mid = 0.5 * (lo + hi)
            p = (fixed, mid) if vertical else (mid, fixed)
            mid_inside = grid.sample(p) >= required
            if mid_inside == lo_inside:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    intervals: list[tuple[float, float]] = []
    start: float | None = None
    prev: tuple[float, float, bool] | None = None
    for current in values:
        v, _, is_inside = current
        if prev is not None and prev[2] != is_inside:
            crossing = refined_crossing(prev, current)
            if is_inside:
                start = crossing
            elif start is not None:
                if crossing - start >= min_length:
                    intervals.append((start, crossing))
                start = None
            prev = current
            continue

        if is_inside and start is None:
            start = v
        elif not is_inside and start is not None:
            if v - start >= min_length:
                intervals.append((start, v))
            start = None
        prev = current

    if start is not None and variable_max - start >= min_length:
        intervals.append((start, variable_max))
    return intervals


def build_monotone_scanline_path(
    model: PolygonModel,
    grid: SDFGrid,
    line_width: float,
    spacing: float,
    vertical: bool = True,
) -> tuple[list[Point], bool]:
    min_x, min_y, max_x, max_y = model.bounds(margin=0.0)
    fixed_min, fixed_max = (min_x + line_width * 0.5, max_x - line_width * 0.5) if vertical else (min_y + line_width * 0.5, max_y - line_width * 0.5)
    variable_min, variable_max = (min_y + line_width * 0.5, max_y - line_width * 0.5) if vertical else (min_x + line_width * 0.5, max_x - line_width * 0.5)
    if fixed_max <= fixed_min or variable_max <= variable_min:
        return [], False

    count = max(2, int(math.floor((fixed_max - fixed_min) / spacing)) + 1)
    path: list[Point] = []
    reverse = False
    unsupported_multi_interval = False

    for i in range(count):
        fixed = fixed_min + i * spacing
        intervals = scanline_intervals(
            model,
            fixed,
            variable_min,
            variable_max,
            line_width=line_width,
            sample_step=spacing * 0.25,
            vertical=vertical,
        )
        if not intervals:
            continue
        if len(intervals) > 1:
            unsupported_multi_interval = True
            intervals = [max(intervals, key=lambda interval: interval[1] - interval[0])]

        a, b = intervals[0]
        segment = [(fixed, b), (fixed, a)] if reverse else [(fixed, a), (fixed, b)]
        if not vertical:
            segment = [(p[1], p[0]) for p in segment]
        if path and not segment_is_inside(model, path[-1], segment[0], line_width * 0.5 - spacing * 0.15, spacing * 0.5):
            connector = grid_astar_connector(grid, path[-1], segment[0], required_clearance=line_width * 0.5 - spacing * 0.15)
            if connector is None:
                unsupported_multi_interval = True
            else:
                append_points(path, connector)
        append_points(path, segment)
        reverse = not reverse

    return path, not unsupported_multi_interval


def scanline_fixed_values(fixed_min: float, fixed_max: float, spacing: float, phase: float) -> list[float]:
    if fixed_max < fixed_min:
        return []

    width = fixed_max - fixed_min
    if width <= EPS:
        return [0.5 * (fixed_min + fixed_max)]

    count = max(1, int(math.floor(width / max(spacing, EPS))) + 1)
    covered = spacing * (count - 1)
    start = fixed_min + max(0.0, width - covered) * 0.5 + phase * spacing
    while start - spacing >= fixed_min - EPS:
        start -= spacing

    values: list[float] = []
    fixed = start
    while fixed <= fixed_max + EPS:
        if fixed >= fixed_min - EPS:
            values.append(max(fixed_min, min(fixed_max, fixed)))
        fixed += spacing
    return values


def collect_scanline_spans(
    model: PolygonModel,
    grid: SDFGrid,
    line_width: float,
    spacing: float,
    vertical: bool,
    phase: float,
    required_sdf: float | None,
) -> list[ScanlineSpan]:
    min_x, min_y, max_x, max_y = model.bounds(margin=0.0)
    if vertical:
        fixed_min = min_x + line_width * 0.5
        fixed_max = max_x - line_width * 0.5
        variable_min = min_y + line_width * 0.5
        variable_max = max_y - line_width * 0.5
    else:
        fixed_min = min_y + line_width * 0.5
        fixed_max = max_y - line_width * 0.5
        variable_min = min_x + line_width * 0.5
        variable_max = max_x - line_width * 0.5

    spans: list[ScanlineSpan] = []
    min_interval_length = max(line_width * 0.35, spacing * 0.35)
    for fixed_index, fixed in enumerate(scanline_fixed_values(fixed_min, fixed_max, spacing, phase)):
        intervals = grid_scanline_intervals(
            grid,
            fixed,
            variable_min,
            variable_max,
            line_width=line_width,
            sample_step=spacing * 0.25,
            vertical=vertical,
            required_sdf=required_sdf,
            min_interval_length=min_interval_length,
        )
        for a, b in intervals:
            spans.append(ScanlineSpan(fixed_index, fixed, a, b, vertical))
    return spans


def scanline_span_overlap(a: ScanlineSpan, b: ScanlineSpan) -> float:
    return max(0.0, min(a.high, b.high) - max(a.low, b.low))


def assign_boustrophedon_cells(spans: Sequence[ScanlineSpan], spacing: float) -> list[ScanlineSpan]:
    columns: dict[int, list[ScanlineSpan]] = {}
    for span in spans:
        columns.setdefault(span.fixed_index, []).append(span)
    for column in columns.values():
        column.sort(key=lambda span: (span.low, span.high))

    next_cell_id = 0
    previous_column: list[ScanlineSpan] = []
    assigned: list[ScanlineSpan] = []
    min_overlap = max(spacing * 0.10, EPS)

    for fixed_index in sorted(columns):
        column = columns[fixed_index]
        current_to_previous: dict[int, list[int]] = {idx: [] for idx in range(len(column))}
        previous_to_current: dict[int, list[int]] = {idx: [] for idx in range(len(previous_column))}

        for current_idx, current in enumerate(column):
            for previous_idx, previous in enumerate(previous_column):
                if scanline_span_overlap(current, previous) >= min_overlap:
                    current_to_previous[current_idx].append(previous_idx)
                    previous_to_current[previous_idx].append(current_idx)

        new_column: list[ScanlineSpan] = []
        for current_idx, current in enumerate(column):
            predecessors = current_to_previous[current_idx]
            if len(predecessors) == 1 and len(previous_to_current[predecessors[0]]) == 1:
                cell_id = previous_column[predecessors[0]].cell_id
            else:
                cell_id = next_cell_id
                next_cell_id += 1
            new_span = current.with_cell(cell_id)
            new_column.append(new_span)
            assigned.append(new_span)

        previous_column = new_column

    return assigned


def route_middle_connector(
    grid: SDFGrid,
    existing_path: Sequence[Point],
    start: Point,
    goal: Point,
    required_clearance: float,
    spacing: float,
    spacing_tolerance: float,
    allow_astar: bool = True,
    astar_cache: dict[tuple[int, int, int, int, int], list[Point] | None] | None = None,
) -> list[Point] | None:
    if dist(start, goal) <= EPS:
        return [start, goal]

    direct = [start, goal]
    if segment_is_inside(grid.model, start, goal, required_clearance, spacing * 0.35) and connector_clear_of_path(
        existing_path,
        direct,
        spacing,
        spacing_tolerance,
        skip_tail=6,
    ):
        return direct

    if not allow_astar:
        return None

    cache_key = (
        round(start[0] * 1000),
        round(start[1] * 1000),
        round(goal[0] * 1000),
        round(goal[1] * 1000),
        round(required_clearance * 1000),
    )
    if astar_cache is not None and cache_key in astar_cache:
        connector = astar_cache[cache_key]
    else:
        connector = grid_astar_connector(grid, start, goal, required_clearance=required_clearance)
        if astar_cache is not None:
            astar_cache[cache_key] = connector
    if connector is not None and connector_clear_of_path(
        existing_path,
        connector,
        spacing,
        spacing_tolerance,
        skip_tail=6,
    ):
        return connector

    return None


def build_scanline_cell_path(
    grid: SDFGrid,
    spans: Sequence[ScanlineSpan],
    line_width: float,
    spacing: float,
    spacing_tolerance: float,
    start_high: bool,
) -> list[Point] | None:
    if not spans:
        return None

    path: list[Point] = []
    reverse = start_high
    connector_clearance = line_width * 0.5 - spacing * 0.15
    ordered = sorted(spans, key=lambda span: (span.fixed_index, span.low, span.high))

    for span in ordered:
        segment = span.segment(reverse)
        if path:
            connector = route_middle_connector(
                grid,
                path,
                path[-1],
                segment[0],
                required_clearance=connector_clearance,
                spacing=spacing,
                spacing_tolerance=spacing_tolerance,
            )
            if connector is None:
                return None
            append_points(path, connector)
        append_points(path, segment)
        reverse = not reverse

    return path


def cell_path_variants(
    grid: SDFGrid,
    spans: Sequence[ScanlineSpan],
    line_width: float,
    spacing: float,
    spacing_tolerance: float,
) -> list[list[Point]]:
    variants: list[list[Point]] = []
    seen: set[tuple[tuple[int, int], tuple[int, int], int]] = set()

    for start_high in (False, True):
        path = build_scanline_cell_path(
            grid,
            spans,
            line_width=line_width,
            spacing=spacing,
            spacing_tolerance=spacing_tolerance,
            start_high=start_high,
        )
        if path is None or len(path) < 2:
            continue
        for candidate in (path, list(reversed(path))):
            key = (
                (round(candidate[0][0] * 1000), round(candidate[0][1] * 1000)),
                (round(candidate[-1][0] * 1000), round(candidate[-1][1] * 1000)),
                len(candidate),
            )
            if key in seen:
                continue
            seen.add(key)
            variants.append(candidate)

    return variants


def chain_scanline_cells(
    grid: SDFGrid,
    cell_variants: dict[int, list[list[Point]]],
    cell_centers: dict[int, Point],
    line_width: float,
    spacing: float,
    spacing_tolerance: float,
    start_cell: int,
    astar_cache: dict[tuple[int, int, int, int, int], list[Point] | None] | None = None,
) -> list[Point] | None:
    if start_cell not in cell_variants:
        return None

    connector_clearance = line_width * 0.5 - spacing * 0.15
    all_cells = set(cell_variants)
    cell_count = len(cell_variants)
    beam_width = 16 if cell_count <= 5 else 8
    states: list[tuple[float, list[Point], frozenset[int]]] = []

    for variant in cell_variants[start_cell]:
        remaining = frozenset(all_cells - {start_cell})
        states.append((polyline_length(variant, closed=False) * 0.001, list(variant), remaining))

    while states:
        complete = [(score, path) for score, path, remaining in states if not remaining]
        if complete:
            complete.sort(
                key=lambda item: (
                    path_pair_metrics(item[1], spacing=spacing, spacing_tolerance=spacing_tolerance)[0],
                    count_containment_violations(grid.model, item[1], spacing),
                    item[0],
                )
            )
            return complete[0][1]

        next_states: list[tuple[float, list[Point], frozenset[int]]] = []
        for state_score, path, remaining in states:
            ordered_cells = sorted(
                remaining,
                key=lambda cell_id: (
                    dist2(path[-1], cell_centers[cell_id]),
                    cell_centers[cell_id][0],
                    cell_centers[cell_id][1],
                ),
            )
            search_limit = 6 if cell_count <= 5 else 4
            search_cells = ordered_cells if len(ordered_cells) <= search_limit else ordered_cells[:search_limit]
            for cell_id in search_cells:
                remaining_after = frozenset(cell for cell in remaining if cell != cell_id)
                for variant in cell_variants[cell_id]:
                    connector = route_middle_connector(
                        grid,
                        path,
                        path[-1],
                        variant[0],
                        required_clearance=connector_clearance,
                        spacing=spacing,
                        spacing_tolerance=spacing_tolerance,
                        astar_cache=astar_cache,
                    )
                    if connector is None:
                        continue

                    lookahead = 0.0
                    if remaining_after:
                        lookahead = min(dist(variant[-1], cell_centers[other]) for other in remaining_after)

                    next_path = list(path)
                    append_points(next_path, connector)
                    append_points(next_path, variant)
                    next_score = state_score + polyline_length(connector, closed=False) + lookahead * 0.35
                    next_states.append((next_score, next_path, remaining_after))

        if not next_states:
            return None

        next_states.sort(key=lambda item: item[0])
        states = next_states[:beam_width]

    return None


def chain_scanline_cells_in_order(
    grid: SDFGrid,
    cell_variants: dict[int, list[list[Point]]],
    cell_centers: dict[int, Point],
    order: Sequence[int],
    line_width: float,
    spacing: float,
    spacing_tolerance: float,
    astar_cache: dict[tuple[int, int, int, int, int], list[Point] | None] | None = None,
) -> list[Point] | None:
    path: list[Point] = []
    connector_clearance = line_width * 0.5 - spacing * 0.15

    for order_index, cell_id in enumerate(order):
        if cell_id not in cell_variants:
            return None

        next_center = cell_centers[order[order_index + 1]] if order_index + 1 < len(order) else None
        best: tuple[float, list[Point], list[Point] | None] | None = None
        for variant in cell_variants[cell_id]:
            connector: list[Point] | None = None
            connector_length = 0.0
            if path:
                connector = route_middle_connector(
                    grid,
                    path,
                    path[-1],
                    variant[0],
                    required_clearance=connector_clearance,
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                    astar_cache=astar_cache,
                )
                if connector is None:
                    continue
                connector_length = polyline_length(connector, closed=False)

            lookahead = dist(variant[-1], next_center) if next_center is not None else 0.0
            start_bias = (variant[0][0] + variant[0][1]) * 0.001 if not path else 0.0
            score = connector_length + lookahead * 0.35 + start_bias
            if best is None or score < best[0]:
                best = (score, variant, connector)

        if best is None:
            return None

        _, variant, connector = best
        if connector is not None:
            append_points(path, connector)
        append_points(path, variant)

    return path


def scanline_cell_orders(cell_centers: dict[int, Point]) -> list[list[int]]:
    if not cell_centers:
        return []

    cell_ids = list(cell_centers)
    centroid = (
        sum(cell_centers[cell_id][0] for cell_id in cell_ids) / len(cell_ids),
        sum(cell_centers[cell_id][1] for cell_id in cell_ids) / len(cell_ids),
    )
    base_orders = [
        sorted(cell_ids, key=lambda cell_id: (cell_centers[cell_id][0], cell_centers[cell_id][1])),
        sorted(cell_ids, key=lambda cell_id: (cell_centers[cell_id][1], cell_centers[cell_id][0])),
        sorted(
            cell_ids,
            key=lambda cell_id: math.atan2(cell_centers[cell_id][1] - centroid[1], cell_centers[cell_id][0] - centroid[0]),
        ),
    ]

    orders: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()

    def add_order(order: Sequence[int]) -> None:
        if not order:
            return
        rotations = range(len(order)) if len(order) <= 7 else (0,)
        for rotation in rotations:
            rotated = list(order[rotation:]) + list(order[:rotation])
            for candidate in (rotated, list(reversed(rotated))):
                key = tuple(candidate)
                if key in seen:
                    continue
                seen.add(key)
                orders.append(candidate)

    for order in base_orders:
        add_order(order)

    return orders


def build_cellular_scanline_path_candidates(
    model: PolygonModel,
    grid: SDFGrid,
    line_width: float,
    spacing: float,
    required_sdf: float | None = None,
    spacing_tolerance: float = 0.25,
    limit: int = 6,
) -> list[list[Point]]:
    candidates: list[tuple[tuple[int, int, float, float], list[Point]]] = []

    def unique_candidates() -> list[list[Point]]:
        candidates.sort(key=lambda item: item[0])
        unique: list[list[Point]] = []
        seen_keys: set[tuple[tuple[int, int], tuple[int, int], int]] = set()
        for _, path in candidates:
            key = (
                (round(path[0][0] * 1000), round(path[0][1] * 1000)),
                (round(path[-1][0] * 1000), round(path[-1][1] * 1000)),
                len(path),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique.append(path)
            if len(unique) >= limit:
                break
        return unique

    sweep_options: list[tuple[int, int, bool, float, list[ScanlineSpan]]] = []
    for vertical, phase in ((True, 0.0), (False, 0.0), (True, 0.5), (False, 0.5)):
        spans = assign_boustrophedon_cells(
            collect_scanline_spans(
                model,
                grid,
                line_width=line_width,
                spacing=spacing,
                vertical=vertical,
                phase=phase,
                required_sdf=required_sdf,
            ),
            spacing=spacing,
        )
        if not spans:
            continue
        cell_count = len({span.cell_id for span in spans})
        sweep_options.append((cell_count, len(spans), vertical, phase, spans))

    sweep_options.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    for _, _, _vertical, _phase, spans in sweep_options:
        cells: dict[int, list[ScanlineSpan]] = {}
        for span in spans:
            cells.setdefault(span.cell_id, []).append(span)

        cell_variants: dict[int, list[list[Point]]] = {}
        cell_centers: dict[int, Point] = {}
        for cell_id, cell_spans in cells.items():
            variants = cell_path_variants(
                grid,
                cell_spans,
                line_width=line_width,
                spacing=spacing,
                spacing_tolerance=spacing_tolerance,
            )
            if not variants:
                break
            cell_variants[cell_id] = variants
            cell_centers[cell_id] = (
                sum(span.center[0] for span in cell_spans) / len(cell_spans),
                sum(span.center[1] for span in cell_spans) / len(cell_spans),
            )
        else:
            astar_cache: dict[tuple[int, int, int, int, int], list[Point] | None] = {}

            for order in scanline_cell_orders(cell_centers):
                path = chain_scanline_cells_in_order(
                    grid,
                    cell_variants,
                    cell_centers,
                    order,
                    line_width=line_width,
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                    astar_cache=astar_cache,
                )
                if path is None or len(path) < 2:
                    continue

                crossings, close_pairs, min_spacing = path_pair_metrics(
                    path,
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                )
                containment = count_containment_violations(model, path, spacing) if crossings == 0 else 1
                score = (crossings, containment, -min_spacing, polyline_length(path, closed=False))
                candidates.append((score, path))
                if crossings == 0 and containment == 0 and len(unique_candidates()) >= min(2, limit):
                    return unique_candidates()

            clean = [item for item in candidates if item[0][0] == 0 and item[0][1] == 0]
            if clean:
                return unique_candidates()

            ring_path = build_hole_band_scanline_path(
                model,
                grid,
                line_width=line_width,
                spacing=spacing,
                required_sdf=required_sdf,
                spacing_tolerance=spacing_tolerance,
            )
            if ring_path:
                crossings, close_pairs, min_spacing = path_pair_metrics(
                    ring_path,
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                )
                containment = count_containment_violations(model, ring_path, spacing) if crossings == 0 else 1
                score = (crossings, containment, -min_spacing, polyline_length(ring_path, closed=False))
                candidates.append((score, ring_path))
                if crossings == 0 and containment == 0:
                    return unique_candidates()

            start_cells = sorted(
                cell_variants,
                key=lambda cell_id: (cell_centers[cell_id][0], cell_centers[cell_id][1]),
            )
            if len(start_cells) > 1:
                middle = start_cells[len(start_cells) // 2]
                start_cells = [start_cells[0], start_cells[-1], middle]
            tried_starts: set[int] = set()
            for start_cell in start_cells:
                if start_cell in tried_starts:
                    continue
                tried_starts.add(start_cell)
                path = chain_scanline_cells(
                    grid,
                    cell_variants,
                    cell_centers,
                    line_width=line_width,
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                    start_cell=start_cell,
                    astar_cache=astar_cache,
                )
                if path is None or len(path) < 2:
                    continue

                crossings, close_pairs, min_spacing = path_pair_metrics(
                    path,
                    spacing=spacing,
                    spacing_tolerance=spacing_tolerance,
                )
                containment = count_containment_violations(model, path, spacing) if crossings == 0 else 1
                score = (crossings, containment, -min_spacing, polyline_length(path, closed=False))
                candidates.append((score, path))
                if crossings == 0 and containment == 0 and len(unique_candidates()) >= min(2, limit):
                    return unique_candidates()

    return unique_candidates()


def build_hole_band_scanline_path(
    model: PolygonModel,
    grid: SDFGrid,
    line_width: float,
    spacing: float,
    required_sdf: float | None,
    spacing_tolerance: float,
    phase: float = 0.0,
) -> list[Point]:
    if not model.holes:
        return []

    min_x, min_y, max_x, max_y = model.bounds(margin=0.0)
    hole_points = [p for hole in model.holes for p in hole]
    y_cut = sum(p[1] for p in hole_points) / max(1, len(hole_points))
    fixed_min = min_x + line_width * 0.5
    fixed_max = max_x - line_width * 0.5
    variable_min = min_y + line_width * 0.5
    variable_max = max_y - line_width * 0.5
    fixed_values = scanline_fixed_values(fixed_min, fixed_max, spacing, phase=phase)

    lower: list[tuple[float, float, float]] = []
    upper: list[tuple[float, float, float]] = []
    cut_gap = spacing * 0.50
    for fixed in fixed_values:
        intervals = grid_scanline_intervals(
            grid,
            fixed,
            variable_min,
            variable_max,
            line_width=line_width,
            sample_step=spacing * 0.25,
            vertical=True,
            required_sdf=required_sdf,
            min_interval_length=max(line_width * 0.35, spacing * 0.35),
        )
        for a, b in intervals:
            if a < y_cut - cut_gap:
                lo = a
                hi = min(b, y_cut - cut_gap)
                if hi - lo >= spacing * 0.35:
                    lower.append((fixed, lo, hi))
            if b > y_cut + cut_gap:
                lo = max(a, y_cut + cut_gap)
                hi = b
                if hi - lo >= spacing * 0.35:
                    upper.append((fixed, lo, hi))

    path: list[Point] = []
    connector_clearance = line_width * 0.5 - spacing * 0.15

    def append_segment(segment: list[Point]) -> bool:
        if path:
            connector = route_middle_connector(
                grid,
                path,
                path[-1],
                segment[0],
                required_clearance=connector_clearance,
                spacing=spacing,
                spacing_tolerance=spacing_tolerance,
                astar_cache={},
            )
            if connector is None:
                return False
            append_points(path, connector)
        append_points(path, segment)
        return True

    reverse = False
    for fixed, a, b in lower:
        if not append_segment([(fixed, b), (fixed, a)] if reverse else [(fixed, a), (fixed, b)]):
            return []
        reverse = not reverse

    upper_order = list(reversed(upper))
    reverse = False
    if path and upper_order:
        fixed, a, b = upper_order[0]
        if abs(path[-1][0] - fixed) <= EPS and abs(path[-1][1] - a) < abs(path[-1][1] - b):
            reverse = True
    for fixed, a, b in upper_order:
        if not append_segment([(fixed, a), (fixed, b)] if reverse else [(fixed, b), (fixed, a)]):
            return []
        reverse = not reverse

    if len(path) < 2:
        return []
    crossings, _, _ = path_pair_metrics(path, spacing=spacing, spacing_tolerance=spacing_tolerance)
    if crossings != 0 or count_containment_violations(model, path, spacing) != 0:
        return []
    return path


def outer_wall_preserving_scanline_intervals(
    model: PolygonModel,
    fixed: float,
    variable_min: float,
    variable_max: float,
    line_width: float,
    spacing: float,
    wall_count: int,
    vertical: bool,
    hole_bounds: Sequence[tuple[Sequence[Point], tuple[float, float, float, float]]] | None = None,
) -> list[tuple[float, float]]:
    outer_required = line_width * 0.5 + spacing * wall_count
    hole_required = line_width * 0.5
    samples = max(8, int(math.ceil((variable_max - variable_min) / max(spacing * 0.25, EPS))))
    hole_data = hole_bounds
    if hole_data is None:
        hole_data = [(hole, loop_bounds(hole, hole_required)) for hole in model.holes]

    def clear_of_holes(p: Point) -> bool:
        for hole, bounds in hole_data:
            min_x, min_y, max_x, max_y = bounds
            if p[0] < min_x or p[0] > max_x or p[1] < min_y or p[1] > max_y:
                continue
            if point_in_polygon(p, hole) or distance_to_loop(p, hole) < hole_required:
                return False
        return True

    intervals: list[tuple[float, float]] = []
    start: float | None = None
    for i in range(samples + 1):
        v = variable_min + (variable_max - variable_min) * i / samples
        p = (fixed, v) if vertical else (v, fixed)
        inside = (
            point_in_polygon(p, model.outer)
            and distance_to_loop(p, model.outer) >= outer_required
            and clear_of_holes(p)
        )
        if inside and start is None:
            start = v
        elif not inside and start is not None:
            if v - start >= spacing * 0.35:
                intervals.append((start, v))
            start = None

    if start is not None and variable_max - start >= spacing * 0.35:
        intervals.append((start, variable_max))
    return intervals


def build_outer_wall_preserving_hole_band_path(
    model: PolygonModel,
    grid: SDFGrid,
    line_width: float,
    spacing: float,
    wall_count: int,
    spacing_tolerance: float,
    phase: float,
) -> list[Point]:
    if not model.holes:
        return []

    min_x, min_y, max_x, max_y = model.bounds(margin=0.0)
    fixed_min = min_x + line_width * 0.5
    fixed_max = max_x - line_width * 0.5
    variable_min = min_y + line_width * 0.5
    variable_max = max_y - line_width * 0.5
    hole_points = [p for hole in model.holes for p in hole]
    y_cut = sum(p[1] for p in hole_points) / max(1, len(hole_points))
    hole_bounds = [(hole, loop_bounds(hole, line_width * 0.5)) for hole in model.holes]

    lower: list[tuple[float, float, float]] = []
    upper: list[tuple[float, float, float]] = []
    cut_gap = spacing * 0.5
    for fixed in scanline_fixed_values(fixed_min, fixed_max, spacing, phase):
        intervals = outer_wall_preserving_scanline_intervals(
            model,
            fixed,
            variable_min,
            variable_max,
            line_width=line_width,
            spacing=spacing,
            wall_count=wall_count,
            vertical=True,
            hole_bounds=hole_bounds,
        )
        for a, b in intervals:
            if a < y_cut - cut_gap:
                lo = a
                hi = min(b, y_cut - cut_gap)
                if hi - lo >= spacing * 0.35:
                    lower.append((fixed, lo, hi))
            if b > y_cut + cut_gap:
                lo = max(a, y_cut + cut_gap)
                hi = b
                if hi - lo >= spacing * 0.35:
                    upper.append((fixed, lo, hi))

    path: list[Point] = []
    connector_clearance = line_width * 0.5 - spacing * 0.15
    astar_cache: dict[tuple[int, int, int, int, int], list[Point] | None] = {}

    def append_segment(segment: list[Point]) -> bool:
        if path:
            connector = route_middle_connector(
                grid,
                path,
                path[-1],
                segment[0],
                required_clearance=connector_clearance,
                spacing=spacing,
                spacing_tolerance=spacing_tolerance,
                astar_cache=astar_cache,
            )
            if connector is None:
                return False
            append_points(path, connector)
        append_points(path, segment)
        return True

    reverse = False
    for fixed, a, b in lower:
        if not append_segment([(fixed, b), (fixed, a)] if reverse else [(fixed, a), (fixed, b)]):
            return []
        reverse = not reverse

    upper_order = list(reversed(upper))
    reverse = False
    if path and upper_order:
        fixed, a, b = upper_order[0]
        if abs(path[-1][0] - fixed) <= EPS and abs(path[-1][1] - a) < abs(path[-1][1] - b):
            reverse = True
    for fixed, a, b in upper_order:
        if not append_segment([(fixed, a), (fixed, b)] if reverse else [(fixed, b), (fixed, a)]):
            return []
        reverse = not reverse

    return path


def build_outer_wall_preserving_hole_band_candidates(
    model: PolygonModel,
    grid: SDFGrid,
    line_width: float,
    spacing: float,
    wall_count: int,
    spacing_tolerance: float,
) -> list[list[Point]]:
    candidates: list[tuple[tuple[int, int, int, float, float], list[Point]]] = []
    for phase in (0.0, 0.25, 0.5, 0.75):
        path = build_outer_wall_preserving_hole_band_path(
            model,
            grid,
            line_width=line_width,
            spacing=spacing,
            wall_count=wall_count,
            spacing_tolerance=spacing_tolerance,
            phase=phase,
        )
        if len(path) < 2:
            continue
        for candidate in (path, list(reversed(path))):
            crossings, close_pairs, min_spacing = path_pair_metrics(
                candidate,
                spacing=spacing,
                spacing_tolerance=spacing_tolerance,
            )
            retraces = count_immediate_retraces(candidate, spacing)
            containment = 0 if crossings == 0 and retraces == 0 else 1
            score = (
                crossings + retraces,
                containment,
                close_pairs,
                -min_spacing,
                polyline_length(candidate, closed=False),
            )
            candidates.append((score, candidate))

    candidates.sort(key=lambda item: item[0])
    unique: list[list[Point]] = []
    seen: set[tuple[tuple[int, int], tuple[int, int], int]] = set()
    for _, path in candidates:
        key = (
            (round(path[0][0] * 1000), round(path[0][1] * 1000)),
            (round(path[-1][0] * 1000), round(path[-1][1] * 1000)),
            len(path),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
        if len(unique) >= 8:
            break
    return unique


def build_two_band_scanline_path(
    model: PolygonModel,
    grid: SDFGrid,
    line_width: float,
    spacing: float,
    required_sdf: float | None = None,
) -> list[Point]:
    candidates = build_cellular_scanline_path_candidates(
        model,
        grid,
        line_width=line_width,
        spacing=spacing,
        required_sdf=required_sdf,
        spacing_tolerance=0.25,
        limit=1,
    )
    return candidates[0] if candidates else []


def point_line_distance(p: Point, a: Point, b: Point) -> float:
    return point_segment_distance(p, a, b)


def simplify_polyline(points: Sequence[Point], tolerance: float) -> list[Point]:
    if len(points) <= 2:
        return list(points)

    def simplify_range(start: int, end: int, out: list[Point]) -> None:
        max_dist = -1.0
        max_idx = start
        for idx in range(start + 1, end):
            d = point_line_distance(points[idx], points[start], points[end])
            if d > max_dist:
                max_dist = d
                max_idx = idx
        if max_dist > tolerance:
            simplify_range(start, max_idx, out)
            out.pop()
            simplify_range(max_idx, end, out)
        else:
            out.append(points[start])
            out.append(points[end])

    simplified: list[Point] = []
    simplify_range(0, len(points) - 1, simplified)
    deduped: list[Point] = []
    append_points(deduped, simplified)
    return deduped


def build_safe_chained_contours(
    model: PolygonModel,
    grid: SDFGrid,
    contours: Sequence[ContourLoop],
    anchor: Point,
    line_width: float,
    spacing: float,
) -> tuple[list[Point], int]:
    """Connect all contour loops into one stroke.

    This handles holes and branch-heavy cases in the prototype. It is deliberately
    conservative: each contour loop is covered as a loop, and the next loop is
    reached by the shortest sampled connector that stays in the printable domain.
    Production code should replace this with a real CFS contour-tree traversal,
    but this version is useful because it exposes containment and speed problems.
    """

    if not contours:
        return [], 0

    ordered = sorted(range(len(contours)), key=lambda idx: (contours[idx].level_index, -contours[idx].area))
    path: list[Point] = []
    unsafe_connectors = 0
    current_point = anchor
    direction = 1

    for order_pos, current_idx in enumerate(ordered):
        loop = contours[current_idx]
        start_point_idx, _, safe = sampled_connection_to_loop(
            model,
            current_point,
            loop,
            required_clearance=line_width * 0.5 - spacing * 0.15,
            spacing=spacing,
        )
        if path and not safe:
            connector = grid_astar_connector(
                grid,
                current_point,
                loop.points[start_point_idx],
                required_clearance=line_width * 0.5 - spacing * 0.15,
            )
            if connector is None:
                unsafe_connectors += 1
            else:
                append_points(path, connector)

        append_points(path, full_loop(loop.points, start_point_idx, direction))
        current_point = path[-1]
        direction *= -1

    return path, unsafe_connectors


@dataclass
class ValidationMetrics:
    path_points: int
    path_length: float
    contour_count: int
    contour_levels: int
    max_segment: float
    containment_violations: int
    clearance_warnings: int
    min_sdf: float
    self_intersections: int
    spacing_violations: int
    min_nonlocal_spacing: float
    start_outer_distance: float
    end_outer_distance: float
    start_on_outer: bool
    end_on_outer: bool
    unsafe_connectors: int
    disconnected_path_count: int
    endpoint_gap: float
    seam_to_adjacent_spacing: float
    local_uncovered_area_ratio: float
    coverage_ratio: float
    underfill_ratio: float
    overfill_ratio: float
    elapsed_seconds: float
    strategy: str
    ok: bool


def seam_to_adjacent_spacing(path: Sequence[Point], spacing: float) -> float:
    if len(path) < 5:
        return 0.0
    seam_a = path[-2]
    seam_b = path[-1]
    best = float("inf")
    segments = list(zip(path, path[1:]))
    for idx, (a, b) in enumerate(segments[:-1]):
        if idx < 4 or idx >= len(segments) - 5:
            continue
        best = min(best, segment_distance(seam_a, seam_b, a, b))
    return best if best < float("inf") else spacing


def local_uncovered_area_ratio(
    model: PolygonModel,
    path: Sequence[Point],
    line_width: float,
    spacing: float,
    segment_widths: Sequence[float] | None = None,
) -> float:
    if not path:
        return 1.0
    center = path[0]
    radius = spacing * 2.0
    cells = 48
    covered = 0
    inside = 0
    min_x = center[0] - radius
    min_y = center[1] - radius
    step = (radius * 2.0) / cells
    widths = (
        segment_widths
        if segment_widths is not None and len(segment_widths) == max(0, len(path) - 1)
        else [line_width] * max(0, len(path) - 1)
    )
    for y in range(cells):
        py = min_y + (y + 0.5) * step
        for x in range(cells):
            p = (min_x + (x + 0.5) * step, py)
            if dist(p, center) > radius or not model.contains(p):
                continue
            inside += 1
            if any(point_segment_distance(p, a, b) <= width * 0.5 for width, (a, b) in zip(widths, zip(path, path[1:]))):
                covered += 1
    return 0.0 if inside == 0 else 1.0 - covered / inside


def segment_widths_for_path(path: Sequence[Point], contours: Sequence[ContourLoop], line_width: float, spacing: float) -> list[float]:
    if len(path) < 2:
        return []

    adaptive = [loop for loop in contours if loop.line_width > EPS and abs(loop.line_width - line_width) > line_width * 0.01]
    if not adaptive:
        return [line_width] * (len(path) - 1)

    tolerance = max(spacing * 0.32, line_width * 0.32)
    widths: list[float] = []
    for a, b in zip(path, path[1:]):
        mid = lerp(a, b, 0.5)
        width = line_width
        best_distance = float("inf")
        for loop in adaptive:
            d = max(contour_distance(a, loop), contour_distance(mid, loop), contour_distance(b, loop))
            if d < best_distance and d <= tolerance:
                best_distance = d
                width = loop.line_width
        widths.append(width)
    return widths


def raster_coverage(
    model: PolygonModel,
    path: Sequence[Point],
    bounds: tuple[float, float, float, float],
    line_width: float,
    cells: int,
    segment_widths: Sequence[float] | None = None,
) -> tuple[float, float, float]:
    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    if width <= EPS or height <= EPS:
        return 0.0, 1.0, 0.0
    if width >= height:
        nx = cells
        ny = max(12, int(round(cells * height / width)))
    else:
        ny = cells
        nx = max(12, int(round(cells * width / height)))
    cell = max(width / nx, height / ny)
    max_line_width = max([line_width, *(segment_widths or [])])
    radius_cells = max(1, int(math.ceil((max_line_width * 0.5) / cell)) + 1)

    covered = bytearray(nx * ny)

    def mark(p: Point, bead_width: float) -> None:
        ix = int((p[0] - min_x) / width * nx)
        iy = int((p[1] - min_y) / height * ny)
        if ix < -radius_cells or ix >= nx + radius_cells or iy < -radius_cells or iy >= ny + radius_cells:
            return
        radius = bead_width * 0.5
        for y in range(max(0, iy - radius_cells), min(ny, iy + radius_cells + 1)):
            cy = min_y + (y + 0.5) * height / ny
            for x in range(max(0, ix - radius_cells), min(nx, ix + radius_cells + 1)):
                cx = min_x + (x + 0.5) * width / nx
                if dist((cx, cy), p) <= radius + cell * 0.75:
                    covered[y * nx + x] = 1

    widths = list(segment_widths) if segment_widths is not None and len(segment_widths) == len(path) - 1 else [line_width] * max(0, len(path) - 1)
    for width_this_segment, (a, b) in zip(widths, zip(path, path[1:])):
        length = dist(a, b)
        steps = max(1, int(math.ceil(length / max(cell * 0.45, EPS))))
        for i in range(steps + 1):
            mark(lerp(a, b, i / steps), width_this_segment)

    inside_count = 0
    inside_covered = 0
    outside_covered = 0
    outside_count = 0
    for y in range(ny):
        cy = min_y + (y + 0.5) * height / ny
        for x in range(nx):
            cx = min_x + (x + 0.5) * width / nx
            idx = y * nx + x
            inside = model.contains((cx, cy))
            if inside:
                inside_count += 1
                if covered[idx]:
                    inside_covered += 1
            else:
                outside_count += 1
                if covered[idx]:
                    outside_covered += 1

    coverage_ratio = inside_covered / inside_count if inside_count else 0.0
    underfill_ratio = 1.0 - coverage_ratio
    overfill_ratio = outside_covered / max(1, inside_count)
    return coverage_ratio, underfill_ratio, overfill_ratio


def path_pair_metrics(
    path: Sequence[Point],
    spacing: float,
    spacing_tolerance: float,
    local_skip: int = 6,
) -> tuple[int, int, float]:
    if len(path) < 4:
        return 0, 0, float("inf")

    min_allowed = spacing * (1.0 - spacing_tolerance)
    bin_size = max(spacing * 1.5, EPS)
    bins: dict[tuple[int, int], list[int]] = {}
    segments = list(zip(path, path[1:]))
    segment_lengths = [dist(a, b) for a, b in segments]
    prefix_lengths = [0.0]
    for length in segment_lengths:
        prefix_lengths.append(prefix_lengths[-1] + length)
    path_length = prefix_lengths[-1]
    local_skip_distance = spacing * 4.0
    closed_path = dist(path[0], path[-1]) <= spacing * 0.20

    def key(x: float, y: float) -> tuple[int, int]:
        return (math.floor(x / bin_size), math.floor(y / bin_size))

    for idx, (a, b) in enumerate(segments):
        min_x = min(a[0], b[0]) - min_allowed
        min_y = min(a[1], b[1]) - min_allowed
        max_x = max(a[0], b[0]) + min_allowed
        max_y = max(a[1], b[1]) + min_allowed
        k0 = key(min_x, min_y)
        k1 = key(max_x, max_y)
        for by in range(k0[1], k1[1] + 1):
            for bx in range(k0[0], k1[0] + 1):
                bins.setdefault((bx, by), []).append(idx)

    checked: set[tuple[int, int]] = set()
    self_intersections = 0
    spacing_violations = 0
    min_nonlocal_spacing = float("inf")

    for i, (a, b) in enumerate(segments):
        min_x = min(a[0], b[0]) - min_allowed
        min_y = min(a[1], b[1]) - min_allowed
        max_x = max(a[0], b[0]) + min_allowed
        max_y = max(a[1], b[1]) + min_allowed
        k0 = key(min_x, min_y)
        k1 = key(max_x, max_y)
        for by in range(k0[1], k1[1] + 1):
            for bx in range(k0[0], k1[0] + 1):
                for j in bins.get((bx, by), []):
                    if j <= i:
                        continue
                    pair = (i, j)
                    if pair in checked:
                        continue
                    checked.add(pair)

                    index_distance = abs(i - j)
                    if closed_path:
                        index_distance = min(index_distance, len(segments) - index_distance)
                    if index_distance <= local_skip:
                        continue
                    path_gap = max(0.0, prefix_lengths[j] - prefix_lengths[i + 1])
                    if closed_path:
                        occupied_span = prefix_lengths[j + 1] - prefix_lengths[i]
                        path_gap = min(path_gap, max(0.0, path_length - occupied_span))
                    if path_gap <= local_skip_distance:
                        continue

                    c, d = segments[j]
                    pair_distance = segment_distance(a, b, c, d)
                    min_nonlocal_spacing = min(min_nonlocal_spacing, pair_distance)

                    if pair_distance <= 1e-7:
                        self_intersections += 1
                    elif pair_distance < min_allowed:
                        spacing_violations += 1

    return self_intersections, spacing_violations, min_nonlocal_spacing


def count_immediate_retraces(path: Sequence[Point], spacing: float) -> int:
    tolerance = max(spacing * 0.02, EPS)
    min_retrace_length = max(spacing * 0.10, tolerance)
    retraces = 0
    for a, b, c in zip(path, path[1:], path[2:]):
        ab = sub(b, a)
        bc = sub(c, b)
        if dot(ab, ab) <= EPS or dot(bc, bc) <= min_retrace_length * min_retrace_length:
            continue
        if dot(ab, bc) >= 0.0:
            continue
        if point_segment_distance(c, a, b) <= tolerance:
            retraces += 1
    return retraces


def validate_path(
    model: PolygonModel,
    contours: Sequence[ContourLoop],
    path: Sequence[Point],
    line_width: float,
    spacing: float,
    coverage_cells: int,
    coverage_threshold: float,
    spacing_tolerance: float,
    elapsed_seconds: float,
    strategy: str,
    unsafe_connectors: int,
    segment_widths: Sequence[float] | None = None,
) -> ValidationMetrics:
    max_segment = 0.0
    containment_violations = 0
    clearance_warnings = 0
    min_sdf = float("inf")
    outside_tolerance = -spacing * 0.05
    warning_clearance = line_width * 0.5 - spacing * 0.20

    for a, b in zip(path, path[1:]):
        length = dist(a, b)
        max_segment = max(max_segment, length)
        samples = max(2, int(math.ceil(length / max(spacing * 0.25, EPS))))
        for i in range(samples + 1):
            p = lerp(a, b, i / samples)
            sdf = model.sdf(p)
            min_sdf = min(min_sdf, sdf)
            if sdf < outside_tolerance:
                containment_violations += 1
            elif sdf < warning_clearance:
                clearance_warnings += 1

    coverage_ratio, underfill_ratio, overfill_ratio = raster_coverage(
        model,
        path,
        model.bounds(margin=line_width),
        line_width,
        coverage_cells,
        segment_widths=segment_widths,
    )
    self_intersections, spacing_violations, min_nonlocal_spacing = path_pair_metrics(
        path,
        spacing=spacing,
        spacing_tolerance=spacing_tolerance,
    )
    self_intersections += count_immediate_retraces(path, spacing)
    endpoint_gap = dist(path[0], path[-1]) if path else 0.0
    seam_spacing = seam_to_adjacent_spacing(path, spacing)
    local_uncovered = local_uncovered_area_ratio(model, path, line_width, spacing, segment_widths=segment_widths)
    disconnected_path_count = 1 if path else 0

    contour_levels = len({loop.level_index for loop in contours})
    outer_loop = max((loop for loop in contours if loop.level_index == 0), key=lambda loop: loop.area, default=None)
    if outer_loop is not None and path:
        start_outer_distance = distance_to_loop(path[0], outer_loop.points)
        end_outer_distance = distance_to_loop(path[-1], outer_loop.points)
    else:
        start_outer_distance = 0.0
        end_outer_distance = 0.0
    outer_tolerance = max(spacing * 0.55, line_width * 0.55)
    start_on_outer = bool(path) and start_outer_distance <= outer_tolerance
    end_on_outer = bool(path) and end_outer_distance <= outer_tolerance
    path_length = polyline_length(path, closed=False)
    ok = (
        bool(path)
        and len(path) >= 2
        and containment_violations == 0
        and self_intersections == 0
        and spacing_violations <= 3
        and start_on_outer
        and end_on_outer
        and unsafe_connectors == 0
        and disconnected_path_count == 1
        and endpoint_gap <= spacing * 0.01
        and seam_spacing <= spacing * 2.10
        and local_uncovered <= 0.40
        and coverage_ratio >= coverage_threshold
    )

    return ValidationMetrics(
        path_points=len(path),
        path_length=path_length,
        contour_count=len(contours),
        contour_levels=contour_levels,
        max_segment=max_segment,
        containment_violations=containment_violations,
        clearance_warnings=clearance_warnings,
        min_sdf=min_sdf if min_sdf < float("inf") else 0.0,
        self_intersections=self_intersections,
        spacing_violations=spacing_violations,
        min_nonlocal_spacing=min_nonlocal_spacing if min_nonlocal_spacing < float("inf") else 0.0,
        start_outer_distance=start_outer_distance,
        end_outer_distance=end_outer_distance,
        start_on_outer=start_on_outer,
        end_on_outer=end_on_outer,
        unsafe_connectors=unsafe_connectors,
        disconnected_path_count=disconnected_path_count,
        endpoint_gap=endpoint_gap,
        seam_to_adjacent_spacing=seam_spacing,
        local_uncovered_area_ratio=local_uncovered,
        coverage_ratio=coverage_ratio,
        underfill_ratio=underfill_ratio,
        overfill_ratio=overfill_ratio,
        elapsed_seconds=elapsed_seconds,
        strategy=strategy,
        ok=ok,
    )


@dataclass
class LayerResult:
    shape: str
    grid: SDFGrid
    contours: list[ContourLoop]
    path: list[Point]
    metrics: ValidationMetrics
    diagnostics: list[str]
    segment_widths: list[float] = field(default_factory=list)
    line_width: float = 0.0

    def to_json(self) -> dict[str, object]:
        adaptive_widths = [width for width in self.segment_widths if self.line_width > EPS and width < self.line_width * 0.99]
        return {
            "shape": self.shape,
            "grid": {
                "nx": self.grid.nx,
                "ny": self.grid.ny,
                "dx": self.grid.dx,
                "dy": self.grid.dy,
                "max_sdf": self.grid.max_sdf(),
            },
            "contours": {
                "count": len(self.contours),
                "levels": sorted({loop.level_index for loop in self.contours}),
                "per_level": {
                    str(level): sum(1 for loop in self.contours if loop.level_index == level)
                    for level in sorted({loop.level_index for loop in self.contours})
                },
            },
            "path": {
                "points": len(self.path),
                "start": list(self.path[0]) if self.path else None,
                "end": list(self.path[-1]) if self.path else None,
                "adaptive_segments": len(adaptive_widths),
                "min_width": min(self.segment_widths) if self.segment_widths else None,
                "max_speed_multiplier": (self.line_width / min(adaptive_widths)) if adaptive_widths and self.line_width > EPS else 1.0,
            },
            "metrics": self.metrics.__dict__,
            "diagnostics": self.diagnostics,
        }


def plan_one_layer(
    model: PolygonModel,
    grid_cells: int,
    line_width: float,
    spacing: float,
    max_levels: int,
    coverage_cells: int,
    coverage_threshold: float,
    spacing_tolerance: float,
    start_fraction: float,
    exit_fraction: float,
) -> LayerResult:
    started = time.perf_counter()
    grid = build_sdf_grid(model, grid_cells=grid_cells, margin=line_width * 2.0)
    contours = filter_topology_stable_levels(
        filter_printable_contours(
            generate_offset_contours(grid, line_width=line_width, spacing=spacing, max_levels=max_levels),
            spacing=spacing,
        ),
        spacing=spacing,
    )
    diagnostics: list[str] = []
    contours, adaptive_diagnostics = add_terminal_medial_gap_fill(contours, line_width=line_width, spacing=spacing)
    diagnostics.extend(adaptive_diagnostics)

    if not contours:
        elapsed = time.perf_counter() - started
        metrics = validate_path(
            model,
            [],
            [],
            line_width,
            spacing,
            coverage_cells,
            coverage_threshold,
            spacing_tolerance,
            elapsed,
            "none",
            unsafe_connectors=0,
        )
        diagnostics.append("No valid contours were generated. The shape is probably too narrow for this line width.")
        return LayerResult(model.name, grid, contours, [], metrics, diagnostics)

    outer_loop = max((loop for loop in contours if loop.level_index == 0), key=lambda loop: loop.area, default=contours[0])
    start_anchor = point_at_closed_fraction(outer_loop.points, start_fraction)
    exit_anchor = point_at_closed_fraction(outer_loop.points, exit_fraction)
    grouped = loops_by_level(contours)
    multi_loop = any(len(grouped[level]) > 1 for level in grouped)
    first_level = min(grouped) if grouped else 0
    one_hole_ring = multi_loop and len(grouped.get(first_level, [])) == 2 and centroid_spread(grouped[first_level]) < spacing * 2.0
    branch_single_island = multi_loop and len(grouped.get(first_level, [])) == 1
    if one_hole_ring:
        filtered_contours = filter_ring_medial_overlap(contours, spacing)
        if len(filtered_contours) != len(contours):
            diagnostics.append(
                f"Dropped {len(contours) - len(filtered_contours)} medial contour(s) whose opposing ring fronts were below nominal spacing."
            )
            contours = filtered_contours
            grouped = loops_by_level(contours)
            multi_loop = any(len(grouped[level]) > 1 for level in grouped)
            first_level = min(grouped) if grouped else 0
            one_hole_ring = (
                multi_loop and len(grouped.get(first_level, [])) == 2 and centroid_spread(grouped[first_level]) < spacing * 2.0
            )
            branch_single_island = multi_loop and len(grouped.get(first_level, [])) == 1

    ordered_contours = ordered_loops_for_spiral(contours)
    if branch_single_island:
        path, unsafe_connectors = build_branch_connected_fermat(
            contours,
            start_anchor=start_anchor,
            spacing=spacing,
        )
        strategy = "branch_connected_fermat"
    elif multi_loop and len(model.holes) >= 2:
        path, unsafe_connectors = build_merged_hole_connected_fermat(
            model,
            contours,
            start_anchor=start_anchor,
            line_width=line_width,
            spacing=spacing,
        )
        strategy = "merged_hole_connected_fermat"
    else:
        candidate_exits: list[tuple[float, Point]] = [(exit_fraction, exit_anchor)]
        for fraction in (0.67, 0.50, 0.33, 0.25, 0.75, 0.10, 0.90):
            if all(abs(((fraction - existing + 0.5) % 1.0) - 0.5) > 1e-3 for existing, _ in candidate_exits):
                candidate_exits.append((fraction, point_at_closed_fraction(outer_loop.points, fraction)))

        best_candidate: tuple[tuple[int, int, float, int, float], list[Point], int, float] | None = None
        for fraction, candidate_exit in candidate_exits:
            candidate_path, candidate_unsafe = build_single_minimum_connected_fermat(
                ordered_contours,
                start_anchor=start_anchor,
                spacing=spacing,
                port_spacing=spacing,
                exit_anchor=candidate_exit,
                preserve_medial_pockets=not one_hole_ring,
            )
            completed_candidate = complete_outer_boundary_cycle(candidate_path, outer_loop, spacing, spacing_tolerance)
            crossings, close_pairs, min_spacing = path_pair_metrics(
                completed_candidate,
                spacing=spacing,
                spacing_tolerance=spacing_tolerance,
            )
            seam_spacing = seam_to_adjacent_spacing(completed_candidate, spacing)
            seam_penalty = abs(seam_spacing - spacing)
            if seam_spacing < spacing * 0.98:
                seam_penalty += spacing
            score = (candidate_unsafe, crossings, seam_penalty, close_pairs, -min_spacing)
            if best_candidate is None or score < best_candidate[0]:
                best_candidate = (score, completed_candidate, candidate_unsafe, fraction)
            if candidate_unsafe == 0 and crossings == 0 and close_pairs == 0 and seam_penalty <= spacing * 0.05:
                best_candidate = (score, completed_candidate, candidate_unsafe, fraction)
                break

        assert best_candidate is not None
        path = best_candidate[1]
        unsafe_connectors = best_candidate[2]
        if abs(((best_candidate[3] - exit_fraction + 0.5) % 1.0) - 0.5) > 1e-3:
            diagnostics.append(f"Selected boundary exit fraction {best_candidate[3]:.2f} to satisfy local spacing checks.")
        strategy = "single_minimum_connected_fermat"

    if one_hole_ring:
        diagnostics.append("One-hole topology is currently routed as a single contour chain; inspect contour-tree ordering.")
    elif branch_single_island:
        diagnostics.append("Branch topology used contour-tree Fermat child insertion.")
    elif multi_loop:
        diagnostics.append(
            "Multi-hole topology used a conservative merged-cavity CFS fallback; material between merged holes may be omitted."
        )

    completed_path = complete_outer_boundary_cycle(path, outer_loop, spacing, spacing_tolerance)
    if len(completed_path) != len(path) or (path and completed_path and dist(path[-1], completed_path[-1]) > EPS):
        diagnostics.append("Completed the outer-boundary cycle with coincident start/end closure.")
    path = completed_path
    if multi_loop and len(model.holes) >= 2:
        pocket_path, inserted_pockets = insert_uncovered_pocket_spirals(
            model,
            contours,
            path,
            spacing=spacing,
            spacing_tolerance=spacing_tolerance,
        )
        if inserted_pockets:
            diagnostics.append(f"Inserted {inserted_pockets} uncovered pocket Fermat spiral(s) into the global path.")
            path = pocket_path

    if multi_loop:
        gap_path, inserted_gaps, gap_contours = insert_residual_gap_spirals(
            model,
            grid,
            path,
            line_width=line_width,
            spacing=spacing,
            max_levels=max_levels,
            spacing_tolerance=spacing_tolerance,
        )
        if inserted_gaps:
            diagnostics.append(
                f"Inserted {inserted_gaps} residual gap Fermat spiral(s) from {gap_contours} uncovered contour(s)."
            )
            path = gap_path
        elif gap_contours:
            diagnostics.append(f"Detected {gap_contours} residual gap contour(s), but no non-crossing splice was found.")

    segment_widths = segment_widths_for_path(path, contours, line_width=line_width, spacing=spacing)
    elapsed = time.perf_counter() - started
    metrics = validate_path(
        model,
        contours,
        path,
        line_width,
        spacing,
        coverage_cells,
        coverage_threshold,
        spacing_tolerance,
        elapsed,
        strategy,
        unsafe_connectors,
        segment_widths=segment_widths,
    )

    if metrics.containment_violations:
        diagnostics.append(f"{metrics.containment_violations} sampled path points leave the printable polygon.")
    if metrics.self_intersections:
        diagnostics.append(f"{metrics.self_intersections} non-adjacent red path segment pair(s) cross or touch.")
    if metrics.spacing_violations:
        diagnostics.append(
            f"{metrics.spacing_violations} non-adjacent red path segment pair(s) are closer than "
            f"{spacing * (1.0 - spacing_tolerance):.3f}."
        )
    if not metrics.start_on_outer:
        diagnostics.append(f"Path start is {metrics.start_outer_distance:.3f} from the outer contour.")
    if not metrics.end_on_outer:
        diagnostics.append(f"Path end is {metrics.end_outer_distance:.3f} from the outer contour.")
    if metrics.clearance_warnings:
        diagnostics.append(
            f"{metrics.clearance_warnings} sampled path points are inside the polygon but below nominal half-width clearance."
        )
    if metrics.unsafe_connectors:
        diagnostics.append(f"{metrics.unsafe_connectors} connector(s) could not be proven safe by sampled clearance checks.")
    if metrics.disconnected_path_count != 1:
        diagnostics.append(f"Generated {metrics.disconnected_path_count} disconnected extrusion path(s); strict mode requires exactly one.")
    if metrics.endpoint_gap > spacing * 0.01:
        diagnostics.append(f"Endpoint gap {metrics.endpoint_gap:.4f} exceeds zero-closure tolerance.")
    if metrics.seam_to_adjacent_spacing > spacing * 2.10:
        diagnostics.append(
            f"Seam connector spacing {metrics.seam_to_adjacent_spacing:.3f} exceeds nominal spacing tolerance {spacing * 2.10:.3f}."
        )
    if metrics.local_uncovered_area_ratio > 0.40:
        diagnostics.append(f"Local uncovered seam area ratio {metrics.local_uncovered_area_ratio:.3f} exceeds threshold 0.400.")
    if metrics.coverage_ratio < coverage_threshold:
        diagnostics.append(
            f"Coverage {metrics.coverage_ratio:.3f} is below threshold {coverage_threshold:.3f}; inspect SVG."
        )

    return LayerResult(model.name, grid, contours, path, metrics, diagnostics, segment_widths=segment_widths, line_width=line_width)


def svg_points(points: Sequence[Point], bounds: tuple[float, float, float, float], width: int, height: int, pad: int) -> str:
    min_x, min_y, max_x, max_y = bounds
    scale = min((width - pad * 2) / max(max_x - min_x, EPS), (height - pad * 2) / max(max_y - min_y, EPS))

    def tx(p: Point) -> tuple[float, float]:
        x = pad + (p[0] - min_x) * scale
        y = height - pad - (p[1] - min_y) * scale
        return x, y

    return " ".join(f"{tx(p)[0]:.2f},{tx(p)[1]:.2f}" for p in points)


def write_svg(result: LayerResult, path: Path, draw_contours: bool) -> None:
    width = 1100
    height = 850
    pad = 32
    bounds = result.grid.model.bounds(margin=4.0)
    model = result.grid.model

    lines: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fbfbf8"/>',
    ]

    outer = svg_points(model.outer + [model.outer[0]], bounds, width, height, pad)
    lines.append(f'<polyline points="{outer}" fill="none" stroke="#242424" stroke-width="2.2"/>')
    for hole in model.holes:
        hole_points = svg_points(hole + [hole[0]], bounds, width, height, pad)
        lines.append(f'<polyline points="{hole_points}" fill="none" stroke="#555" stroke-width="1.8" stroke-dasharray="5 5"/>')

    if draw_contours:
        for loop in result.contours:
            adaptive = result.line_width > EPS and loop.line_width > EPS and loop.line_width < result.line_width * 0.99
            color = "#7a4cc2" if not loop.closed else ("#c65454" if adaptive else ("#9ab7d8" if loop.level_index % 2 == 0 else "#b7cda3"))
            contour_points = loop.points + ([loop.points[0]] if loop.closed and loop.points else [])
            pts = svg_points(contour_points, bounds, width, height, pad)
            title = ""
            if adaptive:
                title = f"<title>adaptive contour width {loop.line_width:.3f} mm, speed x{loop.speed_multiplier:.3f}</title>"
            elif not loop.closed:
                title = "<title>open medial gap-fill contour</title>"
            lines.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="0.9" opacity="0.60">{title}</polyline>')

    if result.path:
        pts = svg_points(result.path, bounds, width, height, pad)
        lines.append(f'<polyline points="{pts}" fill="none" stroke="#d12f1f" stroke-width="2.0" stroke-linejoin="round" stroke-linecap="round"/>')

        if result.segment_widths and result.line_width > EPS and len(result.segment_widths) == len(result.path) - 1:
            run_start: int | None = None
            for idx, segment_width in enumerate(result.segment_widths + [result.line_width]):
                adaptive = idx < len(result.segment_widths) and segment_width < result.line_width * 0.99
                if adaptive and run_start is None:
                    run_start = idx
                elif not adaptive and run_start is not None:
                    run_points = result.path[run_start : idx + 1]
                    run_width = min(result.segment_widths[run_start:idx])
                    run_pts = svg_points(run_points, bounds, width, height, pad)
                    stroke_width = max(1.1, 2.0 * run_width / result.line_width)
                    speed_multiplier = result.line_width / run_width
                    lines.append(
                        f'<polyline points="{run_pts}" fill="none" stroke="#7f1d1d" stroke-width="{stroke_width:.2f}" '
                        f'stroke-linejoin="round" stroke-linecap="round" opacity="0.95">'
                        f'<title>adaptive extrusion width {run_width:.3f} mm; speed x{speed_multiplier:.3f}</title></polyline>'
                    )
                    run_start = None

        def svg_point(p: Point) -> tuple[float, float]:
            min_x, min_y, max_x, max_y = bounds
            scale = min((width - pad * 2) / max(max_x - min_x, EPS), (height - pad * 2) / max(max_y - min_y, EPS))
            return (pad + (p[0] - min_x) * scale, height - pad - (p[1] - min_y) * scale)

        sx, sy = svg_point(result.path[0])
        ex, ey = svg_point(result.path[-1])
        if len(result.path) >= 2:
            seam = svg_points([result.path[-2], result.path[-1]], bounds, width, height, pad)
            lines.append(f'<polyline points="{seam}" fill="none" stroke="#f28c28" stroke-width="5.0" stroke-linecap="round" opacity="0.85"><title>seam connector</title></polyline>')
        for adjacent in (result.path[: min(8, len(result.path))], result.path[max(0, len(result.path) - 9) : -1]):
            if len(adjacent) >= 2:
                adj = svg_points(adjacent, bounds, width, height, pad)
                lines.append(f'<polyline points="{adj}" fill="none" stroke="#246fbd" stroke-width="3.0" stroke-linecap="round" opacity="0.70"><title>adjacent extrusion lines</title></polyline>')
        lines.append(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="5" fill="#12873f"><title>start</title></circle>')
        lines.append(f'<circle cx="{ex:.2f}" cy="{ey:.2f}" r="5" fill="#6e35b8"><title>end</title></circle>')
        lines.append(
            f'<text x="{sx + 10:.2f}" y="{sy - 12:.2f}" font-family="monospace" font-size="13" fill="#222">'
            f'endpoint_gap {result.metrics.endpoint_gap:.4f} mm; seam_spacing {result.metrics.seam_to_adjacent_spacing:.3f} mm; '
            f'local_uncovered {result.metrics.local_uncovered_area_ratio:.3f}</text>'
        )
        adaptive_widths = [w for w in result.segment_widths if result.line_width > EPS and w < result.line_width * 0.99]
        if adaptive_widths:
            min_width = min(adaptive_widths)
            lines.append(
                f'<text x="{sx + 10:.2f}" y="{sy + 6:.2f}" font-family="monospace" font-size="13" fill="#222">'
                f'adaptive_width {min_width:.3f} mm; speed x{result.line_width / min_width:.3f}; '
                f'segments {len(adaptive_widths)}</text>'
            )

    status = "PASS" if result.metrics.ok else "FAIL"
    subtitle = (
        f"{result.shape}: {status}, strategy={result.metrics.strategy}, "
        f"coverage={result.metrics.coverage_ratio:.3f}, "
        f"cross={result.metrics.self_intersections}, close={result.metrics.spacing_violations}, "
        f"minsep={result.metrics.min_nonlocal_spacing:.3f}, "
        f"gap={result.metrics.endpoint_gap:.3f}, seam={result.metrics.seam_to_adjacent_spacing:.3f}, "
        f"time={result.metrics.elapsed_seconds:.3f}s"
    )
    lines.append(f'<text x="32" y="32" font-family="monospace" font-size="18" fill="#222">{subtitle}</text>')
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_png_file(path: Path, width: int, height: int, pixels: bytearray) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride : (y + 1) * stride])

    data = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(bytes(raw), level=6)),
            chunk(b"IEND", b""),
        ]
    )
    path.write_bytes(data)


def write_png_preview(result: LayerResult, path: Path, draw_contours: bool) -> None:
    width = 1400
    height = 1000
    pad = 48
    bounds = result.grid.model.bounds(margin=4.0)
    min_x, min_y, max_x, max_y = bounds
    scale = min((width - pad * 2) / max(max_x - min_x, EPS), (height - pad * 2) / max(max_y - min_y, EPS))
    x_offset = (width - (max_x - min_x) * scale) * 0.5
    y_offset = (height - (max_y - min_y) * scale) * 0.5
    pixels = bytearray([251, 251, 248] * width * height)

    def tx(p: Point) -> tuple[float, float]:
        return (x_offset + (p[0] - min_x) * scale, height - y_offset - (p[1] - min_y) * scale)

    def set_pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            idx = (y * width + x) * 3
            pixels[idx : idx + 3] = bytes(color)

    def draw_disc(x: float, y: float, radius: float, color: tuple[int, int, int]) -> None:
        r = int(math.ceil(radius))
        for yy in range(int(y) - r, int(y) + r + 1):
            for xx in range(int(x) - r, int(x) + r + 1):
                if (xx - x) * (xx - x) + (yy - y) * (yy - y) <= radius * radius:
                    set_pixel(xx, yy, color)

    def draw_polyline(points: Sequence[Point], color: tuple[int, int, int], radius: float, closed: bool = False) -> None:
        if len(points) < 2:
            return
        pairs = list(zip(points, points[1:]))
        if closed:
            pairs.append((points[-1], points[0]))
        for a, b in pairs:
            ax, ay = tx(a)
            bx, by = tx(b)
            length = math.hypot(bx - ax, by - ay)
            steps = max(1, int(math.ceil(length / max(radius * 0.65, 1.0))))
            for i in range(steps + 1):
                t = i / steps
                draw_disc(ax + (bx - ax) * t, ay + (by - ay) * t, radius, color)

    if draw_contours:
        for loop in result.contours:
            color = (154, 183, 216) if loop.level_index % 2 == 0 else (183, 205, 163)
            draw_polyline(loop.points, color, radius=0.75, closed=True)

    draw_polyline(result.grid.model.outer, (36, 36, 36), radius=1.6, closed=True)
    for hole in result.grid.model.holes:
        draw_polyline(hole, (82, 82, 82), radius=1.3, closed=True)
    draw_polyline(result.path, (209, 47, 31), radius=1.8, closed=False)

    if result.path:
        sx, sy = tx(result.path[0])
        ex, ey = tx(result.path[-1])
        draw_disc(sx, sy, 6.0, (18, 135, 63))
        draw_disc(ex, ey, 6.0, (110, 53, 184))

    write_png_file(path, width, height, pixels)


def write_json(result: LayerResult, path: Path) -> None:
    path.write_text(json.dumps(result.to_json(), indent=2), encoding="utf-8")


def print_summary(result: LayerResult) -> None:
    m = result.metrics
    status = "PASS" if m.ok else "FAIL"
    print(
        f"{result.shape:12s} {status:4s} "
        f"strategy={m.strategy:24s} contours={m.contour_count:3d} levels={m.contour_levels:2d} "
        f"points={m.path_points:5d} coverage={m.coverage_ratio:5.3f} "
        f"cross={m.self_intersections:4d} close={m.spacing_violations:4d} "
        f"minsep={m.min_nonlocal_spacing:5.3f} viol={m.containment_violations:4d} "
        f"gap={dist(result.path[0], result.path[-1]) if result.path else 0.0:5.3f} "
        f"outer=({m.start_outer_distance:4.2f},{m.end_outer_distance:4.2f}) "
        f"clear={m.clearance_warnings:4d} unsafe={m.unsafe_connectors:2d} "
        f"time={m.elapsed_seconds:6.3f}s"
    )
    for diagnostic in result.diagnostics:
        print(f"  - {diagnostic}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shape", choices=sorted(available_shapes()), help="single built-in shape to slice")
    parser.add_argument("--all", action="store_true", help="run every built-in shape")
    parser.add_argument("--list-shapes", action="store_true", help="list built-in shape names and exit")
    parser.add_argument("--out", type=Path, default=Path("build") / "continuous_fermat", help="output directory")
    parser.add_argument("--grid", type=int, default=180, help="SDF grid cells along the longest axis")
    parser.add_argument("--coverage-grid", type=int, default=170, help="coverage verifier grid cells along the longest axis")
    parser.add_argument("--line-width", type=float, default=1.2, help="extrusion line width")
    parser.add_argument("--spacing", type=float, default=1.2, help="spacing between contour centerlines")
    parser.add_argument("--max-levels", type=int, default=256, help="maximum offset contour levels")
    parser.add_argument("--coverage-threshold", type=float, default=0.82, help="minimum filled-area estimate")
    parser.add_argument("--start-fraction", type=float, default=0.0, help="outer contour fraction for the layer cycle cut/start")
    parser.add_argument("--exit-fraction", type=float, default=0.5, help="outer contour fraction for the second boundary slot")
    parser.add_argument(
        "--spacing-tolerance",
        type=float,
        default=0.25,
        help="allowed fractional undershoot before non-adjacent red lines count as too close",
    )
    parser.add_argument("--draw-contours", action="store_true", help="include raw offset contours in the SVG")
    parser.add_argument("--no-svg", action="store_true", help="skip SVG writing")
    parser.add_argument("--no-png", action="store_true", help="skip PNG preview writing")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    shapes = available_shapes()

    if args.list_shapes:
        for name in sorted(shapes):
            print(name)
        return 0

    if args.all or not args.shape:
        selected = sorted(shapes)
    else:
        selected = [args.shape]

    args.out.mkdir(parents=True, exist_ok=True)
    any_failed = False

    for name in selected:
        result = plan_one_layer(
            shapes[name],
            grid_cells=args.grid,
            line_width=args.line_width,
            spacing=args.spacing,
            max_levels=args.max_levels,
            coverage_cells=args.coverage_grid,
            coverage_threshold=args.coverage_threshold,
            spacing_tolerance=args.spacing_tolerance,
            start_fraction=args.start_fraction,
            exit_fraction=args.exit_fraction,
        )
        print_summary(result)
        write_json(result, args.out / f"{name}.json")
        if not args.no_svg:
            write_svg(result, args.out / f"{name}.svg", draw_contours=args.draw_contours)
        if not args.no_png:
            write_png_preview(result, args.out / f"{name}.png", draw_contours=args.draw_contours)
        any_failed = any_failed or not result.metrics.ok

    return 2 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
