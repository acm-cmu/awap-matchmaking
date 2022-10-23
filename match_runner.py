import requests


class MatchRunner:
    def __init__(self, match, match_runner_config):
        self.match = match
        self.match_runner_config = match_runner_config

    def sendJob(self):
        """
        Send the job to the match runner by calling Tango API
        You would likely need to download the user submissions from the remote location
        and then send it together with the game engine to the match runner

        You will likely need the requests library to call the Tango API
        Tango API https://docs.autolabproject.com/tango-rest/
        """
        raise NotImplementedError
