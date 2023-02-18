import os
import json
import tempfile
from datetime import datetime

from decode_replay import parse_tango_output, parse_failed_output

# fields can sometime be left empty / unused, depending on what fields need to be accessed/updated in database
class MatchTableSchema:
    match_id: int
    team_1: str
    team_2: str
    match_type: str  # [unranked, ranked, tournament]
    status: str  # [pending, finished]
    outcome: str  # [team_1, team_2]
    elo_change: int  # winner receives + elo_change, loser receives - elo_change
    replay_filename: str
    replay_url: str
    map_name: str

    def __init__(
        self,
        match_id,
        team_1="",
        team_2="",
        match_type="",
        status="",
        outcome="",
        replay_filename="",
        elo_change=0,
        map_name="",
        replay_url="",
    ):
        self.match_id = match_id
        self.team_1 = team_1
        self.team_2 = team_2
        self.match_type = match_type
        self.status = status
        self.outcome = outcome
        self.replay_filename = replay_filename
        self.elo_change = elo_change
        self.map_name = map_name
        self.replay_url = replay_url


# class for all logic regarding uploading/downloading files from s3, as well as working with and parsing files
class StorageHandler:
    def __init__(self, s3_resource=None, dynamodb_resource=None):
        self.s3 = s3_resource
        self.dynamodb_resource = dynamodb_resource

    # DEPRECATED
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
        self,
        tournament_id: int,
        tournament_bracket: list[list[dict[str, str | list[str]]]],
    ):
        json_object = json.dumps(tournament_bracket)
        dest_filename = f"tournament_bracket-{tournament_id}.json"

        # write to a temporary local file, then upload to s3
        with tempfile.TemporaryDirectory() as tempdir:
            local_path = os.path.join(tempdir, dest_filename)
            with open(local_path, "w") as outfile:
                outfile.write(json_object)
            self.s3.upload_file(
                local_path, os.environ["AWS_TOURNEY_BUCKET_NAME"], dest_filename
            )

    # DEPRECATED
    def get_winner_from_replay(self, replay_file: bytes):
        result = json.loads(replay_file.decode("utf-8").split("\n")[-2])
        return result["scores"]["Outcome"]

    def process_replay(self, tango_output: bytes, dest_filename: str) -> int:
        """
        Parses the replay file, uploads it and returns the winner
        """
        replay_line = parse_tango_output(tango_output)
        replay = json.loads(replay_line)

        if replay["winner"] == "red":
            winner = 1
        elif replay["winner"] == "blue":
            winner = 2
        else:
            raise Exception("unknown winner")

        with tempfile.NamedTemporaryFile(mode="w") as replay_file:
            replay_file.write(replay_line)
            self.s3.upload_file(
                replay_file.name, os.environ["AWS_REPLAY_BUCKET_NAME"], dest_filename
            )

        return winner

    def process_failed_replay(self, lines: list[str], dest_filename: str) -> int:
        """
        Uploads a failed replay to s3 error bucket
        """
        with tempfile.NamedTemporaryFile(mode="w") as replay_file:
            replay_file.write("\n".join(lines))
            self.s3.upload_file(
                replay_file.name, os.environ["AWS_ERRLOGS_BUCKET_NAME"], dest_filename
            )
        return 0

    def process_failed_binary(self, file: bytes, dest_filename: str):
        with tempfile.NamedTemporaryFile(mode="wb") as replay_file:
            replay_file.write(file)
            self.s3.upload_file(
                replay_file.name, os.environ["AWS_ERRLOGS_BUCKET_NAME"], dest_filename
            )
        return 0

    def get_replay_url(self, dest_filename: str, expiry_seconds: int = 43200) -> str:
        """
        Gets a temporary URL which can be used to access the replay.
        """
        return self.s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": os.environ["AWS_REPLAY_BUCKET_NAME"],
                "Key": dest_filename,
            },
            ExpiresIn=expiry_seconds,
        )

    def get_errlog_url(self, dest_filename: str, expiry_seconds: int = 43200) -> str:
        """
        Gets a temporary URL which can be used to access the replay.
        """
        return self.s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": os.environ["AWS_ERRLOGS_BUCKET_NAME"],
                "Key": dest_filename,
            },
            ExpiresIn=expiry_seconds,
        )

    def adjust_elo_table(self, new_elos: dict[str, int]):
        curr_player_table = self.dynamodb_resource.Table(
            os.environ["AWS_PLAYER_TABLE_NAME"]
        )
        for team_name, new_elo in new_elos.items():
            try:
                curr_player_table.update_item(
                    Key={"team_name": team_name},
                    UpdateExpression="set current_rating=:r",
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
                    "MATCH_TYPE": match_info.match_type,
                    "MATCH_STATUS": "pending",
                    "OUTCOME": "",
                    "REPLAY_FILENAME": "",
                    "REPLAY_URL": "",
                    "ELO_CHANGE": 0,
                    "LAST_UPDATED": datetime.today().isoformat(),
                    "MAP_NAME": match_info.map_name,
                }
            )
        except Exception as e:
            print("issue with inserting pending match into table")
            print(e)

    def update_finished_match_in_table(self, match_info: MatchTableSchema):
        curr_match_table = self.dynamodb_resource.Table(
            os.environ["AWS_MATCH_TABLE_NAME"]
        )
        try:
            curr_match_table.update_item(
                Key={"MATCH_ID": match_info.match_id},
                UpdateExpression="set MATCH_STATUS=:s, OUTCOME=:o, REPLAY_FILENAME=:r, ELO_CHANGE=:e, LAST_UPDATED=:t, REPLAY_URL=:u",
                ExpressionAttributeValues={
                    ":s": "finished",
                    ":o": match_info.outcome,
                    ":r": match_info.replay_filename,
                    ":e": match_info.elo_change,
                    ":t": datetime.today().isoformat(),
                    ":u": match_info.replay_url,
                },
            )
        except Exception as e:
            print("issue with updating pending match to finished in table")
            print(e)

    def update_failed_match_in_table(self, match_info: MatchTableSchema):
        curr_match_table = self.dynamodb_resource.Table(
            os.environ["AWS_MATCH_TABLE_NAME"]
        )
        try:
            curr_match_table.update_item(
                Key={"MATCH_ID": match_info.match_id},
                UpdateExpression="set MATCH_STATUS=:s, LAST_UPDATED=:t, REPLAY_URL=:u",
                ExpressionAttributeValues={
                    ":s": "failed",
                    ":t": datetime.today().isoformat(),
                    ":u": match_info.replay_url,
                },
            )
        except Exception as e:
            print("issue with updating pending match to finished in table")
            print(e)

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
