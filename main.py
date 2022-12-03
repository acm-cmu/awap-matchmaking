import os
from time import time_ns
from typing import Optional, Union, overload

from fastapi import FastAPI, HTTPException, Response, status, File
from pydantic import BaseModel, BaseSettings
import boto3
from dotenv import load_dotenv

from server.game_engine import GameEngine, setup_game_engine
from server.match_runner import Match, MatchRunner, UserSubmission, MatchCallback
from server.storage_handler import StorageHandler
from server.tango import TangoInterface


class Tournament(BaseModel):
    name: str
    user_submissions: list[UserSubmission]
    game_engine_name: str


class API(FastAPI):
    engine: Optional[GameEngine]
    engine_filename: Optional[dict[str, str]]
    makefile: dict[str, str]

    temp_file_dir: str
    environ: os._Environ[str]
    tango: TangoInterface

    def __init__(self):
        super().__init__()
        self.engine = None
        self.s3_resource = None
        self.engine_filename = None
        load_dotenv()
        self.environ = os.environ
        self.tango = TangoInterface(
            self.environ.get("RESTFUL_KEY"),
            self.environ.get("TANGO_HOSTNAME", "http://localhost"),
            self.environ.get("RESTFUL_PORT", "3000"),
        )

        self.tango.open_courselab()
        self.makefile = self.tango.upload_file(
            self.environ.get("MAKEFILE"), "autograde-Makefile", "Makefile"
        )


app = API()


@app.on_event("startup")
def init_game_engine():
    app.temp_file_dir = app.environ.get("TEMPFILE_DIR", "data")
    os.makedirs(app.temp_file_dir, exist_ok=True)


@app.on_event("startup")
def connect_to_s3():
    _client_key = app.environ.get("AWS_CLIENT_KEY")
    _client_secret = app.environ.get("AWS_CLIENT_SECRET")

    app.s3_resource = boto3.client(
        service_name="s3",
        region_name="us-east-1",
        aws_access_key_id=_client_key,
        aws_secret_access_key=_client_secret,
    )


@app.get("/")
def read_root():
    return {"status": "Everything is OK"}


@app.post("/game_engine")
def set_game_engine(new_engine: GameEngine):
    """
    This endpoint is used to set the game engine to be used for matches,
    and the number of players in the match. It replaces currently set game engine
    """
    try:
        local_path = setup_game_engine(new_engine, app.temp_file_dir)
    except ConnectionError as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not download game engine: {str(exc)}"
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not save engine: {str(exc)}"
        ) from exc

    app.engine = new_engine
    tango_engine_name = f"{time_ns()}-{new_engine.engine_filename}"
    app.engine_filename = app.tango.upload_file(
        local_path, tango_engine_name, new_engine.engine_filename
    )

    return {"status": f"Game engine set to {app.engine.game_engine_name}"}


@app.post("/match/")
def run_single_match(match: Match):
    """
    Run a single match with the given number of players and user submissions.

    Used to run single scrimmage matches requested between teams. These matches are unranked
    and do not adjust elo.

    The number of players should match the number of user submissions, and match
    the number of players set in the game engine.
    Check game engine name matches the game engine set in the game engine endpoint.

    The game engine should be set before calling this endpoint.

    This endpoint should then send the user submissions, together with the game engine, to
    the match runner, which will run the match and return the output.

    IMPORTANT NOTE: You will likely handle this output in a separate callback endpoint!

    The output will consist of a replay file and a final score. The replay file should be uploaded
    to the replay storage, and the final score & remote location of the replay should be added to the database.

    Returns the if the match is successfully added to the queue, as well as the match id.
    """
    if app.engine is None:
        raise HTTPException(status_code=400, detail="Game engine not set yet")

    if match.game_engine_name != app.engine.game_engine_name:
        raise HTTPException(status_code=400, detail="Incompatible game engine")

    if len(match.user_submissions) != app.engine.num_players:
        raise HTTPException(
            status_code=400,
            detail=f"Expected {app.engine.num_players} players,"
            f"received only f{len(match.user_submissions)}",
        )

    if match.num_players != len(match.user_submissions):
        raise HTTPException(
            status_code=400, detail="Number of users should match number of submissions"
        )

    currMatch = MatchRunner(
        match,
        dict(
            makefile=app.makefile,
            engine=app.engine_filename,
            fastapi_host=f"{app.environ['FASTAPI_HOSTNAME']}:{app.environ['FASTAPI_PORT']}",
        ),
        app.tango,
        app.s3_resource,
    )
    return currMatch.sendJob()


@app.post("/single_match_callback/{match_id}")
def run_single_match_callback(match_id: int, file: bytes = File()):
    """
    Callback URL called by Tango when single unranked scrimmage match has finished running.

    Parses the resulting JSON object and places the returned replay file into S3 bucket.

    Since match is unranked, no need to parse output / adjust rankings
    """
    print("test")
    print(match_id)
    print("file_size:", len(file))
    print(file)
    # TODO: figure out what format Tango returns the game info in; for now assume json with team names and replay info
    # storageHandler = StorageHandler(app.s3_resource)
    # storageHandler.upload_replay(match_replay_obj)


@app.post("/scrimmage")
def run_scrimmage(tournament: Tournament):
    """
    Run a set of scrimmages with the given user submissions and game engine.

    Sets up scrimmage matches between the teams specified in the given request. The endpoint
    should automatically determine 4-5 bots of similar elo for each bot and run these scrimmage matches.
    Elo should be adjusted according to match results.

    """
    raise NotImplementedError


@app.post("/tournament/")
def run_tournament(tournament: Tournament):
    """
    Run a tournament with the given user submissions and game engine.
    Based on the number of players in the game engine, the user submissions will be
    split into matches, and each match will be added to the match queue.
    The winner of each match will then be added to the next match, until there is only
    one user submission left, which will be the winner of the tournament.

    The replay will be uploaded, and remote location of the replay & score of each match will be added to the database.

    When the tournament is finished, a bracket should be generated for the tournament, and the bracket should be uploaded to the database.

    The bracket should look something like this for a 4 player tournament with 2 players in each match:
    bracket = [[match1, match2], [match3]]
    match1 = {"player1": "user1", "player2": "user2", "winner": "user1", "replay_remote_directory": "replay1"}

    IMPORTANT NOTE: Likely requires you to create a separate thread to run the tournament, as it will take a while to run.

    Returns if the tournament is added to tournament queue, the tournament id.
    """
    raise NotImplementedError
