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

*Not yet pulled.*

## Rainfall (CHIRPS)

*Not yet pulled.*

## Land cover (ESA WorldCover)

*Not yet pulled.*
