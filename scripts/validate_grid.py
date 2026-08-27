# scripts/validate_grid.py
"""
Validate the 100m model grid (Phase 2, step 6 of CLAUDE.md): confirm all
layers are pixel-aligned (identical shape + geotransform + CRS, not just
resolution), check for unexpected nodata gaps inside the valid mask,
spot-check individual cells for physical plausibility across layers, and
save a combined preview plot.
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

OUT_DIR = "data/processed"
FILES = {
    "grid_template": f"{OUT_DIR}/grid_template.tif",
    "elevation": f"{OUT_DIR}/lagos_elevation_100m.tif",
    "landcover": f"{OUT_DIR}/lagos_landcover_100m.tif",
    "rainfall": f"{OUT_DIR}/lagos_rainfall_100m_2025.tif",
    "ward_id": f"{OUT_DIR}/lagos_ward_id_100m.tif",
}


def main():
    # --- alignment check ---
    print("=== Alignment check ===")
    profiles = {}
    for name, path in FILES.items():
        with rasterio.open(path) as src:
            profiles[name] = {
                "shape": (src.height, src.width),
                "transform": src.transform,
                "crs": src.crs,
            }
            print(f"{name:12s} shape={profiles[name]['shape']}  crs={src.crs}")

    ref = profiles["grid_template"]
    all_aligned = True
    for name, p in profiles.items():
        shape_ok = p["shape"] == ref["shape"]
        transform_ok = p["transform"] == ref["transform"]
        crs_ok = p["crs"] == ref["crs"]
        ok = shape_ok and transform_ok and crs_ok
        all_aligned &= ok
        status = "OK" if ok else "MISALIGNED"
        print(f"  {name:12s} vs grid_template: shape={shape_ok} transform={transform_ok} crs={crs_ok}  [{status}]")
    print(f"\nAll layers pixel-aligned: {all_aligned}")
    if not all_aligned:
        raise SystemExit("Alignment check FAILED -- stop here, do not proceed to flow direction.")

    # --- load data ---
    with rasterio.open(FILES["grid_template"]) as src:
        valid = src.read(1).astype(bool)
        transform = src.transform
    with rasterio.open(FILES["elevation"]) as src:
        elev = src.read(1)
    with rasterio.open(FILES["landcover"]) as src:
        lc = src.read()
        lc_bands = [src.descriptions[i] for i in range(src.count)]
    with rasterio.open(FILES["rainfall"]) as src:
        rain = src.read()
        rain_bands = [src.descriptions[i] for i in range(src.count)]
    with rasterio.open(FILES["ward_id"]) as src:
        ward_id = src.read(1)

    built_up = lc[lc_bands.index("built_up")]
    water_total = lc[lc_bands.index("water_total")]
    veg_total = lc[lc_bands.index("vegetation_total")]
    rain_annual = np.nansum(rain, axis=0)
    rain_annual = np.where(valid, rain_annual, np.nan)

    # --- nodata-inside-valid-mask check ---
    print("\n=== Nodata-within-valid-mask check ===")
    n_valid = int(valid.sum())
    for name, arr in [("elevation", elev), ("built_up frac", built_up),
                       ("water_total frac", water_total), ("rain_annual", rain_annual)]:
        n_nan = int(np.isnan(arr[valid]).sum())
        print(f"  {name:18s}: {n_nan:,} / {n_valid:,} valid cells are NaN "
              f"({100*n_nan/n_valid:.3f}%)")

    # --- spot checks ---
    print("\n=== Spot checks ===")
    lookup = pd.read_csv(f"{OUT_DIR}/ward_id_lookup.csv").set_index("ward_id")

    def describe_cell(row, col, label):
        wid = int(ward_id[row, col])
        ward_name = lookup.loc[wid, "wardname"] if wid in lookup.index else "(none)"
        lga_name = lookup.loc[wid, "lganame"] if wid in lookup.index else "(none)"
        print(f"  {label} @ (row={row}, col={col}):")
        print(f"    elevation: {elev[row, col]:.2f} m")
        print(f"    built_up: {built_up[row, col]:.2f}  water: {water_total[row, col]:.2f}  veg: {veg_total[row, col]:.2f}")
        print(f"    ward: {ward_name} / {lga_name}")

    # most water-dominant valid cell
    water_masked = np.where(valid, water_total, -1)
    r, c = np.unravel_index(np.argmax(water_masked), water_masked.shape)
    describe_cell(r, c, "Highest water-fraction cell (expect: low elevation, lagoon-adjacent ward)")

    # most built-up-dominant valid cell
    built_masked = np.where(valid, built_up, -1)
    r, c = np.unravel_index(np.argmax(built_masked), built_masked.shape)
    describe_cell(r, c, "Highest built-up-fraction cell (expect: Lagos Island/Ikeja/Mainland)")

    # a cell inside Epe or Ibeju Lekki, check vegetation dominance
    wards_gdf = gpd.read_file("data/raw/boundaries/grid3_lagos_wards.geojson")
    outer_ids = lookup[lookup["lganame"].isin(["Epe", "Ibeju Lekki"])].index
    outer_mask = np.isin(ward_id, outer_ids) & valid
    if outer_mask.any():
        veg_in_outer = np.where(outer_mask, veg_total, -1)
        r, c = np.unravel_index(np.argmax(veg_in_outer), veg_in_outer.shape)
        describe_cell(r, c, "Highest vegetation cell within Epe/Ibeju-Lekki (expect: high veg fraction)")

    # --- combined preview plot ---
    print("\n=== Saving combined preview plot ===")
    extent = None
    with rasterio.open(FILES["grid_template"]) as src:
        b = src.bounds
        extent = (b.left, b.right, b.bottom, b.top)

    fig, axes = plt.subplots(2, 2, figsize=(16, 8))

    im0 = axes[0, 0].imshow(np.where(valid, elev, np.nan), cmap="terrain", extent=extent, vmin=0, vmax=50)
    axes[0, 0].set_title("Elevation (m), 100m avg from GLO-30")
    plt.colorbar(im0, ax=axes[0, 0], shrink=0.7)

    im1 = axes[0, 1].imshow(np.where(valid, built_up, np.nan), cmap="Reds", extent=extent, vmin=0, vmax=1)
    axes[0, 1].set_title("Built-up fraction, 100m from WorldCover")
    plt.colorbar(im1, ax=axes[0, 1], shrink=0.7)

    im2 = axes[1, 0].imshow(rain_annual, cmap="Blues", extent=extent)
    axes[1, 0].set_title("2025 annual rainfall (mm), bilinear from CHIRPS")
    plt.colorbar(im2, ax=axes[1, 0], shrink=0.7)

    im3 = axes[1, 1].imshow(np.where(valid, ward_id, np.nan), cmap="tab20", extent=extent)
    axes[1, 1].set_title("Ward ID (vector rasterization)")
    plt.colorbar(im3, ax=axes[1, 1], shrink=0.7)

    for ax in axes.flat:
        ax.set_aspect("equal")

    fig.suptitle("Lagos 100m model grid -- four aligned layers")
    fig.tight_layout()
    fig.savefig(f"{OUT_DIR}/grid_combined_preview.png", dpi=150)
    print(f"  saved {OUT_DIR}/grid_combined_preview.png")


if __name__ == "__main__":
    main()
