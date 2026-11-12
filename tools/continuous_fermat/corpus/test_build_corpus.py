from __future__ import annotations

import unittest

import numpy as np

import build_corpus


def vertical_loop(points: list[tuple[float, float]], z0: float = 0.0, z1: float = 1.0) -> np.ndarray:
    triangles = []
    for index, point in enumerate(points):
        following = points[(index + 1) % len(points)]
        a = (*point, z0)
        b = (*following, z0)
        c = (*following, z1)
        d = (*point, z1)
        triangles.extend(((a, b, c), (a, c, d)))
    return np.asarray(triangles, dtype=np.float64)


class SectionGeometryTest(unittest.TestCase):
    def test_one_island_with_hole(self) -> None:
        outer = vertical_loop([(0, 0), (10, 0), (10, 10), (0, 10)])
        inner = vertical_loop([(3, 3), (7, 3), (7, 7), (3, 7)])
        geometry, segments = build_corpus.section_geometry(np.concatenate((outer, inner)), 0.5, 1e-8)
        self.assertEqual(segments, 16)
        self.assertEqual(build_corpus.polygon_count(geometry), 1)
        self.assertEqual(len(geometry.interiors), 1)
        self.assertAlmostEqual(geometry.area, 84.0)

    def test_disconnected_loops_are_two_islands(self) -> None:
        left = vertical_loop([(0, 0), (2, 0), (2, 2), (0, 2)])
        right = vertical_loop([(4, 0), (6, 0), (6, 2), (4, 2)])
        geometry, _ = build_corpus.section_geometry(np.concatenate((left, right)), 0.5, 1e-8)
        self.assertEqual(build_corpus.polygon_count(geometry), 2)

    def test_endpoint_noise_is_quantized_closed(self) -> None:
        loop = vertical_loop([(0, 0), (2, 0), (2, 2), (0, 2)])
        loop[1, 0, 0] += 1e-10
        geometry, _ = build_corpus.section_geometry(loop, 0.5, 1e-8)
        self.assertEqual(build_corpus.polygon_count(geometry), 1)


if __name__ == "__main__":
    unittest.main()
