# scripts/validate_rainfall.py
"""
Validate the clipped monthly CHIRPS rasters: confirm CRS/resolution,
compute ward-masked monthly totals for Lagos, plot the seasonal cycle,
and check it against Lagos's known bimodal rainfall pattern (peak
Jun/Jul, August break, secondary Sep/Oct peak).
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.mask import mask

BOUNDARY_PATH = "data/raw/boundaries/grid3_lagos_wards.geojson"
YEAR = 2025
MONTHS = [f"{m:02d}" for m in range(1, 13)]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def main():
    wards = gpd.read_file(BOUNDARY_PATH)
    ward_union = [wards.union_all()]

    monthly_mean = []
    for month in MONTHS:
        path = f"data/raw/rainfall/lagos_chirps_{YEAR}_{month}.tif"
        with rasterio.open(path) as src:
            if month == "01":
                print(f"CRS: {src.crs}")
                print(f"Resolution (deg): {src.res}")
                print(f"Shape: {src.height} x {src.width}")
            clipped, _ = mask(src, ward_union, crop=False, nodata=np.nan)
            data = clipped[0]
            data = np.where(data == src.nodata, np.nan, data)
            monthly_mean.append(float(np.nanmean(data)))

    print("\nWard-masked monthly mean rainfall (mm):")
    for label, val in zip(MONTH_LABELS, monthly_mean):
        print(f"  {label}: {val:6.1f}")

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(MONTH_LABELS, monthly_mean, color="steelblue")
    ax.set_ylabel("Mean rainfall (mm)")
    ax.set_title(f"CHIRPS monthly rainfall over Lagos wards, {YEAR}")
    fig.tight_layout()
    fig.savefig("data/raw/rainfall/lagos_rainfall_2025_seasonal.png", dpi=150)
    print("\nSaved plot to data/raw/rainfall/lagos_rainfall_2025_seasonal.png")

    # Bimodal pattern check: peak in Jun/Jul, dip in Aug, secondary peak Sep/Oct
    jun_jul = max(monthly_mean[5], monthly_mean[6])
    aug = monthly_mean[7]
    sep_oct = max(monthly_mean[8], monthly_mean[9])
    peak_month = MONTH_LABELS[int(np.argmax(monthly_mean))]

    print("\nBimodal pattern check:")
    print(f"  Overall peak month: {peak_month}")
    print(f"  Jun/Jul max: {jun_jul:.1f} mm | Aug: {aug:.1f} mm | Sep/Oct max: {sep_oct:.1f} mm")

    checks_passed = True
    if peak_month not in ("Jun", "Jul"):
        print(f"  FLAG: overall peak is {peak_month}, not Jun/Jul as expected.")
        checks_passed = False
    if not (aug < jun_jul):
        print("  FLAG: no August dip relative to the Jun/Jul peak (expected 'August break').")
        checks_passed = False
    if not (sep_oct > aug):
        print("  FLAG: no secondary Sep/Oct rebound above the August dip.")
        checks_passed = False
    if checks_passed:
        print("  Matches expected Lagos bimodal pattern (Jun/Jul peak, Aug dip, Sep/Oct secondary peak).")
    else:
        print("  Pattern does NOT cleanly match the expected bimodal shape -- see flags above.")


if __name__ == "__main__":
    main()
