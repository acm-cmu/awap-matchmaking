import os
import sys
from time import time_ns
from typing import Optional, Union, overload
from decode_replay import parse_tango_output

from fastapi import FastAPI, HTTPException, Response, status, File
from pydantic import BaseModel, BaseSettings
import boto3
from dotenv import load_dotenv

from server.game_engine import (
    GameEngine,
    MapSelection,
    choose_map,
    download_game_engine,
    reload_game_engine,
)
from server.match_runner import Match, MatchRunner, MatchType, UserSubmission
from server.storage_handler import StorageHandler, MatchTableSchema
from server.tango import TangoInterface
from server.ranked_game_runner import (
    OngoingRankedMatchTable,
    RankedGameRunner,
    RankedScrimmages,
)
from server.tournament_runner import OngoingTourneyTable, TournamentRunner, Tournament
from util import AtomicCounter


class API(FastAPI):
    engine: Optional[GameEngine]
    engine_filename: Optional[dict[str, str]]
    maps: Optional[MapSelection]

    makefile: dict[str, str]

    temp_file_dir: str
    environ: os._Environ[str]
    tango: TangoInterface

    ongoing_batch_match_runners_table: dict[int, dict[int, int]]
    scrimmage_table: dict[int, OngoingRankedMatchTable]
    tourney_table: dict[int, OngoingTourneyTable]

    match_counter: AtomicCounter
    tourney_counter: AtomicCounter

    def __init__(self):
        super().__init__()
        self.engine = None
        self.s3_resource = None
        self.maps = None
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
        self.scrimmage_table = {}
        self.tourney_table = {}

        self.tourney_counter = AtomicCounter(1)


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

    handler = StorageHandler(dynamodb_resource=app.dynamodb_resource)
    next_match_id = handler.get_next_match_id()
    print(next_match_id)
    app.match_counter = AtomicCounter(next_match_id)


@app.get("/")
def read_root():
    return {"status": "Everything is OK"}


@app.post("/game_engine")
def set_game_engine(new_engine: GameEngine):
    """
    This endpoint is used to set the game engine to be used for matches,
    and the number of players in the match.

    It downloads the game engine and makefile from the provided links, and it sets
    these files as the currently running game engine.
    """
    try:
        local_engine_path, local_makefile_path = download_game_engine(
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
    return setup_game_engine(new_engine, local_engine_path, local_makefile_path)


@app.post("/game_engine_reload")
def reuse_game_engine():
    try:
        engine, local_engine_path, local_makefile_path = reload_game_engine(
            app.temp_file_dir
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not reload engine: {str(exc)}"
        ) from exc
    return setup_game_engine(engine, local_engine_path, local_makefile_path)


def setup_game_engine(
    new_engine: GameEngine, local_engine_path: str, local_makefile_path: str
):
    app.engine = new_engine
    tango_engine_name = f"{new_engine.engine_filename}"
    app.engine_filename = app.tango.upload_file(
        local_engine_path, tango_engine_name, new_engine.engine_filename
    )

    app.makefile = app.tango.upload_file(
        local_makefile_path, "autograde-Makefile", "Makefile"
    )

    for layer in new_engine.map_choice.tourney_map_order:
        if len(layer) % 2 != 1:
            raise HTTPException(
                status_code=400,
                detail=f"Tournament layer {layer} does not have an odd number of maps (rounds)",
            )

    app.maps = new_engine.map_choice

    return {"status": f"Game engine set to {app.engine.game_engine_name}"}


@app.post("/match/")
def run_single_match(match: Match):
    """
    Run a single (unranked) match with the given number of players and user submissions.

    Used to run single scrimmage matches requested between teams. These matches are unranked
    and do not adjust elo.

    The number of players should match the number of user submissions, and match
    the number of players set in the game engine.

    The game engine should be set before calling this endpoint.

    The endpoint will instantly generate a match id, insert a "pending" row into the match
    database table, and add the match to the job queue before returning.

    Then, at some point in the future, the match will finish running. When that occurs, the
    endpoint will upload the replay file to replay storage and update the match database
    with the "finished" status and match results.

    (notice that this endpoint will NOT notify the caller when the match is finished; it is
    the responsibility of the caller to keep on checking the database to see if the match has finished.)
    """
    if app.engine is None or app.maps is None:
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

    map_chosen = choose_map(app.maps, MatchType.UNRANKED)

    currMatch = MatchRunner(
        match,
        next(app.match_counter),
        dict(
            makefile=app.makefile,
            engine=app.engine_filename,
            fastapi_host=app.fastapi_host,
        ),
        app.tango,
        app.s3_resource,
        app.dynamodb_resource,
        "single_match_callback",
        MatchType.UNRANKED,
        game_map=map_chosen,
    )
    return currMatch.sendJob()


@app.post("/single_match_callback/{match_id}")
def run_single_match_callback(match_id: int, file: bytes = File()):
    """
    (INTERNAL USE ONLY)

    Callback URL called by Tango when single unranked scrimmage match has finished running.

    Parses the resulting JSON object and places the returned replay file into S3 bucket.

    Updates the match database; sets the match status to "finished" and updates outcome /
    replay file location.

    Since match is unranked, there is no need to parse output / adjust rankings
    """
    print("match_id: ", match_id)
    print("file_size:", len(file))
    dest_filename = f"unranked-{match_id}.json"
    storageHandler = StorageHandler(
        s3_resource=app.s3_resource, dynamodb_resource=app.dynamodb_resource
    )

    try:
        winner = storageHandler.process_replay(file, dest_filename)
        temp_url = storageHandler.get_replay_url(dest_filename)
        storageHandler.update_finished_match_in_table(
            MatchTableSchema(
                match_id,
                outcome="team" + str(winner),
                replay_filename=dest_filename,
                replay_url=temp_url,
            )
        )
    except Exception as exc:
        storageHandler.update_failed_match_in_table(MatchTableSchema(match_id))
        print(file, file=sys.stderr)
        raise HTTPException(status_code=400, detail="Bad replay from tango") from exc


@app.post("/scrimmage")
def run_scrimmage(ranked_scrimmages: RankedScrimmages):
    """
    Run a set of ranked scrimmages with the given user submissions and game engine. Elo is
    adjusted according to match results.

    Sets up scrimmage matches between the teams specified in the given request. It automatically
    determines 4 bots of similar elo for each individual one and runs these scrimmage matches. The endpoint
    returns to caller if scrimmage has been successfully started.

    (actual scrimmage matches will take some time; the endpoint will NOT notify the caller when
    matches are finished running)

    It will upload scrimmage replays to replay storage as matches finish running. It will update
    elo once at the end of the scrimmage.
    """
    if app.engine is None or app.maps is None:
        raise HTTPException(status_code=400, detail="Game engine not set yet")

    if ranked_scrimmages.game_engine_name != app.engine.game_engine_name:
        raise HTTPException(status_code=400, detail="Incompatible game engine")

    map_selection = app.maps

    scrimmage_id = time_ns()

    app.scrimmage_table[scrimmage_id] = OngoingRankedMatchTable()

    try:
        rankedGameRunner = RankedGameRunner(
            app.dynamodb_resource,
            app.match_counter,
            scrimmage_id,
            app.scrimmage_table[scrimmage_id],
            dict(
                makefile=app.makefile,
                engine=app.engine_filename,
                fastapi_host=app.fastapi_host,
            ),
            app.tango,
            app.s3_resource,
            game_map_chooser=lambda: choose_map(map_selection, MatchType.RANKED),
        )
        rankedGameRunner.run_ranked_scrimmage(ranked_scrimmages)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"scrimmage_id": scrimmage_id}


@app.post("/scrimmage_callback/{scrimmage_id}/{match_id}")
def run_scrimmage_callback(scrimmage_id: int, match_id: int, file: bytes = File()):
    """
    (INTERNAL USE ONLY)

    Callback URL called by Tango when tournament match has finished running.

    Parses the resulting JSON object and places the returned replay file into S3 bucket.

    Updates the match database; sets the match status to "finished" and updates outcome /
    replay file location.

    Parses the results and put the winner into the "ongoing_tournaments" dict
    """
    print("received scrimmage callback for match_id: ", match_id)
    dest_filename = f"ranked_scrimmage-{match_id}.json"
    storageHandler = StorageHandler(
        s3_resource=app.s3_resource, dynamodb_resource=app.dynamodb_resource
    )

    try:
        winner = storageHandler.process_replay(file, dest_filename)
        app.scrimmage_table[scrimmage_id](match_id, winner, dest_filename)

    except Exception as exc:
        storageHandler.update_failed_match_in_table(MatchTableSchema(match_id))
        app.scrimmage_table[scrimmage_id](match_id, -1, "")
        print(str(exc), file=sys.stderr)
        print(file, file=sys.stderr)
        raise HTTPException(status_code=400, detail="Malformed tango output") from exc


@app.post("/tournament/")
def run_tournament(tournament: Tournament):
    """
    Run a tournament with the given user submissions and game engine. Only the top
    num_tournament_spots players will participate in the tournament. If there are
    not enough players, the bracket will be padded with byes.

    Based on the number of players in the game engine, the user submissions will be
    split into matches, and each match will be added to the match queue.
    The winner of each match will then be added to the next match, until there is only
    one user submission left, which will be the winner of the tournament.

    The replay will be uploaded, and remote location of the replay & score of each match will be added to the database.

    When the tournament is finished, a bracket should be generated for the tournament, and the bracket should be uploaded to the database.

    The bracket should look something like this for a 4 player tournament with 2 players in each match:
    bracket = [[match1, match2], [match3]]
    match1 = {"player1": "user1", "player2": "user2", "winner": "user1", "replay_remote_directory": "replay1"}

    Returns if the tournament is added to tournament queue, the tournament id.
    """
    if app.engine is None or app.maps is None:
        raise HTTPException(status_code=400, detail="Game engine not set yet")

    if tournament.game_engine_name != app.engine.game_engine_name:
        raise HTTPException(status_code=400, detail="Incompatible game engine")

    tournament_id = time_ns()
    app.tourney_table[tournament_id] = OngoingTourneyTable()

    rankedGameRunner = TournamentRunner(
        app.dynamodb_resource,
        app.match_counter,
        tournament_id,
        app.tourney_table[tournament_id],
        dict(
            makefile=app.makefile,
            engine=app.engine_filename,
            fastapi_host=app.fastapi_host,
        ),
        app.tango,
        app.s3_resource,
        app.maps.tourney_map_order,
    )
    rankedGameRunner.run_tournament(tournament)
    return {"tournament_id": tournament_id}


@app.post("/tournament_callback/{tournament_id}/{match_id}")
def run_tournament_callback(tournament_id: int, match_id: int, file: bytes = File()):
    """
    (INTERNAL USE ONLY)

    Callback URL called by Tango when tournament match has finished running.

    Parses the resulting JSON object and places the returned replay file into S3 bucket.

    Updates the match database; sets the match status to "finished" and updates outcome /
    replay file location.

    Parses the results and put the winner into the "ongoing_tournaments" dict
    """
    print("received tournament callback for match_id: ", match_id)
    dest_filename = f"tournament-{match_id}.json"
    storageHandler = StorageHandler(
        s3_resource=app.s3_resource, dynamodb_resource=app.dynamodb_resource
    )

    try:
        winner = storageHandler.process_replay(file, dest_filename)
        app.tourney_table[tournament_id](match_id, winner, dest_filename)
    except Exception as exc:
        storageHandler.update_failed_match_in_table(MatchTableSchema(match_id))
        app.tourney_table[tournament_id](match_id, -1, "")
        print(str(exc), file=sys.stderr)
        print(file, file=sys.stderr)
        raise HTTPException(status_code=400, detail="Bad tango output") from exc
