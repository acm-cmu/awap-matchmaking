# used to run ranked scrimamge matches
# you would likely need spin up a python worker thread to run the matches
import os
from pydantic import BaseModel
from server.match_runner import UserSubmission, MatchRunner


class RankedScrimmages(BaseModel):
    user_submissions: list[UserSubmission]
    game_engine_name: str


class RankedGameRunner:
    def __init__(self, dynamodb_resource):
        self.player_table = dynamodb_resource.Table(os.environ["AWS_PLAYER_TABLE_NAME"])

    def run_round_robin(self, ranked_scrimmage: RankedScrimmages):
        # get the ratings of the users specified in the list
        scrimmage_players = MatchRunner.get_match_players_info(
            self.player_table, ranked_scrimmage.user_submissions
        )
        print(scrimmage_players)

        # run matches with surrounding ~4 teams and adjust elo
        pass
