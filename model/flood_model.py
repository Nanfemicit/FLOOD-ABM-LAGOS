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

Sink handling (no valid downhill neighbor under D8 steepest descent):
  - pit cells (real micro-topography, see derive_flow_direction.py):
    water routed here -- whether generated locally or arriving from an
    upstream cell -- has nowhere further to go and accumulates in place.
  - flat cells (~99% water/lagoon interior): treated as an absorbing
    boundary. Water routed here exits the tracked system; a running
    total is kept rather than silently discarding it.

Scope note: infiltration/drainage capacities below are the legacy
hourly rates (mm/hr) applied ONCE per step, not scaled to whatever
timespan the rainfall input represents. Fed an hourly storm that's a
reasonable single-step model. Fed a monthly CHIRPS total (as the smoke
test does), that capacity is trivial against the input and nearly
everything becomes "leftover" to route -- which is fine for smoke-testing
whether the routing plumbing sends water somewhere physically sane, but
this is not a calibrated storm simulation. That's Phase 3's job, using
an actual identified storm event, not a raw monthly total.

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

    def apply_rainfall(self, rainfall_mm):
        """
        One step: apply `rainfall_mm` (2D array, (height, width), mm) as a
        single pulse, run infiltration + drainage, then route whatever's
        left one hop downhill along the flow direction field, handling
        pits and flats as described in the module docstring.
        """
        valid = self.valid_mask
        flow_dir = self.grid.properties["flow_direction"].data.T

        water = np.where(valid, rainfall_mm, 0.0).astype("float64")
        infil = np.minimum(water, self.infil_mm)
        water -= infil
        drain = np.minimum(water, self.drain_mm)
        water -= drain
        leftover = water  # what's left after infiltration + drainage, to be routed

        pit_mask = (flow_dir == PIT) & valid
        flat_mask = (flow_dir == FLAT) & valid
        dir_mask = self._has_valid_dir & valid

        depth = np.zeros_like(leftover)

        # pits: local leftover has nowhere to go, accumulates in place
        depth[pit_mask] += leftover[pit_mask]

        # flats/water: local leftover exits the tracked system (absorbing boundary)
        self.exited_total_mm_cells += float(leftover[flat_mask].sum())

        # directed cells: route one hop to target, classify by what the target is
        rows, cols = np.where(dir_mask)
        trows = self._target_rows[rows, cols]
        tcols = self._target_cols[rows, cols]
        moving = leftover[rows, cols]

        left_domain = ~self._in_bounds[rows, cols] | ~valid[trows, tcols]
        target_dir = np.where(left_domain, 0, flow_dir[trows, tcols])
        target_is_pit = target_dir == PIT
        target_is_flat = target_dir == FLAT
        target_is_normal = (~left_domain) & (~target_is_pit) & (~target_is_flat)

        self.exited_total_mm_cells += float(moving[left_domain].sum())
        self.exited_total_mm_cells += float(moving[target_is_flat].sum())

        pit_idx = target_is_pit
        np.add.at(depth, (trows[pit_idx], tcols[pit_idx]), moving[pit_idx])
        normal_idx = target_is_normal
        np.add.at(depth, (trows[normal_idx], tcols[normal_idx]), moving[normal_idx])

        water_layer = self.grid.properties["water_depth"]
        water_layer.data = (water_layer.data.T + depth).T.copy()

        self.n_steps_run += 1
        return depth  # this step's contribution, (height, width), for smoke-test reporting

    @property
    def flooded_mask(self):
        return (self.water_depth >= self.flood_threshold_mm) & self.valid_mask
