# used to run ranked scrimamge matches
# you would likely need spin up a python worker thread to run the matches
import os
from pydantic import BaseModel
from server.match_runner import UserSubmission


class RankedScrimmages(BaseModel):
    user_submissions: list[UserSubmission]
    game_engine_name: str


class ScrimamgePlayer:
    user_info: UserSubmission
    rating: int

    def __init__(self, user_info: UserSubmission, rating: int):
        self.user_info = user_info
        self.rating = rating


class RankedGameRunner:
    def __init__(self, dynamodb_resource):
        self.player_table = dynamodb_resource.Table(os.environ["AWS_PLAYER_TABLE_NAME"])

    def run_round_robin(self, players: RankedScrimmages):
        # get the ratings of the users specified in the list
        scrimmage_players = []
        for user in players.user_submissions:
            try:
                currPlayer = ScrimamgePlayer(
                    user,
                    self.player_table.get_item(Key={"TEAM_NAME": user.username})[
                        "Item"
                    ]["RATING"],
                )
                scrimmage_players.append(currPlayer)
            except:
                # the specified user is not in the database
                pass
        scrimmage_players = sorted(scrimmage_players, key=lambda x: x.rating)
        print(scrimmage_players)

        # run matches with surrounding ~4 teams and adjust elo
        pass
