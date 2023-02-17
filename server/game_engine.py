import json
import os
import random

from pydantic import BaseModel
import requests

from server.match_runner import MatchType


persistent = "engine-persistent.json"


class MapSelection(BaseModel):
    unranked_possible_maps: list[str]
    ranked_possible_maps: list[str]
    tourney_map_order: list[list[str]]


class GameEngine(BaseModel):
    game_engine_name: str
    engine_filename: str
    engine_download_url: str
    makefile_filename: str
    makefile_download_url: str
    num_players: int
    map_choice: MapSelection


def download_game_engine(game_engine: GameEngine, data_dir: str):
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

    persistent_path = os.path.join(data_dir, persistent)
    with open(persistent_path, "w", encoding="utf-8") as file:
        persistent_save = dict(
            engine_path=engine_path,
            makefile_path=makefile_path,
            engine_details=game_engine.dict(),
        )
        json.dump(persistent_save, file)

    return engine_path, makefile_path


def reload_game_engine(data_dir: str):
    persistent_path = os.path.join(data_dir, persistent)
    with open(persistent_path, "r", encoding="utf-8") as file:
        contents = json.load(file)
        engine_path = contents["engine_path"]
        makefile_path = contents["makefile_path"]
        engine = GameEngine.parse_obj(contents["engine_details"])
    print(engine)
    return engine, engine_path, makefile_path


def choose_map(map_selection: MapSelection, match_type: MatchType) -> str:
    if match_type == MatchType.UNRANKED:
        return random.choice(map_selection.unranked_possible_maps)
    if match_type == MatchType.RANKED:
        return random.choice(map_selection.ranked_possible_maps)
    raise Exception("dont use this for Tournament")
