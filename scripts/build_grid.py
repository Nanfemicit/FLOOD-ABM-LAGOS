# scripts/build_grid.py
"""
Build the common 100m model grid (Phase 2, step 1-5 of CLAUDE.md): a
shared raster template, clipped and masked to the Lagos ward union, that
elevation, land cover, rainfall, and ward ID are all resampled onto so
they are pixel-aligned with each other.

CRS: EPSG:32631 (WGS 84 / UTM zone 31N). Lagos sits at ~2.7-4.4 deg E,
comfortably inside zone 31N's 0-6 deg E span, so this gives genuinely
square 100m cells with minimal distortion -- EPSG:4326 degrees would
not (a "0.05 deg" cell is not a fixed size in meters and gets more
elongated east-west as you move away from the equator... though at
Lagos's low latitude the distortion is modest, it's still not exact,
and an ABM whose rules talk about mm/hr flowing between neighboring
cells should have cells that are actually the same physical size).

Outputs (data/processed/):
  - grid_template.tif           : ward-union validity mask (1=inside, 0=outside)
  - lagos_elevation_100m.tif    : DEM, averaged from 30m
  - lagos_landcover_100m.tif    : per-class + aggregate land cover fractions, multi-band
  - lagos_rainfall_100m_2025.tif: 12-band monthly rainfall, bilinear-regridded from ~5.5km
  - lagos_ward_id_100m.tif      : ward ID per cell (vector rasterization, not resampling)
  - ward_id_lookup.csv          : ward_id -> wardcode/wardname/lganame
"""
import gzip
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject

BOUNDARY_PATH = "data/raw/boundaries/grid3_lagos_wards.geojson"
DEM_PATH = "data/raw/elevation/lagos_dem_glo30.tif"
LANDCOVER_PATH = "data/raw/landcover/lagos_landcover.tif"
RAINFALL_SOURCE_CACHE = Path("data/raw/rainfall/_source_cache")
OUT_DIR = Path("data/processed")

TARGET_CRS = "EPSG:32631"
CELL_SIZE = 100.0
YEAR = 2025
MONTHS = [f"{m:02d}" for m in range(1, 13)]

# WorldCover class code -> short name, and the aggregate groups used for
# the convenience "vegetation_total" / "water_total" bands.
LC_CLASSES = {
    10: "tree_cover",
    20: "shrubland",
    30: "grassland",
    40: "cropland",
    50: "built_up",
    60: "bare_sparse_veg",
    80: "water_permanent",
    90: "wetland_herbaceous",
    95: "mangroves",
}
VEGETATION_CODES = [10, 20, 30, 40]
WATER_CODES = [80, 90, 95]


def build_target_grid(wards_utm):
    union = wards_utm.union_all()
    minx, miny, maxx, maxy = union.bounds
    minx = CELL_SIZE * np.floor(minx / CELL_SIZE)
    miny = CELL_SIZE * np.floor(miny / CELL_SIZE)
    maxx = CELL_SIZE * np.ceil(maxx / CELL_SIZE)
    maxy = CELL_SIZE * np.ceil(maxy / CELL_SIZE)
    width = int((maxx - minx) / CELL_SIZE)
    height = int((maxy - miny) / CELL_SIZE)
    transform = from_origin(minx, maxy, CELL_SIZE, CELL_SIZE)
    print(f"Target grid: {width} x {height} cells, {CELL_SIZE}m, {TARGET_CRS}")
    print(f"  bounds: ({minx}, {miny}) - ({maxx}, {maxy})")
    return transform, width, height, union


def write_raster(path, data, transform, crs, nodata, descriptions=None, compress="lzw"):
    count = 1 if data.ndim == 2 else data.shape[0]
    height, width = data.shape[-2], data.shape[-1]
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=count,
        dtype=data.dtype, crs=crs, transform=transform, nodata=nodata, compress=compress,
    ) as dst:
        if data.ndim == 2:
            dst.write(data, 1)
        else:
            dst.write(data)
        if descriptions:
            for i, desc in enumerate(descriptions, start=1):
                dst.set_band_description(i, desc)
    print(f"  wrote {path}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wards = gpd.read_file(BOUNDARY_PATH)
    wards_utm = wards.to_crs(TARGET_CRS)
    transform, width, height, union = build_target_grid(wards_utm)
    shape = (height, width)

    # --- step 1: grid template / validity mask (vector -> raster) ---
    print("\n[1/5] Rasterizing ward union validity mask...")
    valid_mask = rasterize(
        [(union, 1)], out_shape=shape, transform=transform, fill=0, dtype="uint8"
    )
    write_raster(OUT_DIR / "grid_template.tif", valid_mask, transform, TARGET_CRS, nodata=0)
    n_valid = int(valid_mask.sum())
    print(f"  {n_valid:,} / {valid_mask.size:,} cells inside the ward union "
          f"({100*n_valid/valid_mask.size:.1f}%)")

    # --- step 2: elevation, average resampling from 30m ---
    print("\n[2/5] Resampling DEM (average, 30m -> 100m)...")
    with rasterio.open(DEM_PATH) as src:
        elev = np.full(shape, np.nan, dtype="float32")
        reproject(
            source=rasterio.band(src, 1), destination=elev,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=transform, dst_crs=TARGET_CRS,
            resampling=Resampling.average, dst_nodata=np.nan,
        )
    elev = np.where(valid_mask == 1, elev, np.nan)
    write_raster(OUT_DIR / "lagos_elevation_100m.tif", elev, transform, TARGET_CRS, nodata=np.nan)

    # --- step 3: land cover, per-class fractions from 10m, average resampling ---
    # Each class's presence is a 0/1 mask at native 10m resolution; averaging
    # that mask onto the coarser grid gives the fraction of the cell covered
    # by that class. Normalize by the resampled "was this source pixel inside
    # the ward mask at all" fraction so ward-edge cells (where some 10m source
    # pixels are legitimately nodata) don't get an artificially low fraction.
    print("\n[3/5] Resampling land cover (per-class fractions, 10m -> 100m)...")
    with rasterio.open(LANDCOVER_PATH) as src:
        lc_data = src.read(1)
        lc_transform, lc_crs = src.transform, src.crs

    src_valid = (lc_data != 0).astype("float32")
    valid_frac = np.zeros(shape, dtype="float32")
    reproject(
        source=src_valid, destination=valid_frac,
        src_transform=lc_transform, src_crs=lc_crs,
        dst_transform=transform, dst_crs=TARGET_CRS,
        resampling=Resampling.average, dst_nodata=0.0,
    )

    class_fracs = {}
    for code, name in LC_CLASSES.items():
        class_mask = (lc_data == code).astype("float32")
        out = np.zeros(shape, dtype="float32")
        reproject(
            source=class_mask, destination=out,
            src_transform=lc_transform, src_crs=lc_crs,
            dst_transform=transform, dst_crs=TARGET_CRS,
            resampling=Resampling.average, dst_nodata=0.0,
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            normalized = np.where(valid_frac > 0, out / valid_frac, 0.0)
        class_fracs[name] = normalized.astype("float32")

    veg_total = sum(class_fracs[LC_CLASSES[c]] for c in VEGETATION_CODES)
    water_total = sum(class_fracs[LC_CLASSES[c]] for c in WATER_CODES)

    band_order = list(LC_CLASSES.values()) + ["vegetation_total", "water_total"]
    bands = [class_fracs[n] for n in LC_CLASSES.values()] + [veg_total, water_total]
    lc_stack = np.stack(bands).astype("float32")
    lc_stack = np.where(valid_mask[None, :, :] == 1, lc_stack, np.nan)
    write_raster(
        OUT_DIR / "lagos_landcover_100m.tif", lc_stack, transform, TARGET_CRS,
        nodata=np.nan, descriptions=band_order,
    )
    print(f"  bands: {band_order}")

    # --- step 4: rainfall, bilinear regrid from ~5.5km ---
    # NOTE: this does not add real spatial detail. CHIRPS' native ~5.5km
    # blocks are the finest scale the data actually resolves; bilinear
    # interpolation here only aligns that inherently coarse value onto the
    # finer 100m grid so it can be combined with the other layers, it does
    # not manufacture new sub-block spatial information about the storm.
    # Reprojecting from the wide-area source cache (not the already
    # ward-bbox-clipped file) so the source always fully covers the target
    # extent -- no risk of edge cells landing outside the source raster.
    print("\n[4/5] Regridding CHIRPS rainfall (bilinear, ~5.5km -> 100m)...")
    rain_bands = []
    for month in MONTHS:
        gz_path = RAINFALL_SOURCE_CACHE / f"chirps-v2.0.{YEAR}.{month}.tif.gz"
        tmp_tif = RAINFALL_SOURCE_CACHE / f"_tmp_{YEAR}_{month}.tif"
        with gzip.open(gz_path, "rb") as f_in, open(tmp_tif, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        with rasterio.open(tmp_tif) as src:
            # CHIRPS fills unresolved cells (deep ocean, etc.) with -9999
            # but does not tag it as nodata in the file's own metadata --
            # without src_nodata here, bilinear blends real rainfall with
            # -9999 sentinels wherever one falls in the interpolation
            # kernel, producing nonsense (caught via the combined preview
            # plot: annual totals came out at -70,000mm near the coast).
            out = np.full(shape, np.nan, dtype="float32")
            reproject(
                source=rasterio.band(src, 1), destination=out,
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=transform, dst_crs=TARGET_CRS,
                resampling=Resampling.bilinear,
                src_nodata=-9999.0, dst_nodata=np.nan,
            )
        tmp_tif.unlink()
        rain_bands.append(out)
        print(f"  {YEAR}-{month} regridded")

    rain_stack = np.stack(rain_bands).astype("float32")
    rain_stack = np.where(valid_mask[None, :, :] == 1, rain_stack, np.nan)
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    write_raster(
        OUT_DIR / f"lagos_rainfall_100m_{YEAR}.tif", rain_stack, transform, TARGET_CRS,
        nodata=np.nan, descriptions=[f"{YEAR}-{m}" for m in month_labels],
    )

    # --- step 5: ward ID, vector rasterization (not resampling) ---
    print("\n[5/5] Rasterizing ward IDs (vector -> raster)...")
    wards_utm = wards_utm.reset_index(drop=True)
    wards_utm["ward_id"] = wards_utm.index + 1  # 0 reserved for nodata/no-ward
    shapes = list(zip(wards_utm.geometry, wards_utm["ward_id"]))
    ward_id_raster = rasterize(
        shapes, out_shape=shape, transform=transform, fill=0, dtype="int32"
    )
    unassigned_in_valid = int(((ward_id_raster == 0) & (valid_mask == 1)).sum())
    print(f"  {unassigned_in_valid:,} valid-mask cells got no ward ID "
          f"(polygon topology / cell-center sampling gaps -- expected to be small)")
    write_raster(OUT_DIR / "lagos_ward_id_100m.tif", ward_id_raster, transform, TARGET_CRS, nodata=0)

    lookup = wards_utm[["ward_id", "wardcode", "wardname", "lganame"]].copy()
    lookup.to_csv(OUT_DIR / "ward_id_lookup.csv", index=False)
    print(f"  wrote {OUT_DIR / 'ward_id_lookup.csv'} ({len(lookup)} wards)")

    print("\nDone.")


if __name__ == "__main__":
    main()
