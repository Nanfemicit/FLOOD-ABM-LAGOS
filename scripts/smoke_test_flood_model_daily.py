# scripts/smoke_test_flood_model_daily.py
"""
Second-generation smoke test, still a first look, not a validated result:
run FloodModel across a real multi-day rainy stretch using daily CHIRPS
(not the whole month in one hop, per the rebuilt apply_rainfall()).

Stretch chosen: 2025-06-02 through 2025-06-04. Picked by inspecting all
30 days of June 2025 ward-area-mean daily rainfall (18.87 / 27.80 /
12.98mm) -- the longest run of consecutive days that were ALL
individually substantial, rather than one large spike day surrounded by
near-zero days (which several other candidates in June were, e.g. the
49.62mm single-day spike on the 14th). A genuine sustained multi-day
event is what daily stepping is meant to be tested against.
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
DAILY_RAINFALL_PATH = f"{OUT_DIR}/lagos_rainfall_100m_daily_2025_06_02-04.tif"


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
    with rasterio.open(DAILY_RAINFALL_PATH) as src:
        daily_labels = [src.descriptions[i] for i in range(src.count)]
        daily_rain = [np.nan_to_num(src.read(i + 1), nan=0.0) for i in range(src.count)]
    with rasterio.open(f"{OUT_DIR}/lagos_landcover_100m.tif") as src:
        lc = src.read()
        lc_bands = [src.descriptions[i] for i in range(src.count)]
    with rasterio.open(f"{OUT_DIR}/lagos_ward_id_100m.tif") as src:
        ward_id = src.read(1)
    lookup = pd.read_csv(f"{OUT_DIR}/ward_id_lookup.csv").set_index("ward_id")

    print("Rainy stretch:", ", ".join(daily_labels))
    for label, rain in zip(daily_labels, daily_rain):
        print(f"  {label}: mean {rain[valid].mean():.1f}mm, max {rain[valid].max():.1f}mm")

    model = FloodModel(elev, flow_dir, valid)
    print(f"\nModel built. infil={model.infil_mm}mm drain={model.drain_mm}mm "
          f"flood_threshold={model.flood_threshold_mm}mm")

    for label, rain in zip(daily_labels, daily_rain):
        depth_added, n_hops = model.apply_rainfall(rain)
        flooded_today = int((model.flooded_mask).sum())
        print(f"\n=== {label} ===")
        print(f"  routing hops to convergence: {n_hops}")
        print(f"  cumulative flooded cells (depth >= {model.flood_threshold_mm}mm): "
              f"{flooded_today:,} / {int(valid.sum()):,} ({100*flooded_today/int(valid.sum()):.2f}%)")
        print(f"  cumulative exited total: {model.exited_total_mm_cells:,.0f} mm*cells")
        print(f"  max standing depth: {model.water_depth[valid].max():.1f}mm")

    flooded = model.flooded_mask
    n_flooded = int(flooded.sum())
    n_valid = int(valid.sum())

    print(f"\n=== Final result after {len(daily_labels)} days ===")
    print(f"Flooded cells: {n_flooded:,} / {n_valid:,} ({100*n_flooded/n_valid:.2f}%)")

    # --- sanity check, same structure as the monthly smoke test ---
    print("\n=== Sanity check: flooded cell characteristics ===")
    dry_land = valid & (flow_dir != -1)
    print(f"  elevation -- flooded median: {np.median(elev[flooded]):.2f}m  |  "
          f"dry-land median: {np.median(elev[dry_land]):.2f}m")
    print(f"  elevation -- flooded mean:   {elev[flooded].mean():.2f}m  |  "
          f"dry-land mean:   {elev[dry_land].mean():.2f}m")

    pit_mask = (flow_dir == -2) & valid
    frac_flooded_is_pit = float((flooded & pit_mask).sum()) / n_flooded if n_flooded else float("nan")
    print(f"  flooded cells that are pits: {100*frac_flooded_is_pit:.1f}%  "
          f"(vs {100*int(pit_mask.sum())/n_valid:.1f}% of all valid cells)")

    built_up = np.nan_to_num(lc[lc_bands.index("built_up")], nan=0.0)
    water_frac = np.nan_to_num(lc[lc_bands.index("water_total")], nan=0.0)
    if n_flooded:
        print(f"  built_up frac -- flooded mean: {built_up[flooded].mean():.3f}  |  grid-wide: {built_up[valid].mean():.3f}")
        print(f"  water frac    -- flooded mean: {water_frac[flooded].mean():.3f}  |  grid-wide: {water_frac[valid].mean():.3f}")

    wids = ward_id[flooded]
    wids = wids[wids != 0]
    lga_counts = pd.Series(wids).map(lookup["lganame"]).value_counts()
    print(f"\n  flooded cells span {lga_counts.shape[0]} of 20 LGAs; top contributors:")
    for lga, cnt in lga_counts.head(8).items():
        print(f"    {lga}: {int(cnt):,}")

    same_six = {"Epe", "Ikorodu", "Badagry", "Ibeju Lekki", "Eti Osa", "Ojo"}
    top_six_here = set(lga_counts.head(6).index)
    print(f"\n  overlap with the six LGAs flagged in the sink/monthly-smoke-test analyses: "
          f"{len(same_six & top_six_here)}/6 ({sorted(same_six & top_six_here)})")

    # --- preview plot ---
    fig, axes = plt.subplots(1, 2, figsize=(16, 4))
    axes[0].imshow(np.where(valid, elev, np.nan), cmap="terrain", extent=extent, vmin=0, vmax=50)
    axes[0].set_title("Elevation (m)")

    axes[1].imshow(np.where(valid, elev, np.nan), cmap="Greys", extent=extent, vmin=0, vmax=50)
    fr, fc = np.where(flooded)
    fx = transform.c + (fc + 0.5) * transform.a
    fy = transform.f + (fr + 0.5) * transform.e
    axes[1].scatter(fx, fy, s=1, c="red", alpha=0.5, label=f"flooded ({n_flooded:,} cells)")
    axes[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.08))
    axes[1].set_title(f"Flooded cells after {len(daily_labels)} daily steps ({daily_labels[0]} to {daily_labels[-1]})")

    for ax in axes:
        ax.set_aspect("equal")
    fig.suptitle("FloodModel daily-step smoke test -- first look, not a validated result")
    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/flood_smoke_test_daily_preview.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {OUT_DIR}/flood_smoke_test_daily_preview.png")


if __name__ == "__main__":
    main()
