header = "====== BEGIN REPLAY HERE ======"

red_broken = "===== RED BROKEN ====="
blue_broken = "===== BLUE BROKEN ====="


def parse_tango_output(file: bytes) -> str | int:
    decoded = file.decode("utf-8")
    lines = decoded.splitlines()
    for i, line in enumerate(lines):
        if line == red_broken:
            return -1
        if line == blue_broken:
            return -2
        if line == header:
            return lines[i + 1]

    raise Exception("bad replay")


def normalize_output(winner: int, filename: str) -> tuple[int, str]:
    if winner < 0:
        return (-winner, "")
    return (winner, filename)
