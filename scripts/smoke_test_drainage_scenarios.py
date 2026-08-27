# scripts/smoke_test_drainage_scenarios.py
"""
Same storm, same city, two drainage-maintenance realities. Runs the
June 2-4 2025 daily-stepped smoke test twice, identical rainfall and
identical (SCS/NRCS-grounded, land-cover-blended) infiltration both
times, varying only drain_mm:
  - well-maintained: drain_mm = 20 (design-standard urban minor drainage)
  - poorly-maintained: drain_mm = 1.5 (documented-as-common blocked/
    degraded condition, see data/README.md)

This is close to the actual argument the project exists to make: hold
climate (rainfall) and infiltration (land cover) fixed, vary only the
governance/maintenance variable, and see how much the outcome moves.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

from model.flood_model import FloodModel, compute_infiltration_rate

OUT_DIR = "data/processed"
DAILY_RAINFALL_PATH = f"{OUT_DIR}/lagos_rainfall_100m_daily_2025_06_02-04.tif"

URBAN_CORE_LGAS = ["Lagos Island", "Ikeja", "Lagos Mainland"]


def load_grid():
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
    return dict(valid=valid, transform=transform, extent=extent, elev=elev, flow_dir=flow_dir,
                daily_labels=daily_labels, daily_rain=daily_rain, lc=lc, lc_bands=lc_bands,
                ward_id=ward_id, lookup=lookup)


def run_scenario(data, drain_mm, label):
    infil_array = compute_infiltration_rate(data["lc"], data["lc_bands"])
    model = FloodModel(data["elev"], data["flow_dir"], data["valid"],
                        infil_mm=infil_array, drain_mm=drain_mm)
    hop_counts = []
    daily_depth_snapshots = []
    for rain in data["daily_rain"]:
        _, n_hops = model.apply_rainfall(rain)
        hop_counts.append(n_hops)
        daily_depth_snapshots.append(model.water_depth.copy())
    flooded = model.flooded_mask
    print(f"  routing hops per day: {hop_counts} (cap={model.MAX_ROUTING_HOPS})")
    return model, flooded, daily_depth_snapshots


def describe_scenario(data, model, flooded, daily_depth_snapshots, label):
    valid = data["valid"]
    n_valid = int(valid.sum())
    n_flooded = int(flooded.sum())
    print(f"\n{'='*60}\n{label} (drain_mm={model.drain_mm})\n{'='*60}")
    print(f"Flooded: {n_flooded:,} / {n_valid:,} ({100*n_flooded/n_valid:.2f}%)")
    print(f"Max standing depth: {model.water_depth[valid].max():.1f}mm")
    print(f"Exited total: {model.exited_total_mm_cells:,.0f} mm*cells")

    # --- final day vs cumulative peak: only meaningful now that depth can recede ---
    final_depth = daily_depth_snapshots[-1]
    peak_depth = np.maximum.reduce(daily_depth_snapshots)
    final_flooded = (final_depth >= model.flood_threshold_mm) & valid
    peak_flooded = (peak_depth >= model.flood_threshold_mm) & valid
    n_final = int(final_flooded.sum())
    n_peak = int(peak_flooded.sum())
    receded = int((peak_flooded & ~final_flooded).sum())
    print(f"\nFinal-day (day 3) flooded: {n_final:,} / {n_valid:,} ({100*n_final/n_valid:.2f}%)")
    print(f"Cumulative peak (any of the 3 days) flooded: {n_peak:,} / {n_valid:,} ({100*n_peak/n_valid:.2f}%)")
    print(f"Cells that peaked flooded but had receded below threshold by day 3: {receded:,} "
          f"({100*receded/n_peak:.2f}% of peak, if peak>0)" if n_peak else "")
    print(f"Final-day max depth: {final_depth[valid].max():.1f}mm  |  "
          f"Peak (any day) max depth: {peak_depth[valid].max():.1f}mm")

    lc, lc_bands, ward_id, lookup = data["lc"], data["lc_bands"], data["ward_id"], data["lookup"]
    built_up = np.nan_to_num(lc[lc_bands.index("built_up")], nan=0.0)

    # --- pit vs non-pit breakdown (the whole point of this run) ---
    pit_mask = (data["flow_dir"] == -2) & valid
    n_flooded_pit = int((flooded & pit_mask).sum())
    n_flooded_nonpit = n_flooded - n_flooded_pit
    print(f"\nFlooded cells that are pits: {n_flooded_pit:,} / {n_flooded:,} "
          f"({100*n_flooded_pit/n_flooded:.2f}%)")
    print(f"Flooded cells that are NOT pits (new, from the conveyance cap): {n_flooded_nonpit:,} "
          f"({100*n_flooded_nonpit/n_flooded:.2f}%)")
    nonpit_flooded = flooded & ~pit_mask
    depth = model.water_depth
    print(f"\nDepth breakdown (mm) -- confirms whether pits still drive the tail:")
    print(f"  ALL flooded    : mean={depth[flooded].mean():>8.1f}  median={np.median(depth[flooded]):>7.1f}  max={depth[flooded].max():>9.1f}")
    if n_flooded_pit:
        print(f"  PIT flooded    : mean={depth[flooded & pit_mask].mean():>8.1f}  median={np.median(depth[flooded & pit_mask]):>7.1f}  max={depth[flooded & pit_mask].max():>9.1f}")
    if n_flooded_nonpit:
        nonpit_depth = depth[nonpit_flooded]
        print(f"  NON-PIT flooded: mean={nonpit_depth.mean():>8.1f}  median={np.median(nonpit_depth):>7.1f}  max={nonpit_depth.max():>9.1f}")
        nonpit_built = built_up[nonpit_flooded]
        print(f"  non-pit built-up fraction: mean {nonpit_built.mean():.3f}, "
              f"count with built_up>0.5: {int((nonpit_built > 0.5).sum()):,}")

    # --- built-up core, specifically ---
    core_ids = lookup[lookup["lganame"].isin(URBAN_CORE_LGAS)].index
    core_mask = np.isin(ward_id, core_ids) & valid
    core_flooded = flooded & core_mask
    n_core = int(core_mask.sum())
    n_core_flooded = int(core_flooded.sum())
    print(f"\nBuilt-up core ({', '.join(URBAN_CORE_LGAS)}):")
    print(f"  {n_core_flooded:,} / {n_core:,} core cells flooded ({100*n_core_flooded/n_core:.2f}%)")
    if n_core_flooded:
        core_depth = model.water_depth[core_flooded]
        print(f"  core flooded-cell depth: mean {core_depth.mean():.1f}mm, max {core_depth.max():.1f}mm")
    print(f"  core mean built_up fraction: {built_up[core_mask].mean():.3f} "
          f"(near-zero infiltration buffer -- these cells depend almost entirely on drain_mm)")

    # --- LGA breakdown ---
    wids = ward_id[flooded]
    wids = wids[wids != 0]
    lga_counts = pd.Series(wids).map(lookup["lganame"]).value_counts()
    print(f"\nFlooded cells span {lga_counts.shape[0]} of 20 LGAs; top contributors:")
    for lga, cnt in lga_counts.head(8).items():
        print(f"  {lga}: {int(cnt):,}")

    return dict(n_valid=n_valid, n_flooded=n_flooded, n_core=n_core, n_core_flooded=n_core_flooded,
                lga_counts=lga_counts)


def main():
    data = load_grid()
    print("Rainy stretch:", ", ".join(data["daily_labels"]))

    model_wet, flooded_wet, snaps_wet = run_scenario(data, 20.0, "WELL-MAINTAINED")
    stats_wet = describe_scenario(data, model_wet, flooded_wet, snaps_wet, "WELL-MAINTAINED (drain_mm=20)")

    model_poor, flooded_poor, snaps_poor = run_scenario(data, 1.5, "POORLY-MAINTAINED")
    stats_poor = describe_scenario(data, model_poor, flooded_poor, snaps_poor, "POORLY-MAINTAINED (drain_mm=1.5)")

    print(f"\n{'='*60}\nCOMPARISON\n{'='*60}")
    print(f"Overall flooded: {100*stats_wet['n_flooded']/stats_wet['n_valid']:.2f}% (well) vs "
          f"{100*stats_poor['n_flooded']/stats_poor['n_valid']:.2f}% (poor) -- "
          f"{stats_poor['n_flooded']/max(stats_wet['n_flooded'],1):.1f}x")
    print(f"Built-up core flooded: {100*stats_wet['n_core_flooded']/stats_wet['n_core']:.2f}% (well) vs "
          f"{100*stats_poor['n_core_flooded']/stats_poor['n_core']:.2f}% (poor)")

    # --- preview: side-by-side flooded maps ---
    fig, axes = plt.subplots(1, 2, figsize=(16, 4))
    valid, elev, transform, extent = data["valid"], data["elev"], data["transform"], data["extent"]
    for ax, flooded, label, n in [
        (axes[0], flooded_wet, "Well-maintained (drain_mm=20)", stats_wet["n_flooded"]),
        (axes[1], flooded_poor, "Poorly-maintained (drain_mm=1.5)", stats_poor["n_flooded"]),
    ]:
        ax.imshow(np.where(valid, elev, np.nan), cmap="Greys", extent=extent, vmin=0, vmax=50)
        fr, fc = np.where(flooded)
        fx = transform.c + (fc + 0.5) * transform.a
        fy = transform.f + (fr + 0.5) * transform.e
        ax.scatter(fx, fy, s=1, c="red", alpha=0.5, label=f"flooded ({n:,} cells)")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08))
        ax.set_title(label)
        ax.set_aspect("equal")
    fig.suptitle("Same storm (June 2-4 2025), same infiltration -- drain_mm varied only")
    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/drainage_scenario_comparison.png", dpi=150, bbox_inches="tight")
    print(f"\nSaved {OUT_DIR}/drainage_scenario_comparison.png")


if __name__ == "__main__":
    main()
