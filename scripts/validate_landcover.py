# scripts/validate_landcover.py
"""
Validate the clipped ESA WorldCover raster: confirm CRS/resolution, plot
the classified map, report the class breakdown, and sanity-check the
spatial pattern against known Lagos geography (water over the lagoon,
built-up concentrated in Lagos Island/Ikeja/Mainland, more vegetation
toward Epe and Ibeju-Lekki).
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Patch
from rasterio.mask import mask

RASTER_PATH = "data/raw/landcover/lagos_landcover.tif"
BOUNDARY_PATH = "data/raw/boundaries/grid3_lagos_wards.geojson"

# ESA WorldCover v200 official class codes / labels / colors
CLASSES = {
    10: ("Tree cover", "#006400"),
    20: ("Shrubland", "#ffbb22"),
    30: ("Grassland", "#ffff4c"),
    40: ("Cropland", "#f096ff"),
    50: ("Built-up", "#fa0000"),
    60: ("Bare / sparse vegetation", "#b4b4b4"),
    70: ("Snow and ice", "#f0f0f0"),
    80: ("Permanent water bodies", "#0064c8"),
    90: ("Herbaceous wetland", "#0096a0"),
    95: ("Mangroves", "#00cf75"),
    100: ("Moss and lichen", "#fae6a0"),
}


def main():
    with rasterio.open(RASTER_PATH) as src:
        print(f"CRS: {src.crs}")
        print(f"Resolution (deg): {src.res}")
        print(f"Shape: {src.height} x {src.width}")
        print(f"nodata: {src.nodata}")
        data = src.read(1)
        extent = (src.bounds.left, src.bounds.right, src.bounds.bottom, src.bounds.top)

    valid = data[data != 0]
    total = valid.size
    print(f"\nClass breakdown ({total:,} valid pixels inside ward mask):")
    vals, counts = np.unique(valid, return_counts=True)
    for v, c in sorted(zip(vals.tolist(), counts.tolist()), key=lambda x: -x[1]):
        label, _ = CLASSES.get(v, (f"Unknown ({v})", "#000000"))
        print(f"  {v:3d}  {label:28s} {c:>10,}  ({100*c/total:5.2f}%)")

    # --- plot classified map ---
    present_codes = sorted(int(v) for v in vals)
    cmap = ListedColormap([CLASSES.get(c, ("", "#000000"))[1] for c in present_codes])
    bounds = present_codes + [present_codes[-1] + 1]
    norm = BoundaryNorm(bounds, cmap.N)
    plot_data = np.ma.masked_where(data == 0, data)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.imshow(plot_data, cmap=cmap, norm=norm, extent=extent)
    legend_handles = [
        Patch(facecolor=CLASSES[c][1], label=CLASSES[c][0]) for c in present_codes
    ]
    ax.legend(handles=legend_handles, loc="upper center", bbox_to_anchor=(0.5, -0.05),
               ncol=4, fontsize=8)
    ax.set_title("ESA WorldCover v200 (2021), masked to Lagos ward union")
    fig.tight_layout()
    fig.savefig("data/raw/landcover/lagos_landcover_preview.png", dpi=150, bbox_inches="tight")
    print("\nSaved plot to data/raw/landcover/lagos_landcover_preview.png")

    # --- spatial sanity check: built-up / vegetation fraction by LGA ---
    wards = gpd.read_file(BOUNDARY_PATH)
    urban_lgas = ["Lagos Island", "Ikeja", "Lagos Mainland"]
    outer_lgas = ["Epe", "Ibeju Lekki"]

    veg_codes = {10, 20, 30, 40}
    built_code = 50

    print("\nSpatial sanity check (built-up % vs vegetation % by LGA group):")
    with rasterio.open(RASTER_PATH) as src:
        for group_name, lga_list in [("Dense urban core", urban_lgas), ("Outer LGAs", outer_lgas)]:
            sub = wards[wards["lganame"].isin(lga_list)]
            geom = [sub.union_all()]
            clipped, _ = mask(src, geom, crop=True, nodata=0)
            d = clipped[0]
            v = d[d != 0]
            n = v.size
            built_pct = 100 * np.isin(v, [built_code]).sum() / n
            veg_pct = 100 * np.isin(v, list(veg_codes)).sum() / n
            water_pct = 100 * np.isin(v, [80, 90, 95]).sum() / n
            print(f"  {group_name} ({', '.join(lga_list)}):")
            print(f"    built-up: {built_pct:5.2f}%  vegetation: {veg_pct:5.2f}%  water/wetland: {water_pct:5.2f}%")


if __name__ == "__main__":
    main()
