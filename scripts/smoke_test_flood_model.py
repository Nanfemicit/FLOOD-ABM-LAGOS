# scripts/smoke_test_flood_model.py
"""
First-look smoke test, not a validated result: wire FloodModel up to the
real 100m grid and run ONE step using June 2025's actual CHIRPS monthly
total (357mm, the seasonal peak -- picked because it's the easiest month
to visually confirm something happened, per the request that prompted
this script). Reports whether flooded cells land somewhere physically
sane (low-lying, poorly-drained) rather than scattered randomly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

from model.flood_model import FloodModel

OUT_DIR = "data/processed"


def main():
    with rasterio.open(f"{OUT_DIR}/grid_template.tif") as src:
        valid = src.read(1).astype(bool)
        transform = src.transform
        b = src.bounds
        extent = (b.left, b.right, b.bottom, b.top)
    with rasterio.open(f"{OUT_DIR}/lagos_elevation_100m.tif") as src:
        elev = np.nan_to_num(src.read(1), nan=0.0).astype("float32")
    with rasterio.open(f"{OUT_DIR}/lagos_flow_direction_100m.tif") as src:
        flow_dir = src.read(1)
    with rasterio.open(f"{OUT_DIR}/lagos_rainfall_100m_2025.tif") as src:
        rain_bands = [src.descriptions[i] for i in range(src.count)]
        june_idx = rain_bands.index("2025-Jun")
        june_rain = src.read(june_idx + 1)
        june_rain = np.nan_to_num(june_rain, nan=0.0)
    with rasterio.open(f"{OUT_DIR}/lagos_landcover_100m.tif") as src:
        lc = src.read()
        lc_bands = [src.descriptions[i] for i in range(src.count)]
    with rasterio.open(f"{OUT_DIR}/lagos_ward_id_100m.tif") as src:
        ward_id = src.read(1)
    lookup = pd.read_csv(f"{OUT_DIR}/ward_id_lookup.csv").set_index("ward_id")

    print(f"June 2025 rainfall: mean {june_rain[valid].mean():.1f}mm, "
          f"max {june_rain[valid].max():.1f}mm (over valid cells)")

    model = FloodModel(elev, flow_dir, valid)
    print(f"\nModel built. infil={model.infil_mm}mm drain={model.drain_mm}mm "
          f"flood_threshold={model.flood_threshold_mm}mm")

    model.apply_rainfall(june_rain)

    flooded = model.flooded_mask
    n_flooded = int(flooded.sum())
    n_valid = int(valid.sum())
    print(f"\n=== Step 1 result ===")
    print(f"Flooded cells (depth >= {model.flood_threshold_mm}mm): {n_flooded:,} / {n_valid:,} ({100*n_flooded/n_valid:.2f}%)")
    print(f"Water exited the tracked system (flats/off-domain): {model.exited_total_mm_cells:,.0f} mm*cells")
    print(f"Max depth reached: {model.water_depth[valid].max():.1f}mm")

    # --- sanity check: are flooded cells low-lying / poorly-drained? ---
    print("\n=== Sanity check: flooded cell characteristics vs grid-wide ===")
    elev_flooded = elev[flooded]
    elev_all = elev[valid]
    print(f"  elevation -- flooded mean: {elev_flooded.mean():.2f}m  |  grid-wide mean: {elev_all.mean():.2f}m")
    print(f"  elevation -- flooded max:  {elev_flooded.max():.2f}m  |  grid-wide max:  {elev_all.max():.2f}m")

    pit_mask = (flow_dir == -2) & valid
    flat_mask = (flow_dir == -1) & valid
    frac_flooded_is_pit = float((flooded & pit_mask).sum()) / n_flooded if n_flooded else float("nan")
    frac_flooded_is_flat = float((flooded & flat_mask).sum()) / n_flooded if n_flooded else float("nan")
    print(f"  flooded cells that are pits (accumulation sinks): {100*frac_flooded_is_pit:.1f}%")
    print(f"  flooded cells that are flats (should be ~0%, those exit not accumulate): {100*frac_flooded_is_flat:.1f}%")

    built_up = np.nan_to_num(lc[lc_bands.index("built_up")], nan=0.0)
    water_frac = np.nan_to_num(lc[lc_bands.index("water_total")], nan=0.0)
    print(f"  built_up frac -- flooded mean: {built_up[flooded].mean():.3f}  |  grid-wide: {built_up[valid].mean():.3f}")
    print(f"  water frac    -- flooded mean: {water_frac[flooded].mean():.3f}  |  grid-wide: {water_frac[valid].mean():.3f}")

    wids = ward_id[flooded]
    wids = wids[wids != 0]
    lga_counts = pd.Series(wids).map(lookup["lganame"]).value_counts()
    print(f"\n  flooded cells span {lga_counts.shape[0]} of 20 LGAs; top contributors:")
    for lga, cnt in lga_counts.head(8).items():
        print(f"    {lga}: {int(cnt):,}")

    # --- preview plot ---
    fig, axes = plt.subplots(1, 2, figsize=(16, 4))
    axes[0].imshow(np.where(valid, elev, np.nan), cmap="terrain", extent=extent, vmin=0, vmax=50)
    axes[0].set_title("Elevation (m)")

    overlay = np.where(valid, 0, np.nan)
    overlay = np.where(flooded, 1, overlay)
    axes[1].imshow(np.where(valid, elev, np.nan), cmap="Greys", extent=extent, vmin=0, vmax=50)
    fr, fc = np.where(flooded)
    fx = transform.c + (fc + 0.5) * transform.a
    fy = transform.f + (fr + 0.5) * transform.e
    axes[1].scatter(fx, fy, s=1, c="red", alpha=0.5, label=f"flooded ({n_flooded:,} cells)")
    axes[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.08))
    axes[1].set_title(f"Flooded cells after 1 step (June 2025 rainfall, {june_rain[valid].mean():.0f}mm mean)")

    for ax in axes:
        ax.set_aspect("equal")
    fig.suptitle("FloodModel smoke test -- first look, not a validated result")
    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/flood_smoke_test_preview.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {OUT_DIR}/flood_smoke_test_preview.png")


if __name__ == "__main__":
    main()
