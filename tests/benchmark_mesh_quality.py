"""Offline CPU/RSS smoke benchmark: python -m tests.benchmark_mesh_quality --grid 707."""
import argparse
import json
import resource
import struct
import sys
import time

import numpy as np

from vercel_app.mesh_quality import _RECORD, process_stl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", type=int, default=316)
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    n = args.grid
    x, y = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
    points = np.stack([x, y, (x % 7) * (y % 11)], axis=-1).astype(float)
    triangles = np.stack([points[:-1, :-1], points[1:, :-1], points[1:, 1:],
                          points[:-1, :-1], points[1:, 1:], points[:-1, 1:]], axis=2).reshape(-1, 3, 3)
    if args.repair:
        triangles[::7] = triangles[::7, [0, 2, 1], :]
    records = np.zeros(len(triangles), dtype=_RECORD)
    records["vertices"] = triangles
    raw = bytes(80) + struct.pack("<I", len(triangles)) + records.tobytes()
    del records
    del points, triangles, x, y
    start = time.monotonic()
    report, output = process_stl(raw, repair=args.repair)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({"faces": 2 * n * n, "bytes": len(raw), "seconds": round(time.monotonic() - start, 3),
                      "peak_rss_mib": round(rss / (1024 ** 2 if sys.platform == "darwin" else 1024), 1),
                      "status": report["status"], "stats": report["stats"], "issues": report["issues"],
                      "repair_bytes": len(output) if output else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
