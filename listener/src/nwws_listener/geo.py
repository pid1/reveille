"""point-in-polygon, maidenhead grid squares, and polygon/box overlap.

the polygons nws draws are a few dozen km across, so planar geometry in
degrees is accurate enough here. nothing needs a projection.
"""

from __future__ import annotations

Point = tuple[float, float]  # (lat, lon)


def contains(poly: list[Point], p: Point) -> bool:
    """ray casting. a point exactly on an edge may land either way, which is
    fine: the grid-square check catches it as 'nearby' at worst."""
    lat, lon = p
    inside = False
    n = len(poly)
    for i in range(n):
        y1, x1 = poly[i]
        y2, x2 = poly[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x:
                inside = not inside
    return inside


def maidenhead(lat: float, lon: float, precision: int = 6) -> str:
    lon += 180.0
    lat += 90.0
    s = chr(ord("A") + int(lon // 20)) + chr(ord("A") + int(lat // 10))
    lon %= 20
    lat %= 10
    s += str(int(lon // 2)) + str(int(lat // 1))
    if precision >= 6:
        lon %= 2
        lat %= 1
        s += chr(ord("a") + int(lon * 12)) + chr(ord("a") + int(lat * 24))
    return s


def grid_box(grid: str) -> tuple[float, float, float, float]:
    """(south, west, north, east) of a 4- or 6-character maidenhead locator."""
    g = grid.strip()
    lon = (ord(g[0].upper()) - ord("A")) * 20 - 180
    lat = (ord(g[1].upper()) - ord("A")) * 10 - 90
    lon += int(g[2]) * 2
    lat += int(g[3])
    dlon, dlat = 2.0, 1.0
    if len(g) >= 6:
        lon += (ord(g[4].lower()) - ord("a")) * (2 / 24)
        lat += (ord(g[5].lower()) - ord("a")) * (1 / 24)
        dlon, dlat = 2 / 24, 1 / 24
    return (lat, lon, lat + dlat, lon + dlon)


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orient(p: Point, q: Point, r: Point) -> float:
        return (q[1] - p[1]) * (r[0] - p[0]) - (q[0] - p[0]) * (r[1] - p[1])

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def overlaps_box(poly: list[Point], box: tuple[float, float, float, float]) -> bool:
    s, w, n, e = box
    corners = [(s, w), (s, e), (n, e), (n, w)]
    if any(s <= lat <= n and w <= lon <= e for lat, lon in poly):
        return True
    if any(contains(poly, c) for c in corners):
        return True
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        for j in range(4):
            if _segments_cross(a, b, corners[j], corners[(j + 1) % 4]):
                return True
    return False
