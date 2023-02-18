import os
from threading import Lock, Semaphore, Thread
from typing import Callable
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


class PostRankedMatchCallback:
    net_elo_changes: dict[str, int]
    net_elo_changes_mutex: Lock
    player_1: MatchPlayer
    player_2: MatchPlayer
    match_id: int
    storageHandler: StorageHandler

    def __init__(
        self,
        net_elo_changes: dict[str, int],
        net_elo_changes_mutex: Lock,
        player_1: MatchPlayer,
        player_2: MatchPlayer,
        match_id: int,
        storageHandler: StorageHandler,
    ) -> None:
        self.net_elo_changes = net_elo_changes
        self.net_elo_changes_mutex = net_elo_changes_mutex
        self.player_1 = player_1
        self.player_2 = player_2
        self.match_id = match_id
        self.storageHandler = storageHandler

    def __call__(self, winner: int, replay_filename: str) -> None:
        winner_is_player_1 = winner == 1
        (player_1_change, player_2_change) = Elo.calc_elo_change(
            self.player_1.rating, self.player_2.rating, winner_is_player_1
        )
        with self.net_elo_changes_mutex:
            self.net_elo_changes[self.player_1.user_info.username] += player_1_change
            self.net_elo_changes[self.player_2.user_info.username] += player_2_change
        self.storageHandler.update_finished_match_in_table(
            MatchTableSchema(
                self.match_id,
                outcome="team1" if winner_is_player_1 else "team2",
                replay_filename=replay_filename,
                elo_change=abs(player_1_change),
                replay_url=self.storageHandler.get_replay_url(replay_filename),
            )
        )


class OngoingRankedMatchTable:
    semaphore: Semaphore
    post_match_callbacks: dict[int, Callable[[int, str], None]]

    def __init__(self) -> None:
        self.restart()

    def register(self, match_id: int, callback: PostRankedMatchCallback) -> None:
        self.post_match_callbacks[match_id] = callback

    def restart(self):
        self.semaphore = Semaphore(0)
        self.post_match_callbacks = {}

    def __call__(self, match_id: int, winner: int, replay_name: str) -> None:
        if winner > 0:
            self.post_match_callbacks[match_id](winner, replay_name)
        self.semaphore.release()


class RankedGameRunner:
    # number of matches each team participates in; should be even number and less than total number of teams
    num_matches = 4
    game_map_chooser: Callable[[], str]

    def __init__(
        self,
        dynamodb_resource,
        match_counter: AtomicCounter,
        scrimmage_id,
        scrimmage_entry: OngoingRankedMatchTable,
        match_runner_config,
        tango,
        s3_resource,
        game_map_chooser: Callable[[], str],
    ):
        self.scrimmage_id = scrimmage_id
        self.match_counter = match_counter

        # used to set the callbacks post-match
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
        net_elo_changes_mutex = Lock()

        for scrimmage_player in scrimmage_players:
            players_map[scrimmage_player.user_info.username] = scrimmage_player
            net_elo_changes[scrimmage_player.user_info.username] = 0

        print("running the following matches: ")
        print(matches)

        # run the matches
        storageHandler = StorageHandler(
            s3_resource=self.s3_resource, dynamodb_resource=self.dynamodb_resource
        )

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

            # this will be called by run_scrimmage_callback when it is done
            post_match_callback = PostRankedMatchCallback(
                net_elo_changes,
                net_elo_changes_mutex,
                player_1,
                player_2,
                currMatch.match_id,
                storageHandler,
            )
            self.scrimmage_entry.register(currMatch.match_id, post_match_callback)
            currMatch.sendJob()

        print("waiting for matches to finish")

        for _ in matches:
            self.scrimmage_entry.semaphore.acquire()

        # apply all the changes in net_elo_changes
        updated_elos = {}
        for key, value in net_elo_changes.items():
            updated_elos[key] = players_map[key].rating + value
        print("completed scrimmage with following final elos: ")
        print(updated_elos)
        storageHandler.adjust_elo_table(updated_elos)
