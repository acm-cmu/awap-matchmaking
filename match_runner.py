import requests


class MatchRunner:
    def __init__(self, match, match_runner_config):
        self.match = match
        self.match_runner_config = match_runner_config

    def sendJob(self):
        """
        Send the job to the match runner by calling Tango API
        """
        raise NotImplementedError
