

# Development Setup
## Matchmaking Server Setup

This project uses python3 and virtualenv to manage a separate python environment

1. Install virtualenv
` pip install virtualenv `

2. Ensure it is installed by
`virtualenv --version`

3. Activate vritualenv
`source matchmaking/bin/activate`

4. Install requirements of this project
`pip install -r requirements.txt`

5. (Development) Install pre-commit message
`pre-commit install`

To deactivate the virtualenv, just use `deactivate`.

## Running the matchmaking server

This project uses [FastAPI](https://fastapi.tiangolo.com/).

Install required modules:

pip3 install -r ./requirements.txt

To run the server, run the following command in the root directory

`uvicorn main:app --reload`

It should now run on `http://localhost:8000`

You will able to also see an interactive documentation at `http://localhost:8000/docs`

## Running the Tango containerized server

We are currently using docker to help containerize Tango (&redis). You will need to get [docker](https://docs.docker.com/get-docker/)

1. Make sure to pull the Tango submodule
`git pull --recurse-submodules`

2. Initialize configuration files
`make`

3. Update the tango host address to your local volume. You can do this by going to the Tango folder, right clicking on volume and then `Copy Path`
`DOCKER_TANGO_HOST_VOLUME_PATH=/home/ec2-user/autolab-docker/Tango/volumes`

4. Create awap_image. This is the virtual container image, `awap_image`, that we spin up to run game matches.
`docker build --no-cache -t awap_image ./vmms`

5. Spin up tango services
`docker compose up`

6. Check if the services are running sucessfully by visiting `localhost:3000`

7. In the future, you only need to do step 5 to get the services up. You can also run the services from the images section of your Docker Dashboard.
