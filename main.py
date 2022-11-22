import os
from typing import Optional, Union

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, BaseSettings
import boto3
from dotenv import load_dotenv
from game_engine import GameEngine, setup_game_engine

from match_runner import Match, MatchRunner, UserSubmission

DATA_DIR = "data"


class Tournament(BaseModel):
    name: str
    user_submissions: list[UserSubmission]
    game_engine_name: str


class API(FastAPI):
    engine: Optional[GameEngine] = None
    dotenv_loaded: bool = False
    s3_resource = None


app = API()


@app.on_event("startup")
def load_env():
    if not app.dotenv_loaded:
        app.dotenv_loaded = True
        load_dotenv()


@app.on_event("startup")
def init_game_engine():
    os.makedirs(DATA_DIR, exist_ok=True)


@app.on_event("startup")
def connect_to_s3():
    load_env()
    _client_key = os.environ["AWS_CLIENT_KEY"]
    _client_secret = os.environ["AWS_CLIENT_SECRET"]

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
        setup_game_engine(new_engine, DATA_DIR)
        app.engine = new_engine
    except ConnectionError as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not download game engine: {str(exc)}"
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not save engine: {str(exc)}"
        ) from exc

    return {"status": f"Game engine set to {app.engine.game_engine_name}"}


@app.post("/match/")
def run_single_match(match: Match):
    """
    Run a single match with the given number of players and user submissions.
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

    # run the match (TODO: set match config)
    currMatch = MatchRunner(match, {}, app.s3_resource)
    return currMatch.sendJob()


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
