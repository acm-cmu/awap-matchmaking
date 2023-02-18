from concurrent.futures import ThreadPoolExecutor
import os
from threading import Lock, Semaphore, Thread
from time import time
from typing import Any, Optional
from pydantic import BaseModel
from server.match_runner import (
    MatchType,
    UserSubmission,
    MatchRunner,
    Match,
    MatchPlayer,
)
from server.ranked_game_runner import OngoingRankedMatchTable
from server.storage_handler import StorageHandler, MatchTableSchema
from server.tango import TangoInterface
from util import AtomicCounter


class Tournament(BaseModel):
    bracket: str
    user_submissions: list[UserSubmission]
    game_engine_name: str
    num_tournament_spots: int


class TourneyPairUpRunner:
    tourney_id: int
    engine_name: str
    semaphore: Semaphore
    maps: list[str]
    p1: Optional[MatchPlayer]
    p2: Optional[MatchPlayer]
    storageHandler: StorageHandler
    match_counter: AtomicCounter
    tango: TangoInterface
    parent: "OngoingTourneyTable"
    parent_lock: Lock

    next_match_id: int
    p1wins: int
    p2wins: int
    replayLocs: list[str]
    matchWinners: list[int]

    def __init__(
        self,
        tourney_id: int,
        engine_name: str,
        parent: "OngoingTourneyTable",
        maps: list[str],
        p1: Optional[MatchPlayer],
        p2: Optional[MatchPlayer],
        storageHandler: StorageHandler,
        match_counter: AtomicCounter,
        match_runner_config,
        tango: TangoInterface,
        s3_resource,
        dynamodb_resource,
    ) -> None:
        self.tourney_id = tourney_id
        self.engine_name = engine_name
        self.p1 = p1
        self.p2 = p2
        self.maps = maps
        self.match_counter = match_counter
        self.match_runner_config = match_runner_config
        self.tango = tango
        self.s3_resource = s3_resource
        self.dynamodb_resource = dynamodb_resource
        self.parent = parent
        self.storageHandler = storageHandler

        self.next_match_id = -1
        self.p1wins = 0
        self.p2wins = 0
        self.replayLocs = []
        self.matchWinners = []

    def __call__(self, winner: int, replay: str) -> None:
        if winner == 1:
            self.p1wins += 1
        elif winner == 2:
            self.p2wins += 1
        else:
            self.replayLocs.append("failed")
            self.matchWinners.append(-1)
            self.semaphore.release()
            return

        replay_url = self.storageHandler.get_replay_url(replay)

        self.storageHandler.update_finished_match_in_table(
            MatchTableSchema(
                self.next_match_id,
                outcome=f"team{winner}",
                replay_filename=replay,
                replay_url=replay_url,
            ),
        )

        self.replayLocs.append(replay_url)
        self.matchWinners.append(winner)
        self.semaphore.release()

    def start(self) -> tuple[dict[str, Any], MatchPlayer]:
        if self.p1 is None or self.p2 is None:
            actual_player = self.p1
            if actual_player is None:
                actual_player = self.p2
            assert actual_player is not None
            return {
                "player1": actual_player.user_info.username,
                "player2": "bye",
                "winner": actual_player.user_info.username,
                "replay_filename": [],
            }, actual_player

        self.p1wins = 0
        self.p2wins = 0
        self.semaphore = Semaphore(0)
        self.replayLocs = []
        self.matchWinners = []

        for match_map in self.maps:
            match = self.create_match(self.p1, self.p2, match_map)
            self.parent.register(match.match_id, self)
            match.sendJob()
            self.semaphore.acquire()

        winner = self.p1 if self.p1wins >= self.p2wins else self.p2
        print(
            f"Match completed: {self.p1.user_info.username} vs {self.p2.user_info.username} {self.p1wins}-{self.p2wins}"
        )

        return {
            "player1": self.p1.user_info.username,
            "player2": self.p2.user_info.username,
            "overall_winner": winner.user_info.username,
            "replay_filename": self.replayLocs,
            "map_winners": self.matchWinners,
        }, winner

    def create_match(
        self, p1: MatchPlayer, p2: MatchPlayer, match_map: str
    ) -> MatchRunner:
        match = Match(
            game_engine_name=self.engine_name,
            num_players=2,  # assumes 1v1 now
            user_submissions=[p1.user_info, p2.user_info],
        )

        currMatch = MatchRunner(
            match,
            next(self.match_counter),
            self.match_runner_config,
            self.tango,
            self.s3_resource,
            self.dynamodb_resource,
            f"tournament_callback/{self.tourney_id}",
            MatchType.TOURNAMENT,
            match_map,
        )
        self.next_match_id = currMatch.match_id
        print(
            f"Match {currMatch.match_id}: {p1.user_info.username} vs {p2.user_info.username}"
        )
        return currMatch


class OngoingTourneyTable:
    callbacks: dict[int, TourneyPairUpRunner]
    lock: Lock

    def __init__(self) -> None:
        self.callbacks = {}
        self.lock = Lock()

    def register(self, match_id: int, pair: TourneyPairUpRunner):
        with self.lock:
            self.callbacks[match_id] = pair

    def __call__(self, match_id: int, winner: int, replay: str) -> None:
        with self.lock:
            self.callbacks[match_id](winner, replay)

    def clear(self):
        with self.lock:
            self.callbacks.clear()


class TournamentRunner:
    match_map_order: list[list[str]]
    tourney_table_entry: OngoingTourneyTable

    def __init__(
        self,
        dynamodb_resource,
        match_counter: AtomicCounter,
        tournament_id,
        tourney_table_entry: OngoingTourneyTable,
        match_runner_config,
        tango,
        s3_resource,
        match_map_order: list[list[str]],
    ):
        self.tournament_id = tournament_id
        self.match_counter = match_counter

        # used to set the callbacks post-match
        self.tourney_table_entry = tourney_table_entry

        # used for the matchrunner
        self.match_runner_config = match_runner_config
        self.tango = tango
        self.s3_resource = s3_resource
        self.dynamodb_resource = (
            dynamodb_resource  # also needed for player table / looking up elos
        )

        self.match_map_order = match_map_order

    def run_tournament(self, tournament: Tournament):
        # get the ratings of the users specified in the list
        tournament_players = MatchRunner.get_match_players_info(
            self.dynamodb_resource.Table(os.environ["AWS_PLAYER_TABLE_NAME"]),
            tournament.user_submissions,
        )

        # set up a thread that will run the tournament
        thread = Thread(
            target=self.tournament_worker_thread,
            args=(tournament, tournament_players),
        )
        thread.daemon = True
        thread.start()

    @staticmethod
    def is_pow_two(n):
        return (n != 0) and (n & (n - 1) == 0)

    def tournament_worker_thread(
        self, tournament: Tournament, tournament_players: list[Optional[MatchPlayer]]
    ):

        print("=== TOURNAMENT SEED LIST ===:")
        for i, player in enumerate(tournament_players):
            if player is not None:
                print(i + 1, player.user_info.username)
        print("=== TOURNAMENT SEED DONE ===:")

        # pad the list of players with byes until power of 2
        while not self.is_pow_two(len(tournament_players)):
            tournament_players.append(None)

        # set up tournament bracket; keep on playing adjacent teams, only keeping the winner
        curr_tournament_layer: list[Optional[MatchPlayer]] = []

        for i in range(len(tournament_players) // 2):
            curr_tournament_layer.append(tournament_players[i])
            curr_tournament_layer.append(
                tournament_players[len(tournament_players) - i - 1]
            )

        complete_tournament_results = []
        storageHandler = StorageHandler(
            s3_resource=self.s3_resource, dynamodb_resource=self.dynamodb_resource
        )

        layer = 0
        num_specified_layer_maps = len(self.match_map_order)

        while len(curr_tournament_layer) > 1:
            pairups = [
                TourneyPairUpRunner(
                    self.tournament_id,
                    tournament.game_engine_name,
                    self.tourney_table_entry,
                    self.match_map_order[layer % num_specified_layer_maps],
                    curr_tournament_layer[i],
                    curr_tournament_layer[i + 1],
                    storageHandler,
                    self.match_counter,
                    self.match_runner_config,
                    self.tango,
                    self.s3_resource,
                    self.dynamodb_resource,
                )
                for i in range(0, len(curr_tournament_layer), 2)
            ]

            raw_results = list(
                ThreadPoolExecutor(16).map(TourneyPairUpRunner.start, pairups)
            )

            results = []

            if len(raw_results) > 1:
                for i in range(len(raw_results) // 2):
                    results.append(raw_results[i])
                    results.append(raw_results[len(raw_results) - i - 1])
            else:
                results = raw_results

            curr_tournament_layer = [winner for (_, winner) in results]

            complete_tournament_results.append([r for (r, _) in results])
            layer += 1

        # tournament has been completed; upload final tournament bracket onto s3
        print("completed tournament with following bracket: ")
        print(complete_tournament_results)
        storageHandler = StorageHandler(s3_resource=self.s3_resource)
        storageHandler.upload_tournament_bracket(
            self.tournament_id, complete_tournament_results
        )

        self.tourney_table_entry.callbacks.clear()
