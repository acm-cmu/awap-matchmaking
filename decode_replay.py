import sys


header = "====== BEGIN REPLAY HERE ======"


class FailedReplayException(Exception):
    lines: list[str]

    def __init__(self, lines: list[str]) -> None:
        super().__init__()
        self.lines = lines


def parse_tango_output(file: bytes):
    decoded = file.decode("utf-8")
    lines = decoded.splitlines()
    for i, line in enumerate(lines):
        if line == header:
            return lines[i + 1]

    raise FailedReplayException(lines)


def normalize_output(winner: int, filename: str) -> tuple[int, str]:
    if winner < 0:
        print("Won by default", file=sys.stderr)
        return (-winner, "")
    return (winner, filename)


def parse_failed_output(file: bytes):
    return file.decode("utf-8")


def make_errlog_name(filename: str):
    return "failed-" + filename.replace("awap23r", "log")


def handle_exception(exc: Exception, storageHandler, filename, file):
    if isinstance(exc, FailedReplayException):
        storageHandler.process_failed_replay(exc.lines, filename)
        print(exc.lines, file=sys.stderr)
    else:
        storageHandler.process_failed_binary(file, filename)
        print(file.decode(), file=sys.stderr)
    print(str(exc), file=sys.stderr)
    return storageHandler.get_errlog_url(filename)
