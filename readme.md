

## Setup

This project uses python3 and virtualenv to manage a separate python environment

1. Install virtualenv
` pip install virtualenv `

2. Ensure it is installed by
`virtualenv --version`

3. Activate vritualenv
`source matchmaking/bin/activate`

4. Install requirements of this project
`pip install -r requirements.txt`

To deactivate the virtualenv, just use `deactivate`.

## Running the server

This project uses [FastAPI](https://fastapi.tiangolo.com/).

To run the server, run the following command in the root directory

`uvicorn main:app --reload`

It should now run on `http://localhost:8000`

You will able to also see an interactive documentation at `http://localhost:8000/docs`
