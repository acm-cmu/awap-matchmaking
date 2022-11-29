from datetime import datetime
import json
import tempfile
import time
from fastapi import HTTPException
from pydantic import BaseModel
import requests
import boto3
import random
import os
import requests.exceptions as reqexc
from typing import Any

from server.game_engine import ENGINE_NAME


COURSE_LAB = "awap"
MAKEFILE = "bots/autograde-Makefile"


class UserSubmission(BaseModel):
    username: str
    s3_bucket_name: str
    s3_object_name: str


class Match(BaseModel):
    game_engine_name: str
    num_players: int
    user_submissions: list[UserSubmission]


class MatchCallback(BaseModel):
    team_name_1: str
    team_name_2: str
    game_replay: list[Any]


class MatchRunner:
    def __init__(self, match: Match, match_runner_config, s3_resource):
        self.match = match
        self.match_runner_config = match_runner_config
        self.s3 = s3_resource

        self.hostname = os.environ["TANGO_HOSTNAME"]
        self.tango_port = os.environ["RESTFUL_PORT"]
        self.key = os.environ["RESTFUL_KEY"]

        self.openCourselab()

    def openCourselab(self):
        try:
            response = requests.get(
                f"{self.hostname}:{self.tango_port}/open/{self.key}/{COURSE_LAB}/"
            )
            response.raise_for_status()
        except reqexc.ConnectionError as exc:
            raise HTTPException(
                status_code=500, detail="Could not connect to Tango"
            ) from exc
        except reqexc.HTTPError as exc:
            raise HTTPException(
                status_code=500, detail=f"Error from tango: {str(exc)}"
            ) from exc

    def uploadFile(self, pathname: str) -> str:
        try:
            filename = pathname.split("/")[-1]
            header = {"Filename": filename}
            with open(pathname, "rb") as file:
                response = requests.post(
                    f"{self.hostname}:{self.tango_port}/upload/{self.key}/{COURSE_LAB}/",
                    data=file.read(),
                    headers=header,
                )
                response.raise_for_status()
                print(response.json())
        except reqexc.HTTPError as exc:
            raise HTTPException(
                status_code=500, detail="Could not connect to Tango"
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not read file to upload: {exc.strerror}"
            ) from exc
        return filename

    def sendJob(self):
        """
        Send the job to the match runner by calling Tango API
        You would likely need to download the user submissions from the remote location
        and then send it together with the game engine to the match runner

        You will likely need the requests library to call the Tango API
        Tango API https://docs.autolabproject.com/tango-rest/
        """
        try:
            with tempfile.TemporaryDirectory() as tempdir:
                match_id = datetime.now().isoformat()

                makefilename = self.uploadFile(MAKEFILE)
                enginename = self.uploadFile("bots/run-match.py")
                files = [
                    {"localFile": makefilename, "destFile": "Makefile"},
                    {"localFile": enginename, "destFile": enginename},
                ]

                for i, submission in enumerate(self.match.user_submissions):
                    local_path = os.path.join(tempdir, f"team{i+1}.py")
                    self.s3.download_file(
                        submission.s3_bucket_name, submission.s3_object_name, local_path
                    )

                    filename = self.uploadFile(local_path)
                    files.append({"localFile": filename, "destFile": filename})

                # TODO add callback url
                requestObj = {
                    "image": "awap_image",
                    "jobName": match_id,
                    "output_file": "output.json",
                    "timeout": 10,
                    "files": files,
                    "callback_url": "http://172.26.71.250:8000/single_match_callback/",
                }

                print(requestObj)

                response = requests.post(
                    f"{self.hostname}:{self.tango_port}/addJob/{self.key}/{COURSE_LAB}/",
                    data=json.dumps(requestObj),
                )
                print(response.json())
                response.raise_for_status()

        except reqexc.HTTPError as exc:
            raise HTTPException(
                status_code=500, detail="Error connecting to Tango"
            ) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=exc.strerror) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
