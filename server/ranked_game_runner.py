# used to run ranked scrimamge matches
# you would likely need spin up a python worker thread to run the matches
import functools
import os
from threading import Lock, Semaphore, Thread
import threading
from time import time
from typing import Callable, Optional
from pydantic import BaseModel
from server.match_runner import (
    MatchType,
    UserSubmission,
    MatchRunner,
    Match,
    MatchPlayer,
    MatchTableSchema,
)
from server.storage_handler import StorageHandler
from util import AtomicCounter


class RankedScrimmages(BaseModel):
    user_submissions: list[UserSubmission]
    game_engine_name: str


class Elo:
    k = 20

    @staticmethod
    def calc_expected_score(first_elo: int, second_elo: int):
        return 1 / (1 + pow(10, (second_elo - first_elo) / 400))

    # returns tuple representing (change to first team's elo, change to second team's elo)
    @staticmethod
    def calc_elo_change(first_elo: int, second_elo: int, first_team_won: bool):
        score = 1 if first_team_won else 0
        expected_score = Elo.calc_expected_score(first_elo, second_elo)
        rating_change = int(Elo.k * (score - expected_score))
        return (rating_change, -1 * rating_change)


class RankedScrimmagePostMatchCallbacks:
    matches_left: int  # must acquire lock!
    matches_left_mutex: Lock

    post_match_callbacks: dict[int, Callable[[int, str], None]]
    post_scrimmage_callback: Callable[[], None]

    def __init__(
        self, num_matches: int, post_scrimmage_callback: Callable[[], None]
    ) -> None:
        self.matches_left = num_matches
        self.matches_left_mutex = Lock()
        self.post_match_callbacks = {}
        self.post_scrimmage_callback = post_scrimmage_callback


class RankedScrimmageTableEntry:
    callbacks: Optional[RankedScrimmagePostMatchCallbacks]

    def __init__(self):
        self.callbacks = None

    def setup(self, num_matches: int, post_scrimmage_callback: Callable[[], None]):
        self.callbacks = RankedScrimmagePostMatchCallbacks(
            num_matches, post_scrimmage_callback
        )

    def register(self, match_id: int, callback: Callable[[int, str], None]):
        if self.callbacks is None:
            raise Exception()
        self.callbacks.post_match_callbacks[match_id] = callback

    def run_callback(self, match_id: int, outcome: int, replay_file: str):
        if self.callbacks is None:
            raise Exception()

        matches_left = 0

        self.callbacks.post_match_callbacks[match_id](outcome, replay_file)
        self.callbacks.matches_left_mutex.acquire()
        self.callbacks.matches_left -= 1
        matches_left = self.callbacks.matches_left
        self.callbacks.matches_left_mutex.release()

        if matches_left == 0:
            self.callbacks.post_scrimmage_callback()


class RankedGameRunner:
    # number of matches each team participates in; should be even number and less than total number of teams
    num_matches = 4
    game_map_chooser: Callable[[], str]

    def __init__(
        self,
        dynamodb_resource,
        match_counter: AtomicCounter,
        scrimmage_id,
        scrimmage_entry: RankedScrimmageTableEntry,
        match_runner_config,
        tango,
        s3_resource,
        game_map_chooser: Callable[[], str],
    ):
        self.scrimmage_id = scrimmage_id
        self.match_counter = match_counter

        self.scrimmage_entry = scrimmage_entry

        # used for the matchrunner
        self.match_runner_config = match_runner_config
        self.tango = tango
        self.s3_resource = s3_resource
        self.dynamodb_resource = (
            dynamodb_resource  # also needed for player table / looking up elos
        )
        self.game_map_chooser = game_map_chooser

    def run_ranked_scrimmage(self, ranked_scrimmage: RankedScrimmages):
        if len(ranked_scrimmage.user_submissions) < RankedGameRunner.num_matches:
            # TODO: handle error
            print("too few players to run scrimmages")
            return ""

        # get the ratings of the users specified in the list
        scrimmage_players = MatchRunner.get_match_players_info(
            self.dynamodb_resource.Table(os.environ["AWS_PLAYER_TABLE_NAME"]),
            ranked_scrimmage.user_submissions,
        )

        # set up a thread that will run the scrimmage matches
        thread = Thread(
            target=self.ranked_scrimmage_thread,
            args=(ranked_scrimmage, scrimmage_players),
        )
        thread.daemon = True
        thread.start()

    def ranked_scrimmage_thread(
        self, ranked_scrimmage: RankedScrimmages, scrimmage_players: list[MatchPlayer]
    ):
        # determine which matches to run
        matches: set[tuple[str, str]] = set({})
        index_lower_bound = 0
        index_upper_bound = len(scrimmage_players) - 1 - RankedGameRunner.num_matches
        for i, curr_player in enumerate(scrimmage_players):
            # run matches with num_matches/2 teams above and num_matches/2 teams below
            # use upper and lower bound, in case team is one of the highest (or lowest) rated teams and aren't enough teams above (or below)
            bot_index = min(index_upper_bound, max(index_lower_bound, i - 2))
            for j in range(bot_index, bot_index + RankedGameRunner.num_matches + 1):
                opponent = scrimmage_players[j]
                if opponent.user_info.username != curr_player.user_info.username:
                    # use tuple instead of object since tuples are hashable
                    if opponent.rating < curr_player.rating:
                        matches.add(
                            (
                                opponent.user_info.username,
                                curr_player.user_info.username,
                            )
                        )
                    else:
                        matches.add(
                            (
                                curr_player.user_info.username,
                                opponent.user_info.username,
                            )
                        )

        players_map: dict[str, MatchPlayer] = {}
        net_elo_changes: dict[str, int] = {}

        for scrimmage_player in scrimmage_players:
            players_map[scrimmage_player.user_info.username] = scrimmage_player
            net_elo_changes[scrimmage_player.user_info.username] = 0

        print("running the following matches: ")
        print(matches)

        # run the matches
        storageHandler = StorageHandler(
            s3_resource=self.s3_resource, dynamodb_resource=self.dynamodb_resource
        )

        def post_tango_callback(
            player_1: MatchPlayer,
            player_2: MatchPlayer,
            match_id: int,
            winner: int,
            replay_filename: str,
        ):
            winner_is_player_1 = winner == 1
            (player_1_change, player_2_change) = Elo.calc_elo_change(
                player_1.rating, player_2.rating, winner_is_player_1
            )
            net_elo_changes[player_1.user_info.username] += player_1_change
            net_elo_changes[player_2.user_info.username] += player_2_change
            storageHandler.update_finished_match_in_table(
                MatchTableSchema(
                    match_id,
                    outcome="team1" if winner_is_player_1 else "team2",
                    replay_filename=replay_filename,
                    elo_change=abs(player_1_change),
                    replay_url=storageHandler.get_replay_url(replay_filename),
                )
            )

        def post_scrimmage_callback():
            # apply all the changes in net_elo_changes
            updated_elos = {}
            for key, value in net_elo_changes.items():
                updated_elos[key] = players_map[key].rating + value
            print("completed scrimmage with following final elos: ")
            print(updated_elos)
            storageHandler = StorageHandler(dynamodb_resource=self.dynamodb_resource)
            storageHandler.adjust_elo_table(updated_elos)
            del self.scrimmage_entry

        self.scrimmage_entry.setup(len(matches), post_scrimmage_callback)

        for (player_1_name, player_2_name) in matches:
            player_1 = players_map[player_1_name]
            player_2 = players_map[player_2_name]

            match = Match(
                game_engine_name=ranked_scrimmage.game_engine_name,
                num_players=2,
                user_submissions=[player_1.user_info, player_2.user_info],
            )

            currMatch = MatchRunner(
                match,
                next(self.match_counter),
                self.match_runner_config,
                self.tango,
                self.s3_resource,
                self.dynamodb_resource,
                f"scrimmage_callback/{self.scrimmage_id}",
                MatchType.RANKED,
                game_map=self.game_map_chooser(),
            )

            # this function will be called by run_scrimmage_callback when it is done
            post_match_callback = functools.partial(
                post_tango_callback, player_1, player_2, currMatch.match_id
            )
            self.scrimmage_entry.register(currMatch.match_id, post_match_callback)
            currMatch.sendJob()
