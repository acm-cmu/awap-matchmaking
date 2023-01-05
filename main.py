import json
import os
from socket import socket
from time import time_ns
from typing import Optional, Union, overload

from fastapi import FastAPI, HTTPException, Response, status, File
from pydantic import BaseModel, BaseSettings
import boto3
from dotenv import load_dotenv

from server.game_engine import GameEngine, setup_game_engine
from server.match_runner import Match, MatchRunner, UserSubmission
from server.storage_handler import StorageHandler
from server.tango import TangoInterface
from server.ranked_game_runner import RankedGameRunner, RankedScrimmages
from server.tournament_runner import TournamentRunner, Tournament


class API(FastAPI):
    engine: Optional[GameEngine]
    engine_filename: Optional[dict[str, str]]
    makefile: dict[str, str]

    temp_file_dir: str
    environ: os._Environ[str]
    tango: TangoInterface

    ongoing_batch_match_runners_table: dict[int, dict[int, str]]

    def __init__(self):
        super().__init__()
        self.engine = None
        self.s3_resource = None
        self.dynamodb_resource = None
        self.engine_filename = None
        load_dotenv()
        self.environ = os.environ
        self.tango = TangoInterface(
            self.environ.get("RESTFUL_KEY"),
            self.environ.get("TANGO_HOSTNAME", "http://localhost"),
            self.environ.get("RESTFUL_PORT", "3000"),
        )

        self.tango.open_courselab()
        self.fastapi_host = (
            f"{self.environ.get('FASTAPI_HOSTNAME')}:{self.environ.get('FASTAPI_PORT')}"
        )

        self.ongoing_batch_match_runners_table = {}


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


@app.on_event("startup")
def connect_to_dynamodb():
    _client_key = app.environ.get("AWS_CLIENT_KEY")
    _client_secret = app.environ.get("AWS_CLIENT_SECRET")

    app.dynamodb_resource = boto3.resource(
        service_name="dynamodb",
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
    and the number of players in the match. It replaces currently set game engine.
    """
    try:
        local_engine_path, local_makefile_path = setup_game_engine(
            new_engine, app.temp_file_dir
        )
    except ConnectionError as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not download game engine: {str(exc)}"
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not save engine: {str(exc)}"
        ) from exc

    app.engine = new_engine
    tango_engine_name = f"{new_engine.engine_filename}"
    app.engine_filename = app.tango.upload_file(
        local_engine_path, tango_engine_name, new_engine.engine_filename
    )

    app.makefile = app.tango.upload_file(
        local_makefile_path, "autograde-Makefile", "Makefile"
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
            fastapi_host=app.fastapi_host,
        ),
        app.tango,
        app.s3_resource,
        "single_match_callback",
    )
    return currMatch.sendJob()


@app.post("/single_match_callback/{match_id}")
def run_single_match_callback(match_id: int, file: bytes = File()):
    """
    Callback URL called by Tango when single unranked scrimmage match has finished running.

    Parses the resulting JSON object and places the returned replay file into S3 bucket.

    Since match is unranked, no need to parse output / adjust rankings
    """
    print("match_id: ", match_id)
    print("file_size:", len(file))
    storageHandler = StorageHandler(app.s3_resource)
    storageHandler.upload_replay(match_id, file, "unranked")


@app.post("/scrimmage")
def run_scrimmage(ranked_scrimmages: RankedScrimmages):
    """
    Run a set of ranked scrimmages with the given user submissions and game engine.

    Sets up scrimmage matches between the teams specified in the given request. The endpoint
    should automatically determine 4-5 bots of similar elo for each bot and run these scrimmage matches.
    Elo should be adjusted according to match results.

    """
    if app.engine is None:
        raise HTTPException(status_code=400, detail="Game engine not set yet")

    if ranked_scrimmages.game_engine_name != app.engine.game_engine_name:
        raise HTTPException(status_code=400, detail="Incompatible game engine")

    scrimmage_id = time_ns()
    rankedGameRunner = RankedGameRunner(
        app.dynamodb_resource,
        scrimmage_id,
        app.ongoing_batch_match_runners_table,
        dict(
            makefile=app.makefile,
            engine=app.engine_filename,
            fastapi_host=app.fastapi_host,
        ),
        app.tango,
        app.s3_resource,
    )
    rankedGameRunner.run_ranked_scrimmage(ranked_scrimmages)
    return scrimmage_id


@app.post("/scrimmage_callback/{scrimmage_id}/{match_id}")
def run_scrimmage_callback(scrimmage_id: int, match_id: int, file: bytes = File()):
    """
    Callback URL called by Tango when tournament match has finished running.

    Parses the resulting JSON object and places the returned replay file into S3 bucket.

    Parse the results and put the winner into the "ongoing_tournaments" dict
    """
    print("received scrimmage callback for match_id: ", match_id)
    storageHandler = StorageHandler(app.s3_resource)
    storageHandler.upload_replay(match_id, file, "ranked_scrimmage")
    app.ongoing_batch_match_runners_table[scrimmage_id][
        match_id
    ] = storageHandler.get_winner_from_replay(file)


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
    if app.engine is None:
        raise HTTPException(status_code=400, detail="Game engine not set yet")

    if tournament.game_engine_name != app.engine.game_engine_name:
        raise HTTPException(status_code=400, detail="Incompatible game engine")

    tournament_id = time_ns()
    rankedGameRunner = TournamentRunner(
        app.dynamodb_resource,
        tournament_id,
        app.ongoing_batch_match_runners_table,
        dict(
            makefile=app.makefile,
            engine=app.engine_filename,
            fastapi_host=app.fastapi_host,
        ),
        app.tango,
        app.s3_resource,
    )
    rankedGameRunner.run_tournament(tournament)
    return tournament_id


@app.post("/tournament_callback/{tournament_id}/{match_id}")
def run_tournament_callback(tournament_id: int, match_id: int, file: bytes = File()):
    """
    Callback URL called by Tango when tournament match has finished running.

    Parses the resulting JSON object and places the returned replay file into S3 bucket.

    Parse the results and put the winner into the "ongoing_tournaments" dict
    """
    print("received tournament callback for match_id: ", match_id)
    storageHandler = StorageHandler(app.s3_resource)
    storageHandler.upload_replay(match_id, file, "tournament")
    app.ongoing_batch_match_runners_table[tournament_id][
        match_id
    ] = storageHandler.get_winner_from_replay(file)
