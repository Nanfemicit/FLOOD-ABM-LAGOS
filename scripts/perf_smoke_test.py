# scripts/perf_smoke_test.py
"""
Performance smoke test, not real model rules: build a minimal Mesa model
over the full 355,146-cell Lagos grid two ways --

  (A) PropertyLayer-backed: elevation and flow direction live as numpy
      arrays on mesa.space.PropertyLayer, the trivial "step" is one
      vectorized numpy operation over the valid cells.
  (B) Naive per-cell Agent: one mesa.Agent per active cell, each holding
      its own elevation/flow_direction attributes, the trivial "step" is
      the same operation but dispatched once per agent.

Both do the identical trivial operation (increment elevation by a
constant) so the comparison isolates dispatch/object overhead, not
workload differences. This only measures whether PropertyLayer is worth
building rules on top of before any real rules exist -- not a benchmark
of the eventual model.
"""
import time

import mesa
import numpy as np
import rasterio

ELEVATION_PATH = "data/processed/lagos_elevation_100m.tif"
FLOWDIR_PATH = "data/processed/lagos_flow_direction_100m.tif"
GRID_TEMPLATE_PATH = "data/processed/grid_template.tif"
N_STEPS = 5


def load_data():
    with rasterio.open(GRID_TEMPLATE_PATH) as src:
        valid = src.read(1).astype(bool)
    with rasterio.open(ELEVATION_PATH) as src:
        elev = src.read(1)
    with rasterio.open(FLOWDIR_PATH) as src:
        flowdir = src.read(1)
    elev = np.nan_to_num(elev, nan=0.0).astype("float32")
    return valid, elev, flowdir


# ---------------- (A) PropertyLayer ----------------
class PropertyLayerModel(mesa.Model):
    def __init__(self, valid, elev, flowdir):
        super().__init__()
        height, width = valid.shape
        self.valid = valid
        elev_layer = mesa.space.PropertyLayer("elevation", width, height, default_value=0.0, dtype=np.float32)
        elev_layer.data = elev.T.copy()
        flowdir_layer = mesa.space.PropertyLayer("flow_direction", width, height, default_value=0, dtype=np.int16)
        flowdir_layer.data = flowdir.T.copy()
        self.grid = mesa.space.SingleGrid(width, height, torus=False, property_layers=[elev_layer, flowdir_layer])

    def step(self):
        layer = self.grid.properties["elevation"]
        layer.data[self.valid.T] += 0.001


# ---------------- (B) Naive per-cell Agent ----------------
class CellAgent(mesa.Agent):
    def __init__(self, model, elevation, flow_dir):
        super().__init__(model)
        self.elevation = elevation
        self.flow_dir = flow_dir

    def step(self):
        self.elevation += 0.001


class AgentGridModel(mesa.Model):
    def __init__(self, valid, elev, flowdir):
        super().__init__()
        height, width = valid.shape
        self.grid = mesa.space.SingleGrid(width, height, torus=False)
        rows, cols = np.where(valid)
        for r, c in zip(rows, cols):
            a = CellAgent(self, float(elev[r, c]), int(flowdir[r, c]))
            self.grid.place_agent(a, (int(c), int(r)))

    def step(self):
        self.agents.do("step")


def time_steps(model, n_steps):
    times = []
    for _ in range(n_steps):
        t0 = time.perf_counter()
        model.step()
        times.append(time.perf_counter() - t0)
    return times


def main():
    valid, elev, flowdir = load_data()
    n_active = int(valid.sum())
    print(f"Active cells: {n_active:,}\n")

    print("=== (A) PropertyLayer-backed model ===")
    t0 = time.perf_counter()
    pl_model = PropertyLayerModel(valid, elev, flowdir)
    setup_a = time.perf_counter() - t0
    print(f"  setup time: {setup_a:.4f} s")
    times_a = time_steps(pl_model, N_STEPS)
    print(f"  step times over {N_STEPS} steps: {[f'{t*1000:.3f}ms' for t in times_a]}")
    print(f"  mean step time: {np.mean(times_a)*1000:.3f} ms")

    print("\n=== (B) Naive per-cell Agent model ===")
    t0 = time.perf_counter()
    agent_model = AgentGridModel(valid, elev, flowdir)
    setup_b = time.perf_counter() - t0
    print(f"  setup time (agent creation + placement): {setup_b:.4f} s")
    print(f"  agent count: {len(agent_model.agents):,}")
    times_b = time_steps(agent_model, N_STEPS)
    print(f"  step times over {N_STEPS} steps: {[f'{t*1000:.3f}ms' for t in times_b]}")
    print(f"  mean step time: {np.mean(times_b)*1000:.3f} ms")

    print("\n=== Summary ===")
    mean_a = np.mean(times_a)
    mean_b = np.mean(times_b)
    print(f"  PropertyLayer mean step: {mean_a*1000:.3f} ms")
    print(f"  Naive Agent   mean step: {mean_b*1000:.3f} ms")
    print(f"  Speedup (Agent / PropertyLayer): {mean_b/mean_a:.1f}x")
    print(f"  Setup time -- PropertyLayer: {setup_a:.3f}s, Naive Agent: {setup_b:.3f}s "
          f"(setup, not the step, but relevant if the grid is rebuilt often)")


if __name__ == "__main__":
    main()
