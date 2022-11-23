import os

from pydantic import BaseModel
import requests


ENGINE_NAME = os.environ.get("ENGINE_NAME", "engine.py")


class GameEngine(BaseModel):
    game_engine_name: str
    download_url: str
    num_players: int


def setup_game_engine(game_engine: GameEngine, data_dir: str):
    """
    Downloads the game engine

    If there is a failure, raises an exception.
    """
    path = os.path.join(data_dir, ENGINE_NAME)

    with open(path, "wb") as file:
        response = requests.get(game_engine.download_url, allow_redirects=True)
        response.raise_for_status()
        file.write(response.content)
