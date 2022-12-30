import os

from pydantic import BaseModel
import requests


class GameEngine(BaseModel):
    game_engine_name: str
    engine_filename: str
    engine_download_url: str
    makefile_filename: str
    makefile_download_url: str
    num_players: int


def setup_game_engine(game_engine: GameEngine, data_dir: str):
    """
    Downloads the game engine and associated makefile

    If there is a failure, raises an exception.
    """
    engine_path = os.path.join(data_dir, game_engine.engine_filename)
    with open(engine_path, "wb") as file:
        response = requests.get(game_engine.engine_download_url, allow_redirects=True)
        response.raise_for_status()
        file.write(response.content)

    makefile_path = os.path.join(data_dir, game_engine.makefile_filename)
    with open(makefile_path, "wb") as file:
        response = requests.get(game_engine.makefile_download_url, allow_redirects=True)
        response.raise_for_status()
        file.write(response.content)

    return engine_path, makefile_path
