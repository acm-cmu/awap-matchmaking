import os
import json
import tempfile
from datetime import datetime

# class for all logic regarding uploading/downloading files from s3, as well as working with and parsing files
class StorageHandler:
    def __init__(self, s3_resource):
        self.s3 = s3_resource

    def upload_replay(
        self, match_id: int, replay_file: bytes, dest_filename_prefix: str
    ):

        dest_filename = f"{dest_filename_prefix}-{match_id}.json"

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

    @staticmethod
    def adjust_elo_table(dynamodb_table, new_elos: dict[str, int]):
        for team_name, new_elo in new_elos.items():
            try:
                dynamodb_table.update_item(
                    Key={"tid": team_name},
                    UpdateExpression="set RATING=:r",
                    ExpressionAttributeValues={":r": new_elo},
                )
            except Exception as e:
                print("issue with updating rating")
                print(e)
                pass

    def upload_other_stuff():
        raise NotImplementedError
