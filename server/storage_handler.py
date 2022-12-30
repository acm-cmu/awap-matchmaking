import os
import json
import tempfile
from datetime import datetime

# suggestion on having a storage handler class
# possible also handle transactions with the database
class StorageHandler:
    def __init__(self, s3_resource):
        self.s3 = s3_resource

    def upload_replay(self, match_id: int, replay_file: bytes):

        dest_filename = f"scrimmage-{match_id}.json"

        # write to a temporary local file, then upload to s3
        with tempfile.TemporaryDirectory() as tempdir:
            local_path = os.path.join(tempdir, dest_filename)
            with open(local_path, "w") as outfile:
                outfile.write(replay_file.decode("utf-8"))
            self.s3.upload_file(
                local_path, os.environ["AWS_REPLAY_BUCKET_NAME"], dest_filename
            )

    def upload_other_stuff():
        raise NotImplementedError
