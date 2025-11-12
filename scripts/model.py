# scripts/model.py
from mesa import Model
from mesa.time import BaseScheduler
from mesa.datacollection import DataCollector
import numpy as np

class FloodModel(Model):
    """
    Minimal flood-learning model:
    rainfall → infiltration → drainage; track flooded cells & total water.
    """

    def __init__(self, width=30, height=30,
                 storm_mm=50, storm_duration_min=120,
                 infil_mm_per_hr=10, drain_mm_per_hr=5, dt_min=1):
        super().__init__()
        self.width, self.height = width, height
        self.schedule = BaseScheduler(self)

        # parameters
        self.storm_mm = float(storm_mm)
        self.storm_duration_min = float(storm_duration_min)
        self.infil_mm_per_min = float(infil_mm_per_hr) / 60.0
        self.drain_mm_per_min = float(drain_mm_per_hr) / 60.0
        self.dt_min = float(dt_min)
        self.t_min = 0.0
        self.rain_rate_mm_per_min = self.storm_mm / max(1.0, self.storm_duration_min)

        # fields
        self.elev = np.random.uniform(0.0, 1.0, (height, width))
        self.water = np.zeros_like(self.elev)
        self.flood_threshold_mm = 50.0

        # data collection
        self.datacollector = DataCollector(model_reporters={
            "Time_min": lambda m: m.t_min,
            "FloodedCells": self._count_flooded,
            "TotalWater_mm": lambda m: float(np.sum(m.water)),
        })

    def step(self):
        self.t_min += self.dt_min
        self._rainfall()
        self._infiltration()
        self._drainage()
        self.datacollector.collect(self)
        if self.t_min >= self.storm_duration_min and float(np.max(self.water)) < 0.1:
            self.running = False

    def _rainfall(self):
        if self.t_min <= self.storm_duration_min:
            self.water += self.rain_rate_mm_per_min * self.dt_min

    def _infiltration(self):
        infil = np.minimum(self.water, self.infil_mm_per_min * self.dt_min)
        self.water -= infil

    def _drainage(self):
        drain = np.minimum(self.water, self.drain_mm_per_min * self.dt_min)
        self.water -= drain

    def _count_flooded(self):
        return int(np.sum(self.water >= self.flood_threshold_mm))
