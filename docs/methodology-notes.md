# Reconciling resolution: why the data layers don't match, and why that's fine

FLOOD-ABM-LAGOS combines three raster sources at three very different 
native resolutions: elevation at 30m, land cover at 10m, and rainfall at 
roughly 5.5km. That gap isn't a flaw to engineer away. It reflects a real 
limit in what each measurement method can detect.

Elevation and land cover come from satellite imagery fine enough to 
resolve the ground at 10-30m. Rainfall is different in kind. CHIRPS 
doesn't measure rain directly everywhere, it infers it by blending 
satellite images of cloud-top temperature with sparse ground gauge 
readings, and that blending process only produces a trustworthy estimate 
at roughly 5.5km blocks. Going finer would mean claiming a precision the 
method doesn't actually have.

Rasterizing everything onto one common 100m model grid means two 
different things happen depending on the direction. For elevation and 
land cover, going from finer to coarser is real information 
consolidation, values are genuinely being averaged or summarized. For 
land cover specifically, keeping the fractional class composition per 
cell (say, 40% built-up, 55% vegetation, 5% water) preserves more of the 
original detail than forcing a single dominant label, and that fractional 
mix feeds the model's infiltration parameter directly.

For rainfall, going from coarser to finer is the opposite operation. 
Regridding a 5.5km rainfall estimate onto 100m cells does not create new 
spatial information about where the rain fell more or less heavily. It 
spreads one real, coarse value smoothly across many small cells that all 
effectively share it. Within a given storm event, every 100m cell inside 
the same rainfall block gets essentially the same input.

That turns out to help the argument rather than weaken it. If two 
neighboring cells in the model produce different flood outcomes, and the 
rainfall hitting both of them is functionally identical because they sit 
inside the same coarse rainfall cell, the difference has to come from 
what's underneath them: drainage, infiltration, elevation. The resolution 
mismatch structurally forces local variation in flood severity to be 
explained by governance and infrastructure, not by the storm itself. 
Real rainfall variation does survive at city-wide scale, comparing 
somewhere like Epe against Ikeja, since that spans multiple distinct 
rainfall cells and reflects an actual climate difference across the 
metro area.

## A related mismatch: the land cover and rainfall years

The land cover layer is a 2021 snapshot (ESA WorldCover, still the most 
recent global release available). The rainfall data is pulled for 2025, 
the most recently complete year in CHIRPS. So the model, once assembled, 
will be running a 2025 storm against Lagos's built environment as it 
looked four years earlier, not against the city exactly as it stands 
today. If meaningful new construction happened in that gap, and Lagos 
does urbanize fast, the infiltration layer understates how much 
impervious surface actually exists now.

This doesn't need solving immediately. It becomes a real decision at the 
calibration stage, when a specific historical storm event is chosen to 
test the model against, since at that point the rainfall year and the 
land cover year are implicitly being treated as describing the same 
Lagos. The honest options are accepting the gap as a documented 
limitation, common practice in real hydrology work, or letting proximity 
to 2021 be one factor in choosing which flood event to calibrate 
against, alongside how well-documented and severe it was.
