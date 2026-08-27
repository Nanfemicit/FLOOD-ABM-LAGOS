# scripts/derive_flow_direction.py
"""
Derive D8 (8-neighbor steepest descent) flow direction from the 100m
elevation layer, using pysheds directly on the raster array -- this is a
raster computation, not agent behavior, so it stays out of Mesa entirely.

richdem was tried first (CLAUDE.md's other named option) but its wheel
fails to build against Python 3.13 -- its bundled pybind11 code predates
CPython 3.11's internal frame-object changes (C2027/C2660/ssize_t errors
in pybind11.h/numpy.h). pysheds is pure Python + numba, installs cleanly,
and exposes the same D8 steepest-descent primitive, so it's the one
actually used here.

v1 deliberately does NOT fill depressions/pits before routing. In a flood
model, a local sink is not automatically a bug to be corrected away --
it can be a real place water ponds. Filling it here would silently erase
that. Sinks are instead flagged and left for a later, explicit decision
(fill vs. treat as a real ponding cell) once the drainage/overflow rules
are being designed.

Output encoding matches pysheds' own D8 scheme (standard ESRI convention):
  1=E  2=SE  4=S  8=SW  16=W  32=NW  64=N  128=NE   (valid downslope direction)
  -1 = flat   (no single steepest-descent neighbor -- a tie, not a true low point)
  -2 = pit    (true local minimum -- every neighbor is higher or off-grid)
   0 = nodata (outside the ward mask, matches grid_template.tif)
"""
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pysheds.grid import Grid

ELEVATION_PATH = "data/processed/lagos_elevation_100m.tif"
GRID_TEMPLATE_PATH = "data/processed/grid_template.tif"
LANDCOVER_PATH = "data/processed/lagos_landcover_100m.tif"
WARD_ID_PATH = "data/processed/lagos_ward_id_100m.tif"
WARD_LOOKUP_PATH = "data/processed/ward_id_lookup.csv"
OUT_PATH = "data/processed/lagos_flow_direction_100m.tif"

# ESRI D8 code -> (d_row, d_col) neighbor offset. Grid is north-up
# (row 0 = north edge), so +row = south.
DIRMAP_OFFSETS = {
    1: (0, 1),      # E
    2: (1, 1),      # SE
    4: (1, 0),      # S
    8: (1, -1),     # SW
    16: (0, -1),    # W
    32: (-1, -1),   # NW
    64: (-1, 0),    # N
    128: (-1, 1),   # NE
}


def main():
    grid = Grid.from_raster(ELEVATION_PATH)
    dem = grid.read_raster(ELEVATION_PATH)
    fdir = grid.flowdir(dem, routing="d8")
    fdir = np.asarray(fdir, dtype="int16")

    with rasterio.open(GRID_TEMPLATE_PATH) as src:
        valid = src.read(1).astype(bool)
        transform = src.transform
        crs = src.crs

    assert fdir.shape == valid.shape, "flow direction shape doesn't match grid_template"

    with rasterio.open(
        OUT_PATH, "w", driver="GTiff", height=fdir.shape[0], width=fdir.shape[1],
        count=1, dtype="int16", crs=crs, transform=transform, nodata=0, compress="lzw",
    ) as dst:
        dst.write(fdir, 1)
    print(f"Saved {OUT_PATH}")

    # ================= validation =================
    print("\n=== Alignment check ===")
    with rasterio.open(OUT_PATH) as src:
        print(f"  shape={src.shape} transform==grid_template: {src.transform == transform}  crs==grid_template: {src.crs == crs}")

    with rasterio.open(ELEVATION_PATH) as src:
        elev = src.read(1)

    n_valid = int(valid.sum())
    vals, counts = np.unique(fdir[valid], return_counts=True)
    print("\n=== Flow direction class breakdown (within valid mask) ===")
    for v, c in sorted(zip(vals.tolist(), counts.tolist())):
        label = {-2: "pit", -1: "flat"}.get(v, f"dir={v}")
        print(f"  {label:8s}: {c:>8,}  ({100*c/n_valid:.2f}%)")

    dir_codes = [1, 2, 4, 8, 16, 32, 64, 128]
    has_dir = np.isin(fdir, dir_codes)
    n_dir = int(has_dir.sum())

    # --- check 1: flow points downhill ---
    print("\n=== Check: flow direction points to lower elevation ===")
    rows, cols = np.where(has_dir)
    codes = fdir[rows, cols]
    target_rows = rows.copy()
    target_cols = cols.copy()
    for code, (dr, dc) in DIRMAP_OFFSETS.items():
        m = codes == code
        target_rows[m] = rows[m] + dr
        target_cols[m] = cols[m] + dc
    in_bounds = (
        (target_rows >= 0) & (target_rows < fdir.shape[0]) &
        (target_cols >= 0) & (target_cols < fdir.shape[1])
    )
    src_elev = elev[rows[in_bounds], cols[in_bounds]]
    tgt_elev = elev[target_rows[in_bounds], target_cols[in_bounds]]
    delta = src_elev - tgt_elev
    n_checked = delta.size
    n_downhill = int((delta > 0).sum())
    n_flat_delta = int((delta == 0).sum())
    n_uphill = int((delta < 0).sum())
    print(f"  {n_checked:,} directed cells checked")
    print(f"  downhill (source > target): {n_downhill:,} ({100*n_downhill/n_checked:.2f}%)")
    print(f"  equal elevation:            {n_flat_delta:,} ({100*n_flat_delta/n_checked:.2f}%)")
    print(f"  uphill (source < target):   {n_uphill:,} ({100*n_uphill/n_checked:.2f}%)  <- should be ~0, real bug if not")
    print(f"  mean elevation drop: {delta.mean():.3f} m, min: {delta.min():.3f} m")

    # --- check 2: near-water cells trend toward water ---
    print("\n=== Check: near-water land cells flow toward higher water fraction ===")
    with rasterio.open(LANDCOVER_PATH) as src:
        lc = src.read()
        lc_bands = [src.descriptions[i] for i in range(src.count)]
    water = lc[lc_bands.index("water_total")]
    water = np.nan_to_num(water, nan=0.0)

    # "near water": land cells (water < 0.5) with at least one 8-neighbor at water >= 0.5
    is_land = water < 0.5
    is_water_dom = water >= 0.5
    from scipy.ndimage import binary_dilation
    water_dom_dilated = binary_dilation(is_water_dom, structure=np.ones((3, 3)))
    near_water_land = is_land & water_dom_dilated & valid & has_dir

    nr, nc = np.where(near_water_land)
    ncodes = fdir[nr, nc]
    ntr, ntc = nr.copy(), nc.copy()
    for code, (dr, dc) in DIRMAP_OFFSETS.items():
        m = ncodes == code
        ntr[m] = nr[m] + dr
        ntc[m] = nc[m] + dc
    nb = (ntr >= 0) & (ntr < fdir.shape[0]) & (ntc >= 0) & (ntc < fdir.shape[1])
    src_water = water[nr[nb], nc[nb]]
    tgt_water = water[ntr[nb], ntc[nb]]
    toward_water = int((tgt_water >= src_water).sum())
    print(f"  {nb.sum():,} near-water land cells with a valid flow direction")
    print(f"  flow toward equal-or-higher water fraction: {toward_water:,} ({100*toward_water/nb.sum():.2f}%)")

    # --- sink clustering ---
    print("\n=== Sink clustering ===")
    lookup = pd.read_csv(WARD_LOOKUP_PATH).set_index("ward_id")
    with rasterio.open(WARD_ID_PATH) as src:
        ward_id = src.read(1)

    built_up = lc[lc_bands.index("built_up")]
    built_up = np.nan_to_num(built_up, nan=0.0)

    for label, code in [("pit", -2), ("flat", -1)]:
        mask = (fdir == code) & valid
        n = int(mask.sum())
        mean_built = float(built_up[mask].mean()) if n else float("nan")
        mean_water = float(water[mask].mean()) if n else float("nan")
        mean_built_all = float(built_up[valid].mean())
        mean_water_all = float(water[valid].mean())
        print(f"\n  {label} cells: {n:,} total")
        print(f"    mean built_up fraction: {mean_built:.3f}  (vs {mean_built_all:.3f} grid-wide)")
        print(f"    mean water fraction:    {mean_water:.3f}  (vs {mean_water_all:.3f} grid-wide)")

        # Aggregate by LGA across ALL affected wards, not just the top few
        # individual wards -- taking top-N wards before grouping understates
        # LGAs where the count is spread thinly across many small wards
        # rather than piled into one or two.
        wids = ward_id[mask]
        wids = wids[wids != 0]
        lga_series = pd.Series(wids).map(lookup["lganame"])
        lga_counts = lga_series.value_counts()
        print(f"    spread across {lga_counts.shape[0]} of 20 LGAs; top contributors:")
        cum = 0
        for lga, cnt in lga_counts.head(8).items():
            cum += cnt
            print(f"      {lga}: {int(cnt):,}  (cumulative {100*cum/n:.1f}%)")

    # --- preview plot: pit/flat locations over elevation ---
    print("\n=== Saving preview plot ===")
    import matplotlib.pyplot as plt

    with rasterio.open(GRID_TEMPLATE_PATH) as src:
        b = src.bounds
        extent = (b.left, b.right, b.bottom, b.top)

    fig, ax = plt.subplots(figsize=(16, 4))
    elev_masked = np.where(valid, elev, np.nan)
    ax.imshow(elev_masked, cmap="Greys", extent=extent, vmin=0, vmax=50)
    pit_rows, pit_cols = np.where((fdir == -2) & valid)
    flat_rows, flat_cols = np.where((fdir == -1) & valid)
    px = transform.c + (pit_cols + 0.5) * transform.a
    py = transform.f + (pit_rows + 0.5) * transform.e
    fx = transform.c + (flat_cols + 0.5) * transform.a
    fy = transform.f + (flat_rows + 0.5) * transform.e
    ax.scatter(fx, fy, s=1, c="deepskyblue", alpha=0.3, label=f"flat ({len(flat_rows):,}, ~water surfaces)")
    ax.scatter(px, py, s=2, c="red", alpha=0.6, label=f"pit ({len(pit_rows):,}, local sinks)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=2, markerscale=6)
    ax.set_title("D8 flow direction: pit and flat (no-valid-downslope) cells over elevation")
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig("data/processed/flow_direction_sinks_preview.png", dpi=150, bbox_inches="tight")
    print("  saved data/processed/flow_direction_sinks_preview.png")

    print("\nDone.")


if __name__ == "__main__":
    main()
