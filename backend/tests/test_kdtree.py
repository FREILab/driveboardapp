"""Unit tests for the kd-tree used by the nearest-neighbor path optimizer."""

import math
import random

import pytest
from jobimport import kdtree


def _brute_force_nearest(points, query):
    best = None
    best_d2 = None
    for p in points:
        d2 = sum((a - b) ** 2 for a, b in zip(p, query))
        if best_d2 is None or d2 < best_d2:
            best, best_d2 = p, d2
    return best, best_d2


def test_empty_tree_returns_none():
    tree = kdtree.Tree(2)
    node, distsq = tree.nearest([0.0, 0.0])
    assert node is None
    assert distsq is None


def test_single_point():
    tree = kdtree.Tree(2)
    tree.insert([1.0, 2.0], data="a")
    node, distsq = tree.nearest([1.0, 2.0])
    assert node.data == "a"
    assert math.isclose(distsq, 0.0, abs_tol=1e-9)


def test_nearest_matches_brute_force():
    points = [
        [0.0, 0.0],
        [10.0, 10.0],
        [5.0, 1.0],
        [3.0, 8.0],
        [-2.0, 4.0],
        [7.0, 7.0],
    ]
    tree = kdtree.Tree(2)
    for i, p in enumerate(points):
        tree.insert(p, data=i)

    for query in ([4.0, 2.0], [9.0, 9.0], [-1.0, 3.0], [6.0, 6.5]):
        node, distsq = tree.nearest(query)
        expected_point, expected_d2 = _brute_force_nearest(points, query)
        assert math.isclose(distsq, expected_d2, abs_tol=1e-9)
        assert list(node.pos[:2]) == expected_point


def test_check_empty_skips_consumed_nodes():
    tree = kdtree.Tree(2)
    n0 = tree.insert([0.0, 0.0], data="zero")
    tree.insert([10.0, 10.0], data="far")
    # Mark the closest node as consumed.
    n0.data = None
    node, _ = tree.nearest([0.1, 0.1], checkempty=True)
    assert node is not None
    assert node.data == "far"


# ---------------------------------------------------------------------------
# randomized comparison against a brute-force reference
# ---------------------------------------------------------------------------


def _build(points):
    tree = kdtree.Tree(len(points[0]))
    nodes = [tree.insert(p, data=i) for i, p in enumerate(points)]
    return tree, nodes


def _d2(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _random_points(rng, n, dim, grid=None):
    if grid:  # integer grid: lots of ties and duplicate coordinates
        return [[float(rng.randint(0, grid)) for _ in range(dim)] for _ in range(n)]
    return [[rng.uniform(-50, 50) for _ in range(dim)] for _ in range(n)]


@pytest.mark.parametrize("dim", [1, 2, 3, 4])
@pytest.mark.parametrize("grid", [None, 6])
def test_nearest_matches_brute_force_random(dim, grid):
    rng = random.Random(1234 + dim)
    points = _random_points(rng, 250, dim, grid)
    tree, _ = _build(points)
    for query in _random_points(rng, 150, dim, grid) + points[:20]:
        node, distsq = tree.nearest(query)
        _, expected = _brute_force_nearest(points, query)
        assert distsq == pytest.approx(expected, abs=1e-9)
        # the returned node really lies at the reported distance
        assert _d2(node.pos, query) == pytest.approx(distsq, abs=1e-9)
        assert points[node.data] == node.pos


@pytest.mark.parametrize("grid", [None, 5])
def test_nearest_checkempty_after_consuming_nodes(grid):
    """The optimizer consumes nodes (data=None); queries must skip exactly those."""
    rng = random.Random(99)
    points = _random_points(rng, 200, 2, grid)
    tree, nodes = _build(points)
    alive = set(range(len(points)))
    order = list(range(len(points)))
    rng.shuffle(order)
    for step, i in enumerate(order[:-1]):
        nodes[i].data = None
        alive.discard(i)
        if step % 9:  # query after most, not all, removals
            continue
        query = _random_points(rng, 1, 2, grid)[0]
        node, distsq = tree.nearest(query, checkempty=True)
        _, expected = _brute_force_nearest([points[j] for j in alive], query)
        assert node.data in alive
        assert distsq == pytest.approx(expected, abs=1e-9)
    # a consumed node is still found when empties are not being skipped
    node, distsq = tree.nearest(points[order[0]])
    assert distsq == pytest.approx(0.0, abs=1e-12)


def test_checkempty_with_everything_consumed_returns_none():
    tree, nodes = _build([[0.0, 0.0], [1.0, 1.0], [2.0, 0.0]])
    for n in nodes:
        n.data = None
    assert tree.nearest([0.5, 0.5], checkempty=True) == (None, None)
    node, _ = tree.nearest([0.5, 0.5])  # without checkempty they are still there
    assert node is not None


def test_checkempty_single_consumed_root_returns_none():
    tree = kdtree.Tree(2)
    root = tree.insert([3.0, 3.0], data="x")
    root.data = None
    assert tree.nearest([3.0, 3.0], checkempty=True) == (None, None)


def test_duplicate_points_are_distinct_nodes():
    tree = kdtree.Tree(2)
    a = tree.insert([1.0, 1.0], data="a")
    b = tree.insert([1.0, 1.0], data="b")
    assert a is not b
    node, distsq = tree.nearest([1.0, 1.0])
    assert node.data in ("a", "b") and distsq == 0.0
    # consuming the one that was found leaves the other as the answer
    node.data = None
    other, distsq = tree.nearest([1.0, 1.0], checkempty=True)
    assert other.data == ("b" if node is a else "a") and distsq == 0.0


def test_insert_returns_node_and_tracks_depth():
    tree = kdtree.Tree(1)
    assert tree.level == 0
    for i in range(5):  # ascending 1-D inserts degenerate to a right-leaning chain
        node = tree.insert([float(i)], data=i)
        assert node.pos == [float(i)] and node.data == i
    assert tree.level == 4
    # descending values go left of the root and are still found
    tree.insert([-1.0], data="neg")
    node, distsq = tree.nearest([-0.9])
    assert node.data == "neg" and distsq == pytest.approx(0.01)


def test_node_directions_alternate_with_depth():
    tree = kdtree.Tree(2)
    root = tree.insert([5.0, 5.0], data=0)
    left = tree.insert([1.0, 9.0], data=1)
    right = tree.insert([9.0, 1.0], data=2)
    deeper = tree.insert([0.0, 0.0], data=3)  # left of root, then below left on y
    assert (root.dir, left.dir, right.dir, deeper.dir) == (0, 1, 1, 0)
    assert (root.level, left.level, right.level, deeper.level) == (0, 1, 1, 2)
    assert root.left is left and root.right is right and left.left is deeper


def test_counters_count_queries_and_visits_and_reset():
    rng = random.Random(7)
    tree, _ = _build(_random_points(rng, 100, 2))
    for _ in range(5):
        tree.nearest([0.0, 0.0])
    assert tree.nnearest == 5
    assert 5 <= tree.count <= 5 * 100
    tree.resetcounters()
    assert (tree.nnearest, tree.count) == (0, 0)


def test_rectangles_bound_every_point_below_them():
    rng = random.Random(3)
    points = _random_points(rng, 60, 3)
    tree, nodes = _build(points)

    def subtree(n):
        yield n
        for child in (n.left, n.right):
            if child is not None:
                yield from subtree(child)

    for n in nodes:
        pts = [m.pos for m in subtree(n)]
        for i in range(3):
            assert n.rect.min[i] == min(p[i] for p in pts)
            assert n.rect.max[i] == max(p[i] for p in pts)


def test_hyperrectangle_does_not_alias_its_inputs():
    lo, hi = [0.0, 0.0], [1.0, 1.0]
    rect = kdtree.Hyperrectangle(2, lo, hi)
    rect.extend([5.0, -5.0])
    assert (lo, hi) == ([0.0, 0.0], [1.0, 1.0])
    assert rect.min == [0.0, -5.0] and rect.max == [5.0, 1.0]
    assert rect.distance_squared([6.0, 1.0]) == pytest.approx(1.0)
    assert str(rect) == "[(2) [0.0, -5.0]:[5.0, 1.0]]"


def test_str_renders_the_whole_tree():
    assert str(kdtree.Tree(2)) == "None"
    tree = kdtree.Tree(2)
    tree.insert([1.0, 1.0], data=0)
    tree.insert([0.0, 2.0], data=1)
    tree.insert([2.0, 0.0], data=2)
    text = str(tree)
    assert text.count("\n") == 3
    assert "L:" in text and "R:" in text
    assert "[1.0, 1.0]" in text and "[0.0, 2.0]" in text and "[2.0, 0.0]" in text
