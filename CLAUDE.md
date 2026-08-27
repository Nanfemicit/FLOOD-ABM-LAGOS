# FLOOD-ABM-LAGOS — Project Context

This file exists so any Claude Code session in this repo starts with full context instead of a blank slate. Read this before touching any code.

---

## 1. Why this project exists

Lagos floods every rainy season. The damage recurs and worsens rather than triggering adaptation, unlike, say, earthquake-prone regions where a disaster tends to update building codes. The standard framing treats this as a climate problem: more rain, worse floods.

This project argues that framing is incomplete. Flooding in Lagos is what rainfall does when it meets planning failures, poor drainage, and the absence of institutional feedback loops. The climate signal is real, but it is compounding governance failure, not acting alone. The model exists to make that interaction visible and testable rather than asserted.

There's also a human cost dimension the standard flood models don't count: missed work, lost wages, health impacts, mental health toll, all sharper given an already-low standard of living for many affected communities. Standard hydrological models score flood extent. This project wants to also show who bears it.

The purpose isn't a static academic output. It's meant to let people engage directly, adjust the conditions, and watch the argument play out rather than read it as a claim in a paper.

---

## 2. ABM fundamentals (for reference, not just onboarding)

An agent-based model is a population of independent agents following simple local rules, placed on a shared environment, simulated forward in time. The flood pattern that emerges is not directly programmed. It comes out of local interactions between cells, rainfall, and drainage. That emergence is the core value of ABM over traditional macro flood modeling: it lets governance and infrastructure variables produce visibly different outcomes from the same climate input.

Four building blocks:

- **Agents** — grid cells or households
- **Environment** — the Lagos landscape (elevation, land cover, ward boundaries)
- **Rules** — e.g. "if incoming water exceeds drainage capacity, overflow to neighboring cells"
- **Parameters** — the dials that isolate climate variables from institutional/governance variables (see section 3)

---

## 3. The four parameters, and why they matter

| Parameter | What it represents |
|---|---|
| Storm intensity (mm) | The climate signal |
| Storm duration (min) | The climate signal |
| Infiltration rate (mm/hr) | Governed by land cover — paving, lost wetlands, concrete. A planning and design choice. |
| Drainage capacity (mm/hr) | The capacity and upkeep of the drainage system. A governance question. |

This is the mechanism that makes the argument playable rather than asserted: hold storm intensity fixed, drop drainage capacity, and watch the same rainfall produce a worse flood. The same rain floods a city differently depending on choices the city has made. This is already true of the existing (synthetic) model's rule logic and should be preserved through the rebuild, not reinvented.

---

## 4. Literature grounding

- **Koç & Işık (2020, *Natural Hazards*)** — multi-agent model for sustainable governance of urban flood risk mitigation; agents negotiate across social, economic, and environmental dimensions.
- **MEGADAPT (Mexico City ABM)** — closest published precedent to this project's argument. Treats vulnerability as emerging from the interplay between decision-makers' mental models and the biophysical response, rather than from hazard exposure alone.
- **2022 systematic review (Springer, *Natural Hazards*)** — confirms social vulnerability indicators (income, employment, risk perception, coping capacity) are standard components of flood ABMs, not a soft add-on.
- Field terms to anchor further reading: **coupled human-natural systems**, **socio-hydrology**.

---

## 5. Honest current state of the repo (as of this rebuild decision)

Repo: `github.com/Nanfemicit/FLOOD-ABM-LAGOS`, 14 commits, Python 100%.

**What exists:**
- `data/` and `scripts/` folders
- `run.py` — six lines, launches the old Mesa server on port 8521
- `server.py` — defines a `portray_cell` function coloring grid cells red/blue by a flood threshold, wires up a `CanvasGrid`, two `ChartModule` charts (FloodedCells, TotalWater_mm), and four `UserSettableParameter` sliders matching the parameters in section 3
- `requirements.txt`

**What's blocked:** `server.py` imports `mesa.visualization.modules.CanvasGrid`, `mesa.visualization.ModularVisualization.ModularServer`, and `mesa.visualization.UserParam.UserSettableParameter`. Mesa 3.x removed all three. Mesa's visualization system was rebuilt around **SolaraViz**, built on the Solara reactive web framework, which supports grid displays, plots, and standalone deployable web apps (not just Jupyter). This is a rewrite of the visualization layer, not a patch.

**What it currently runs on:** a synthetic 30x30 grid. Not real Lagos geography. The rule logic (rainfall → infiltration → drainage → overflow) is sound and worth carrying forward. The environment it operates on is not.

---

## 6. The rebuild decision

Decision: rebuild the **environment layer** using real Lagos geographic data, rather than patching the synthetic grid or discarding the working model outright.

Important distinction to hold onto: "starting fresh" means a new environment layer, not deleting a year of working rule logic. The overflow/infiltration/drainage rules already encode the argument correctly. What changes is what feeds them.

### Data sources identified

- **Rainfall (working dataset):** CHIRPS satellite-gauge rainfall estimates. Chosen over Nigerian Meteorological Agency (NiMet) records because NiMet data is institutional and typically requires a formal request with unpredictable turnaround — an open-ended dependency this project can't afford to block on. NiMet records can be requested in parallel and swapped in later for validation/rigor.
- **Ward boundaries:** GRID3 Lagos State Administrative Boundaries on openAFRICA, which includes an operational ward boundary layer specific to Lagos. (Note: HDX's Nigeria admin boundary dataset only has ward-level detail for northeast Nigeria — not usable for Lagos wards specifically.)
- **Elevation:** SRTM or Copernicus DEM. HDX also hosts a 90m-resolution Nigeria DEM as a faster fallback if higher resolution isn't needed immediately.
- **Land cover / infiltration proxy:** ESA WorldCover, a free global land cover product distinguishing built-up, vegetated, and water surfaces — used to ground the infiltration parameter in something real instead of a guessed default.

---

## 7. Phased build plan

**Phase 0 — Decisions (locked, not to be re-litigated per session):**
1. Rebuild the environment layer; keep the existing rule logic.
2. Interactive layer is Mesa 3.x SolaraViz, not a custom web stack, for version one.

**Phase 1 — Data acquisition** (currently in progress; the long pole of this project, deliberately given no natural deadline, so treat it as needing a manufactured one)
- Download GRID3 Lagos ward boundary layer → `data/raw/boundaries/`, validate it opens cleanly in GeoPandas
- Pull DEM (SRTM/Copernicus, or HDX 90m fallback)
- Pull first CHIRPS rainfall extract
- Pull ESA WorldCover land cover extract

**Phase 2 — Rebuild environment layer**
- Rasterize DEM to the model grid
- Derive flow direction (simple 8-neighbor steepest descent is sufficient for v1; richdem/pysheds available if more rigor is needed later). **Before running this: decide explicitly whether flow direction is derived on the buffered raster (recommended, so flow at the ward edge isn't artificially truncated by a hard boundary) or the ward-masked one — don't let this happen implicitly by whatever raster happens to be loaded. See the buffer-vs-mask note under the DEM entry in `data/README.md`.**
- Tag each cell with elevation, land cover class, ward ID

**Phase 3 — Recouple parameters to real records**
- Calibrate storm intensity/duration against actual historical Lagos rainfall events from CHIRPS, not arbitrary slider ranges. **When selecting which storm event(s) to calibrate against, weigh proximity to the 2021 land cover snapshot alongside how well-documented and severe the flood event was. These two criteria may pull toward different years — a well-documented severe flood far from 2021 vs. a closer-to-2021 event with thinner documentation — and that tension should be named explicitly when the choice is made, not resolved by default toward whichever criterion is easiest to satisfy.**
- **Model time step: decided on daily CHIRPS rainfall**, not hourly. Considered against two alternatives: NASA IMERG (real hourly data, but introduces this project's first auth requirement and a second, coarser resolution-mismatch story on top of the one already documented in `docs/methodology-notes.md`), and disaggregated hourly CHIRPS (an invented within-day shape layered on top of a real daily total — the least defensible option, since it weakens the "calibrated against a real event" claim at exactly the point a reviewer would first push on it). Reasoning: stays consistent with the zero-auth, real-data-only pattern used throughout Phase 1; the core governance argument depends on differentiating outcomes by drainage/infiltration/elevation, not by sub-daily rainfall shape, so within-day intensity isn't load-bearing for what the model needs to demonstrate; and daily stepping directly addresses the 38.5%-flooded artifact produced by forcing a whole month's rainfall through one hourly-capacity hop in the first smoke test. This reframes storm duration from minutes to number of rainy days, and storm intensity from a single value to mm/day — an evolution of the original four-parameter design for the real-data context, not a departure from it. IMERG remains a possible future upgrade for a specific, well-documented calibration event, not pursued now.
- Add a first-pass vulnerability layer per ward (population density or income proxy) — this is where the human-cost argument becomes spatial and the MEGADAPT framing becomes concretely implementable
- **Conveyance-capacity limit: implemented.** `model/flood_model.py`'s routing loop now caps how much water a directed (non-pit) cell can pass downstream per hop at `drain_mm`, reusing the existing scenario parameter rather than inventing a new one — excess settles as standing depth at that cell immediately, the same way a pit's local leftover already does. This closed the gap the note above used to describe: the June 2-4 daily smoke test no longer shows 100% pit-only flooding (well-maintained: 60.6% pit / 39.4% non-pit; poorly-maintained: 7.8% pit / 92.2% non-pit — see `data/README.md` "Infiltration parameterization" entry and `scripts/smoke_test_drainage_scenarios.py`).
- **PROVISIONAL — no recession mechanism yet.** Traced explicitly: `water_depth` is currently add-only. Nothing in `apply_rainfall()` ever reduces standing depth once it's settled — not across hops within a day, not across subsequent days, not even through a dry day (zero rainfall in produces zero change to standing depth, it doesn't drain on its own). This means flood extent and depth in this model can only grow across a multi-day event, never recede, which doesn't match real flood behavior. **Every multi-day result produced so far (including the June 2-4 well/poor drainage comparison: 1.39% vs 19.27% flooded, built-up core 5.99% vs 74.48%) is a peak-accumulated high-water mark, not a snapshot that could recede — treat these numbers as provisional until a recession mechanism (draining standing depth via `drain_mm` over subsequent calls, worked through but not yet implemented) lands and the comparison is re-run.** Evaporation as a separate loss term, and re-mobilizing already-settled water back into the flow network, are both real further refinements, deliberately deferred past the recession fix itself.

**Phase 4 — Interactive layer**
- Wire the rebuilt model into Mesa 3.x SolaraViz with a `SpaceRenderer` over the real Lagos raster
- Same four parameter sliders, now operating on real geography
- Deploy as a standalone Solara web app
- `model/flood_model.py`'s rainfall-stepping method is currently named `apply_rainfall()`, not `step()` — it needed a custom signature (a rainfall array argument) and Mesa 3.x's `Model.step()` is wrapped by internal event-scheduling machinery that doesn't accept one. SolaraViz needs `step()` to be the zero-argument entry point Mesa calls each tick, reading rainfall and the other parameters from model state that the UI sliders set, rather than `apply_rainfall()` staying the primary entry point. Not solved yet, just flagged so it isn't rediscovered from scratch when this phase starts.

**Phase 5 — Public documentation**
- Document the build in public via Medium (*Learning Out Loud(er)*) as each phase completes, walking readers through the reasoning step by step

---

## 8. The public-facing vision (long-term, but shapes structure now)

This is not meant to end as a repo and a static report. The goal is something explorable and alive: a person drops in, understands the premise quickly, then changes parameters and watches flood outcomes shift in front of them. Closer to an immersive data-exploration site than an academic deliverable, shareable and almost campaign-like, without sacrificing rigor.

**Implication for how the rebuild should proceed, even at this early stage:** every naming, data structure, and code organization decision should assume a public reader is eventually looking at this, not just a grader or a future version of Ruth. Keep the repo readable by an outsider from the start rather than retrofitting explanations later. This is also why SolaraViz (deployable, not just a local dev server) was chosen over the old visualization approach — version one should already be a step toward shareable, not a private debugging tool that gets replaced wholesale later.

---

## 9. Open note — not a blocker, but worth tracking

The administrative level at which data is collected and reported (neighborhood vs. ward vs. LGA vs. state) is not a neutral technical choice. It determines what becomes analytically visible, and whoever draws those boundaries made a political decision, named or not. This surfaced directly in the master's thesis work (a shift in results depending on which administrative level was used) and will likely surface again here, since Lagos ward and LGA boundaries carry their own histories of definition. Worth a running note as the build proceeds — flagged as a possible future essay, not a current priority.

---

## 10. Immediate next step (as of this handoff)

The GRID3 Lagos ward boundary file is the foundation everything else clips to. If it hasn't already been downloaded and validated in GeoPandas at the point this file is read, that is the first task — before any model code is touched.
