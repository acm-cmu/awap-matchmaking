import json
from fastapi import HTTPException
import requests
import requests.exceptions as reqexc


class TangoInterface:
    COURSELAB = "awap"

    def __init__(
        self,
        key: str,
        tango_hostname: str,
        tango_port: str,
    ):
        self.tango_host = f"{tango_hostname}:{tango_port}"
        self.key = key

    def open_courselab(self):
        try:
            response = requests.get(
                f"{self.tango_host}/open/{self.key}/{self.COURSELAB}/"
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
        return response.json()

    def upload_file(
        self, local_path: str, tango_name: str, vm_name: str
    ) -> dict[str, str]:
        try:
            header = {"filename": tango_name}
            with open(local_path, "rb") as file:
                response = requests.post(
                    f"{self.tango_host}/upload/{self.key}/{self.COURSELAB}/",
                    data=file.read(),
                    headers=header,
                )
                response.raise_for_status()
        except reqexc.HTTPError as exc:
            raise HTTPException(
                status_code=500, detail="Could not connect to Tango"
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not read file to upload: {exc.strerror}"
            ) from exc

        return {"localFile": tango_name, "destFile": vm_name}

    def add_job(
        self,
        jobname: str,
        files: list[dict[str, str]],
        output_filename: str,
        callback_url: str,
    ):
        try:
            request_obj = {
                "image": "awap_image",
                "jobName": jobname,
                "files": files,
                "output_file": output_filename,
                "callback_url": callback_url,
                "timeout": 30,
            }

            response = requests.post(
                f"{self.tango_host}/addJob/{self.key}/{self.COURSELAB}/",
                data=json.dumps(request_obj),
            )
            response.raise_for_status()
        except reqexc.HTTPError as exc:
            raise HTTPException(
                status_code=500, detail="Error connecting to Tango"
            ) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=exc.strerror) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return response.json()
