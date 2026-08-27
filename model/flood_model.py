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

Known limitation, flagged rather than fixed here: infil_mm/drain_mm
below are still the legacy *hourly* rates (10mm, 5mm) applied once per
*daily* call -- a smaller version of the same unit mismatch that caused
the monthly-in-one-hop artifact, not a new one, but not resolved either.
CLAUDE.md's Phase 3 note reframes storm intensity as mm/day under daily
stepping, which suggests capacity should probably become an explicit
mm/day figure too -- that's a parameter-value decision for Phase 3, not
the mechanical change this pass was asked to make.

Land-cover-driven (spatially-varying) infiltration is documented as an
intended future step in docs/methodology-notes.md but is NOT implemented
here -- this pass ports the legacy single-global-rate behavior faithfully
rather than quietly upgrading it.
"""
import mesa
import numpy as np

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
        infil_mm / drain_mm: legacy hourly rates (mm/hr), applied once
        per step -- see module docstring scope note.
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
        One day's step: apply `rainfall_mm` (2D array, (height, width), mm,
        a single day's CHIRPS total) as a pulse, run infiltration + drainage
        once, then route whatever's left downhill along the flow direction
        field -- repeatedly, until every unit of mobile water has either
        settled at a pit or exited via a flat/off-domain boundary (or the
        defensive hop cap is hit; see module docstring for why that's not
        expected to bind).

        Returns (depth_added, n_hops): this day's contribution to standing
        depth, and how many routing hops it took to converge -- surfaced
        for smoke-test / validation reporting, not needed for the state
        update itself (which already happened, on self.water_depth).
        """
        valid = self.valid_mask
        flow_dir = self.grid.properties["flow_direction"].data.T

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
                moving = cur[is_dir]
                trows = self._target_rows[dr, dc]
                tcols = self._target_cols[dr, dc]
                left_domain = ~self._in_bounds[dr, dc] | ~valid[trows, tcols]

                if left_domain.any():
                    self.exited_total_mm_cells += float(moving[left_domain].sum())
                keep = ~left_domain
                mobile[dr, dc] = 0.0  # all directed water leaves its source cell this hop

                if keep.any():
                    np.add.at(mobile, (trows[keep], tcols[keep]), moving[keep])

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
