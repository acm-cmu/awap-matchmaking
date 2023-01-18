import os
import math
from threading import Thread
from pydantic import BaseModel
from server.match_runner import UserSubmission, MatchRunner, Match, MatchPlayer
from server.storage_handler import StorageHandler, MatchTableSchema


class Tournament(BaseModel):
    user_submissions: list[UserSubmission]
    game_engine_name: str
    num_tournament_spots: int


class TournamentRunner:
    def __init__(
        self,
        dynamodb_resource,
        tournament_id,
        ongoing_batch_match_runners,
        match_runner_config,
        tango,
        s3_resource,
    ):
        self.tournament_id = tournament_id

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
        self, tournament: Tournament, tournament_players: list[MatchPlayer]
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

                # TODO: assume Bo5 for now
                player1Wins = 0
                player2Wins = 0
                replayLocations = []

                while player1Wins < 3 and player2Wins < 3:
                    match = Match(
                        game_engine_name=tournament.game_engine_name,
                        num_players=2,  # TODO: tournament just assumes 1v1 for now
                        user_submissions=[
                            curr_tournament_layer[i].user_info,
                            curr_tournament_layer[i + 1].user_info,
                        ],
                    )

                    currMatch = MatchRunner(
                        match,
                        self.match_runner_config,
                        self.tango,
                        self.s3_resource,
                        self.dynamodb_resource,
                        f"tournament_callback/{self.tournament_id}",
                        "tournament",
                    )
                    currMatch.sendJob()

                    # wait for the match to finish
                    while (
                        currMatch.match_id
                        not in self.ongoing_batch_match_runners_table[
                            self.tournament_id
                        ]
                    ):
                        pass

                    # TODO: assume winner is either 1 or 2, so winner_id either 0 or 1
                    winner_id = self.ongoing_batch_match_runners_table[
                        self.tournament_id
                    ][currMatch.match_id]
                    if winner_id == 1:
                        player1Wins += 1
                    else:
                        player2Wins += 1

                    replayLocations.append(f"tournament-{currMatch.match_id}.json")

                    # update match table with finished match results
                    storageHandler.update_finished_match_in_table(
                        MatchTableSchema(
                            currMatch.match_id,
                            outcome="team1" if player1Wins else "team2",
                            replay_filename=f"tournament-{currMatch.match_id}.json",
                        ),
                    )

                # add the winner to the next tournament layer
                winner_id = 0 if player1Wins == 3 else 1
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

        # tournament has been completed; upload final tournament bracket onto s3
        print("completed tournament with following bracket: ")
        print(complete_tournament_results)
        storageHandler = StorageHandler(s3_resource=self.s3_resource)
        storageHandler.upload_tournament_bracket(
            self.tournament_id, complete_tournament_results
        )
        self.ongoing_batch_match_runners_table.pop(self.tournament_id)
