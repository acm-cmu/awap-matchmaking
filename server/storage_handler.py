import os
import json
import tempfile
from datetime import datetime
from .match_runner import MatchCallback

# suggestion on having a storage handler class
# possible also handle transactions with the database
class StorageHandler:
    def __init__(self, s3_resource):
        self.s3 = s3_resource

    def upload_replay(self, match_replay_obj: MatchCallback):
        dest_filename = f"match-{match_replay_obj.team_name_1}-{match_replay_obj.team_name_2}-{datetime.now().isoformat()}.json"
        match_replay_json = json.dumps(match_replay_obj.game_replay)

        with tempfile.TemporaryDirectory() as tempdir:
            local_path = os.path.join(tempdir, dest_filename)
            with open(local_path, "w") as outfile:
                outfile.write(match_replay_json)
            self.s3.upload_file(
                local_path, os.environ["AWS_REPLAY_BUCKET_NAME"], dest_filename
            )

    def upload_other_stuff():
        raise NotImplementedError
