header = "====== BEGIN REPLAY HERE ======"


def parse_tango_output(file: bytes):
    decoded = file.decode("utf-8")
    lines = decoded.splitlines()
    for i, line in enumerate(lines):
        if line == header:
            return lines[i + 1]

    raise Exception("bad replay")
