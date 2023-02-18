import sys


header = "====== BEGIN REPLAY HERE ======"


def parse_tango_output(file: bytes):
    decoded = file.decode("utf-8")
    lines = decoded.splitlines()
    for i, line in enumerate(lines):
        if line == header:
            return lines[i + 1]

    raise Exception("bad replay")


def normalize_output(winner: int, filename: str) -> tuple[int, str]:
    if winner < 0:
        print("Won by default", file=sys.stderr)
        return (-winner, "")
    return (winner, filename)


def parse_failed_output(file: bytes):
    return file.decode("utf-8")
