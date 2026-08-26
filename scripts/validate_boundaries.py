# scripts/validate_boundaries.py
"""Load the GRID3 Lagos ward boundary layer, confirm it opens cleanly, and plot it."""
import geopandas as gpd
import matplotlib.pyplot as plt

BOUNDARY_PATH = "data/raw/boundaries/grid3_lagos_wards.geojson"


def main():
    gdf = gpd.read_file(BOUNDARY_PATH)

    print(f"Loaded {len(gdf)} features")
    print(f"CRS: {gdf.crs}")
    print(f"Columns: {list(gdf.columns)}")
    print(f"Total bounds (minx, miny, maxx, maxy): {gdf.total_bounds}")
    print(f"LGAs represented: {gdf['lganame'].nunique()}")
    print(f"Status values: {gdf['status'].unique()}")

    fig, ax = plt.subplots(figsize=(8, 10))
    gdf.plot(ax=ax, column="lganame", edgecolor="black", linewidth=0.2, legend=False)
    ax.set_title(f"GRID3 Lagos Ward Boundaries (n={len(gdf)})")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig("data/raw/boundaries/lagos_wards_preview.png", dpi=150)
    print("Saved plot to data/raw/boundaries/lagos_wards_preview.png")


if __name__ == "__main__":
    main()
