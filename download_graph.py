import osmnx as ox
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def download_and_save_graph(city_name="Antakya, Hatay, Turkey", filename="antakya_graph.graphml"):
    logger.info(f"Downloading graph for {city_name}...")
    try:
        G = ox.graph_from_place(city_name, network_type="drive")
        logger.info(f"Saving graph to {filename}...")
        ox.save_graphml(G, filepath=filename)
        logger.info("Done!")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    download_and_save_graph()
