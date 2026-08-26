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

## Rainfall (CHIRPS)

*Not yet pulled.*

## Land cover (ESA WorldCover)

*Not yet pulled.*
