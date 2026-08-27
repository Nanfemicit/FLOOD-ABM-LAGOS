# Data provenance log

Running log of every data source pulled into this project: where it came from,
what it actually is (as opposed to what it claims to be), any quality issues
found, and what modeling decision was made because of them. One entry per
source, added as each is pulled. See `CLAUDE.md` for why this matters here
specifically — administrative boundary choices are political, not neutral,
and this project has already hit that once (see entry below).

---

## Ward boundaries

**File:** `data/raw/boundaries/grid3_lagos_wards.geojson`

**Source:** GRID3 NGA "Operational Wards v1.0", pulled via the ArcGIS Hub
FeatureServer (`services3.arcgis.com/BU6Aadhn6tbBEdyk/.../NGA_Ward_Boundaries`),
filtered to `statename='Lagos'`. The original path — openAFRICA/CKAN, as
named in `CLAUDE.md` — is blocked by a Cloudflare bot challenge from this
environment, so the pull went through the underlying ArcGIS service instead.
Attributed in GRID3's own metadata to eHealth Africa and Proxy Logics, 2020.
GRID3 categorizes this layer itself as `Boundaries/Administrative
(non-authoritative)/Adm 3`.

**What it actually is:** 377 features for Lagos State. This is not 377
INEC electoral wards. It's 245 INEC electoral wards + 132 Lagos State
LCDA-level wards (LCDAs = Local Council Development Areas, a Lagos-specific
administrative layer the state carved out of the original 20 LGAs),
merged into a single boundary set. Nowhere in GRID3's own metadata —
not the ArcGIS item description, not the full ISO19139 metadata.xml — is
this composition documented. It was reconstructed by cross-referencing
Ikeja LGA's 18 ward names in the file against INEC's official 10-ward list
for Ikeja; the extra 8 names (Akiode, Olusosun, Onilekere, Seriki-Aro,
Wasimi, etc.) don't appear in any INEC list, which is what surfaced the gap.

**Known quality issue:** every one of the 377 rows carries `source: INEC`
in its attributes, regardless of whether that row is actually an INEC
electoral ward or a Lagos State LCDA ward. The `source` field cannot be
used to separate the two layers within the file — it's uniformly wrong
for the LCDA-derived rows.

**Modeling decision:** using the full 377-unit layer rather than filtering
down to the 245-ward INEC-only subset. Reasoning: LCDAs are Lagos's own
local service-delivery governance layer (closer to where drainage
maintenance and flood response actually get administered), which is more
directly relevant to this project's governance argument than an electoral
boundary would be. This is itself an administrative-level choice with
downstream analytical consequences — see `CLAUDE.md` §9.

---

## Elevation (DEM)

**File:** `data/raw/elevation/lagos_dem_glo30.tif`

**Source:** Copernicus DEM GLO-30, pulled directly from the public AWS Open
Data bucket (`s3://copernicus-dem-30m`, `eu-central-1`, no auth/signing
required). Three 1x1 degree tiles (N06/E002, N06/E003, N06/E004) covering
the ward boundary extent plus a 0.05 degree buffer, merged and clipped via
`scripts/fetch_dem.py`. ~30m (1 arc-second) resolution, CRS EPSG:4326,
elevation range across the clipped extent is roughly -0.03m to 110m.
LZW-compressed on write to keep the committed file to ~27MB; the raw
full-degree source tiles are cached locally under
`data/raw/elevation/_tile_cache/` and gitignored (regenerable by re-running
the script, not meant to be committed).

**Known quality issue:** GLO-30 is a **DSM** (digital surface model), not
a bare-earth DTM — it includes building and canopy heights, not just
ground elevation. Visually this shows up as a mottled, high-frequency
texture over the dense urban core (Lagos Island / Ikeja), where building
rooftops read as small terrain bumps rather than actual ground relief.
This is exactly the area most relevant to the project's human-cost
argument, so flow-direction/drainage derivation in Phase 2 should account
for this rather than trusting raw DSM values as ground truth in built-up
wards — worth a running note, possibly a smoothing pass or a bare-earth
alternative (Copernicus does not publish one; SRTM is also a DSM) if
routing artifacts show up later.

**Modeling decision:** proceeding with GLO-30 as pulled for now (matches
`CLAUDE.md`'s stated preference for SRTM/Copernicus over the HDX 90m
fallback), revisiting only if the DSM-vs-DTM issue visibly distorts flow
routing once Phase 2 derives flow direction from this raster.

**Note:** the raster on disk is clipped to the ward extent *plus* a 0.05
degree buffer (see above) — that buffer deliberately extends past the
ward boundaries for flow-routing margin. Any statistic reported as a
property of Lagos itself (highest point, elevation range, mean elevation,
etc.) must be computed against the raster masked to the ward union, not
the full clipped-and-buffered raster, or the buffer zone will silently
contaminate the number. Confirmed this the hard way: the raw max over the
full buffered raster is 109.88m, ~23km outside any ward and almost
certainly terrain from north of the study area; the actual max inside the
ward union is 75.51m, in Alakuko Ajegunle ward (Ifako/Ijaye LGA).

## Rainfall (CHIRPS)

**Files:** `data/raw/rainfall/lagos_chirps_2025_{01..12}.tif` — one GeoTIFF
per month.

**Source:** CHIRPS-2.0 `africa_monthly` product, pulled directly from the
UCSB Climate Hazards Center's public data server
(`data.chc.ucsb.edu/products/CHIRPS-2.0/africa_monthly/tifs/`), no auth
wall — plain `.tif.gz` files, no Earth Engine account needed. 0.05°
resolution, CRS EPSG:4326. Downloaded via `scripts/fetch_rainfall.py`,
which decompresses the cached source files and clips each to the ward
boundary extent (bounding box, not ward-masked — see note below).
Year: **2025**, the most recently complete year at the time of pulling
(2026 data on the server only runs through July). Raw monthly `.tif.gz`
source files are cached locally under `data/raw/rainfall/_source_cache/`
and gitignored (regenerable, not committed).

**Known quality issue (download, not data):** the CHC server is slow
enough that a plain `curl --max-time 60` loop silently truncated 8 of
12 monthly files without a nonzero exit code — the corruption only
surfaced when `gzip -t` was run against each file afterward. Re-pulled
with per-file integrity verification and retry-on-failure. Worth keeping
in mind for the rainfall and land cover pulls to come: verify archive
integrity explicitly, don't trust a "download completed" byte count
alone.

**Validated:** CRS and 0.05° resolution confirmed. Monthly totals were
computed **masked to the ward union**, not the raw bbox-clipped raster —
same buffer-vs-mask lesson as the DEM entry above; the rainfall clip has
no flow-routing buffer, but the bbox clip still includes non-ward area
around Lagos's irregular boundary, so ward-masking was applied before
summarizing. The seasonal shape matches Lagos's known bimodal pattern: a
sharp peak in June (357mm), a drop through July-August (90-92mm — the
"August break," arguably starting a month earlier than the canonical
August-only framing but consistent with known year-to-year variability
in when the break lands), a secondary September/October peak (225mm /
216mm), then taper into the Nov-Jan dry season (156mm down to 12mm). See
`data/raw/rainfall/lagos_rainfall_2025_seasonal.png`.

**Modeling decision:** using 2025 as the reference year for now. This is
one year of CHIRPS estimates, not a multi-year climatology — Phase 3's
calibration against historical events should pull additional years
rather than treating 2025 as representative on its own.

**Note:** this 2025 pull validates that the data pipeline works and
produces a seasonal shape consistent with real Lagos rainfall — that's
all it validates. It is not automatically the calibration dataset for
Phase 3. Which year(s) to calibrate storm intensity/duration against is
an explicit decision to be made once specific historical Lagos flood
events are identified, not assumed by default to be whichever year
happened to be most recently complete when this file was pulled.

## Land cover (ESA WorldCover)

## Land cover (ESA WorldCover)

**File:** `data/raw/landcover/lagos_landcover.tif`

**Source:** ESA WorldCover v200 (2021), the improved-algorithm release
over the original 2020 v100 version. Pulled directly from the public AWS
Open Data bucket (`s3://esa-worldcover`, no auth/signing required), tile
path found by browsing the bucket structure the same way as the DEM
(`v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif`, 3x3 degree
tiles). Two tiles cover the Lagos extent: N06E000 and N06E003. 10m
nominal resolution (defined as exactly 1/12000 degree, ~8.33e-5 deg —
true ground resolution varies slightly with latitude, this is not a
bug), CRS EPSG:4326. Downloaded and merged via `scripts/fetch_landcover.py`,
raw source tiles (~272MB) cached locally under
`data/raw/landcover/_source_cache/` and gitignored.

**Masked to the ward union from the start**, not a bounding box — this
was the explicit ask after the DEM buffer-vs-mask issue, so
`fetch_landcover.py` clips directly against `wards.union_all()` rather
than `total_bounds`. Output is ~3.7MB (LZW-compressed; categorical data
compresses well).

**Download reliability:** applied the rainfall-pull lesson from the
start too — every tile is opened and read with rasterio (not just
checked for a nonzero exit code or plausible file size) before being
trusted, with retry on both a failed read and a failed connection. Caught
one transient DNS resolution failure on the second tile mid-download;
the retry loop handled it on the next attempt.

**Class breakdown** (11 WorldCover classes, 9 present in the Lagos
extent; % of valid/non-nodata pixels):

| Code | Class | % |
|---|---|---|
| 10 | Tree cover | 37.37% |
| 50 | Built-up | 25.97% |
| 80 | Permanent water bodies | 15.36% |
| 30 | Grassland | 13.46% |
| 90 | Herbaceous wetland | 3.66% |
| 95 | Mangroves | 2.35% |
| 60 | Bare / sparse vegetation | 1.26% |
| 40 | Cropland | 0.40% |
| 20 | Shrubland | 0.18% |

**Validated:** CRS and resolution confirmed. Plotted at
`data/raw/landcover/lagos_landcover_preview.png` — the classified map is
visually unmistakable as Lagos: permanent water fills the lagoon exactly
where expected, and built-up (red) forms a dense, contiguous blob over
the Lagos Island / Ikeja / Lagos Mainland core, consistent with the DSM
building-height noise already seen in that same area in the DEM entry
above. Ran a direct spatial check rather than eyeballing it:

| LGA group | Built-up | Vegetation | Water/wetland |
|---|---|---|---|
| Dense urban core (Lagos Island, Ikeja, Lagos Mainland) | 83.34% | 12.83% | 3.65% |
| Outer LGAs (Epe, Ibeju Lekki) | 4.29% | 67.24% | 26.96% |

Matches expectation cleanly — no flags.

**Modeling decision:** using the 2021 v200 release as-is. This is a
single-year land cover snapshot; land cover changes slower than rainfall
year-to-year, so unlike the CHIRPS entry there's no immediate "which
year" calibration question, but it's still a 2021 snapshot being paired
with 2025 rainfall and a boundary layer of unclear vintage — worth a
running note if temporal mismatch between layers ever becomes a modeling
concern.
