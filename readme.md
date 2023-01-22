# Development Setup

## Matchmaking Server Setup

This project uses python3 and virtualenv to manage a separate python environment

1. Install virtualenv
   `pip install virtualenv`

2. Ensure it is installed by
   `virtualenv --version`

3. Create the virtual environment
   `python3 -m venv matchmaking`

4. Activate virtualenv
   `source matchmaking/bin/activate`

5. Install requirements of this project
   `pip install -r requirements.txt`

6. (Development) Install pre-commit message
   `pre-commit install`

7. Configure the environment variables
   `cp .env.template .env`

   Edit `.env` in your favorite text editor. The following keys should be changed:

   - `RESTFUL_KEY`

     This is key used for Tango. It can arbitrarily be set to "test" during testing, but will need to be changed for production.

   - `TANGO_HOSTNAME`

     This is your computer's ip address (see below). For local testing, it should take the form "http://[your ip address]"

   - `AWS_CLIENT_KEY`

     This is the AWS access key ID. You can find this in the permissions file on Google Drive.

   - `AWS_CLIENT_SECRET`

     This is the secret AWS access key. This can also be found in the permissions file on Google Drive.

   - `AWS_REPLAY_BUCKET_NAME`

     This is the folder on s3 that replays are uploaded into.

   - `AWS_PLAYER_TABLE_NAME`

     This is the table on dynamoDB on AWS that stores player info (team name, rating, ...)

   The other settings may be edited as necessary.

8. Enable permissions to run the FastAPI script.

   `chmod +x ./run-fastapi.sh`

To deactivate the virtualenv, just use `deactivate`.

## Running the matchmaking server

This project uses [FastAPI](https://fastapi.tiangolo.com/).

After following the above steps, to run the server:

1. Determine your IP address by running the `ip address` (or `ipconfig getifaddr en0` on Mac) command. Set this as the `TANGO_HOSTNAME` in the .env file.

2. Run the following command with the appropriate arguments:

`./run-fastapi.sh IP_ADDRESS [PORT]`

where `PORT` should be a port number (if omitted, it will default to `8000`).

It should now run on `http://0.0.0.0:8000`.

You will able to also see an interactive documentation at `http://0.0.0.0:8000/docs`

## Running the Tango containerized server

We are currently using docker to help containerize Tango (&redis). You will need to get [docker](https://docs.docker.com/get-docker/)

1. Make sure to pull the Tango submodule
   `git pull --recurse-submodules`
   `git submodule update --init --recursive`

2. Initialize configuration files (if you already have a .env file, you can comment out that line from the Makefile)
   `make`

3. Update the tango host address to your local volume. You can do this by going to the Tango folder, right clicking on volume and then `Copy Path` to `.env` (if this folder does not exist, then you may have to run the following docker commands in steps 5-7 once first)
   `DOCKER_TANGO_HOST_VOLUME_PATH=/home/ec2-user/autolab-docker/Tango/volumes`

4. Create awap_image. This is the virtual container image, `awap_image`, that we spin up to run game matches.
   `docker build --no-cache -t awap_image ./vmms`

5. Spin up tango services
   `docker compose up`

6. Check if the services are running sucessfully by visiting `localhost:3000`

7. In the future, you only need to do step 5 to get the services up. You can also run the services from the images section of your Docker Dashboard.

# Usage

Run the matchmaking server using the two commands in two different terminals, as mentioned above:

`docker compose up`

`./run-fastapi.sh IP_ADDRESS [PORT]`

The first step is to set the game engine. Upload the game engine file to S3. Select the file, click on "actions", then "share with presigned URL", and specify some time. Do the same thing with the game engine makefile. Then, hit the following endpoint to set the game engine:

```
POST: http://localhost:8000/game_engine
body:
{
    "game_engine_name": [name for game engine (arbitrarily set)],
    "engine_filename": [filename to be downloaded from s3],
    "engine_download_url": [presigned URL from above],
    "makefile_filename": [filename for makefile on s3],
    "makefile_download_url: [presigned URL from above],
    "num_players": [number of players for each game]
}
```

The makefile should contain the commands used to run the game. It is executed every time a match is run (so it should include commands such as `python3 run_game_file.py`, for example). An example game `simple.py` and `autograde-Makefile` are on S3 (maybe).

If there are multiple files in the game engine, then maybe you should tar the game engine file and include an untarring command in the makefile. ?

Now, you can run a single scrimmage match between two teams using the following endpoint:

```
POST: http://localhost:8000/match
body:
{
    "game_engine_name": [name specified in previous step],
    "num_players": 2,
    "user_submissions": [
         {
            "username": [first team name],
            "s3_bucket_name": [S3 bucket bot is located in],
            "s3_object_name": [bot filename in S3 bucket]
         },
         {
            "username": [second team name],
            "s3_bucket_name": [S3 bucket bot is located in],
            "s3_object_name": [bot filename in S3 bucket]
        },
    ]
}
```

This will upload the bots to Tango and run the commands in the makefile. The autograder output gets uploaded to S3 in the replays bucket specified by `AWS_REPLAY_BUCKET_NAME` in the .env file.

You can run a tournament using the following endpoint:

```
POST: http://localhost:8000/tournament
body:
{
   "game_engine_name": "name specified in previous step",
   "user_submissions": [
        {
            "username": "testteam1",
            "s3_bucket_name": "awap23-bots",
            "s3_object_name": "bot1.py"
        },
        {
            "username": "testteam2",
            "s3_bucket_name": "awap23-bots",
            "s3_object_name": "bot2.py"
        },
        {
            "username": "testteam3",
            "s3_bucket_name": "awap23-bots",
            "s3_object_name": "bot3.py"
        }
        ...
    ]
}
```

The endpoint will lookup the usernames on the database and seed the teams in a bracket according to their rating. It will place the replay files into the replay bucket with the filename `tournament-[match-id].json`. It will also upload a summary of the tournament bracket into the replay bucket with the filename `tournament_bracket-[tournament_id].json`. The tournament summary might take the following format for a bracket with 3 teams, for example:

```
[
   [
      {'player1': 'testteam1', 'player2': 'bye', 'winner': 'testteam1', 'replay_filename': ''},
      {'player1': 'testteam2', 'player2': 'testteam3', 'winner': 'testteam2', 'replay_filename': 'tournament-1672697344770654000.json'}
   ],
   [
      {'player1': 'testteam1', 'player2': 'testteam2', 'winner': 'testteam1', 'replay_filename': 'tournament-1672697348070820000.json'}
   ]
]
```

Similarly, you can run a batch of ranked scrimmages using the following endpoint:

```
POST: http://localhost:8000/scrimmage
body:
{
   "game_engine_name": "name specified in previous step",
   "num_tournament_spots": 8,
   "user_submissions": [
        {
            "username": "testteam1",
            "s3_bucket_name": "awap23-bots",
            "s3_object_name": "bot1.py"
        },
        {
            "username": "testteam2",
            "s3_bucket_name": "awap23-bots",
            "s3_object_name": "bot2.py"
        },
        {
            "username": "testteam3",
            "s3_bucket_name": "awap23-bots",
            "s3_object_name": "bot3.py"
        }
        ...
    ]
}
```

The endpoint will lookup the usernames and ratings on the database and run scrimmage matches between teams with similar ratings. It will place the replay files into the replay bucket with the filename `ranked_scrimmage-[match-id].json` and adjust the elo according to winners/losers from each match.

## Deleting old files

To delete all Python files with a leading number in the file name (e.g. `123-team1.py`) **accessed** more than 60 minutes ago in a target directory, run:

```bash
find . -type f -name "[1234567890]*.py" -amin +60 -delete
```

(Replace `-delete` with `-print` to preview the list of files to delete)

For now we don't automatically delete any old submissions on the Docker job manager container.
