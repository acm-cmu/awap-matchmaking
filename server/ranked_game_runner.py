# used to run ranked scrimamge matches
# you would likely need spin up a python worker thread to run the matches
import os
from threading import Thread
from pydantic import BaseModel
from server.match_runner import UserSubmission, MatchRunner, Match, MatchPlayer
from server.storage_handler import StorageHandler


class RankedScrimmages(BaseModel):
    user_submissions: list[UserSubmission]
    game_engine_name: str


class Elo:
    k = 20

    def calc_expected_score(first_elo: int, second_elo: int):
        return 1 / (1 + pow(10, (second_elo - first_elo) / 400))

    # returns tuple representing (change to first team's elo, change to second team's elo)
    def calc_elo_change(first_elo: int, second_elo: int, first_team_won: bool):
        score = 1 if first_team_won else 0
        expected_score = Elo.calc_expected_score(first_elo, second_elo)
        rating_change = int(Elo.k * (score - expected_score))
        return (rating_change, -1 * rating_change)


class RankedGameRunner:
    # number of matches each team participates in; should be even number and less than total number of teams
    num_matches = 4

    def __init__(
        self,
        dynamodb_resource,
        scrimmage_id,
        ongoing_batch_match_runners,
        match_runner_config,
        tango,
        s3_resource,
    ):
        self.player_table = dynamodb_resource.Table(os.environ["AWS_PLAYER_TABLE_NAME"])
        self.scrimmage_id = scrimmage_id

        # global table mapping: scrimmage -> match_id -> winner
        # the match callback will update this map, and the scrimmage will wait until the matchid appears in the dict
        self.ongoing_batch_match_runners = ongoing_batch_match_runners
        ongoing_batch_match_runners[scrimmage_id] = {}

        # used for the matchrunner
        self.match_runner_config = match_runner_config
        self.tango = tango
        self.s3_resource = s3_resource

    def run_ranked_scrimmage(self, ranked_scrimmage: RankedScrimmages):
        if len(ranked_scrimmage.user_submissions) < RankedGameRunner.num_matches:
            # TODO: handle error
            return ""

        # get the ratings of the users specified in the list
        scrimmage_players = MatchRunner.get_match_players_info(
            self.player_table, ranked_scrimmage.user_submissions
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
        matches = set({})
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

        players_map = {}
        net_elo_changes = {}
        for scrimmage_player in scrimmage_players:
            players_map[scrimmage_player.user_info.username] = scrimmage_player
            net_elo_changes[scrimmage_player.user_info.username] = 0

        print("running the following matches: ")
        print(matches)

        # run the matches
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
                self.match_runner_config,
                self.tango,
                self.s3_resource,
                f"scrimmage_callback/{self.scrimmage_id}",
            )
            currMatch.sendJob()

            # wait for the match to finish
            while (
                currMatch.match_id
                not in self.ongoing_batch_match_runners[self.scrimmage_id]
            ):
                pass

            # adjust elo according to winner
            # TODO: assume winner is either 1 or 2 right now
            winner_is_player_1 = (
                self.ongoing_batch_match_runners[self.scrimmage_id][currMatch.match_id]
                == 1
            )
            (player_1_change, player_2_change) = Elo.calc_elo_change(
                player_1.rating, player_2.rating, winner_is_player_1
            )
            net_elo_changes[player_1_name] += player_1_change
            net_elo_changes[player_2_name] += player_2_change

        # apply all the changes in net_elo_changes
        updated_elos = {}
        for key, value in net_elo_changes.items():
            updated_elos[key] = players_map[key].rating + value
        storageHandler = StorageHandler(self.s3_resource)
        storageHandler.adjust_elo_table(self.player_table, updated_elos)

        # tournament has been completed; upload final tournament bracket onto s3
        print("completed scrimmage with following final elos: ")
        print(updated_elos)
        self.ongoing_batch_match_runners.pop(self.scrimmage_id)
