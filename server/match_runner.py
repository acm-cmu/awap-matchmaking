from enum import Enum
import json
import random
import tempfile
import time
from fastapi import HTTPException
from pydantic import BaseModel
import requests
import boto3
import os
import requests.exceptions as reqexc
from typing import Any, Optional

from server.tango import TangoInterface
from server.storage_handler import StorageHandler, MatchTableSchema

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


class MatchPlayer:
    user_info: UserSubmission
    rating: int

    def __init__(self, user_info: UserSubmission, rating: int):
        self.user_info = user_info
        self.rating = rating


class MatchType(Enum):
    UNRANKED = 1
    RANKED = 2
    TOURNAMENT = 3


class MatchRunner:
    match: Match
    tango: TangoInterface
    match_type: MatchType

    files_param: list

    callback_endpoint: str
    game_map: str

    def __init__(
        self,
        match: Match,
        match_id: int,
        match_runner_config: dict,
        tango: TangoInterface,
        s3_resource,
        dynamodb_resource,
        callback_endpoint,
        match_type: MatchType,
        game_map: str,
    ):
        self.match = match
        self.s3 = s3_resource
        self.dynamodb_resource = dynamodb_resource
        self.match_id = match_id

        self.files_param = [
            match_runner_config.get("makefile"),
            match_runner_config.get("engine"),
        ]

        self.tango = tango
        self.fastapi_host = match_runner_config["fastapi_host"]
        self.callback_endpoint = callback_endpoint
        self.match_type = match_type
        self.game_map = game_map

    def uploadFile(self, pathname: str) -> dict[str, str]:
        filename = pathname.split("/")[-1]
        with_id = f"{self.match_id}-{filename}"
        return self.tango.upload_file(pathname, with_id, filename)

    def sendJob(self):
        """
        Send the job to the match runner by calling Tango API
        You would likely need to download the user submissions from the remote location
        and then send it together with the game engine to the match runner

        You will likely need the requests library to call the Tango API
        Tango API https://docs.autolabproject.com/tango-rest/
        """
        with tempfile.TemporaryDirectory() as tempdir:
            for i, submission in enumerate(self.match.user_submissions):
                local_path = os.path.join(tempdir, f"team{i+1}.py")
                self.s3.download_file(
                    submission.s3_bucket_name, submission.s3_object_name, local_path
                )
                self.files_param.append(self.uploadFile(local_path))

            config_path = os.path.join(tempdir, "config.json")
            config = dict(
                map=self.game_map,
                red_bot="team1",
                blue_bot="team2",
            )

            with open(config_path, "w", encoding="utf-8") as config_f:
                json.dump(config, config_f)

            self.files_param.append(self.uploadFile(config_path))

        callback_url = f"{self.fastapi_host}/{self.callback_endpoint}/{self.match_id}"
        output_file = f"output-{self.match_id}.json"

        # insert pending job into match table and return job id
        storageHandler = StorageHandler(dynamodb_resource=self.dynamodb_resource)
        storageHandler.insert_pending_match_into_table(
            MatchTableSchema(
                self.match_id,
                team_1=self.match.user_submissions[0].username,
                team_2=self.match.user_submissions[1].username,
                match_type=self.match_type.name.lower(),
                map_name=self.game_map,
            )
        )

        return self.tango.add_job(
            str(self.match_id),
            self.files_param,
            output_file,
            callback_url,
        )

    @staticmethod
    def get_match_players_info(dynamodb_table, players: list[UserSubmission]):
        table_username_key = "team_name"
        table_rating_column_name = "current_rating"

        match_player_info = []
        for user in players:
            try:
                currPlayer = MatchPlayer(
                    user,
                    dynamodb_table.get_item(Key={table_username_key: user.username})[
                        "Item"
                    ][table_rating_column_name],
                )
                match_player_info.append(currPlayer)
            except:
                # the specified user is not in the database
                print(f"{user.username} rating info could not be found")
                pass
        match_player_info = sorted(
            match_player_info, key=lambda x: x.rating, reverse=True
        )
        return match_player_info
