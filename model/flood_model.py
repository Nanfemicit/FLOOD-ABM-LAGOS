# model/flood_model.py
"""
FloodModel: rainfall -> infiltration -> drainage -> flow-routed overflow,
wired onto the real 100m Lagos grid built in Phase 2.

Rule logic (rainfall/infiltration/drainage) is ported from
legacy/model.py's FloodModel, which ran the same three-step pipeline on
a synthetic 30x30 grid with no real overflow mechanic -- CLAUDE.md always
described "overflow to neighboring cells" as part of the argument, but
the actual legacy code never routed water anywhere; it just subtracted
infiltration and drainage and left whatever remained sitting in the
originating cell. That gap is what this file closes, using the D8 flow
direction derived in scripts/derive_flow_direction.py.

Grid state (elevation, flow direction, standing water depth) lives on
mesa.space.PropertyLayer, not per-cell Agent objects -- per
scripts/perf_smoke_test.py, PropertyLayer is ~48x faster per step than
one Agent per active cell at this cell count (355,146 cells).

Sink handling (no valid downhill neighbor under D8 steepest descent),
unchanged from the first version of this file:
  - pit cells (real micro-topography, see derive_flow_direction.py):
    water routed here -- whether generated locally or arriving from an
    upstream cell -- has nowhere further to go and accumulates in place.
  - flat cells (~99% water/lagoon interior): treated as an absorbing
    boundary. Water routed here exits the tracked system; a running
    total is kept rather than silently discarding it.

Time step: one call = one day of real CHIRPS daily rainfall (see
CLAUDE.md Phase 3 -- daily was chosen over hourly IMERG and over
disaggregated/invented hourly CHIRPS). Routing is decoupled from the
rainfall input entirely: after a day's rainfall is added, the leftover
(post-infiltration/drainage) water is routed downhill for as many hops
as it takes to settle -- not one hop per call, which was the bug in the
first version (see the 38.5%-flooded artifact from the whole-month
smoke test in the previous commit). Water needs the chance to actually
reach downstream LGAs within a simulated day.

Routing-hop policy: run to full convergence (every mobile unit of water
has either settled at a pit or exited via a flat/off-domain boundary),
backstopped by a generous hop cap purely as defensive programming. This
is provably safe to do: the flow-direction validation already confirmed
100% of directed cells flow to strictly lower elevation, which means no
cell can ever receive water back from a path it sent water down -- the
network has no cycles, so convergence is guaranteed in a finite number
of hops (bounded by the longest strictly-descending elevation chain in
the grid), not an open-ended loop. A hop cap alone (without running to
convergence) was the other option on the table; convergence was chosen
because an arbitrary cap would leave water "parked" mid-network for a
reason that has nothing to do with the physics, just wherever the cap
happened to land -- since convergence is provably going to terminate
anyway, capping it early would only be trading a real answer for a
cheaper wrong one, with no actual runtime problem to justify the trade.

Per-hop outflow cap (conveyance capacity): a directed (non-pit) cell can
pass along at most drain_mm of its currently-mobile water per hop --
whatever's above that stays at the cell as standing depth, immediately,
the same way a pit's local leftover already does. Reuses drain_mm rather
than inventing a second capacity parameter: this deliberately treats a
cell's local absorption rate (how fast water leaves through the ground
or a drain) and the network's conveyance capacity (how much a channel
can carry onward) as the same underlying quantity. That's a real
simplification -- in reality a cell could drain locally at one rate but
sit on a channel with a completely different carrying capacity -- kept
because splitting them needs a second number this project has no more
grounding for than the first, not because they're actually the same
thing physically.

Mechanical consequence worth flagging: because capped excess resolves to
depth immediately rather than re-queuing for another attempt, this
doesn't blow up convergence time the way a "retry every hop" version
would have -- if it had instead let excess remain mobile and re-attempt
the cap on subsequent hops, hop count would scale with backlog_volume /
drain_mm at every capacity-constrained cell, which is unbounded by
rainfall magnitude and could plausibly exceed MAX_ROUTING_HOPS under
low-drain_mm/high-rainfall combinations -- a real risk with the "retry"
design that the "resolve immediately" design avoids by construction.
Checked empirically at drain_mm=1.5 (the more constrained of the two
tested scenarios): hop counts stayed in the same range as the uncapped
version, no sign of the slow-convergence failure mode.

Standing-depth drainage (recession): at the start of each apply_rainfall()
call, before that day's rainfall is used at all, water_depth is reduced
by up to drain_mm per cell, floored at zero, applied uniformly including
at pits, with the drained amount added to exited_total_mm_cells. This is
what fixes the previously-flagged "never recedes" gap -- water_depth can
now go down between calls, not just up. Sequencing is deliberate: this
happens BEFORE today's rainfall/routing/cap pipeline, not after and not
merged with it, specifically so drain_mm isn't spent twice on the same
day's fresh water (once here against old depth, again via the per-hop
cap against new depth). Pits get no special-casing: this is a local,
target-independent loss (soak-away, minor unmodeled conveyance, eventual
pumping), not routing, so "no downhill neighbor" is irrelevant to it --
consistent with how drain_mm was already used untargeted against fresh
rainfall before the per-hop cap existed.

Known limitation, not resolved here: infil_mm/drain_mm are still applied
once per *daily* call as flat hourly-rate-shaped numbers, not scaled to
a day. Less severe now that infiltration is land-cover-grounded rather
than an arbitrary legacy guess, but the underlying unit question CLAUDE.md's
Phase 3 note raises (storm intensity is now mm/day; should capacity be an
explicit mm/day figure too?) is still open.

Infiltration is now spatially varying, grounded in the SCS/NRCS curve
number method rather than a single legacy constant (see
INFILTRATION_RATES_MM_HR / compute_infiltration_rate below, and the
"Infiltration parameterization" entry in data/README.md for the full
sourcing). infil_mm can be passed as either a scalar (legacy behavior,
kept for testing/back-compat) or a (height, width) array from
compute_infiltration_rate -- numpy's elementwise ops in apply_rainfall()
don't care which. drain_mm stays a scalar, explicit scenario parameter
(well-maintained vs. poorly-maintained infrastructure) rather than a
single "true" value -- see data/README.md for the documented range.

Order-of-operations note: infiltration and drainage are subtracted from
every valid cell BEFORE the routing loop classifies pit/flat/directed
cells, which means infiltration also runs on water/wetland/mangrove
cells (flow_dir == FLAT) before their absorbing-boundary treatment takes
over. With infil_mm now 0 on those classes by construction, this is a
correction, not just a no-op: previously (flat legacy rate) those cells
were silently losing up to 10mm/hr to infiltration that shouldn't apply
to open water, which slightly under-reported exited_total_mm_cells.
drain_mm still applies uniformly to those cells, unchanged -- same
category of mismatch, out of scope here since drain_mm was asked to stay
untouched.
"""
import mesa
import numpy as np

# Infiltration rates (mm/hr) per WorldCover class, grounded in the SCS/NRCS
# curve number method rather than an invented single global constant.
# Sources (both read directly, not from a secondary summary):
#   - built_up: impervious, TR-55 (NRCS, 1986) Table 2-2a note 2 -- paved
#     surfaces get CN=98 regardless of underlying soil group, i.e. ~0mm/hr.
#   - water_permanent / wetland_herbaceous / mangroves: NEH Part 630 Ch.7
#     (NRCS, Jan 2009) 630.0701 -- soils with a water table within 60cm of
#     the surface are classified Group D regardless of texture, since
#     already-saturated ground (or open water itself) can't absorb more.
#     ~0mm/hr.
#   - tree_cover: Hydrologic Soil Group A, reflecting Lagos's natural
#     Coastal Plain Sands substrate on well-structured, canopy-protected
#     ground. Rate is the NEH Table 7-1 boundary for the "deep soil, water
#     table >100cm" row: Ksat >10.0 micrometers/s (>1.42 in/hr) -> 36.1mm/hr.
#   - grassland / shrubland / bare_sparse_veg / cropland: Group B, same
#     substrate but less structural protection than tree cover (or, for
#     cropland, tillage compaction). NEH Table 7-1 B range lower bound:
#     >4.0 to <=10.0 micrometers/s (>0.57 to <=1.42 in/hr) -> 14.5mm/hr.
# Convention: every value is the LOWER bound of its HSG's documented range
# where the range is open-ended or wide -- a conservative (flood-risk-
# leaning) choice, applied consistently, not picked per-class to hit a
# target. cropland gets the same rate as grassland/shrubland rather than
# a separate Group C treatment; it's under 0.5% of the Lagos grid, so the
# distinction has negligible effect either way.
INFILTRATION_RATES_MM_HR = {
    "built_up": 0.0,
    "water_permanent": 0.0,
    "wetland_herbaceous": 0.0,
    "mangroves": 0.0,
    "tree_cover": 36.1,
    "shrubland": 14.5,
    "grassland": 14.5,
    "bare_sparse_veg": 14.5,
    "cropland": 14.5,
}


def compute_infiltration_rate(lc_stack, lc_bands):
    """
    Blend per-cell infiltration capacity (mm/hr) from fractional land cover
    composition, using INFILTRATION_RATES_MM_HR -- not a single dominant
    class per cell, the actual fractional mix already computed in
    data/processed/lagos_landcover_100m.tif.

    lc_stack: (n_bands, height, width) array, e.g. from reading that file.
    lc_bands: list of band names in the same order as lc_stack's first axis.
    Returns: (height, width) array, NaN-safe (cells outside the land cover
    mask are returned as 0.0, since they're excluded via valid_mask
    elsewhere in FloodModel regardless).
    """
    height, width = lc_stack.shape[1:]
    rate = np.zeros((height, width), dtype="float64")
    for cls, r in INFILTRATION_RATES_MM_HR.items():
        idx = lc_bands.index(cls)
        frac = np.nan_to_num(lc_stack[idx], nan=0.0)
        rate += frac * r
    return rate


# D8 code -> (d_row, d_col) neighbor offset. Must match the convention
# used in scripts/derive_flow_direction.py (grid is north-up: +row = south).
DIRMAP_OFFSETS = {
    1: (0, 1),      # E
    2: (1, 1),      # SE
    4: (1, 0),      # S
    8: (1, -1),     # SW
    16: (0, -1),    # W
    32: (-1, -1),   # NW
    64: (-1, 0),    # N
    128: (-1, 1),   # NE
}
PIT = -2
FLAT = -1
DIR_CODES = list(DIRMAP_OFFSETS.keys())


class FloodModel(mesa.Model):
    def __init__(self, elevation, flow_dir, valid_mask,
                 infil_mm=10.0, drain_mm=5.0, flood_threshold_mm=50.0, seed=None):
        """
        elevation, flow_dir, valid_mask: 2D arrays, shape (height, width),
        pixel-aligned (as produced by scripts/build_grid.py and
        scripts/derive_flow_direction.py).
        infil_mm: mm/hr, applied once per daily step. Pass a (height, width)
        array from compute_infiltration_rate() for the SCS/NRCS-grounded,
        spatially-varying rate (the intended normal usage); the scalar
        default (10.0, the old legacy flat guess) is kept only for quick
        tests that don't need land cover loaded.
        drain_mm: mm/hr, applied once per daily step. Deliberately kept a
        single scalar scenario parameter, not derived from any dataset --
        see data/README.md for the documented well-maintained/
        poorly-maintained range this is meant to be set within.
        flood_threshold_mm: legacy's flooded-cell threshold (50mm).
        """
        super().__init__(seed=seed)
        assert elevation.shape == flow_dir.shape == valid_mask.shape
        height, width = elevation.shape
        self.height, self.width = height, width
        self.valid_mask = valid_mask.astype(bool)
        self.infil_mm = infil_mm
        self.drain_mm = drain_mm
        self.flood_threshold_mm = flood_threshold_mm

        elev_layer = mesa.space.PropertyLayer("elevation", width, height, default_value=0.0, dtype=np.float32)
        elev_layer.data = elevation.T.copy()
        flowdir_layer = mesa.space.PropertyLayer("flow_direction", width, height, default_value=0, dtype=np.int16)
        flowdir_layer.data = flow_dir.T.copy()
        water_layer = mesa.space.PropertyLayer("water_depth", width, height, default_value=0.0, dtype=np.float64)
        self.grid = mesa.space.SingleGrid(
            width, height, torus=False,
            property_layers=[elev_layer, flowdir_layer, water_layer],
        )

        # Sanity-check the PropertyLayer's (width, height) storage against
        # our (height, width) arrays round-trips correctly -- this is
        # exactly the kind of silent transpose bug worth catching before
        # trusting any downstream result.
        assert np.array_equal(self.grid.properties["elevation"].data.T, elevation), \
            "PropertyLayer transpose round-trip failed for elevation"
        assert np.array_equal(self.grid.properties["flow_direction"].data.T, flow_dir), \
            "PropertyLayer transpose round-trip failed for flow_direction"

        self.exited_total_mm_cells = 0.0  # running total of leftover water (mm, summed per cell) that left the tracked domain
        self.n_steps_run = 0

        self._precompute_routing_targets(flow_dir)

    def _precompute_routing_targets(self, flow_dir):
        h, w = flow_dir.shape
        rows, cols = np.indices((h, w))
        target_rows = rows.copy()
        target_cols = cols.copy()
        for code, (dr, dc) in DIRMAP_OFFSETS.items():
            m = flow_dir == code
            target_rows[m] = rows[m] + dr
            target_cols[m] = cols[m] + dc
        in_bounds = (target_rows >= 0) & (target_rows < h) & (target_cols >= 0) & (target_cols < w)
        # clip out-of-bounds targets back to self; out_of_bounds cells are
        # routed to "exits the domain" explicitly below, this clip only
        # prevents an invalid array index
        self._target_rows = np.where(in_bounds, target_rows, rows)
        self._target_cols = np.where(in_bounds, target_cols, cols)
        self._in_bounds = in_bounds
        self._has_valid_dir = np.isin(flow_dir, DIR_CODES)

    @property
    def water_depth(self):
        """Current standing/accumulated water depth per cell (row, col) mm."""
        return self.grid.properties["water_depth"].data.T

    MOBILE_EPS = 1e-6  # water below this (mm) per cell is treated as settled, not worth another hop
    MAX_ROUTING_HOPS = 2000  # defensive cap only -- see module docstring, convergence is expected well before this

    def apply_rainfall(self, rainfall_mm):
        """
        One day's step: first drain existing standing depth by up to
        drain_mm (see module docstring, "Standing-depth drainage"), then
        apply `rainfall_mm` (2D array, (height, width), mm, a single day's
        CHIRPS total) as a pulse, run infiltration + drainage
        once, then route whatever's left downhill along the flow direction
        field -- repeatedly, until every unit of mobile water has either
        settled at a pit or exited via a flat/off-domain boundary (or the
        defensive hop cap is hit; see module docstring for why that's not
        expected to bind).

        Returns (depth_added, n_hops): this day's newly-settled contribution
        to standing depth (pits + capacity-capped excess from today's
        rainfall only -- does NOT include the standing-depth drainage
        applied at the top of this call, which reduces self.water_depth
        directly before depth_added is even computed), and how many
        routing hops it took to converge. Surfaced for smoke-test /
        validation reporting, not needed for the state update itself
        (which already happened, on self.water_depth).
        """
        valid = self.valid_mask
        flow_dir = self.grid.properties["flow_direction"].data.T

        # Standing-depth drainage: reduce whatever's already accumulated
        # from previous calls by up to drain_mm, floored at zero, BEFORE
        # today's rainfall enters the picture at all. Applied uniformly to
        # every valid cell, pits included -- see module docstring for why
        # pits get no special-casing here (this is a local, target-independent
        # loss, not routing) and why "before" rather than "after" today's
        # pipeline (avoids applying drain_mm twice to the same day's fresh
        # water, which already goes through the per-hop outflow cap below).
        water_layer = self.grid.properties["water_depth"]
        standing = water_layer.data.T
        drained_standing = np.minimum(standing, self.drain_mm)
        water_layer.data = (standing - drained_standing).T.copy()
        self.exited_total_mm_cells += float(drained_standing[valid].sum())

        water = np.where(valid, rainfall_mm, 0.0).astype("float64")
        infil = np.minimum(water, self.infil_mm)
        water -= infil
        drain = np.minimum(water, self.drain_mm)
        water -= drain
        mobile = water  # what's left after infiltration + drainage, still in transit

        depth_added = np.zeros_like(mobile)
        n_hops = 0

        while n_hops < self.MAX_ROUTING_HOPS:
            rows, cols = np.where(mobile > self.MOBILE_EPS)
            if rows.size == 0:
                break

            cur = mobile[rows, cols]
            cur_dir = flow_dir[rows, cols]
            is_pit = cur_dir == PIT
            is_flat = cur_dir == FLAT
            is_dir = np.isin(cur_dir, DIR_CODES)

            if is_pit.any():
                np.add.at(depth_added, (rows[is_pit], cols[is_pit]), cur[is_pit])
            if is_flat.any():
                self.exited_total_mm_cells += float(cur[is_flat].sum())
            # both pit and flat cells are now settled/exited; clear them from mobile
            mobile[rows[is_pit], cols[is_pit]] = 0.0
            mobile[rows[is_flat], cols[is_flat]] = 0.0

            if is_dir.any():
                dr, dc = rows[is_dir], cols[is_dir]
                total = cur[is_dir]
                # per-hop outflow cap: at most drain_mm can leave a directed
                # cell this hop, regardless of pit status -- see module
                # docstring for why this reuses drain_mm rather than a new
                # parameter, and what it assumes.
                outflow = np.minimum(total, self.drain_mm)
                excess = total - outflow
                if np.any(excess > 0):
                    np.add.at(depth_added, (dr, dc), excess)

                trows = self._target_rows[dr, dc]
                tcols = self._target_cols[dr, dc]
                left_domain = ~self._in_bounds[dr, dc] | ~valid[trows, tcols]

                if left_domain.any():
                    self.exited_total_mm_cells += float(outflow[left_domain].sum())
                keep = ~left_domain
                mobile[dr, dc] = 0.0  # cell fully processed this hop: outflow moves on, excess became depth

                if keep.any():
                    np.add.at(mobile, (trows[keep], tcols[keep]), outflow[keep])

            n_hops += 1

        if n_hops >= self.MAX_ROUTING_HOPS and mobile.sum() > self.MOBILE_EPS:
            # Should not happen given the no-cycles guarantee (see module
            # docstring) -- surfacing loudly rather than silently truncating
            # if it ever does, since that would mean the guarantee broke.
            raise RuntimeError(
                f"Routing hit MAX_ROUTING_HOPS ({self.MAX_ROUTING_HOPS}) without converging; "
                f"{mobile.sum():.1f}mm still mobile. This should be impossible under strict "
                f"downhill D8 routing -- investigate before trusting this result."
            )

        water_layer = self.grid.properties["water_depth"]
        water_layer.data = (water_layer.data.T + depth_added).T.copy()

        self.n_steps_run += 1
        return depth_added, n_hops

    @property
    def flooded_mask(self):
        return (self.water_depth >= self.flood_threshold_mm) & self.valid_mask
