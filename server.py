# server.py
from mesa.visualization.modules import CanvasGrid, ChartModule
from mesa.visualization.ModularVisualization import ModularServer
from mesa.visualization.UserParam import UserSettableParameter
from scripts.model import FloodModel

def portray_cell(agent):
    m = agent.model
    x, y = agent.pos
    w = m.water[y, x]
    color = "red" if w >= m.flood_threshold_mm else "blue"
    return {"Shape": "rect", "Color": color, "Filled": "true", "w": 1, "h": 1}

grid = CanvasGrid(portray_cell, 30, 30, 600, 600)

charts = [
    ChartModule([{"Label": "FloodedCells", "Color": "Black"}], data_collector_name="datacollector"),
    ChartModule([{"Label": "TotalWater_mm", "Color": "Blue"}], data_collector_name="datacollector"),
]

params = {
    "width": 30,
    "height": 30,
    "storm_mm": UserSettableParameter("slider", "Storm (mm)", 50, 0, 200, 5),
    "storm_duration_min": UserSettableParameter("slider", "Duration (min)", 120, 10, 240, 10),
    "infil_mm_per_hr": UserSettableParameter("slider", "Infiltration (mm/hr)", 10, 0, 50, 1),
    "drain_mm_per_hr": UserSettableParameter("slider", "Drainage (mm/hr)", 5, 0, 100, 1),
    "dt_min": 1,
}

server = ModularServer(FloodModel, [grid] + charts, "Flood Learning ABM — Lagos", params)
