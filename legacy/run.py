# run.py
from server import server

if __name__ == "__main__":
    server.port = 8521
    server.launch()
