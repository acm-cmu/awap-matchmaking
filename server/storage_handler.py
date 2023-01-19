import os
import json
import tempfile
from datetime import datetime

# fields can sometime be left empty / unused, depending on what fields need to be accessed/updated in database
class MatchTableSchema:
    match_id: int
    team_1: str
    team_2: str
    type: str  # [unranked, ranked, tournament]
    status: str  # [pending, finished]
    outcome: str  # [team_1, team_2]
    elo_change: int  # winner receives + elo_change, loser receives - elo_change
    replay_filename: str

    def __init__(
        self,
        match_id,
        team_1="",
        team_2="",
        type="",
        status="",
        outcome="",
        replay_filename="",
        elo_change=0,
    ):
        self.match_id = match_id
        self.team_1 = team_1
        self.team_2 = team_2
        self.type = type
        self.status = status
        self.outcome = outcome
        self.replay_filename = replay_filename
        self.elo_change = elo_change


# class for all logic regarding uploading/downloading files from s3, as well as working with and parsing files
class StorageHandler:
    def __init__(self, s3_resource=None, dynamodb_resource=None):
        self.s3 = s3_resource
        self.dynamodb_resource = dynamodb_resource

    def upload_replay(self, dest_filename: str, replay_file: bytes):
        # write to a temporary local file, then upload to s3
        with tempfile.TemporaryDirectory() as tempdir:
            local_path = os.path.join(tempdir, dest_filename)
            with open(local_path, "w") as outfile:
                outfile.write(replay_file.decode("utf-8"))
            self.s3.upload_file(
                local_path, os.environ["AWS_REPLAY_BUCKET_NAME"], dest_filename
            )

    def upload_tournament_bracket(
        self, tournament_id: int, tournament_bracket: list[list[dict[str, str]]]
    ):
        json_object = json.dumps(tournament_bracket)
        dest_filename = f"tournament_bracket-{tournament_id}.json"

        # write to a temporary local file, then upload to s3
        with tempfile.TemporaryDirectory() as tempdir:
            local_path = os.path.join(tempdir, dest_filename)
            with open(local_path, "w") as outfile:
                outfile.write(json_object)
            self.s3.upload_file(
                local_path, os.environ["AWS_REPLAY_BUCKET_NAME"], dest_filename
            )

    # TODO: properly implement
    # should also correctly parse results for bots that throw errors, etc.
    def get_winner_from_replay(self, replay_file: bytes):
        result = json.loads(replay_file.decode("utf-8").split("\n")[-2])
        return result["scores"]["Outcome"]

    def adjust_elo_table(self, new_elos: dict[str, int]):
        curr_player_table = self.dynamodb_resource.Table(
            os.environ["AWS_PLAYER_TABLE_NAME"]
        )
        for team_name, new_elo in new_elos.items():
            try:
                curr_player_table.update_item(
                    Key={"tid": team_name},
                    UpdateExpression="set RATING=:r",
                    ExpressionAttributeValues={":r": new_elo},
                )
            except Exception as e:
                print("issue with updating rating")
                print(e)
                pass

    def insert_pending_match_into_table(self, match_info: MatchTableSchema):
        curr_match_table = self.dynamodb_resource.Table(
            os.environ["AWS_MATCH_TABLE_NAME"]
        )
        try:
            # TODO: dynamically generate, rather than hard coding?
            curr_match_table.put_item(
                Item={
                    "MATCH_ID": match_info.match_id,
                    "TEAM_1": match_info.team_1,
                    "TEAM_2": match_info.team_2,
                    "MATCH_TYPE": match_info.type,
                    "MATCH_STATUS": "pending",
                    "OUTCOME": "",
                    "REPLAY_FILENAME": "",
                    "ELO_CHANGE": 0,
                }
            )
        except Exception as e:
            print("issue with inserting pending match into table")
            print(e)
            pass

    def update_finished_match_in_table(self, match_info: MatchTableSchema):
        curr_match_table = self.dynamodb_resource.Table(
            os.environ["AWS_MATCH_TABLE_NAME"]
        )
        try:
            curr_match_table.update_item(
                Key={"MATCH_ID": match_info.match_id},
                UpdateExpression="set MATCH_STATUS=:s, OUTCOME=:o, REPLAY_FILENAME=:r, ELO_CHANGE=:e",
                ExpressionAttributeValues={
                    ":s": "finished",
                    ":o": match_info.outcome,
                    ":r": match_info.replay_filename,
                    ":e": match_info.elo_change,
                },
            )
        except Exception as e:
            print("issue with updating pending match to finished in table")
            print(e)
            pass

    def get_next_match_id(self) -> int:
        curr_match_table = self.dynamodb_resource.Table(
            os.environ["AWS_MATCH_TABLE_NAME"]
        )

        entries = curr_match_table.scan(
            Select="SPECIFIC_ATTRIBUTES", ProjectionExpression="MATCH_ID"
        )

        if entries["Count"] == 0:
            return 1

        return 1 + max(x["MATCH_ID"] for x in entries["Items"])
