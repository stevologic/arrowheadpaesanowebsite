"""Karen r6–r11 matrices, generated from her harness scripts (no network)."""
from __future__ import annotations

import copy
import json
import string
from pathlib import Path

from tools.chiefs_narrative import collect, facts

FIXTURES = Path(__file__).resolve().parent / "fixtures"

MW7_TEAMS = (
    ("Miami Dolphins", "MIA", "Dolphins", ["Miami", "Dolphins", "the Dolphins", "Fins"]),
    ("Los Angeles Chargers", "LAC", "Chargers", ["Los Angeles", "Chargers", "the Chargers", "LA", "L.A."]),
    ("New York Jets", "NYJ", "Jets", ["New York", "Jets", "the Jets", "NY", "N.Y."]),
    ("Tampa Bay Buccaneers", "TB", "Buccaneers", ["Tampa Bay", "Buccaneers", "the Buccaneers", "Bucs", "Tampa"]),
    ("Las Vegas Raiders", "LV", "Raiders", ["Las Vegas", "Raiders", "the Raiders", "Vegas"]),
    ("Los Angeles Rams", "LAR", "Rams", ["Los Angeles", "Rams", "LA"]),
)
MW7_FORMS = (
    ("{} was 18 first downs, 88 rush, 246 net pass, 25:39.", "KC-was", True),
    ("{} had 18 first downs and 25:39.", "KC-had", True),
    ("{} held the ball 25:39.", "KC-held", True),
    ("{} was 19 first downs, 119 rush, 210 net pass, 34:21.", "OPP-was", False),
    ("{} had 19 first downs and 34:21.", "OPP-had", False),
    ("{} held the ball 34:21.", "OPP-held", False),
)

MX8_SUBJ = {
    "MIA": [
        ("Miami", "Miami’s"),
        ("the Dolphins", "the Dolphins’"),
        ("Dolphins", "the Dolphins’"),
        ("the Fins", "the Fins’"),
        ("Fins", "the Fins’"),
    ],
    "LV": [
        ("Las Vegas", "Las Vegas’s"),
        ("the Raiders", "the Raiders’"),
        ("Raiders", "the Raiders’"),
        ("Vegas", "Vegas’s"),
    ],
    "LAC": [
        ("Los Angeles", "Los Angeles’s"),
        ("the Chargers", "the Chargers’"),
        ("LA", "LA’s"),
        ("L.A.", "L.A.’s"),
    ],
    "NYJ": [
        ("New York", "New York’s"),
        ("the Jets", "the Jets’"),
        ("NY", "NY’s"),
        ("N.Y.", "N.Y.’s"),
    ],
    "TB": [
        ("Tampa Bay", "Tampa Bay’s"),
        ("the Buccaneers", "the Buccaneers’"),
        ("the Bucs", "the Bucs’"),
        ("Tampa", "Tampa’s"),
        ("Bucs", "the Bucs’"),
    ],
    "LAR": [
        ("Los Angeles", "Los Angeles’s"),
        ("the Rams", "the Rams’"),
        ("LA", "LA’s"),
        ("L.A.", "L.A.’s"),
    ],
}
MX8_OK = {
    "ofd": "19",
    "oclk": "34:21",
    "ory": "119",
    "kfd": "18",
    "kclk": "25:39",
    "kry": "88",
}
MX8_SWAP = {
    "ofd": "18",
    "oclk": "25:39",
    "ory": "88",
    "kfd": "19",
    "kclk": "34:21",
    "kry": "119",
}
MX8_OFF = {
    "ofd": "23",
    "oclk": "31:05",
    "ory": "140",
    "kfd": "22",
    "kclk": "28:55",
    "kry": "101",
}
MX8_TEMPLATES = (
    ("T1", "{S} had {ofd} first downs and {oclk}."),
    ("T2", "{S} finished with {ofd} first downs and {oclk} of possession."),
    ("T3", "{S} had {ofd} first downs, {ory} rushing yards and {oclk}."),
    ("T4", "{S} had {ofd} first downs and {oclk} to the Chiefs’ {kfd} and {kclk}."),
    ("T5", "{S} held the ball {oclk} and Kansas City held it {kclk}."),
    ("T6", "{S} was {ofd} first downs and {oclk}."),
    ("K1", "Kansas City had {kfd} first downs and {kclk}."),
    ("K2", "Kansas City finished with {kfd} first downs and {kclk} of possession."),
    ("K3", "Kansas City had {kfd} first downs, {kry} rushing yards and {kclk}."),
    ("K4", "Kansas City had {kfd} first downs and {kclk} to {P} {ofd} and {oclk}."),
)

T32_TEAMS = {
    "ARI": ("Arizona", "Cardinals"),
    "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"),
    "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"),
    "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"),
    "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"),
    "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"),
    "LAC": ("Los Angeles", "Chargers"),
    "LAR": ("Los Angeles", "Rams"),
    "LV": ("Las Vegas", "Raiders"),
    "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"),
    "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"),
    "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"),
    "SF": ("San Francisco", "49ers"),
    "SEA": ("Seattle", "Seahawks"),
    "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"),
    "WSH": ("Washington", "Commanders"),
}

OVERREACH = (
    ("LV", "Kansas City flies to Las Vegas on Saturday.", False),
    ("LV", "Allegiant Stadium in Las Vegas holds 65,000.", False),
    ("LV", "Kansas City won 27-20 at Allegiant Stadium in Las Vegas.", False),
    ("LV", "Kansas City held the ball 25:39 in Las Vegas.", False),
    ("LV", "Kansas City had 18 first downs in Vegas.", False),
    ("LV", "In Las Vegas, Kansas City had 18 first downs and 25:39.", False),
    ("LV", "In Las Vegas, Kansas City held the ball 34:21.", True),
    ("LV", "The Vegas crowd was loud from the opening kickoff to the 2:00 warning.", False),
    ("LV", "Vegas odds had Kansas City at -2.5 with a 47.5 total.", False),
    ("LV", "Las Vegas is 3-0 and hosts Kansas City.", False),
    ("LV", "The Raiders were 3-0.", False),
    ("MIA", "Kansas City flies to Las Vegas on Saturday.", False),
    ("MIA", "Kansas City flies to Las Vegas on Saturday after holding the ball 25:39 in Miami.", False),
    ("MIA", "Allegiant Stadium in Las Vegas is next after 25:39 of possession in Miami.", False),
    ("MIA", "New York was the last team to beat Kansas City in September.", False),
    ("MIA", "New York had 18 first downs and 25:39.", True),
    ("MIA", "Kansas City had 18 first downs, the same number New York posted last week.", False),
    ("NYJ", "New York Giants fans were in Kansas City for 18 first downs and 25:39.", False),
    ("MIA", "Tampa Bay had 18 first downs and 25:39.", True),
    ("MIA", "Green Bay had 18 first downs and 25:39.", True),
    ("MIA", "Green Bay was 18 first downs, 88 rush, 246 net pass, 25:39.", True),
    ("MIA", "Tampa Bay was 18 first downs, 88 rush, 246 net pass, 25:39.", True),
    ("TB", "Green Bay had 18 first downs and 25:39.", True),
    ("TB", "Green Bay was 19 first downs, 119 rush, 210 net pass, 34:21.", True),
    ("TB", "Tampa Bay had 19 first downs and 34:21.", False),
    ("TB", "Tampa Bay had 18 first downs and 25:39.", True),
    ("LAR", "Los Angeles was 19 first downs, 119 rush, 210 net pass, 34:21.", False),
    ("LAR", "Los Angeles was 18 first downs, 88 rush, 246 net pass, 25:39.", True),
    ("LAR", "Los Angeles held the ball 25:39.", True),
    ("LAR", "The Chargers had 18 first downs and 25:39.", True),
    ("LAR", "The Rams had 19 first downs and 34:21.", False),
    ("LAC", "The Rams were 18 first downs, 88 rush, 246 net pass, 25:39.", True),
    ("LV", "The Raiders had 19 first downs to the Chiefs’ 18.", False),
    ("LV", "The Raiders had 18 first downs to the Chiefs’ 19.", True),
    ("LV", "The Chiefs had 18 first downs to the Raiders’ 19.", False),
    ("LV", "The Raiders had 19 first downs and 34:21 to the Chiefs’ 18 and 25:39.", False),
    ("LV", "The Raiders had 19 first downs and 25:39 to the Chiefs’ 18 and 34:21.", True),
    ("LV", "Vegas had 19 first downs; Kansas City had 18.", False),
    ("LV", "Vegas had 18 first downs; Kansas City had 19.", True),
    ("MIA", "The Dolphins had 19 first downs to the Chiefs’ 18.", False),
    ("MIA", "The Dolphins had 18 first downs to the Chiefs’ 19.", True),
)

OV2 = (
    ("LV", "The Raiders were 18 first downs, 88 rush, 246 net pass, 25:39.", True),
    ("LV", "Raiders were 18 first downs, 88 rush, 246 net pass, 25:39.", True),
    ("LV", "The Raiders were 19 first downs, 119 rush, 210 net pass, 34:21.", False),
    ("MIA", "The Dolphins were 18 first downs, 88 rush, 246 net pass, 25:39.", True),
    ("MIA", "The Raiders were 18 first downs, 88 rush, 246 net pass, 25:39.", True),
    ("LV", "The Raiders held the ball 25:39 to the Chiefs’ 34:21.", True),
    ("LV", "The Raiders held the ball 34:21 to the Chiefs’ 25:39.", False),
    ("LV", "The Chiefs held the ball 34:21 to the Raiders’ 25:39.", True),
    ("LV", "The Chiefs held the ball 25:39 to the Raiders’ 34:21.", False),
    ("LV", "The Raiders had 19 first downs and 25:39 to the Chiefs’ 18 and 34:21.", True),
    ("LV", "Las Vegas had 25:39 of possession to Kansas City’s 34:21.", True),
    ("LV", "The Raiders had 25:39 to Kansas City’s 34:21.", True),
    ("LV", "Kansas City had 34:21 to the Raiders’ 25:39.", True),
    ("MIA", "Miami had 25:39 of possession to Kansas City’s 34:21.", True),
    ("MIA", "The Dolphins had 19 first downs and 25:39 to the Chiefs’ 18 and 34:21.", True),
    ("MIA", "Miami held the ball 25:39 to Kansas City’s 34:21.", True),
)

# pv.py (40) + pv2 (9) + pv3 (6) = 55; plus the two r11 Miami-against extras → 57.
PROBES = (
    ("MIA", "Las Vegas had 22 first downs per game through three weeks.", False),
    ("MIA", "The Raiders had 21 first downs against Denver.", False),
    ("MIA", "Las Vegas had 21 first downs and 31:10 of possession against Denver last week.", False),
    ("MIA", "Las Vegas held the ball 33:12 last week against Denver.", False),
    ("MIA", "Cousins and the Raiders had 24 first downs in their opener.", False),
    ("MIA", "The new offense had 19 first downs and 34:21.", False),
    ("MIA", "Denver had 17 first downs against Kansas City in Week 1.", False),
    ("LV", "Las Vegas had 22 first downs per game through three weeks.", False),
    ("LV", "The Raiders had 21 first downs against Denver.", False),
    ("LV", "Las Vegas had 21 first downs against Denver last week.", False),
    ("LV", "Las Vegas held the ball 33:12 last week against Denver.", False),
    ("LV", "Las Vegas averaged 22 first downs a game coming in, and had 19 on Sunday.", False),
    ("LV", "Las Vegas had 22 first downs per game coming in but only 18 on Sunday.", True),
    ("LV", "Denver had 17 first downs against Kansas City in Week 1.", False),
    ("LV", "Miami had 19 first downs against Kansas City in Week 3.", False),
    ("LV", "Miami had 18 first downs against Kansas City in Week 3.", True),
    ("LV", "Kansas City had 18 first downs in Miami and 18 again in Las Vegas.", False),
    ("LAC", "LA traffic made the Chargers' trip home a slog.", False),
    ("MIA", "LA traffic made the Chargers' trip home a slog.", False),
    ("NYJ", "The NY Times called it the best win of the season.", False),
    ("MIA", "The NY Times had 19 first downs worth of praise for Mahomes.", False),
    ("TB", "Tampa weather pushed kickoff back 45 minutes.", False),
    ("MIA", "Tampa weather pushed kickoff back 45 minutes.", False),
    ("TB", "Bucs fans left early.", False),
    ("MIA", "Bucs fans had 18 first downs of their own to cheer in August.", False),
    ("MIA", "Fins up was the chant at Hard Rock before the 34:21 possession edge.", False),
    ("MIA", "Fins up was the chant at Hard Rock before the 25:39 possession edge.", False),
    ("LAC", "L.A. Confidential is still the best football-free movie about Los Angeles.", False),
    ("LAR", "L.A. Confidential is still the best football-free movie about Los Angeles.", False),
    ("MIA", "The Fins’ 19 first downs were not enough.", False),
    ("MIA", "The Fins’ 18 first downs were not enough.", True),
    ("MIA", "Kansas City held the Fins to 19 first downs and 34:21.", False),
    ("MIA", "Kansas City held the Fins to 18 first downs.", True),
    ("LAC", "LA had 19 first downs and 34:21.", False),
    ("LAR", "LA had 19 first downs and 34:21.", False),
    ("MIA", "LA had 19 first downs and 34:21.", True),
    ("NYJ", "NY had 19 first downs and 34:21.", False),
    ("NYJ", "N.Y. held the ball 34:21.", False),
    ("LV", "The Raiders had 19 first downs and 25:39 to the Chiefs’ 18 and 34:21.", True),
    ("LV", "Kansas City held the ball 34:21 and Las Vegas held the ball 25:39.", True),
    ("LV", "The Raiders had 19 first downs and 25:39 to the Chiefs’ 18 and 34:21.", True),
    ("LV", "The Raiders had 19 first downs and 34:21 to the Chiefs’ 18 and 25:39.", False),
    ("LV", "Kansas City had 18 first downs and 34:21 to the Raiders’ 19 and 25:39.", True),
    ("LV", "Las Vegas had 19 first downs and 25:39 to Kansas City’s 18 and 34:21.", True),
    ("LV", "Las Vegas had 19 first downs and 25:39.", True),
    ("LV", "The Raiders had 19 first downs and 25:39 of possession.", True),
    ("MIA", "Miami had 19 first downs and 25:39 to Kansas City’s 18 and 34:21.", True),
    ("LV", "The Raiders had 21 first downs against Denver.", False),
    ("LV", "Las Vegas had 22 first downs per game through three weeks.", False),
    ("MIA", "Miami had 19 first downs and 25:39.", True),
    ("MIA", "Kansas City had 18 first downs and 34:21.", True),
    ("MIA", "Kansas City had 18 first downs and 25:39.", False),
    ("LV", "Kansas City had 18 first downs and 34:21.", True),
    ("MIA", "Miami finished with 19 first downs and 25:39 of possession.", True),
    ("MIA", "Miami had 19 first downs, 119 rushing yards and 25:39.", True),
    ("MIA", "Miami had 18 first downs against Kansas City.", True),
    ("MIA", "Miami had 18 first downs against the Chiefs.", True),
)

PV10_POSSESSION = (
    ("MIA", "A", "Kansas City burned 5:29 on the drive.", False),
    ("MIA", "A", "Kansas City put together a 10-play, 65-yard, 5:29 drive.", False),
    ("MIA", "A", "Miami had 19 first downs, 329 yards and a 5:29 drive.", False),
    ("MIA", "A", "Kansas City went to halftime at 14-7 with 7:30 of possession in the second quarter.", False),
    ("MIA", "A", "Kansas City had 7:30 of possession in the second quarter.", False),
    ("MIA", "A", "The 3:25 PM CT kickoff at Allegiant is next.", False),
    ("MIA", "A", "Kansas City held the ball 25:39 and Miami 34:21.", False),
    ("MIA", "A", "Kansas City held the ball 34:21 and Miami 25:39.", True),
    ("MIA", "A", "Time of possession: KC 25:39, MIA 34:21.", False),
    ("MIA", "A", "Time of possession: KC 34:21, MIA 25:39.", True),
    ("MIA", "A", "Time of possession: Kansas City 25:39, Miami 34:21.", False),
    ("MIA", "A", "Time of possession: Kansas City 34:21, Miami 25:39.", True),
    ("LV5", "A", "Kansas City burned 5:29 on the drive.", False),
    ("LV5", "A", "Las Vegas had 15 first downs, 275 yards and a 5:29 drive.", False),
    ("LV5", "A", "The 3:25 PM CT kickoff at Allegiant was the Raiders’ last shot.", False),
    ("LV5", "A", "Kansas City held the ball 31:12 and Las Vegas 28:48.", False),
    ("LV5", "A", "Kansas City held the ball 28:48 and Las Vegas 31:12.", True),
    ("LV5", "A", "Time of possession: KC 31:12, LV 28:48.", False),
    ("LV5", "A", "Time of possession: KC 28:48, LV 31:12.", True),
    ("MIA", "P1", "Miami had 19 first downs, 88 rushing yards and 34:21.", True),
    ("MIA", "P1", "Miami had 19 first downs, 119 rushing yards and 34:21.", False),
    ("MIA", "P1", "Kansas City had 18 first downs, 119 rushing yards and 25:39.", True),
    ("MIA", "P1", "Kansas City had 18 first downs, 88 rushing yards and 25:39.", False),
    ("LV5", "P1", "Las Vegas had 15 first downs, 140 rushing yards and 28:48.", True),
    ("LV5", "P1", "Las Vegas had 15 first downs, 95 rushing yards and 28:48.", False),
    ("LV5", "P1", "Kansas City had 22 first downs, 95 rushing yards and 31:12.", True),
    ("LV5", "P1", "Kansas City had 22 first downs, 140 rushing yards and 31:12.", False),
    ("LV5", "P1", "Las Vegas averaged 18 first downs in Week 4.", True),
    ("LV5", "P1", "Las Vegas averaged 15 first downs in Week 4.", False),
    ("MIA", "P1", "Miami averaged 18 first downs in Week 3.", True),
    ("MIA", "P1", "Miami averaged 19 first downs in Week 3.", False),
    ("LV5", "P1", "Las Vegas averaged 22 first downs a game coming in, and had 18 on Sunday.", True),
    ("LV5", "P1", "Las Vegas averaged 22 first downs a game coming in, and had 15 on Sunday.", False),
    ("LV5", "P1", "Las Vegas averaged 22 first downs coming in.", False),
    ("LV5", "P1", "Las Vegas averaged 18 first downs coming in.", False),
    ("LV5", "P1", "Las Vegas averaged 18 first downs per game coming in.", False),
    ("MIA", "P1", "Las Vegas averaged 22 first downs coming in.", False),
    ("LV5", "P1", "The Raiders averaged 22 first downs a game before Sunday and managed 15 against Kansas City.", False),
    ("LV5", "LVR", "Las Vegas ran for 95 yards.", False),
    ("LV5", "LVR", "The Raiders rushed for 95 yards.", False),
    ("LV5", "LVR", "Las Vegas had 95 rushing yards.", False),
    ("LV5", "LVR", "Las Vegas had 15 first downs and 95 rushing yards.", False),
    ("LV5", "LVR", "The Raiders had 15 first downs, 95 rushing yards and 28:48.", False),
    ("LV5", "LVR", "Vegas had 15 first downs, 95 rushing yards and 28:48.", False),
    ("LV5", "LVR", "The Raiders finished with 95 rushing yards and 28:48 of possession.", False),
    ("LV5", "LVR", "Las Vegas ran for 140 yards.", True),
    ("LV5", "LVR", "Kansas City ran for 140 yards and Las Vegas for 95.", False),
    ("LV", "LVR", "Las Vegas had 19 first downs, 119 rushing yards and 34:21.", False),
    ("LV", "LVR", "The Raiders had 119 rushing yards.", False),
    ("MIA", "LVR", "Miami had 19 first downs, 119 rushing yards and 34:21.", False),
    ("MIA", "T5", "Miami held the ball 34:21 and Kansas City held it 28:55.", True),
    ("MIA", "T5", "Miami held the ball 34:21 and Kansas City held it 25:39.", False),
    ("MIA", "T5", "Miami held the ball 34:21 and Kansas City 28:55.", True),
    ("MIA", "T5", "Kansas City held it for 28:55.", True),
    ("MIA", "T5", "Kansas City had the ball for 28:55.", True),
    ("MIA", "T5", "Kansas City had it for 28:55.", True),
    ("MIA", "T5", "Kansas City’s offense was on the field for 28:55.", True),
    ("MIA", "T5", "Kansas City possessed it for 28:55.", True),
    ("LV5", "T5", "Las Vegas held the ball 28:48 and Kansas City held it 30:00.", True),
    ("LV5", "D", "Kansas City put together a 34:21 drive.", True),
    ("LV5", "D", "Kansas City had 34:21 of possession in the second quarter.", True),
    ("MIA", "LVR", "Kansas City ran for 119 yards.", True),
    ("MIA", "LVR", "Kansas City ran for 88 yards.", False),
    ("LV5", "LVR", "The Chiefs rushed for 95 yards.", True),
    ("SEA", "T5", "Time of possession (33:12) and first-down volume (21) that kept Seattle’s offense on the sideline.", False),
    ("SEA", "T5", "Time of possession (33:12) and first-down volume (13) that kept Seattle’s offense on the sideline.", True),
    ("LV5", "T5", "Time of possession (31:12) and first-down volume (22) that kept the Raiders’ offense on the sideline.", False),
    ("LV5", "T5", "Time of possession (31:12) and first-down volume (15) that kept the Raiders’ offense on the sideline.", True),
)

PV11A = (
    ("MIA", "The Miami box is the warning label: 18 first downs, 3-of-7, 25:39, 88 rush yards.", False),
    ("MIA", "The Miami box: 18 first downs, 3-of-7, 25:39 and 88 rush yards.", False),
    ("MIA", "In Miami, Kansas City had 18 first downs, 25:39 and 88 rush yards.", False),
    ("MIA", "The Miami box is the warning label: 18 first downs, 3-of-7, 25:39, 152 rush yards.", True),
    ("MIA", "Walker still got 24 carries and 117 yards against Indianapolis.", False),
    ("MIA", "Mahomes threw for 382 yards against the Colts.", False),
    ("MIA", "Walker carried 18 times for 70 yards; 18 attempts is a feature back's workload.", False),
    ("MIA", "Walker still got the carry volume — 18 attempts is a feature back's workload — but 70 yards is 3.9 a carry.", False),
    ("MIA", "If Las Vegas copies Miami’s 34:21, he will need 35 attempts or a run game that actually exists.", False),
    ("MIA", "Two Miami field goals kept the Dolphins in earshot until Kelce ended it.", True),
    ("MIA", "Kansas City’s 31-10 opener was 220 rush yards and 33:42 of clock.", False),
    ("MIA", "Four sacks in the Denver opener, then a quiet night against Indianapolis.", False),
)

PV11B = (
    ("SEA", "Time of possession (33:12) and first-down volume (21) that kept Seattle’s offense on the sideline.", False),
    ("SEA", "Time of possession (26:48) and first-down volume (21) that kept Seattle’s offense on the sideline.", True),
    ("SEA", "Time of possession (34:12) and first-down volume (21) that kept Seattle’s offense on the sideline.", True),
    ("SEA", "Time of possession (33:12) and first-down volume (13) that kept Seattle’s offense on the sideline.", True),
    ("IND", "Time of possession (37:00) and 29 first downs that kept the Colts' offense on the sideline longer than it wanted", False),
    ("IND", "Time of possession (33:00) and 29 first downs that kept the Colts' offense on the sideline longer than it wanted", True),
    ("IND", "Time of possession (35:00) and 29 first downs that kept the Colts' offense on the sideline longer than it wanted", True),
    ("IND", "Time of possession (37:00) and 24 first downs that kept the Colts' offense on the sideline longer than it wanted", True),
    ("LV5", "Time of possession (31:12) and first-down volume (22) that kept the Raiders’ offense on the sideline.", False),
    ("LV5", "Time of possession (28:48) and first-down volume (22) that kept the Raiders’ offense on the sideline.", True),
    ("LV5", "Time of possession (30:00) and first-down volume (22) that kept the Raiders’ offense on the sideline.", True),
    ("LV5", "Time of possession (31:12) and first-down volume (15) that kept the Raiders’ offense on the sideline.", True),
    ("LV5", "Kansas City’s 31:12 of possession kept the Raiders’ offense on the sideline.", False),
    ("LV5", "The Chiefs held the ball 31:12 and kept Las Vegas’s offense on the sideline.", False),
    ("LV5", "The Chiefs held the ball 28:48 and kept Las Vegas’s offense on the sideline.", True),
    ("LV5", "Time of possession (28:48) kept the Raiders’ offense on the sideline.", True),
    ("LV5", "Las Vegas had 15 first downs, 95 rushing yards and 28:48.", False),
    ("LV5", "Las Vegas had 95 rushing yards.", False),
    ("LV5", "Las Vegas averaged 22 first downs coming in.", False),
    ("LV5", "Kansas City had 95 rushing yards.", True),
    ("LV5", "Kansas City had 22 first downs, 95 rushing yards and 31:12.", True),
    ("LV5", "Las Vegas had 140 rushing yards.", True),
)

CITY_CTX = {
    "MIA": ("Miami Dolphins", "MIA", "Dolphins"),
    "LV": ("Las Vegas Raiders", "LV", "Raiders"),
    "LAR": ("Los Angeles Rams", "LAR", "Rams"),
    "LAC": ("Los Angeles Chargers", "LAC", "Chargers"),
    "NYJ": ("New York Jets", "NYJ", "Jets"),
    "TB": ("Tampa Bay Buccaneers", "TB", "Buccaneers"),
}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def story(*body: str) -> dict:
    return {
        "phase": {"type": "regular"},
        "record": "3-0",
        "storyline": {"body": list(body)},
    }


def what_worked(sentence: str) -> dict:
    return {
        "phase": {"type": "regular"},
        "record": "3-0",
        "lastGameReview": {"whatWorked": [sentence]},
    }


def pipeline(narrative: dict, recap: dict, last: dict, slate: list) -> dict:
    before = copy.deepcopy(narrative)
    issues = facts.check_review(narrative, last, recap, schedule=slate)
    fixed, logs = facts.apply_fact_corrections(
        copy.deepcopy(narrative), issues, recap, last, slate
    )
    leftover = facts.check_review(fixed, last, recap, schedule=slate)
    repaired = (
        facts.repair_offending_copy(fixed, leftover, last, recap)
        if leftover
        else fixed
    )
    before_sents = set(facts._split_sentences(facts.edition_text(before)))
    after_sents = set(facts._split_sentences(facts.edition_text(repaired)))
    return {
        "issues": issues,
        "logs": logs,
        "drops": facts.dropped_sentences(fixed, repaired),
        "removed": sorted(before_sents - after_sents),
        "added": sorted(after_sents - before_sents),
        "changed": bool(logs or leftover),
    }


def mia_box() -> dict:
    return copy.deepcopy(_load("espn_401872952_recap.json"))


def lv5_box() -> dict:
    return copy.deepcopy(_load("espn_lv5_synthetic_recap.json"))


def sea_box() -> dict:
    recap = copy.deepcopy(_load("espn_401873305_recap.json"))
    prior = copy.deepcopy(_load("espn_401873296_recap.json"))
    recap["prior"] = prior
    recap["prior"].setdefault("oppAbbr", prior.get("oppAbbr"))
    return recap


def ind_box() -> dict:
    recap = copy.deepcopy(_load("espn_401872945_recap.json"))
    prior = copy.deepcopy(_load("espn_401872931_recap.json"))
    recap["prior"] = prior
    recap["prior"].setdefault("oppAbbr", prior.get("oppAbbr"))
    return recap


def last_for(name: str, abbr: str, short: str, **extra) -> dict:
    row = {
        "date": "2026-09-27T17:00:00Z",
        "id": "401872952",
        "week": 3,
        "opponent": name,
        "opponentAbbr": abbr,
        "opponentShort": short,
        "completed": True,
        "kcScore": 24,
        "oppScore": 10,
    }
    row.update(extra)
    return row


def relabel_mia(name: str, abbr: str, short: str) -> tuple[dict, dict]:
    recap = mia_box()
    recap.update(oppAbbr=abbr, opponent=name)
    last = last_for(name, abbr, short)
    return recap, last


def ctx(code: str) -> tuple[dict, dict, list]:
    slate = collect.load_cached_schedule()
    if code == "LV5":
        recap = lv5_box()
        last = last_for(
            "Las Vegas Raiders",
            "LV",
            "Raiders",
            id="401872976",
            week=4,
            date="2026-10-04T20:25:00Z",
            kcScore=27,
            oppScore=17,
        )
        slate = copy.deepcopy(slate)
        for game in slate:
            if str(game.get("id")) == "401872976":
                game.update(
                    completed=True,
                    inProgress=False,
                    kcScore=27,
                    oppScore=17,
                )
        return recap, last, slate
    if code == "SEA":
        recap = sea_box()
        last = next(
            (dict(game) for game in slate if str(game.get("id")) == "401873305"),
            last_for(
                "Seattle Seahawks",
                "SEA",
                "Seahawks",
                id="401873305",
                week=4,
                date="2026-08-29T00:00:00Z",
                kcScore=9,
                oppScore=9,
            ),
        )
        return recap, last, slate
    if code == "IND":
        recap = ind_box()
        last = next(
            (dict(game) for game in slate if str(game.get("id")) == "401872945"),
            last_for(
                "Indianapolis Colts",
                "IND",
                "Colts",
                id="401872945",
                week=2,
                date="2026-09-23T00:20:00Z",
                kcScore=33,
                oppScore=30,
            ),
        )
        return recap, last, slate
    name, abbr, short = CITY_CTX[code]
    recap, last = relabel_mia(name, abbr, short)
    return recap, last, slate


def _cap(sentence: str) -> str:
    return sentence[0].upper() + sentence[1:] if sentence else sentence


def mw7_cases() -> list[dict]:
    slate = collect.load_cached_schedule()
    rows = []
    for name, abbr, short, labels in MW7_TEAMS:
        recap, last = relabel_mia(name, abbr, short)
        for lab in labels:
            for tmpl, kind, expect in MW7_FORMS:
                sentence = _cap(tmpl.format(lab))
                rows.append(
                    {
                        "matrix": "mw7",
                        "ctx": abbr,
                        "kind": kind,
                        "sentence": sentence,
                        "expect_flag": expect,
                        "recap": recap,
                        "last": last,
                        "slate": slate,
                    }
                )
    return rows


def _template_fields(tmpl: str) -> list[str]:
    return [
        field
        for _, field, _, _ in string.Formatter().parse(tmpl)
        if field and field not in {"S", "P"}
    ]


def mx8_cases() -> list[dict]:
    rows = []
    for opp, subs in MX8_SUBJ.items():
        recap, last, slate = ctx(opp)
        for tid, tmpl in MX8_TEMPLATES:
            sl = subs if tid[0] == "T" or tid == "K4" else subs[:1]
            for subject, poss in sl:
                fields = _template_fields(tmpl)
                variants = [("correct", None, None)] + [
                    (f"{field}:{kind}", field, kind)
                    for field in fields
                    for kind in ("swap", "off")
                ]
                for vname, field, kind in variants:
                    vals = dict(MX8_OK)
                    if field:
                        vals[field] = (MX8_SWAP if kind == "swap" else MX8_OFF)[
                            field
                        ]
                    sentence = _cap(tmpl.format(S=subject, P=poss, **vals))
                    rows.append(
                        {
                            "matrix": "mx8",
                            "ctx": opp,
                            "tid": tid,
                            "variant": vname,
                            "sentence": sentence,
                            "expect_flag": field is not None,
                            "recap": recap,
                            "last": last,
                            "slate": slate,
                        }
                    )
    return rows


def t32_cases() -> list[dict]:
    prior = mia_box()
    prior["opponent"] = "Miami Dolphins"
    slate0 = collect.load_cached_schedule()
    rows = []
    for abbr, (city, nick) in T32_TEAMS.items():
        recap = {
            "eventId": "401872976",
            "oppAbbr": abbr,
            "opponent": f"{city} {nick}",
            "kc": {
                "firstDowns": "22",
                "rushingYards": "140",
                "netPassingYards": "246",
                "totalYards": "386",
                "possessionTime": "31:12",
                "totalDrives": "9",
            },
            "opp": {
                "firstDowns": "15",
                "rushingYards": "95",
                "netPassingYards": "210",
                "totalYards": "305",
                "possessionTime": "28:48",
                "totalDrives": "9",
            },
            "scoringPlays": [],
            "prior": prior,
        }
        last = last_for(
            f"{city} {nick}",
            abbr,
            nick,
            id="401872976",
            week=4,
            date="2026-10-04T20:25:00Z",
            kcScore=27,
            oppScore=17,
        )
        slate = copy.deepcopy(slate0)
        for game in slate:
            if str(game.get("id")) == "401872976":
                game.update(
                    completed=True,
                    inProgress=False,
                    kcScore=27,
                    oppScore=17,
                    opponent=f"{city} {nick}",
                    opponentAbbr=abbr,
                    opponentShort=nick,
                )
        subjects = [f"The {nick}"]
        if city not in {"Los Angeles", "New York"}:
            subjects.append(city)
        probes = []
        for subject in subjects:
            probes.extend(
                [
                    (f"{subject} had 15 first downs, 95 rushing yards and 28:48.", False),
                    (f"{subject} had 95 rushing yards.", False),
                    (f"{subject} averaged 22 first downs coming in.", False),
                    (f"{subject} had 15 first downs, 140 rushing yards and 28:48.", True),
                    (f"{subject} had 140 rushing yards.", True),
                ]
            )
        probes.extend(
            [
                ("Kansas City had 22 first downs, 140 rushing yards and 31:12.", False),
                ("Kansas City had 22 first downs, 95 rushing yards and 31:12.", True),
                ("Kansas City had 95 rushing yards.", True),
            ]
        )
        for sentence, expect in probes:
            rows.append(
                {
                    "matrix": "t32",
                    "ctx": abbr,
                    "sentence": sentence,
                    "expect_flag": expect,
                    "recap": recap,
                    "last": last,
                    "slate": slate,
                }
            )
    return rows


def _pair_cases(matrix: str, pairs) -> list[dict]:
    rows = []
    for code, sentence, expect in pairs:
        recap, last, slate = ctx(code)
        rows.append(
            {
                "matrix": matrix,
                "ctx": code,
                "sentence": sentence,
                "expect_flag": expect,
                "recap": recap,
                "last": last,
                "slate": slate,
            }
        )
    return rows


def overreach_cases() -> list[dict]:
    return _pair_cases("overreach7", OVERREACH)


def ov2_cases() -> list[dict]:
    return _pair_cases("ov2_7", OV2)


def probe_cases() -> list[dict]:
    return _pair_cases("probes", PROBES)


def pv10_cases() -> list[dict]:
    rows = []
    for code, group, sentence, expect in PV10_POSSESSION:
        recap, last, slate = ctx(code)
        payload = (
            what_worked(sentence)
            if group in {"T5"} and "volume" in sentence
            else story(sentence)
        )
        rows.append(
            {
                "matrix": "pv10",
                "ctx": code,
                "group": group,
                "sentence": sentence,
                "expect_flag": expect,
                "narrative": payload,
                "recap": recap,
                "last": last,
                "slate": slate,
            }
        )
    return rows


def pv11a_cases() -> list[dict]:
    return _pair_cases("pv11a", PV11A)


def pv11b_cases() -> list[dict]:
    rows = []
    for code, sentence, expect in PV11B:
        recap, last, slate = ctx(code)
        rows.append(
            {
                "matrix": "pv11b",
                "ctx": code,
                "sentence": sentence,
                "expect_flag": expect,
                "narrative": what_worked(sentence),
                "recap": recap,
                "last": last,
                "slate": slate,
            }
        )
    return rows


def evaluate(case: dict) -> dict:
    narrative = case.get("narrative") or story(case["sentence"])
    result = pipeline(narrative, case["recap"], case["last"], case["slate"])
    flagged = bool(result["issues"])
    expect = case["expect_flag"]
    ok = flagged == expect and (expect or not result["added"])
    return {**result, "flagged": flagged, "ok": ok}
