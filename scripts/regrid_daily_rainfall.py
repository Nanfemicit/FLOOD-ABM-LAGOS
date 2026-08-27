# scripts/regrid_daily_rainfall.py
"""
Regrid a specific set of daily CHIRPS rasters onto the 100m model grid,
the same way scripts/build_grid.py regrids the monthly product: bilinear
from the wide-area source cache (not a pre-clipped file, so the source
always covers the target extent), with src_nodata=-9999 set explicitly
(the same fix that was needed for the monthly regrid -- CHIRPS never
tags -9999 as nodata in the file's own metadata).
"""
import gzip
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

BOUNDARY_PATH = "data/raw/boundaries/grid3_lagos_wards.geojson"
GRID_TEMPLATE_PATH = "data/processed/grid_template.tif"
DAILY_CACHE = Path("data/raw/rainfall/_source_cache/daily_2025_06")
TARGET_CRS = "EPSG:32631"


def regrid_days(year, month, days, out_path):
    with rasterio.open(GRID_TEMPLATE_PATH) as src:
        transform = src.transform
        shape = (src.height, src.width)
        crs = src.crs

    bands = []
    labels = []
    for day in days:
        gz_path = DAILY_CACHE / f"chirps-v2.0.{year}.{month:02d}.{day:02d}.tif.gz"
        tmp_tif = DAILY_CACHE / f"_tmp_{year}_{month:02d}_{day:02d}.tif"
        with gzip.open(gz_path, "rb") as f_in, open(tmp_tif, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        with rasterio.open(tmp_tif) as src:
            out = np.full(shape, np.nan, dtype="float32")
            reproject(
                source=rasterio.band(src, 1), destination=out,
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=transform, dst_crs=TARGET_CRS,
                resampling=Resampling.bilinear,
                src_nodata=-9999.0, dst_nodata=np.nan,
            )
        tmp_tif.unlink()
        bands.append(out)
        labels.append(f"{year}-{month:02d}-{day:02d}")
        print(f"  regridded {labels[-1]}")

    stack = np.stack(bands).astype("float32")
    with rasterio.open(
        out_path, "w", driver="GTiff", height=shape[0], width=shape[1], count=len(bands),
        dtype="float32", crs=crs, transform=transform, nodata=np.nan, compress="lzw",
    ) as dst:
        dst.write(stack)
        for i, label in enumerate(labels, start=1):
            dst.set_band_description(i, label)
    print(f"Saved {out_path}")
    return stack, labels


if __name__ == "__main__":
    regrid_days(2025, 6, [2, 3, 4], "data/processed/lagos_rainfall_100m_daily_2025_06_02-04.tif")
