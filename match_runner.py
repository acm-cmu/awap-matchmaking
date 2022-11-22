import tempfile
import requests
import boto3
import random
import os.path


class MatchRunner:
    def __init__(self, match, match_runner_config, bucket):
        self.match = match
        self.match_runner_config = match_runner_config
        self.bucket = bucket

    def sendJob(self):
        """
        Send the job to the match runner by calling Tango API
        You would likely need to download the user submissions from the remote location
        and then send it together with the game engine to the match runner

        You will likely need the requests library to call the Tango API
        Tango API https://docs.autolabproject.com/tango-rest/
        """
        with tempfile.TemporaryDirectory() as tempdir:
            for i, userSubmission in enumerate(self.match.user_submissions):
                localBotLocation = os.path.join(tempdir, f"team{i}.py")
                self.bucket.download_file(
                    Filename=localBotLocation, Key=userSubmission.remote_location
                )

        # TODO: send bot to Tango, delete local file

        return {True, 16}
