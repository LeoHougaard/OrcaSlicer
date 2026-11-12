#!/usr/bin/env python3
"""Build and verify an internet-sourced continuous-slicing mesh corpus.

The source is the official Thingi10K Hugging Face mirror. Geometry is kept out
of git; JSONL manifests retain provenance, per-model licensing and SHA-256.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import shutil
import struct
import tarfile
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO_ROOT / "sandboxes" / "continuous_fermat_corpus"
SOURCE_PAGE = "https://github.com/Thingi10K/Thingi10K"
MIRROR_PAGE = "https://huggingface.co/datasets/Thingi10K/Thingi10K"
MIRROR_ROOT = MIRROR_PAGE + "/resolve/main"
ARCHIVE_NAME = "Thingi10K_npz.tar.gz"
ARCHIVE_URL = f"{MIRROR_ROOT}/{ARCHIVE_NAME}"
ARCHIVE_SIZE = 4_083_478_827
ARCHIVE_SHA256 = "0a9e3e7f0df0393c9f12959b5c3691ec01a4032a9c5c13ee9cc9a6e3f3d11e0c"
METADATA_FILES = (
    "contextual_data.csv",
    "input_summary.csv",
    "geometry_data.csv",
    "tag_data.csv",
)
DISALLOWED_LICENSE_PARTS = ("No Derivatives", "unknown")
CORRUPT_IDS = {49_911, 74_463, 286_163, 81_313, 77_942}


def json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def sha256_path(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path, expected_size: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (expected_size is None or path.stat().st_size == expected_size):
        return
    partial = path.with_suffix(path.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "OrcaSlicer-corpus/1"})
    with urllib.request.urlopen(request) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output, 4 * 1024 * 1024)
    if expected_size is not None and partial.stat().st_size != expected_size:
        raise RuntimeError(f"{path.name}: expected {expected_size} bytes, got {partial.stat().st_size}")
    partial.replace(path)


def download_inputs(root: Path, verify_archive_hash: bool) -> None:
    metadata = root / "metadata"
    for name in METADATA_FILES:
        download(f"{MIRROR_ROOT}/metadata/{name}", metadata / name)
    archive = root / "downloads" / ARCHIVE_NAME
    download(ARCHIVE_URL, archive, ARCHIVE_SIZE)
    if verify_archive_hash:
        actual = sha256_path(archive)
        # Hugging Face exposes the archive hash. Keep a hard failure here: an
        # archive change must be reviewed before it can silently alter results.
        if actual != ARCHIVE_SHA256:
            raise RuntimeError(f"archive SHA-256 mismatch: {actual}")


def read_csv_index(path: Path, field: str) -> dict[int, dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return {int(row[field]): row for row in csv.DictReader(stream)}


def truth(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def candidate_records(root: Path, max_faces: int) -> list[dict[str, Any]]:
    metadata = root / "metadata"
    geometry = read_csv_index(metadata / "geometry_data.csv", "file_id")
    context = read_csv_index(metadata / "contextual_data.csv", "Thing ID")
    records: list[dict[str, Any]] = []
    with (metadata / "input_summary.csv").open(newline="", encoding="utf-8-sig") as stream:
        for source in csv.DictReader(stream):
            file_id = int(source["ID"])
            geom = geometry.get(file_id)
            if geom is None or file_id in CORRUPT_IDS:
                continue
            license_name = source.get("License", "").strip()
            reasons: list[str] = []
            if any(part.lower() in license_name.lower() for part in DISALLOWED_LICENSE_PARTS):
                reasons.append("license_not_suitable_for_reproducible_transform_tests")
            if not truth(source.get("Closed", "")):
                reasons.append("metadata_not_closed")
            if not truth(source.get("Single Component", "")):
                reasons.append("metadata_multiple_components")
            if not truth(source.get("PWN", "")) or not truth(geom.get("solid", "")):
                reasons.append("metadata_not_solid")
            if int(geom.get("num_self_intersections", "-1")) != 0:
                reasons.append("metadata_self_intersections")
            face_count = int(geom["num_faces"])
            if face_count > max_faces:
                reasons.append("metadata_too_many_faces")
            if reasons:
                continue
            thing_id = int(source["Thing ID"])
            info = context.get(thing_id, {})
            records.append(
                {
                    "corpus_id": f"thingi10k-{file_id}",
                    "kind": "internet_original",
                    "source_dataset": "Thingi10K",
                    "source_dataset_url": SOURCE_PAGE,
                    "source_mirror_url": MIRROR_PAGE,
                    "source_archive_url": ARCHIVE_URL,
                    "source_archive_member": f"npz/{file_id}.npz",
                    "file_id": file_id,
                    "thing_id": thing_id,
                    "thing_url": f"https://www.thingiverse.com/thing:{thing_id}",
                    "original_file_url": source.get("Link", ""),
                    "name": info.get("Name", ""),
                    "author": info.get("Author", ""),
                    "license": license_name,
                    "category": info.get("Category", ""),
                    "source_date": info.get("Date", ""),
                    "metadata": {
                        "vertices": int(geom["num_vertices"]),
                        "faces": face_count,
                        "euler_characteristic": int(geom["euler_characteristic"]),
                    },
                }
            )
    return sorted(records, key=lambda record: record["file_id"])


def write_candidates(root: Path, max_faces: int) -> list[dict[str, Any]]:
    records = candidate_records(root, max_faces)
    manifest = root / "candidates.jsonl"
    manifest.write_text("".join(json_line(record) for record in records), encoding="utf-8")
    return records


def extract_candidates(root: Path, records: list[dict[str, Any]]) -> None:
    wanted = {record["source_archive_member"]: record for record in records}
    output = root / "models" / "original_npz"
    output.mkdir(parents=True, exist_ok=True)
    found: set[str] = set()
    archive = root / "downloads" / ARCHIVE_NAME
    # Streaming mode bounds memory while reading the multi-gigabyte gzip once.
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            record = wanted.get(member.name.replace("\\", "/"))
            if record is None or not member.isfile():
                continue
            target = output / f"{record['file_id']}.npz"
            source = tar.extractfile(member)
            if source is None:
                continue
            digest = hashlib.sha256()
            with target.open("wb") as out:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
            record["local_path"] = target.relative_to(REPO_ROOT).as_posix()
            record["sha256"] = digest.hexdigest()
            record["bytes"] = target.stat().st_size
            found.add(member.name.replace("\\", "/"))
    missing = sorted(set(wanted) - found)
    if missing:
        raise RuntimeError(f"archive is missing {len(missing)} selected members; first={missing[0]}")
    (root / "extracted.jsonl").write_text(
        "".join(json_line(record) for record in records), encoding="utf-8"
    )


def load_extracted(root: Path) -> list[dict[str, Any]]:
    path = root / "extracted.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def xor_loops(polygons: Iterable[Any]) -> Any:
    from shapely.geometry import GeometryCollection

    result: Any = GeometryCollection()
    for polygon in polygons:
        if polygon.area > 0:
            result = result.symmetric_difference(polygon)
    return result.buffer(0)


def section_geometry(triangles: Any, z: float, tolerance: float) -> tuple[Any, int]:
    import numpy as np
    import shapely
    from shapely.geometry import Polygon

    if len(triangles) == 0:
        return None, 0
    edge_a = triangles[:, (0, 1, 2)]
    edge_b = triangles[:, (1, 2, 0)]
    za = edge_a[:, :, 2]
    zb = edge_b[:, :, 2]
    crosses = ((za <= z) & (zb > z)) | ((zb <= z) & (za > z))
    valid_rows = crosses.sum(axis=1) == 2
    if not np.any(valid_rows):
        return None, 0
    a = edge_a[valid_rows]
    b = edge_b[valid_rows]
    mask = crosses[valid_rows]
    denominator = b[:, :, 2] - a[:, :, 2]
    parameters = np.zeros_like(denominator)
    np.divide(z - a[:, :, 2], denominator, out=parameters, where=mask)
    points = a[:, :, :2] + (b[:, :, :2] - a[:, :, :2]) * parameters[:, :, None]
    segments = points[mask].reshape((-1, 2, 2))
    # Adjacent facets compute their shared edge from opposite directions.
    # Quantization closes sub-ULP endpoint gaps without changing topology at
    # slicer scale.
    segments = np.round(segments / tolerance) * tolerance
    lengths = np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1)
    segments = segments[lengths > tolerance]
    if len(segments) == 0:
        return None, 0
    linework = shapely.linestrings(segments)
    collection = shapely.polygonize(linework)
    faces = list(shapely.get_parts(collection))
    # polygonize emits both the annular face and its inner face. XOR of unique
    # exterior rings reconstructs even/odd solid membership and preserves holes.
    unique: dict[bytes, Polygon] = {}
    for face in faces:
        ring = Polygon(face.exterior)
        key = ring.normalize().wkb
        unique[key] = ring
    geometry = xor_loops(unique.values())
    if geometry.is_empty:
        return None, len(segments)
    return geometry, int(len(segments))


def polygon_count(geometry: Any) -> int:
    if geometry is None or geometry.is_empty:
        return 0
    if geometry.geom_type == "Polygon":
        return 1
    if geometry.geom_type == "MultiPolygon":
        return len(geometry.geoms)
    return sum(1 for geom in geometry.geoms if geom.geom_type == "Polygon")


def verify_model(job: tuple[dict[str, Any], float, float, int, float]) -> dict[str, Any]:
    import numpy as np

    record, layer_height, target_extent, max_layers, overlap_epsilon = job
    started = time.perf_counter()
    path = REPO_ROOT / record["local_path"]
    try:
        with np.load(path) as data:
            vertices = np.asarray(data["vertices"], dtype=np.float64)
            facets = np.asarray(data["facets"], dtype=np.int64)
        if vertices.ndim != 2 or vertices.shape[1] != 3 or facets.ndim != 2 or facets.shape[1] != 3:
            raise ValueError("unexpected NPZ array shape")
        bounds_min = vertices.min(axis=0)
        bounds_max = vertices.max(axis=0)
        extent = bounds_max - bounds_min
        if not np.all(np.isfinite(extent)) or float(extent.max()) <= 0 or float(extent[2]) <= 0:
            raise ValueError("degenerate mesh bounds")
        scale = target_extent / float(extent.max())
        normalized = (vertices - bounds_min) * scale
        normalized_extent = extent * scale
        triangles = normalized[facets]
        z_minimum = triangles[:, :, 2].min(axis=1)
        z_maximum = triangles[:, :, 2].max(axis=1)
        height = float(normalized_extent[2])
        layer_count = min(max_layers, max(1, int(math.ceil(height / layer_height))))
        tolerance = target_extent * 1e-9
        previous = None
        nonempty = 0
        min_overlap = math.inf
        max_holes = 0
        total_segments = 0
        reason = "eligible"
        failed_layer = None
        for layer in range(layer_count):
            z = min(height - tolerance, (layer + 0.5) * layer_height)
            active = (z_minimum <= z) & (z_maximum > z)
            geometry, segment_count = section_geometry(triangles[active], z, tolerance)
            total_segments += segment_count
            islands = polygon_count(geometry)
            if islands == 0:
                reason = "empty_cross_section_inside_mesh_height"
                failed_layer = layer
                break
            nonempty += 1
            if islands != 1:
                reason = "multiple_islands"
                failed_layer = layer
                break
            polygon = geometry if geometry.geom_type == "Polygon" else next(g for g in geometry.geoms if g.geom_type == "Polygon")
            max_holes = max(max_holes, len(polygon.interiors))
            if previous is not None:
                overlap = float(previous.intersection(polygon).area)
                min_overlap = min(min_overlap, overlap)
                area_scale = max(min(float(previous.area), float(polygon.area)), 1.0)
                if overlap <= overlap_epsilon * area_scale:
                    reason = "no_positive_area_overlap"
                    failed_layer = layer
                    break
            previous = polygon
        if reason == "eligible" and nonempty < 2:
            reason = "fewer_than_two_nonempty_layers"
        result = dict(record)
        result["eligibility"] = {
            "eligible": reason == "eligible",
            "reason": reason,
            "failed_layer": failed_layer,
            "layer_height_mm": layer_height,
            "normalization": "uniform_scale_longest_axis_to_target_extent",
            "target_extent_mm": target_extent,
            "uniform_scale": scale,
            "normalized_extent_mm": [float(v) for v in normalized_extent],
            "layers_checked": nonempty,
            "max_holes_in_layer": max_holes,
            "minimum_adjacent_overlap_mm2": None if math.isinf(min_overlap) else min_overlap,
            "intersection_segments": total_segments,
        }
    except Exception as exc:
        result = dict(record)
        result["eligibility"] = {"eligible": False, "reason": "verification_error", "error": repr(exc)}
    result["verification_seconds"] = time.perf_counter() - started
    return result


def verify_records(
    root: Path,
    records: list[dict[str, Any]],
    layer_height: float,
    target_extent: float,
    max_layers: int,
    overlap_epsilon: float,
    workers: int,
    target: int,
    verify_all: bool,
    candidate_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = sorted(records, key=lambda record: (record["metadata"]["faces"], record["file_id"]))
    if candidate_limit:
        records = records[:candidate_limit]
    results: list[dict[str, Any]] = []
    evaluated_ids: set[int] = set()
    batch_size = max(workers * 4, 16)
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for offset in range(0, len(records), batch_size):
            batch = records[offset : offset + batch_size]
            futures = [
                pool.submit(
                    verify_model,
                    (record, layer_height, target_extent, max_layers, overlap_epsilon),
                )
                for record in batch
            ]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                evaluated_ids.add(result["file_id"])
            accepted = sum(bool(item["eligibility"]["eligible"]) for item in results)
            elapsed = time.perf_counter() - started
            print(
                f"verified={len(results)}/{len(records)} eligible={accepted} "
                f"rate={len(results) / max(elapsed, 1e-9):.2f}_models_per_second",
                flush=True,
            )
            if not verify_all and accepted >= target:
                break
    results.sort(key=lambda result: result["file_id"])
    eligible_all = [result for result in results if result["eligibility"]["eligible"]]
    eligible = eligible_all[:target]
    rejected = [result for result in results if not result["eligibility"]["eligible"]]
    eligible_extra = eligible_all[target:]
    unevaluated = [record for record in records if record["file_id"] not in evaluated_ids]
    (root / "eligible.jsonl").write_text("".join(json_line(r) for r in eligible), encoding="utf-8")
    (root / "rejected.jsonl").write_text("".join(json_line(r) for r in rejected), encoding="utf-8")
    (root / "eligible_extra.jsonl").write_text("".join(json_line(r) for r in eligible_extra), encoding="utf-8")
    (root / "unevaluated.jsonl").write_text("".join(json_line(r) for r in unevaluated), encoding="utf-8")
    summary = {
        "source": "Thingi10K official mirror",
        "source_url": SOURCE_PAGE,
        "mirror_url": MIRROR_PAGE,
        "archive_url": ARCHIVE_URL,
        "archive_bytes": ARCHIVE_SIZE,
        "archive_sha256": ARCHIVE_SHA256,
        "candidate_pool": len(records),
        "candidates_verified": len(results),
        "eligible_found": len(eligible_all),
        "eligible_selected": len(eligible),
        "target": target,
        "rejected": len(rejected),
        "eligible_not_selected": len(eligible_extra),
        "unevaluated_after_target_reached": len(unevaluated),
        "verification_seconds": time.perf_counter() - started,
        "settings": {
            "layer_height_mm": layer_height,
            "target_extent_mm": target_extent,
            "max_layers": max_layers,
            "overlap_relative_epsilon": overlap_epsilon,
        },
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return eligible, rejected


def write_binary_stl(npz_path: Path, output_path: Path, scale: float) -> None:
    import numpy as np

    with np.load(npz_path) as data:
        vertices = np.asarray(data["vertices"], dtype=np.float64)
        facets = np.asarray(data["facets"], dtype=np.int64)
    vertices = (vertices - vertices.min(axis=0)) * scale
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as stream:
        stream.write(b"OrcaSlicer Thingi10K continuous corpus".ljust(80, b"\0"))
        stream.write(struct.pack("<I", len(facets)))
        normal = (0.0, 0.0, 0.0)
        for facet in facets:
            triangle = vertices[facet]
            stream.write(struct.pack("<12fH", *normal, *triangle[0], *triangle[1], *triangle[2], 0))


def export_stl(root: Path, limit: int) -> None:
    eligible = [json.loads(line) for line in (root / "eligible.jsonl").read_text(encoding="utf-8").splitlines() if line]
    harness_rows: list[str] = []
    test_mesh_records: list[dict[str, Any]] = []
    for record in eligible[:limit or None]:
        source = REPO_ROOT / record["local_path"]
        target = root / "models" / "eligible_stl" / f"{record['corpus_id']}.stl"
        write_binary_stl(source, target, float(record["eligibility"]["uniform_scale"]))
        test_record = dict(record)
        test_record["test_mesh_path"] = target.relative_to(REPO_ROOT).as_posix()
        test_record["test_mesh_sha256"] = sha256_path(target)
        test_record["test_mesh_bytes"] = target.stat().st_size
        test_record["test_mesh_scale"] = 1.0
        test_mesh_records.append(test_record)
        harness_rows.append(
            "\t".join(
                (
                    record["corpus_id"],
                    target.relative_to(root).as_posix(),
                    record["thing_url"],
                    "1.0",
                )
            )
            + "\n"
        )
    (root / "eligible_harness.tsv").write_text("".join(harness_rows), encoding="utf-8")
    (root / "eligible_test_meshes.jsonl").write_text(
        "".join(json_line(record) for record in test_mesh_records), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("download", "extract", "verify", "all", "export-stl"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--target", type=int, default=1000)
    parser.add_argument("--max-faces", type=int, default=100_000)
    parser.add_argument("--layer-height", type=float, default=0.2)
    parser.add_argument("--target-extent", type=float, default=40.0)
    parser.add_argument("--max-layers", type=int, default=250)
    parser.add_argument("--overlap-epsilon", type=float, default=1e-10)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--verify-all", action="store_true")
    parser.add_argument("--candidate-limit", type=int, default=0)
    parser.add_argument("--verify-archive-hash", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="STL export limit; zero means all")
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.root.mkdir(parents=True, exist_ok=True)

    if args.command in {"download", "all"}:
        download_inputs(args.root, args.verify_archive_hash)
    if args.command in {"extract", "all"}:
        records = write_candidates(args.root, args.max_faces)
        print(f"metadata candidates={len(records)}", flush=True)
        extract_candidates(args.root, records)
    if args.command in {"verify", "all"}:
        records = load_extracted(args.root)
        eligible, _ = verify_records(
            args.root,
            records,
            args.layer_height,
            args.target_extent,
            args.max_layers,
            args.overlap_epsilon,
            args.workers,
            args.target,
            args.verify_all,
            args.candidate_limit,
        )
        print(f"selected eligible={len(eligible)}/{args.target}", flush=True)
        if len(eligible) < args.target:
            return 1
    if args.command == "export-stl":
        export_stl(args.root, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
