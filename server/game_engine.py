import os

from pydantic import BaseModel
import requests


class GameEngine(BaseModel):
    game_engine_name: str
    engine_filename: str
    download_url: str
    num_players: int


def setup_game_engine(game_engine: GameEngine, data_dir: str):
    """
    Downloads the game engine

    If there is a failure, raises an exception.
    """
    path = os.path.join(data_dir, game_engine.engine_filename)

    with open(path, "wb") as file:
        response = requests.get(game_engine.download_url, allow_redirects=True)
        response.raise_for_status()
        file.write(response.content)

    return path
