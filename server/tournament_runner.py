import os
from threading import Thread
from time import time
from typing import Optional
from pydantic import BaseModel
from server.match_runner import (
    MatchType,
    UserSubmission,
    MatchRunner,
    Match,
    MatchPlayer,
)
from server.storage_handler import StorageHandler, MatchTableSchema
from util import AtomicCounter


class Tournament(BaseModel):
    user_submissions: list[UserSubmission]
    game_engine_name: str
    num_tournament_spots: int


class TournamentRunner:
    match_map_order: list[list[str]]

    def __init__(
        self,
        dynamodb_resource,
        match_counter: AtomicCounter,
        tournament_id,
        ongoing_batch_match_runners,
        match_runner_config,
        tango,
        s3_resource,
        match_map_order: list[list[str]],
    ):
        self.tournament_id = tournament_id
        self.match_counter = match_counter

        # global table mapping: tournament_id -> match_id -> winner
        # the match callback will update this map, and the tournament will wait until the matchid appears in the dict
        self.ongoing_batch_match_runners_table = ongoing_batch_match_runners
        self.ongoing_batch_match_runners_table[tournament_id] = {}

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
        tournament_players = tournament_players[: tournament.num_tournament_spots]

        # pad the list of players with byes until power of 2
        while not self.is_pow_two(len(tournament_players)):
            tournament_players.append(None)

        # set up tournament bracket; keep on playing adjacent teams, only keeping the winner
        curr_tournament_layer = []

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
            next_tournament_layer = []
            curr_tournament_layer_results = []
            for i in range(0, len(curr_tournament_layer), 2):
                # play match: curr_tournament_layer[i] vs. curr_tournament_layer[i+1]
                if (
                    curr_tournament_layer[i] is None
                    or curr_tournament_layer[i + 1] is None
                ):
                    actual_player = curr_tournament_layer[i]
                    if actual_player is None:
                        actual_player = curr_tournament_layer[i + 1]

                    curr_tournament_layer_results.append(
                        {
                            "player1": actual_player.user_info.username,
                            "player2": "bye",
                            "winner": actual_player.user_info.username,
                            "replay_filename": "",
                        }
                    )
                    next_tournament_layer.append(actual_player)
                    continue

                # Get number of matches from the number of maps supplied
                layer_maps = self.match_map_order[
                    layer % num_specified_layer_maps
                ]  # we loop back if not enough layers are specified
                print(layer_maps)
                num_matches = len(layer_maps)
                num_wins_req = num_matches // 2 + 1
                print(
                    f"playing up to {num_matches} matches, {num_wins_req} wins requried"
                )

                player1Wins = 0
                player2Wins = 0
                replayLocations = []

                map_num = 0

                while (
                    map_num < num_matches
                    and player1Wins < num_wins_req
                    and player2Wins < num_wins_req
                ):
                    match = Match(
                        game_engine_name=tournament.game_engine_name,
                        num_players=2,  # TODO: tournament just assumes 1v1 for now
                        user_submissions=[
                            curr_tournament_layer[i].user_info,
                            curr_tournament_layer[i + 1].user_info,
                        ],
                    )

                    print("map_num", map_num)

                    currMatch = MatchRunner(
                        match,
                        next(self.match_counter),
                        self.match_runner_config,
                        self.tango,
                        self.s3_resource,
                        self.dynamodb_resource,
                        f"tournament_callback/{self.tournament_id}",
                        MatchType.TOURNAMENT,
                        layer_maps[map_num],
                    )
                    currMatch.sendJob()

                    # wait for the match to finish
                    while (
                        currMatch.match_id
                        not in self.ongoing_batch_match_runners_table[
                            self.tournament_id
                        ]
                    ):
                        time.sleep(1.0)

                    # TODO: assume winner is either 1 or 2, so winner_id either 0 or 1
                    winner_id = self.ongoing_batch_match_runners_table[
                        self.tournament_id
                    ][currMatch.match_id]
                    if winner_id == 1:
                        player1Wins += 1
                    elif winner_id == 2:
                        player2Wins += 1

                    replay_name = f"tournament-{currMatch.match_id}.json"
                    replayLocations.append(replay_name)

                    map_num += 1

                    # update match table with finished match results
                    storageHandler.update_finished_match_in_table(
                        MatchTableSchema(
                            currMatch.match_id,
                            outcome="team1" if player1Wins else "team2",
                            replay_filename=replay_name,
                            replay_url=storageHandler.get_replay_url(replay_name),
                        ),
                    )

                # add the winner to the next tournament layer
                winner_id = 0 if player1Wins > player2Wins else 1
                next_tournament_layer.append(curr_tournament_layer[i + winner_id])

                curr_tournament_layer_results.append(
                    {
                        "player1": curr_tournament_layer[i].user_info.username,
                        "player2": curr_tournament_layer[i + 1].user_info.username,
                        "winner": curr_tournament_layer[
                            i + winner_id
                        ].user_info.username,
                        "replay_filenames": replayLocations,
                    }
                )

            complete_tournament_results.append(curr_tournament_layer_results)
            curr_tournament_layer = next_tournament_layer
            layer += 1

        # tournament has been completed; upload final tournament bracket onto s3
        print("completed tournament with following bracket: ")
        print(complete_tournament_results)
        storageHandler = StorageHandler(s3_resource=self.s3_resource)
        storageHandler.upload_tournament_bracket(
            self.tournament_id, complete_tournament_results
        )
        self.ongoing_batch_match_runners_table.pop(self.tournament_id)
