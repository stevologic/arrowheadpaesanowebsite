"""Deterministic ESPN fact-check for generated edition copy.

The writer is allowed to interpret the tape. It is not allowed to invent a
final score, a player stat line, a TD/FG yardage, or a team FG/TD count
that disagrees with the ESPN box / scoring plays we already handed it.
Checks are regex-vs-box so the same wrong sentence fails every time.
Every generated section is scanned — not just lastGameReview.
"""
from __future__ import annotations

import copy
import re

from . import collect, config, diagrams, phase as phase_mod

# Completions and similar "X-of-Y" / "X/Y" lines must not be read as scores.
_OF_LINE = re.compile(r"\d+\s*[-–]?\s*of\s*[-–]?\s*\d+", re.IGNORECASE)
_SLASH_LINE = re.compile(r"\d+\s*/\s*\d+")

# Game-score contexts only. Bare "3-0" / "6-11" (records) stay out.
# Bare "lost 18-19" is first-down volume, not a final — require game/final/it/KC.
# "N-M game" is handled separately: official score-afters still count,
# but a 3-0 / 2-0 record is not a score-after.
_SCORE_PATTERNS = (
    re.compile(r"\bKC\s+(\d{1,2})\s*[–-]\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(
        r"\bfinal(?:\s+score)?\s+(?:KC\s+)?"
        r"(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:won|lost)\s+it\s+(?:KC\s+)?"
        r"(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:won|lost)\s+KC\s+"
        r"(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:up|ahead|leading|lead)\s+(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
)

# Bare "won 24-10" / "lost 18-19" — check only when the pair is a known score
# (either order). An unknown pair is first downs or other volume, not a final.
_LOOSE_WON_LOST = re.compile(
    r"\b(?:won|lost)\s+(\d{1,2})\s*[–-]\s*(\d{1,2})\b",
    re.IGNORECASE,
)
# "14-10 game" can be a real score-after. "3-0 game" is the slate record.
_GAME_AS_SCORE = re.compile(
    r"\b(\d{1,2})\s*[–-]\s*(\d{1,2})\s+game\b",
    re.IGNORECASE,
)

# Scoring-play yardage only when the claim is a TD/FG. An "88-yard run"
# or "11-yard catch" is not a scoring claim.
_TD_YARDS = re.compile(
    r"(?:from the (\d+)\b|(\d+)[-\s]yard(?:s)?\s+(?:TD|touchdown)\b)",
    re.IGNORECASE,
)
_CLAUSE_BREAK = re.compile(
    r"[.;:!?]|—|,|\n|\b(?:then|after|before|but|and)\b",
    re.IGNORECASE,
)
# Stat claims stay with their list. Split on sentence/comparison pivots, not
# every comma or "and" ("nine drives, 18 first downs, 3-of-7"). A newline
# or markdown heading is a clause boundary so a label on the previous line
# cannot own the next stat.
_STAT_CLAUSE_BREAK = re.compile(
    r"[.;:!?]|—|\n#{0,6}\s*|"
    r"\b(?:against|versus|compared to|whereas|while|but(?!\s+only))\b|"
    r"\bvs\.?\b",
    re.IGNORECASE,
)
_LOCATION_TEAM = re.compile(
    r"\b(?:in|at|into|from|leaves?|leaving|vs\.?|versus|against)\s+(?:the\s+)?"
    r"[A-Za-z][A-Za-z '’-]{1,24}",
    re.IGNORECASE,
)
_OBJECT_TEAM = re.compile(
    r"\b(?:bludgeoned|punished|torched|buried|pounded|gashed|beat)\s+"
    r"(?:the\s+)?[A-Za-z][A-Za-z '’-]{1,24}",
    re.IGNORECASE,
)
_RUSH_ATTEMPT_NEAR = re.compile(
    r"\b(?:rush(?:ing)?|rushes|carries|carry|on the ground)\b",
    re.IGNORECASE,
)

# Completions only: "20/24" or "20-of-24". A bare "24-10" is a score, not a line.
_PASS_LINE = re.compile(
    r"(\d{1,2})\s*(?:/|-of-)\s*(\d{1,2})(?:\s*,\s*(\d{2,3})\s*(?:YDS|yards))?",
    re.IGNORECASE,
)

# Team rates, not a passer line. Checked around the N-of-M token.
_DOWN_CTX = re.compile(
    r"(?:third\s+down|fourth\s+down|on\s+third|red\s+zone|conversions?)",
    re.IGNORECASE,
)

# Bind the line to the player in the same clause. Prefer a miss over a
# false positive: no 96-character "vicinity" windows.
_BIND_VERBS = r"(?:went|was|finished|threw|completed|at)"

_TD_TYPES = frozenset(
    {
        "td",
        "touchdown",
        "passing touchdown",
        "rushing touchdown",
        "receiving touchdown",
        "interception touchdown",
        "fumble recovery touchdown",
        "punt return touchdown",
        "kickoff return touchdown",
    }
)

_FG_TYPES = frozenset({"fg", "field goal", "field-goal"})

_DAYTIME = frozenset({"morning", "midday", "afternoon"})

_WORD_COUNTS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_EDITION_KEYS = (
    "headline",
    "dek",
    "videoHook",
    "theEdge",
    "storyline",
    "lastGameReview",
    "currentState",
    "gamePlan",
    "nextGame",
    "matchups",
    "xsandos",
    "strategies",
    "spotlight",
    "coaching",
    "debates",
    "runOfShow",
    "injuries",
    "personnel",
)

_FG_YARD = re.compile(r"(\d{1,2})[-\s]yard(?:s)?\s+field goal", re.IGNORECASE)
_FG_LIST = re.compile(r"field goals?\s*\(([^)]+)\)", re.IGNORECASE)
_FG_COUNT = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)[ \t]+"
    r"([A-Za-z][A-Za-z ]{1,24}?)?[ \t]*field goals?\b",
    re.IGNORECASE,
)
_TD_COUNT = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)[ \t]+"
    r"([A-Za-z][A-Za-z ]{1,24}?)?[ \t]*touchdowns?\b",
    re.IGNORECASE,
)
_YARD_NIGHT = re.compile(
    r"\d+(?:\.\d+)?[-\s]yard(?:s)?\s+night\b",
    re.IGNORECASE,
)
_FINISHED_NIGHT = re.compile(r"\bfinished the night\b", re.IGNORECASE)
_QB_NIGHT = re.compile(r"\bquarterback night\b", re.IGNORECASE)
_NIGHT_WORD = re.compile(r"\bnights?\b", re.IGNORECASE)
_ECHO_INSTRUCTION = re.compile(
    r"\bdo not (?:call|write|invent|include)\b"
    r"|this was not a night game"
    r"|never state a kickoff"
    r"|FACT CHECK RETRY"
    r"|SENTENCE REPAIR"
    r"|\[PRIVATE",
    re.IGNORECASE,
)
_GARBLED_RECORD = re.compile(
    r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\b"
    r"[^.!?]{0,48}\bgames?\s+of\s+\d{1,2}-\d{1,2}\b",
    re.IGNORECASE,
)
# Same-line team + yards only. Do not let \s+ eat a newline or heading.
_TEAM_GROUND = re.compile(
    r"\b((?:the\s+)?[A-Za-z][A-Za-z'’-]{1,20}"
    r"(?:\s+[A-Za-z][A-Za-z'’-]{1,20}){0,2})(?:['’]s)?"
    r"[ \t]+(?:was[ \t]+)?"
    r"(\d{2,3})[ \t]+"
    r"(?:on the ground|rush(?:ing)? yards)\b",
    re.IGNORECASE,
)
_GROUND_COPULA = frozenset({"is", "are", "was", "were", "be", "been", "am"})
_TEAM_RUSH_YARDS = re.compile(
    r"\b(\d{2,3})\s+(?:team\s+)?rush(?:ing)?\s+yards\b",
    re.IGNORECASE,
)
_RAN_FOR_YARDS = re.compile(
    r"\b(?:ran|rushed) for (\d{2,3}) yards\b",
    re.IGNORECASE,
)
_RUSH_SUBJECT_LEAD = re.compile(
    r"^(?:the\s+)?([A-Za-z][A-Za-z .’-]+?)\s+"
    r"(?:had|finished with|was|were|ran for|rushed for)\b",
    re.IGNORECASE,
)
_TEAM_PASS_YARDS = re.compile(
    r"\b(\d{2,3})\s+(?:net\s+)?passing yards\b",
    re.IGNORECASE,
)
_ABSOLUTE_CLAIM = re.compile(
    r"\b(?:the\s+)?(?:only|lone)\s+"
    r"(?:time|play|shot|miss|completion|incompletion|throw|target|"
    r"deep[- ]middle|vertical)\b"
    r"|\bthe only\b[^.!?]{0,80}"
    r"(?:deep|vertical|miss|shot|drive|interception|incompletion)",
    re.IGNORECASE,
)
_FIRST_PLAY_CLAIM = re.compile(
    r"\bthe first\b(?!\s*-?\s*(?:down|half|quarter|and|open|clean|window|"
    r"read|look|lb|linebacker|snap|safety|level|man|wave|hat|lead|night|"
    r"completion))"
    r"[^.!?—]{0,60}"
    r"(?:\bTD\b|touchdown|field goal|interception|\bINT\b|fumble|"
    r"deep[- ]|vertical|completion)",
    re.IGNORECASE,
)
_FIRST_ORDINAL = re.compile(
    r"\b(?:the\s+)?first\s+(?:lb|linebacker|snap|down|quarter|half|and|"
    r"lead|night)\b"
    r"|\bin the first\b"
    r"|\b1st-and-\d+",
    re.IGNORECASE,
)
_NIGHT_IDIOM = re.compile(
    r"\b\d+(?:\.\d+)?[-\s]?(?:point|tackle|yard)s?\s+nights?\b"
    r"|\bnightmare\b",
    re.IGNORECASE,
)
_NIGHT_THIS_GAME = re.compile(
    r"\bnight game\b"
    r"|\b(?:sunday|monday|thursday|friday|saturday)\s+night\b"
    r"|\bfinished the night\b"
    r"|\bquarterback night\b",
    re.IGNORECASE,
)
_PROSE_KEYS = (
    "dek",
    "videoHook",
    "theEdge",
    "storyline",
    "lastGameReview",
    "currentState",
    "gamePlan",
    "nextGame",
    "matchups",
    "debates",
    "coaching",
    "strategies",
    "runOfShow",
    "spotlight",
)
_AFTER_NON_PLAY = re.compile(
    r"\b(?:a|the|this|that|their)\s+"
    r"(?:win|loss|game|kickoff|season|start|week|result)\b",
    re.IGNORECASE,
)
_SEQUENCE_GAIN = re.compile(
    r"\b(\d{1,3})[-\s]yard(?:s)?\b|\b(\d{1,3})\s+yards\b",
    re.IGNORECASE,
)
_POSSESSION_CLOCK = re.compile(
    r"\b(\d{1,2}:\d{2})\b(?!\s*(?:AM|PM))(?:\s+(?:of\s+)?(?:possession|clock))?",
    re.IGNORECASE,
)
_FIRST_DOWNS = re.compile(r"\b(\d{1,2})\s+first downs?\b", re.IGNORECASE)
_FIRST_DOWN_VOLUME = re.compile(
    r"first-down volume\s*\((\d{1,2})\)",
    re.IGNORECASE,
)
_POSSESSION_HELD = re.compile(
    r"\b(?:held the ball|held it|sat on|had the ball for|"
    r"had the ball\s+\d{1,2}:\d{2}|had it for|"
    r"kept the ball for|kept it for|"
    r"won the clock|lost the clock|time of possession|"
    r"on the field for|possessed it for)\b|"
    r"\bhad\s+\d{1,2}:\d{2}\b|"
    r"\bwas\s+\d{3},\s+\d{1,2},\s+\d{1,2}:\d{2}\b|"
    r"\bstays?\s+on\b",
    re.I,
)
_NFL_ABBR = frozenset(
    {
        "ARI",
        "ATL",
        "BAL",
        "BUF",
        "CAR",
        "CHI",
        "CIN",
        "CLE",
        "DAL",
        "DEN",
        "DET",
        "GB",
        "HOU",
        "IND",
        "JAX",
        "KC",
        "LAC",
        "LAR",
        "LV",
        "MIA",
        "MIN",
        "NE",
        "NO",
        "NYG",
        "NYJ",
        "PHI",
        "PIT",
        "SEA",
        "SF",
        "TB",
        "TEN",
        "WSH",
    }
)
_POSSESSION_OBJECT = re.compile(
    r"\bsat on\s+(?:the\s+)?(?!ball\b|football\b)[A-Za-z][A-Za-z '’-]+|"
    r"\bkeep(?:s|t|ing)?\s+(?:the\s+)?[A-Za-z][A-Za-z '’-]+?['’]s\s+offense|"
    r"\bout-?gained\s+(?:the\s+)?[A-Za-z][A-Za-z '’-]+|"
    r"\bagainst\s+(?:the\s+)?[A-Za-z][A-Za-z '’-]+|"
    r"\bpunched\s+(?:the\s+)?[A-Za-z][A-Za-z '’-]+|"
    r"\b(?:the\s+)?[A-Za-z][A-Za-z '’-]+?\s+(?:game|finale|tape|film|box)\b",
    re.IGNORECASE,
)
_KC_VOICED_TOP = re.compile(
    r"(?:^|\n)\s*time of possession\s*\(\d{1,2}:\d{2}\)",
    re.IGNORECASE,
)
_SAT_ON_BALL = re.compile(
    r"\bsat on the (?:ball|football)\b",
    re.IGNORECASE,
)
_SEGMENT_CLOCK_RANGE = re.compile(
    r"\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}"
)
_GAME_CLOCK_AFTER = re.compile(
    r"\s*(?:"
    r"into\s+the\s+(?:game|first|second|third|fourth|"
    r"opening|quarter|half|period)\b|"
    r"left\b|remaining\b|to\s+go\b|"
    r"(?:in|of)\s+the\s+(?:first|second|third|fourth|"
    r"opening|quarter|half|period)\b|"
    r"in\b(?!\s+(?:the\s+)?[A-Z])|"
    r"mark\b"
    r")",
    re.IGNORECASE,
)
_HAD_ON_GAMEDAY = re.compile(
    r"\b(?:had|only)\s+(\d{1,2})\s+on\s+"
    r"(?:Sunday|Monday|Thursday|Friday|Saturday|gameday)\b",
    re.IGNORECASE,
)
_FIRST_DOWNS_AND_CLOCK = re.compile(
    r"first downs?\s+and\s*$",
    re.IGNORECASE,
)
_RATE_OR_OTHER_GAME = re.compile(
    r"per[-\s]?game|"
    r"(?:first downs?|averaged?)\s+a game|"
    r"\baveraged?\b|"
    r"through\s+\w+\s+weeks?|"
    r"coming in\b",
    re.IGNORECASE,
)
_COMPARISON_TAIL = re.compile(
    r"\b(?:the\s+)?([A-Za-z][A-Za-z .’-]+?)['’]s?\s+"
    r"(\d{1,2})\s+and\s+(\d{1,2}:\d{2})\b"
)
_AGAINST_OTHER_TEAM = re.compile(
    r"\bagainst\s+(?:the\s+)?[A-Z][A-Za-z][A-Za-z'’.-]*"
    r"(?:\s+[A-Z][A-Za-z'’.-]*){0,2}"
)
_GAME_LABEL_TEAM = re.compile(
    r"\b(?:the\s+)?[A-Za-z][A-Za-z '’-]{1,24}?\s+"
    r"(?:tape|film|box|finale|game|night)\b",
    re.IGNORECASE,
)
# Proper-noun score labels only ("Indianapolis 33-30"). Do not strip
# verbs like "lost 24-10" — that used to hide Miami and bind KC.
_GAME_SCORE_LABEL = re.compile(
    r"\b(?:the\s+)?[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]*){0,2}"
    r"\s+\d{1,2}[-–]\d{1,2}\b"
)
_SCRIPT_OPENER = re.compile(
    r"^(?:Open|First-and|Second-and|Third-and|Fourth-and|If)\b",
    re.IGNORECASE,
)
_CARD_HEADING_KEYS = ("title", "topic", "unit", "segment")
_GAME_YARDS = re.compile(r"\b(\d{3})[-\s]yard(?:s)?\b", re.IGNORECASE)
_NEVER_PLAY_CLAIM = re.compile(
    r"\bnever\s+(?:threw|completed|ran|scored|allowed|asked|hit|found|"
    r"targeted|connected)\b",
    re.IGNORECASE,
)
_ONES_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine"
)
_TEEN_WORDS = (
    "ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    "eighteen|nineteen"
)
_TENS_WORDS = "twenty|thirty|forty"
_HYPHEN_NUM = rf"(?:{_TENS_WORDS})-(?:{_ONES_WORDS})"
# Longest match first so "twenty-two touches" is 22, not "two touches".
_NUM_TOKEN = (
    rf"{_HYPHEN_NUM}|\d{{1,3}}|{_TEEN_WORDS}|{_TENS_WORDS}|zero|{_ONES_WORDS}"
)
_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
}
_TOTAL_DRIVES = re.compile(
    rf"\b({_NUM_TOKEN})\s+(?:total\s+)?drives\b",
    re.IGNORECASE,
)
_TOUCH_COUNT = re.compile(
    rf"\b({_NUM_TOKEN})\s+touches\b|"
    rf"\btouched the ball\s+({_NUM_TOKEN})\s+times\b|"
    rf"\bhandled the ball\s+({_NUM_TOKEN})\s+times\b",
    re.IGNORECASE,
)
# Game-total touches only. Red-zone / drive / quarter windows are not
# on the ESPN usage row, so they must not be compared to 20.
_TOUCH_WINDOW = re.compile(
    r"\b(?:"
    r"red[- ]zone|goal[- ]line|"
    r"on (?:this|the|that) drive|(?:this|that|the) drive|"
    r"per quarter|each quarter|by quarter|"
    r"opening (?:drive|script)|two-minute|"
    r"inside the \d+"
    r")\b",
    re.IGNORECASE,
)
# "40-dropback" / "40 dropbacks" are attempt claims. Bare "40-drop vacuum"
# is metaphor and is not Mahomes' pass-attempt total.
_DROP_ATTEMPT = re.compile(
    rf"\b({_NUM_TOKEN})-dropbacks?\b|"
    rf"\b({_NUM_TOKEN})\s+dropbacks?\b|"
    rf"\bthrew\s+({_NUM_TOKEN})\s+passes\b|"
    rf"\battempted\s+({_NUM_TOKEN})\s+passes\b|"
    rf"\b({_NUM_TOKEN})[-\s]attempts?\b",
    re.IGNORECASE,
)
_OWNER_ADVERB = r"(?:(?:\w+ly|already|now|also|still|just|currently)\s+)*"
# "Kansas City has to help after 5 hits" is not a recorded-stat claim.
_OWNER_VERBS = (
    rf"{_OWNER_ADVERB}(?:posted|recorded|notched|did\s+record|"
    rf"(?:had|has)(?!\s+to\b))"
)
_TEAM_UNIT = r"(?:\s+(?:defense|defence|front))?"
_PROPER_NAME = r"(?-i:[A-Z][A-Za-z''-]+(?:\s+[A-Z][A-Za-z''-]+)?)"
_SACK_COUNT = re.compile(
    rf"\bsacked\s+(?P<sack_qb>Mahomes|Willis)\s+(once|twice|{_NUM_TOKEN})(?:\s+times)?\b|"
    rf"\b(?:was\s+)?(?:repeatedly\s+)?sacked[,;]?\s+"
    rf"(once|twice|{_NUM_TOKEN})\s+times(?:\s+in all)?\b|"
    rf"\bsacked\s+(once|twice|{_NUM_TOKEN})(?:\s+times)?\b|"
    rf"\bwent down\s+(once|twice|{_NUM_TOKEN})(?:\s+times)?\s+for sacks\b|"
    rf"\b(?:was\s+)?taken down\s+(once|twice|{_NUM_TOKEN})(?:\s+times)?\b|"
    rf"\b(?:The\s+)?(?P<sack_owner>{_PROPER_NAME})"
    rf"{_TEAM_UNIT}\s+{_OWNER_VERBS}\s+"
    rf"(?P<sack_n>a|an|once|twice|{_NUM_TOKEN})\s+sacks?\b|"
    rf"\b({_NUM_TOKEN})\s+sacks\b",
    re.IGNORECASE,
)
_QB_HIT_COUNT = re.compile(
    rf"\bhit\s+(?P<hit_qb>Mahomes|Willis)\s+(?:\w+\s+){{0,2}}({_NUM_TOKEN})\s+times\b|"
    rf"\bhit\s+(?:\w+\s+){{0,2}}({_NUM_TOKEN})\s+times\b|"
    rf"\b(?:The\s+)?(?P<hit_owner>{_PROPER_NAME})"
    rf"{_TEAM_UNIT}\s+{_OWNER_VERBS}\s+"
    rf"({_NUM_TOKEN})\s+(?:QB\s+)?hits\b|"
    rf"\b({_NUM_TOKEN})\s+QB hits\b|"
    rf"\b({_NUM_TOKEN})\s+hits\b",
    re.IGNORECASE,
)
_SEASON_SPAN = re.compile(
    r"\b(?:this year|last year|this season|last season|"
    r"a year ago|on the year|for the season|"
    rf"through\s+(?:{_NUM_TOKEN})\s+games?|so far|in\s+20\d{{2}})\b",
    re.IGNORECASE,
)
_PRESSURE_OWNER = re.compile(
    rf"\b(?:The\s+)?({_PROPER_NAME}){_TEAM_UNIT}\s+{_OWNER_VERBS}\s+",
)
# "Miami's 5 QB hits" binds; "Kansas City's plan after 5 hits" does not.
_POSSESSIVE_PRESSURE = re.compile(
    rf"\b({_PROPER_NAME})['’]s\s+(?:(?:{_NUM_TOKEN})\s+)?(?:QB\s+)?"
    rf"(?:hits?|sacks?)\b",
    re.IGNORECASE,
)
_QB_ALIASES = {
    "mahomes": "kc_qb",
    "patrick": "kc_qb",
    "willis": "opp_qb",
    "malik": "opp_qb",
}
_TEAM_SIDES = {
    "miami": "kc_qb",
    "dolphins": "kc_qb",
    "mia": "kc_qb",
    "kansas": "opp_qb",
    "chiefs": "opp_qb",
    "kc": "opp_qb",
}
_PRESSURE_WINDOW = re.compile(
    r"\b(?:first|second|third|fourth|q[1-4])\s+quarter\b|"
    r"\b(?:first|second)\s+half\b|"
    r"\b(?:denver|broncos)\s+opener\b|"
    r"\bin the opener\b|"
    r"\bof\s+(?:bo\s+)?nix\b",
    re.IGNORECASE,
)
_ILLEGAL_USE = re.compile(
    r"\billegal[-\s]use\b|\billegal use of hands\b",
    re.IGNORECASE,
)
_SAME_LOOK = re.compile(
    r"\bsame (?:jumbo )?look\b|"
    r"\bboth snaps\b|"
    r"\bboth of the\s+(?:\w+\s+){0,2}snaps\b|"
    r"\bboth\s+goal-line\s+snaps\b|"
    r"\bboth\s+(?:\w+\s+){0,2}snaps\b|"
    r"\bboth\s+(?:\w+\s+){0,2}runs\b|"
    r"\beach\s+(?:\w+\s+){0,2}snaps?\b",
    re.IGNORECASE,
)
_FIRST_MINUTES = re.compile(
    rf"\b(?:first|within|inside(?:\s+of)?)\s+({_NUM_TOKEN})\s+minutes?\b|"
    rf"\b(?:first|within|inside(?:\s+of)?)\s+({_NUM_TOKEN})\s+seconds?\b|"
    rf"\bwithin the opening\s+({_NUM_TOKEN})\s+minutes?\b|"
    rf"\b(?:in under|under|less than|fewer than)\s+({_NUM_TOKEN})\s+minutes?\b|"
    rf"\b(?:in under|under|less than|fewer than)\s+({_NUM_TOKEN})\s+seconds?\b|"
    rf"\bbarely\s+({_NUM_TOKEN})\s+seconds?\s+in\b|"
    rf"\b(?:two|three|{_NUM_TOKEN})[-\s]minute opening\b",
    re.IGNORECASE,
)
_SCORE_CLAIM = re.compile(
    r"\b(?:scored|score|strike|touchdown|opening|td|end zone)\b",
    re.IGNORECASE,
)
_OPENING_DRIVE = re.compile(
    rf"\bopening drive\s+took\s+(\d{{1,2}}:\d{{2}}|{_NUM_TOKEN}\s+minutes?)\b",
    re.IGNORECASE,
)
_INT_AT_CLOCK = re.compile(
    r"\b([A-Z][A-Za-z''.-]+)\s+intercept(?:ed|s|ion)\b"
    r"[^.!?\n]{0,72}?\bQ([1-4])\s+(\d{1,2}:\d{2})\b",
    re.IGNORECASE,
)
_PASS_TD_COUNT = re.compile(
    rf"\bthrew\s+({_NUM_TOKEN})\s+touchdown\s+passes\b|"
    rf"\b({_NUM_TOKEN})\s+touchdown\s+passes\b|"
    rf"\b\d{{1,2}}-of-\d{{1,2}}\s+with\s+({_NUM_TOKEN})\s+touchdowns?\b|"
    rf"\b(?:with|and)\s+({_NUM_TOKEN})\s+touchdowns?\b",
    re.IGNORECASE,
)
# Name tokens stay case-sensitive so IGNORECASE cannot turn "was"/"one"/"an"
# into a player. Only the verb is case-insensitive.
_FUMBLE_PAREN = re.compile(
    r"(?i:fumble)[^.!?]{0,40}\(([A-Z][A-Za-z''-]+)\)",
)
_FUMBLE_FORCED = re.compile(
    r"\b([A-Z][A-Za-z''-]+)\s+"
    r"(?i:forced|recovered|caused|blew up)\b[^.!?]{0,48}(?i:fumble)\b"
    r"|(?i:fumble)[^.!?]{0,48}\b(?i:forced|recovered|caused)\s+by\s+"
    r"([A-Z][A-Za-z''-]+)",
)
_INT_CREDIT = re.compile(
    r"\b([A-Z][A-Za-z''-]+)\s+(?i:intercept(?:ed|ion)|INT)\b"
    r"|\b(?i:intercept(?:ed|ion)|INT)\s+by\s+([A-Z][A-Za-z''-]+)",
)
_NOT_PLAYER_TOKENS = frozenset(
    {
        "then",
        "that",
        "this",
        "those",
        "these",
        "they",
        "them",
        "their",
        "there",
        "one",
        "was",
        "were",
        "and",
        "the",
        "late",
        "deep",
        "middle",
        "an",
        "a",
        "his",
        "her",
        "he",
        "she",
        "it",
        "who",
        "when",
        "after",
        "before",
    }
)
_GARBLED_INITIAL = re.compile(r"\b([A-Z])['’]([A-Z][a-z]{3,})\b")
_BETWEEN_TACKLES = re.compile(r"\bbetween the tackles\b", re.IGNORECASE)
_ZONE_BLITZ_INT = re.compile(
    r"\bzone[- ]blitz\b[^.!?]{0,160}\b(?:int|intercept(?:ion)?)\b",
    re.IGNORECASE,
)
# Repair notes / retry echoes must not re-veto after the claim is gone.
_SCHEME_NOTE = re.compile(
    r"\bdo not (?:call|write|invent|include)\b"
    r"|\bdoes not back\b"
    r"|\bmay not write\b"
    r"|\bSCHEME LIMITS\b"
    r"|\bFACT CHECK RETRY\b"
    r"|\bSENTENCE REPAIR\b",
    re.IGNORECASE,
)
_SCHEME_SNIPPETS = (
    "between the tackles",
    "zone blitz",
    "zone-blitz",
)
_SNAP_LATER = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six)\s+snaps?\b[^.!?]{0,48}\blater\b"
    r"|\blater\b[^.!?]{0,48}\b(?:\d+|one|two|three|four|five|six)\s+snaps?\b",
    re.IGNORECASE,
)
_ORPHAN_OPENER = re.compile(
    r"^(?:He|She|They|It|This|That|Those|These|His|Her|Their|the same)\b",
    re.IGNORECASE,
)
_DANGLING_NAME = re.compile(
    r"^[A-Z][a-z]+(?:\s+at\b|\s+took\b).{0,48}$",
)
_ORPHAN_VERB = re.compile(
    r"\b(?:is|was|were|are|won|lost|had|has|scored|made|hit)\b",
    re.IGNORECASE,
)
_CLOCK_SNIPPET = re.compile(r"^\d{1,2}:\d{2}$")
# Historical salvage cap. Drop-pass publish is gated by leftover errors
# and OFFLINE_WORD_FLOOR, not by how many sentences were removed.
MAX_REPAIR_DROPS = 2
_MAX_REPAIR_DROPS = MAX_REPAIR_DROPS
# Hold automerge (label, no merge, no deploy) when salvage is this noisy
# or the edition is shorter than the in-season desk norm.
HOLD_REPAIR_DROPS = 3
PUBLISH_WORD_FLOOR = 3900

# Qualified counts are not whole-game totals. Compare against the window
# when quarter data can compute it; otherwise skip.
_COUNT_SCOPE = re.compile(
    r"\b("
    r"early|opening|late|"
    r"first[- ]half|second[- ]half|"
    r"(?:in\s+the\s+)?(?:first|second|third|fourth|opening|final)\s+quarter|"
    r"q\s*[1-4]|"
    r"in\s+the\s+game|all\s+game|on\s+the\s+day|overall|\btotal\b"
    r")\b",
    re.IGNORECASE,
)
_WHOLE_GAME_SCOPES = frozenset(
    {"in the game", "all game", "on the day", "overall", "total"}
)
_SCOPE_QUARTERS = {
    "early": frozenset({1, 2}),
    "opening": frozenset({1, 2}),
    "late": frozenset({3, 4}),
    "first-half": frozenset({1, 2}),
    "first half": frozenset({1, 2}),
    "second-half": frozenset({3, 4}),
    "second half": frozenset({3, 4}),
    "first quarter": frozenset({1}),
    "opening quarter": frozenset({1}),
    "second quarter": frozenset({2}),
    "third quarter": frozenset({3}),
    "fourth quarter": frozenset({4}),
    "final quarter": frozenset({4}),
    "q1": frozenset({1}),
    "q2": frozenset({2}),
    "q3": frozenset({3}),
    "q4": frozenset({4}),
}

_AFTER_FOLLOWING = re.compile(
    r"\b(?P<rel>after|following)\s+(?:the\s+)?",
    re.IGNORECASE,
)
_SEQUENCE_SCORE = re.compile(
    r"(?:from the (\d+)\b|(\d+)[-\s]yard(?:s)?\s+(?:TD|touchdown|field goal)\b"
    r"|(\d{1,2})\s*[–-]\s*(\d{1,2}))",
    re.IGNORECASE,
)
_SEQUENCE_TURNOVER = re.compile(
    r"\b(interception|int|fumble|missed(?:\s+\d+[-\s]yard(?:er)?)?|"
    r"turnover on downs)\b",
    re.IGNORECASE,
)
_SEQUENCE_NAME = re.compile(r"[A-Za-z][A-Za-z''-]{3,}")
_SEQUENCE_STOP = frozenset(
    {
        "after",
        "following",
        "that",
        "this",
        "with",
        "from",
        "made",
        "make",
        "then",
        "when",
        "into",
        "touchdown",
        "field",
        "goal",
        "interception",
        "fumble",
        "missed",
        "yard",
        "yards",
        "score",
        "scoring",
        "play",
        "game",
        "quarter",
        "half",
        "first",
        "second",
        "third",
        "fourth",
        "kansas",
        "city",
        "miami",
        "dolphins",
        "chiefs",
        "football",
        "the",
        "and",
        "for",
    }
)

_REQUIRED_COPY_KEYS = ("headline", "dek", "theEdge")
_PROTECTED_KEYS = frozenset(
    {
        "score",
        "result",
        "opponent",
        "label",
        "id",
        "at",
        "tv",
        "generatedAt",
        "updatedAt",
        "slug",
        "generator",
        "edition",
        "record",
        "title",
        "topic",
        "unit",
        "segment",
    }
)


class FactCheckError(RuntimeError):
    """Raised when the review still disagrees with ESPN after one retry."""


def review_text(narrative: dict | None) -> str:
    """Flatten lastGameReview prose for regex checks."""
    review = (narrative or {}).get("lastGameReview") or {}
    if not isinstance(review, dict):
        return ""
    parts: list[str] = []
    for key in ("lede", "score", "label", "opponent"):
        val = review.get(key)
        if val:
            parts.append(str(val))
    for para in review.get("analysis") or []:
        if isinstance(para, dict):
            parts.append(str(para.get("body") or ""))
        elif para:
            parts.append(str(para))
    for take in review.get("takeaways") or []:
        if isinstance(take, dict):
            parts.append(str(take.get("title") or ""))
            parts.append(str(take.get("body") or ""))
        elif take:
            parts.append(str(take))
    for key in ("whatWorked", "whatDidnt"):
        for item in review.get(key) or []:
            if item:
                parts.append(str(item))
    return "\n".join(p for p in parts if p)


def _walk_prose(value, parts: list[str]) -> None:
    if value is None or isinstance(value, (int, float, bool)):
        return
    if isinstance(value, str):
        if value.strip():
            parts.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_prose(item, parts)
        return
    if isinstance(value, list):
        for item in value:
            _walk_prose(item, parts)


def edition_text(narrative: dict | None) -> str:
    """Flatten every generated section the writer can put a score into."""
    payload = narrative or {}
    parts: list[str] = []
    for key in _EDITION_KEYS:
        _walk_prose(payload.get(key), parts)
    return "\n".join(p for p in parts if p)


def prose_text(narrative: dict | None) -> str:
    """Review and narrative prose only — not X's & O's card fields."""
    payload = narrative or {}
    parts: list[str] = []
    for key in _PROSE_KEYS:
        _walk_prose(payload.get(key), parts)
    return "\n".join(p for p in parts if p)


def _play_team(play: dict) -> str:
    return (play.get("team") or "").strip().upper()


def _play_kind(play: dict) -> str:
    typ = (play.get("type") or "").strip().lower()
    if typ in _FG_TYPES or typ == "fg":
        return "fg"
    if typ in _TD_TYPES or "touchdown" in typ or typ == "td":
        return "td"
    return ""


def _play_yards(play: dict):
    value = play.get("yards")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_WEAK_ALIAS_TOKENS = frozenset(
    {
        "bay",
        "new",
        "los",
        "las",
        "angeles",
        "york",
        "green",
        "tampa",
        "kansas",
        "city",
        "saint",
        "st",
        "the",
        "at",
    }
)
# Shared cities stay empty unless the recap opponent is one of the two clubs.
_AMBIGUOUS_CITY_SHORT = {
    "la": ("LAC", "LAR"),
    "ny": ("NYJ", "NYG"),
}
_UNIQUE_SHORT_ALIASES = {
    "tampa": "TB",
    "bucs": "TB",
    "fins": "MIA",
}


def _put_alias(
    aliases: dict[str, str],
    key: str,
    team: str,
    *,
    protected: set[str],
    mode: str,
) -> None:
    token = " ".join((key or "").lower().split())
    if not token or token in _WEAK_ALIAS_TOKENS:
        return
    if len(token) < 3 and token.upper() not in _NFL_ABBR:
        return
    if mode == "trusted":
        aliases[token] = team
        protected.add(token)
        return
    if token in protected:
        return
    prev = aliases.get(token)
    if prev is None:
        aliases[token] = team
        return
    if prev != team:
        aliases[token] = ""


def _add_opponent_aliases(
    aliases: dict[str, str],
    name: str,
    abbr: str,
    *,
    protected: set[str] | None = None,
    mode: str = "trusted",
) -> None:
    tokens = [
        token
        for token in re.findall(r"[A-Za-z]+", name or "")
        if token.lower() not in {"the", "at"}
    ]
    team = abbr or (tokens[-1][:3].upper() if tokens else "")
    if not team:
        return
    hold = protected if protected is not None else set()
    if abbr:
        _put_alias(aliases, abbr, team, protected=hold, mode=mode)
    if tokens:
        _put_alias(aliases, tokens[-1], team, protected=hold, mode=mode)
    if name and len(tokens) >= 2:
        _put_alias(aliases, name, team, protected=hold, mode=mode)
        _put_alias(aliases, " ".join(tokens[:-1]), team, protected=hold, mode=mode)
    if tokens and tokens[0].lower() not in _WEAK_ALIAS_TOKENS:
        if len(tokens) <= 2:
            _put_alias(aliases, tokens[0], team, protected=hold, mode=mode)
    if team == "LV":
        _put_alias(aliases, "vegas", team, protected=hold, mode=mode)
    if team == "TB":
        aliases["bucs"] = team
        aliases["tampa"] = team
        hold.add("bucs")
        hold.add("tampa")
    if team == "MIA":
        aliases["fins"] = team
        hold.add("fins")


def _team_aliases(
    last_game: dict | None,
    recap: dict | None,
    schedule=None,
) -> dict[str, str]:
    aliases = {
        "kc": "KC",
        "chiefs": "KC",
        "kansas city": "KC",
    }
    protected = set(aliases)
    opp = ((recap or {}).get("oppAbbr") or "").strip().upper()
    if opp:
        _put_alias(aliases, opp, opp, protected=protected, mode="trusted")
    _add_opponent_aliases(
        aliases,
        ((last_game or {}).get("opponent") or "").strip(),
        opp,
        protected=protected,
        mode="trusted",
    )
    short = ((last_game or {}).get("opponentShort") or "").strip()
    if short and opp:
        _put_alias(aliases, short, opp, protected=protected, mode="trusted")
    prior = (recap or {}).get("prior") or {}
    prior_abbr = (prior.get("oppAbbr") or "").strip().upper()
    _add_opponent_aliases(
        aliases,
        (prior.get("opponent") or "").strip(),
        prior_abbr,
        protected=protected,
        mode="trusted",
    )
    for game in schedule or []:
        if not isinstance(game, dict):
            continue
        abbr = (game.get("opponentAbbr") or "").strip().upper()
        if not abbr or abbr == "KC":
            continue
        _add_opponent_aliases(
            aliases,
            game.get("opponent") or "",
            abbr,
            protected=protected,
            mode="schedule",
        )
        nick = (game.get("opponentShort") or "").strip()
        if nick:
            _put_alias(aliases, nick, abbr, protected=protected, mode="schedule")
    _add_short_aliases(aliases, last_game, recap)
    return aliases


def _add_short_aliases(
    aliases: dict[str, str],
    last_game: dict | None,
    recap: dict | None,
) -> None:
    """LA/NY are recap-only; Tampa/Bucs/Fins are unique nicknames."""
    recap_teams = _recap_box_teams(last_game, recap)
    for key, options in _AMBIGUOUS_CITY_SHORT.items():
        hits = [abbr for abbr in options if abbr in recap_teams]
        aliases[key] = hits[0] if len(hits) == 1 else ""
    for key, team in _UNIQUE_SHORT_ALIASES.items():
        if aliases.get(key) in {"", None}:
            aliases[key] = team


def _city_and_nick_labels(name: str, abbr: str = "", short: str = "") -> set[str]:
    """Full city ('los angeles') and nickname, never bay/york/angeles alone."""
    labels = set()
    token = (abbr or "").strip().lower()
    if token:
        labels.add(token)
    nick = (short or "").strip().lower()
    if nick and nick not in _WEAK_ALIAS_TOKENS:
        labels.add(nick)
    words = [
        part
        for part in re.findall(r"[A-Za-z]+", name or "")
        if part.lower() not in {"the", "at"}
    ]
    if len(words) >= 2:
        city = " ".join(words[:-1]).lower()
        if city not in _WEAK_ALIAS_TOKENS:
            labels.add(city)
    for word in words:
        low = word.lower()
        if low not in _WEAK_ALIAS_TOKENS and len(low) >= 3:
            labels.add(low)
    if (abbr or "").strip().upper() == "LV" or "vegas" in " ".join(words).lower():
        labels.add("vegas")
    token = (abbr or "").strip().upper()
    if token in {"LAC", "LAR"}:
        labels.add("la")
    if token in {"NYJ", "NYG"}:
        labels.add("ny")
    if token == "TB":
        labels.add("tampa")
        labels.add("bucs")
    if token == "MIA":
        labels.add("fins")
    return labels


def _prior_game_labels(recap: dict | None) -> set[str]:
    prior = (recap or {}).get("prior") or {}
    return _city_and_nick_labels(
        prior.get("opponent") or "",
        prior.get("oppAbbr") or "",
    )


def _last_game_labels(last_game: dict | None, recap: dict | None) -> set[str]:
    return _city_and_nick_labels(
        (last_game or {}).get("opponent") or "",
        ((recap or {}).get("oppAbbr") or (last_game or {}).get("opponentAbbr") or ""),
        (last_game or {}).get("opponentShort") or "",
    )


_KC_SCOPE = frozenset({"kc", "chiefs", "kansas"})
# City names that are not in _FOREIGN_TEAMS nicknames. Used when the
# schedule is not attached so a Denver-week claim is not the Miami box.
_OTHER_CITY_LABELS = frozenset({"denver"})


def _scope_label_map(
    recap: dict | None, last_game: dict | None, schedule=None
) -> list[tuple[str, str]]:
    """Longest-first (label, last|prior|other) opponent names on the slate."""
    assigned: dict[str, str] = {}
    for lab in _last_game_labels(last_game, recap):
        assigned[lab] = "last"
    for lab in _prior_game_labels(recap):
        assigned.setdefault(lab, "prior")
    for lab in ("prior-week", "prior week", "prior game"):
        assigned.setdefault(lab, "prior")
    known = set(assigned) | _KC_SCOPE

    def _add(label: str, scope: str) -> None:
        token = " ".join((label or "").lower().split())
        if len(token) < 3 or token in _WEAK_ALIAS_TOKENS:
            return
        prev = assigned.get(token)
        if prev in {"last", "prior"}:
            return
        if prev and scope == "other":
            return
        assigned[token] = scope
        known.add(token)

    last_week = (last_game or {}).get("week")
    if last_week not in (None, ""):
        assigned[f"week {last_week}"] = "last"
        known.add(f"week {last_week}")
    prior_week = ((recap or {}).get("prior") or {}).get("week")
    if prior_week not in (None, ""):
        assigned.setdefault(f"week {prior_week}", "prior")
        known.add(f"week {prior_week}")
    last_id = str((last_game or {}).get("id") or (recap or {}).get("eventId") or "")
    prior_id = str(((recap or {}).get("prior") or {}).get("eventId") or "")
    for game in schedule or []:
        if not isinstance(game, dict):
            continue
        gid = str(game.get("id") or "")
        if gid and gid == last_id:
            scope = "last"
        elif gid and gid == prior_id:
            scope = "prior"
        else:
            scope = "other"
        for lab in _city_and_nick_labels(
            game.get("opponent") or "",
            game.get("opponentAbbr") or "",
            game.get("opponentShort") or "",
        ):
            _add(lab, scope)
        week = game.get("week")
        if week not in (None, ""):
            _add(f"week {week}", scope)
    last_abbr = (
        (
            (recap or {}).get("oppAbbr")
            or (last_game or {}).get("opponentAbbr")
            or ""
        )
        .strip()
        .upper()
    )
    prior_abbr = (
        ((recap or {}).get("prior") or {}).get("oppAbbr") or ""
    ).strip().upper()
    if last_abbr == "DEN" or str(last_week) == "1":
        assigned["opener"] = "last"
    elif prior_abbr == "DEN" or str(prior_week) == "1":
        assigned["opener"] = "prior"
    else:
        assigned["opener"] = "other"
    for lab in _OTHER_CITY_LABELS:
        _add(lab, "other")
    for lab in _FOREIGN_TEAMS:
        _add(lab, "other")
    return sorted(assigned.items(), key=lambda item: len(item[0]), reverse=True)


def _claim_game_scope(
    text: str,
    claim_at: int,
    recap: dict | None,
    last_game: dict | None,
    schedule=None,
) -> str:
    """Which box owns this claim: last, prior, other, or '' (default last).

    Nearest opponent / week label to the left wins so a Chiefs subject
    after 'Against Indianapolis' still uses the Indianapolis box, and a
    Denver-week number is never checked against last week's Miami box.
    Labels after the claim still count so '33:00 against Indianapolis'
    binds to the prior game.
    """
    start, _ = _stat_clause_span(text, claim_at)
    lookback = text[max(0, start - 32) : start]
    nl = lookback.rfind("\n")
    left_origin = max(0, start - 32)
    if nl >= 0:
        left_origin = left_origin + nl + 1
    left = text[left_origin:claim_at]
    _sent_start, sent_end = _sentence_span(text, claim_at)
    right = text[claim_at:min(sent_end, claim_at + 56)]
    hits: list[tuple[int, int, int, str, int]] = []
    for lab, scope in _scope_label_map(recap, last_game, schedule):
        for hit in re.finditer(_alias_pattern(lab), left, re.IGNORECASE):
            abs_start = left_origin + hit.start()
            hits.append(
                (
                    abs_start,
                    left_origin + hit.end(),
                    claim_at - abs_start,
                    scope,
                    len(lab),
                )
            )
        for hit in re.finditer(_alias_pattern(lab), right, re.IGNORECASE):
            if hit.start() == 0:
                continue
            hits.append(
                (
                    claim_at + hit.start(),
                    claim_at + hit.end(),
                    hit.start(),
                    scope,
                    len(lab),
                )
            )
    kept: list[tuple[int, int, int, str, int]] = []
    for item in sorted(hits, key=lambda row: row[4], reverse=True):
        start, end, _dist, _scope, _length = item
        if any(k_start <= start and end <= k_end for k_start, k_end, *_ in kept):
            continue
        kept.append(item)
    if not kept:
        return ""
    return min(kept, key=lambda row: row[2])[3]


def _ground_subject(raw: str, aliases: dict[str, str]) -> str:
    """Team token for a same-line 'Name 88 on the ground' match.

    Heading leftovers ('Miami run game 88') do not bind. Extra words
    before the team ('leaves Miami is 88') do not bind. A trailing
    copula ('Indianapolis was 117') is stripped so the team still binds.
    """
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", raw or "")
    while words and words[-1].lower() in _GROUND_COPULA:
        words.pop()
    if not words or any(word.lower() in _GROUND_COPULA for word in words):
        return ""

    def _extras(before: list[str]) -> bool:
        return any(word.lower() != "the" for word in before)

    last = words[-1]
    if _alias_team(last, aliases):
        if _extras(words[:-1]):
            return ""
        return last
    if len(words) >= 2:
        pair = " ".join(words[-2:]).lower()
        if pair in aliases:
            if _extras(words[:-2]):
                return ""
            return " ".join(words[-2:])
    return ""


def _player_teams(recap: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        team = _play_team(play)
        player = (play.get("player") or "").strip()
        if not team or not player:
            continue
        out[player.lower()] = team
        last = player.split()[-1]
        if len(last) >= 3:
            out[last.lower()] = team
    return out


_YARD_LINE_TEAM = re.compile(
    r"\b(?:the\s+)?(?:kc|chiefs|kansas city|mia|miami|dolphins)\s+\d{1,2}\b",
    re.IGNORECASE,
)


def _is_decimal_dot(text: str, pos: int) -> bool:
    """True for the period in '61.1', not a sentence end."""
    if pos <= 0 or pos >= len(text) - 1 or text[pos] != ".":
        return False
    return text[pos - 1].isdigit() and text[pos + 1].isdigit()


def _is_clock_colon(text: str, pos: int) -> bool:
    """True for the colon in '34:21', not a clause break."""
    if pos <= 0 or pos >= len(text) - 1 or text[pos] != ":":
        return False
    return text[pos - 1].isdigit() and text[pos + 1].isdigit()


def _stat_clause_span(text: str, index: int) -> tuple[int, int]:
    """Clause holding this index, split on sentence/comparison pivots only."""
    start, end = 0, len(text or "")
    for hit in _STAT_CLAUSE_BREAK.finditer(text or ""):
        token = hit.group(0)
        if token == "." and (
            _is_initial_dot(text, hit.start()) or _is_decimal_dot(text, hit.start())
        ):
            continue
        if token == ":" and _is_clock_colon(text, hit.start()):
            continue
        if hit.end() <= index:
            start = hit.end()
        elif hit.start() >= index:
            end = hit.start()
            break
    return start, end


def _subject_clause(text: str, start: int) -> str:
    """Prose from the start of this clause up to the claim — the subject side.

    Scoring claims still split on list commas so 'Miami answered with a
    3-yard touchdown, went back up on a 5-yard touchdown' does not keep
    Miami as the subject of the second score. Box-stat checks use
    `_stat_clause_span` instead, which keeps 'nine drives, 18 first downs'
    on one subject.
    """
    clause_start = 0
    for hit in _CLAUSE_BREAK.finditer(text[:start]):
        clause_start = hit.end()
    return _YARD_LINE_TEAM.sub(" ", text[clause_start:start])


def _alias_pattern(name: str) -> str:
    """Word pattern. LA/NY also match L.A. / N.Y."""
    compact = re.sub(r"[.\s]", "", (name or "").lower())
    if compact in _AMBIGUOUS_CITY_SHORT:
        first, last = compact
        return rf"(?<![A-Za-z]){first}\.?\s*{last}\.?(?![A-Za-z])"
    return rf"\b{re.escape(name)}\b"


def _alias_hits(span: str, names: dict[str, str]) -> list[tuple[int, int, str]]:
    """Mentions in span. A multi-word city wins over a weak last-word suffix."""
    hits: list[tuple[int, int, str]] = []
    for name in sorted(names, key=len, reverse=True):
        team = names.get(name) or ""
        if len(name) < 2 or not team:
            continue
        for hit in re.finditer(_alias_pattern(name), span or "", re.IGNORECASE):
            if any(
                start <= hit.start() and hit.end() <= end
                for start, end, _team in hits
            ):
                continue
            hits.append((hit.start(), hit.end(), team))
    return hits


def _last_name_pos(span: str, names: dict[str, str]) -> tuple[int, str]:
    """Rightmost listed name in span and its start index, or (-1, '')."""
    best = ""
    best_pos = -1
    for start, _end, team in _alias_hits(span, names):
        if start >= best_pos:
            best_pos = start
            best = team
    return best_pos, best


def _last_name_in(span: str, names: dict[str, str]) -> str:
    return _last_name_pos(span, names)[1]


def _bound_team(
    text: str,
    start: int,
    end: int,
    aliases: dict[str, str],
    player_teams: dict[str, str],
) -> str:
    """Bind a scoring claim to the nearest subject before it, not any team in the sentence.

    'Kelce's 11-yard touchdown answered Miami' is KC. A team named after
    the claim (the opponent being answered) does not win.
    """
    del end  # subject is always before the claim
    subject = _subject_clause(text, start)
    player = _last_name_in(subject, player_teams)
    if player:
        return player
    return _last_name_in(_LOCATION_TEAM.sub(" ", subject), aliases)


def _player_aliases(recap: dict | None) -> dict[str, str]:
    """Last-name → last-name for ESPN box players (usage, passers, scorers)."""
    aliases: dict[str, str] = {}
    payload = recap or {}
    for row in (
        list(payload.get("touches") or [])
        + list(payload.get("passing") or [])
        + list(payload.get("leaders") or [])
    ):
        if not isinstance(row, dict):
            continue
        last = _player_last(row.get("player") or "")
        if last:
            aliases[last] = last
    for play in payload.get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        last = _player_last(play.get("player") or "")
        if last:
            aliases[last] = last
    return aliases


def _leader_stat_yards(recap: dict | None, category: str) -> dict[str, int]:
    """Player last-name → yards from the ESPN leaders block."""
    out: dict[str, int] = {}
    needle = (category or "").lower()
    for row in (recap or {}).get("leaders") or []:
        if not isinstance(row, dict):
            continue
        if needle not in (row.get("category") or "").lower():
            continue
        last = _player_last(row.get("player") or "")
        hit = re.search(r"(\d{2,3})\s*YDS", row.get("value") or "", re.I)
        if last and hit:
            out[last] = int(hit.group(1))
    return out


def _player_owns_stat(
    text: str, claim_at: int, players: dict[str, str], aliases: dict[str, str]
) -> str:
    """Player last name when that player is closer than any team in the clause."""
    if not text or not players:
        return ""
    start, _ = _stat_clause_span(text, claim_at)
    prefix = _LOCATION_TEAM.sub(" ", text[start:claim_at])
    poss = re.search(
        r"([A-Za-z][A-Za-z''-]+(?:\s+[A-Za-z][A-Za-z''-]+)?)['’]s\s*$",
        prefix,
    )
    if poss:
        last = _player_last(poss.group(1))
        if last in players:
            return last
    player_pos, player = _last_name_pos(prefix, players)
    team_pos, _ = _last_name_pos(prefix, aliases)
    if player and player_pos > team_pos:
        return player
    return ""


def _strip_against_object(
    raw: str, text: str, start: int, aliases: dict[str, str]
) -> str:
    """Drop the object of a leading or clause-split 'against Team'."""
    cleaned = re.sub(
        r"^\s*against\s+(?:the\s+)?[A-Za-z][A-Za-z'’.-]*"
        r"(?:\s+[A-Za-z][A-Za-z'’.-]*){0,2}\s+",
        " ",
        raw or "",
        flags=re.I,
    )
    if re.search(r"\bagainst\s*$", text[max(0, start - 16) : start], re.I):
        stripped = re.sub(r"^\s+(?:the\s+)?", "", cleaned, flags=re.I)
        words = re.findall(r"[A-Za-z][A-Za-z'’-]*", stripped[:80])
        for n in range(min(3, len(words)), 0, -1):
            phrase = " ".join(words[:n])
            if _alias_team(phrase, aliases):
                cleaned = re.sub(
                    rf"^\s*(?:the\s+)?{re.escape(phrase)}\b",
                    " ",
                    cleaned,
                    count=1,
                    flags=re.I,
                )
                break
    return cleaned


def _bound_box_team(text: str, claim_at: int, aliases: dict[str, str]) -> str:
    """Nearest team subject in this clause, ignoring in/at location names."""
    start, _ = _stat_clause_span(text, claim_at)
    raw = _strip_against_object(text[start:claim_at], text, start, aliases)
    prefix = _POSSESSION_OBJECT.sub(
        " ",
        _OBJECT_TEAM.sub(
            " ",
            _LOCATION_TEAM.sub(
                " ",
                _GAME_SCORE_LABEL.sub(
                    " ",
                    _GAME_LABEL_TEAM.sub(
                        " ", _YARD_LINE_TEAM.sub(" ", raw)
                    ),
                ),
            ),
        ),
    )
    poss = re.search(
        r"([A-Za-z][A-Za-z '’-]+?)['’]s\s*$",
        prefix,
    )
    if poss:
        named = _alias_team(poss.group(1), aliases)
        if named:
            return named
    return _last_name_in(prefix, aliases)


def _scrub_box_labels(span: str) -> str:
    return _POSSESSION_OBJECT.sub(
        " ",
        _OBJECT_TEAM.sub(
            " ",
            _LOCATION_TEAM.sub(
                " ",
                _GAME_SCORE_LABEL.sub(
                    " ",
                    _GAME_LABEL_TEAM.sub(
                        " ", _YARD_LINE_TEAM.sub(" ", span or "")
                    ),
                ),
            ),
        ),
    )


def _possession_local_span(text: str, match: re.Match) -> tuple[int, int]:
    """Clause holding this clock, split on 'to' when it separates two TOPs.

    '25:39 to Miami's 34:21' is two claims. 'squeezed that offense to 88
    and 25:39' is one claim — only one side has a clock, so do not split.
    """
    start, end = _stat_clause_span(text, match.start())
    for hit in re.finditer(r"\bto\b", text[start:end], re.I):
        abs_at = start + hit.start()
        left = text[start:abs_at]
        right = text[start + hit.end() : end]
        if not (_POSSESSION_CLOCK.search(left) and _POSSESSION_CLOCK.search(right)):
            continue
        if match.start() < abs_at:
            return start, abs_at
        return start + hit.end(), end
    return start, end


def _first_alias_in(span: str, aliases: dict[str, str]) -> str:
    """Leftmost team mention in span — nearest after a clock."""
    best = ""
    best_pos = None
    for start, _end, team in _alias_hits(span, aliases):
        if best_pos is None or start < best_pos:
            best_pos = start
            best = team
    return best


def _alias_leading_phrase(phrase: str, aliases: dict[str, str]) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", phrase or "")
    for n in range(min(3, len(words)), 0, -1):
        named = _alias_team(" ".join(words[:n]), aliases)
        if named:
            return named
    return ""


def _bound_possession_team(
    text: str, match: re.Match, aliases: dict[str, str]
) -> str:
    """Nearest team mention on this clock's own clause.

    Possessive 'Miami's 34:21' and '34:21 for Miami' beat an earlier
    Chiefs subject so a split line keeps each TOP with its club. Do not
    look across another possession clock or a 'to' that separates two
    clocks — that is how #144's both-ways bind was supposed to work.
    """
    start, end = _possession_local_span(text, match)
    local = _strip_against_object(text[start:end], text, start, aliases)
    held = re.search(
        r"((?:the\s+)?[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,2})\s+"
        r"(?:held|hold(?:s|ing)?)\s+(?:the ball|it)\b",
        text[start:match.end()],
    )
    if held:
        named = _alias_team(held.group(1), aliases)
        if named:
            return named
    # "Time of possession: KC 28:48, LV 31:12" — the code before the clock owns it.
    lead_clock = re.search(
        r"((?:the\s+)?[A-Za-z][A-Za-z .’-]*?)\s*$",
        text[start:match.start()],
    )
    if lead_clock:
        named = _team_code(lead_clock.group(1), aliases) or _alias_team(
            lead_clock.group(1), aliases
        )
        if named:
            return named
    if re.search(r"\b(?:held|hold(?:s|ing)?)\s+(?:the ball|it)\b", local, re.I):
        lead = re.match(
            r"\s*(?:the\s+)?([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,2})",
            local,
        )
        if lead:
            named = _alias_team(lead.group(1), aliases)
            if named:
                return named
    prefix = _scrub_box_labels(text[start:match.start()])
    suffix = _scrub_box_labels(text[match.end() : end])
    poss = re.search(
        r"([A-Za-z][A-Za-z '’-]+?)['’]s\s*$",
        prefix,
    )
    if poss:
        named = _alias_team(poss.group(1), aliases)
        if named:
            return named
    attached = re.match(
        r"^\s+for\s+(?:the\s+)?([A-Za-z][A-Za-z '’-]+)",
        suffix,
        re.I,
    )
    if attached:
        named = _alias_leading_phrase(attached.group(1), aliases)
        if named:
            return named
    left_pos, left_team = _last_name_pos(prefix, aliases)
    right_team = _first_alias_in(suffix, aliases)
    if left_team and right_team:
        left_dist = len(prefix) - left_pos if left_pos >= 0 else 10**9
        right_hit = None
        for name, abbr in aliases.items():
            if abbr != right_team:
                continue
            hit = re.search(rf"\b{re.escape(name)}\b", suffix, re.I)
            if hit and (right_hit is None or hit.start() < right_hit):
                right_hit = hit.start()
        right_dist = 10**9 if right_hit is None else right_hit
        return right_team if right_dist <= left_dist else left_team
    return right_team or left_team


def _claim_quote(text: str, match: re.Match) -> str:
    """Sentence (or match token) so salvage drops only this claim.

    ``_sentence_at`` does not stop on newlines, so a previous field
    without a period used to become part of the quote and miss the
    lede on drop. A short token like '70 rushing yards' used to drop
    Walker's own 70 as well.
    """
    start, end = _sentence_span(text, match.start())
    nl = text.rfind("\n", start, match.start())
    if nl >= 0:
        start = nl + 1
    sentence = text[start:end].strip()
    if sentence and match.group(0) in sentence:
        return sentence
    return match.group(0)


def _clock_quote(text: str, match: re.Match) -> str:
    return _claim_quote(text, match)


def _attempt_kind(text: str, match: re.Match) -> str:
    """Rushing attempts vs dropbacks / pass attempts from the local clause."""
    token = match.group(0).lower()
    if "drop" in token or "pass" in token or "threw" in token:
        return "pass"
    start, end = _stat_clause_span(text, match.start())
    window = text[max(start, match.start() - 48) : min(end, match.end() + 16)]
    if _RUSH_ATTEMPT_NEAR.search(window):
        return "rush"
    return "pass"


def _explicit_pass_attempt(match: re.Match) -> bool:
    token = match.group(0).lower()
    return bool(re.search(r"drop|pass|threw", token))


def _score_team_for_yards(recap: dict | None, kind: str, yards: int) -> str:
    """Team that owns this scoring-play distance on the ESPN list.

    When only one club has an 11-yard TD, that play's team wins — do not
    bind the claim to Miami just because Miami is named nearby.
    """
    owners = [
        team
        for team, bag in official_yards_by_team(recap, kind).items()
        if yards in bag
    ]
    if len(owners) == 1:
        return owners[0]
    return ""


def official_missed_fg_yards(recap: dict | None) -> set[int]:
    out: set[int] = set()
    for row in (recap or {}).get("driveResults") or []:
        if not isinstance(row, dict):
            continue
        if (row.get("result") or "").lower() != "missed fg":
            continue
        yards = row.get("yards")
        try:
            if yards not in (None, ""):
                out.add(int(yards))
        except (TypeError, ValueError):
            continue
    for play in _plays(recap):
        text = (play.get("text") or "").lower()
        if "no good" not in text and "wide" not in text and "miss" not in text:
            continue
        if "field goal" not in text and "yarder" not in text:
            continue
        yards = play.get("yards")
        try:
            if yards not in (None, ""):
                out.add(int(yards))
        except (TypeError, ValueError):
            continue
    return out


def _parse_count_word(raw: str):
    token = (raw or "").strip().lower()
    if token in _WORD_COUNTS:
        return _WORD_COUNTS[token]
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def _norm_team_phrase(phrase: str) -> str:
    token = " ".join((phrase or "").lower().split())
    token = re.sub(r"^the\s+", "", token)
    return token.strip(" .")


def _team_code(phrase: str, aliases: dict[str, str] | None = None) -> str:
    """Resolve a subject to an NFL code. Bare LV/TB/PHI beat the alias table."""
    token = _norm_team_phrase(phrase)
    if not token:
        return ""
    compact = token.replace(".", "").replace(" ", "")
    if compact.upper() in _NFL_ABBR and compact.isalpha() and len(compact) <= 3:
        return compact.upper()
    if aliases:
        return _alias_team(token, aliases)
    return ""


def _alias_team(phrase: str, aliases: dict[str, str]) -> str:
    token = _norm_team_phrase(phrase)
    if not token:
        return ""
    compact = token.replace(".", "").replace(" ", "")
    for key in (token, compact):
        team = aliases.get(key) or ""
        if team:
            return team
    for alias in sorted(aliases, key=len, reverse=True):
        if not aliases.get(alias):
            continue
        if re.search(_alias_pattern(alias), token, re.IGNORECASE):
            return aliases[alias]
    return ""


def _box_yards_int(raw) -> int | None:
    token = str(raw or "").replace(",", "").strip()
    if not token:
        return None
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def official_box_yards_by_team(
    recap: dict | None, kind: str, game: str = "last"
) -> dict[str, int]:
    """Team rushing / passing totals from the last or prior-game box."""
    payload = recap or {}
    if game == "prior":
        payload = payload.get("prior") or {}
    field = "rushingYards" if kind == "rush" else "netPassingYards"
    out: dict[str, int] = {}
    kc = _box_yards_int((payload.get("kc") or {}).get(field))
    if kc is not None:
        out["KC"] = kc
    opp_abbr = (payload.get("oppAbbr") or "").strip().upper()
    opp = _box_yards_int((payload.get("opp") or {}).get(field))
    if opp is not None and opp_abbr:
        out[opp_abbr] = opp
    return out


def official_yards_by_team(recap: dict | None, kind: str) -> dict[str, set[int]]:
    """Scoring-play yards, or team box yards when kind is rush/pass.

    Rush/pass look at the last-game recap and the prior-game recap so a
    Colts-week rushing total cannot be swapped for Walker's line.
    """
    if kind in {"rush", "pass"}:
        by_team: dict[str, set[int]] = {}
        for game in ("last", "prior"):
            for team, yards in official_box_yards_by_team(recap, kind, game).items():
                by_team.setdefault(team, set()).add(yards)
        return by_team
    by_team = {}
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        if kind and _play_kind(play) != kind:
            continue
        yards = _play_yards(play)
        if yards is None:
            continue
        team = _play_team(play) or "?"
        by_team.setdefault(team, set()).add(yards)
    return by_team


def _play_quarter(play: dict):
    try:
        return int(play.get("quarter"))
    except (TypeError, ValueError):
        return None


def official_count_by_team(
    recap: dict | None, kind: str, quarters: frozenset | None = None
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        if _play_kind(play) != kind:
            continue
        if quarters is not None:
            qtr = _play_quarter(play)
            if qtr not in quarters:
                continue
        team = _play_team(play) or "?"
        counts[team] = counts.get(team, 0) + 1
    return counts


def _normalize_scope_token(raw: str) -> str:
    token = re.sub(r"\s+", " ", (raw or "").lower().replace("–", "-"))
    token = re.sub(r"^in the ", "", token)
    token = token.replace("q ", "q")
    return token


def _count_scope(text: str, start: int, end: int):
    """Return None (whole game), a quarter frozenset, or 'skip'."""
    window = text[max(0, start - 72) : min(len(text), end + 72)]
    match = _COUNT_SCOPE.search(window)
    if not match:
        return None
    token = _normalize_scope_token(match.group(1))
    if token in _WHOLE_GAME_SCOPES:
        return None
    if token in _SCOPE_QUARTERS:
        return _SCOPE_QUARTERS[token]
    return "skip"


def _pair_allowed(pair: tuple[int, int], pairs: set[tuple[int, int]]) -> bool:
    """Score order is irrelevant: 24-10 and 10-24 are the same pair."""
    return pair in pairs or (pair[1], pair[0]) in pairs


def _mask_stat_lookalikes(text: str) -> str:
    masked = _OF_LINE.sub("X-of-Y", text)
    return _SLASH_LINE.sub("X/Y", masked)


def _int_pair(a, b):
    try:
        return int(a), int(b)
    except (TypeError, ValueError):
        return None


def allowed_score_pairs(last_game: dict | None, recap: dict | None) -> set[tuple[int, int]]:
    """Official final plus every ESPN score-after. Both orders are allowed.

    0-0 is always a real game state (kickoff, or any tie at the start).
    """
    pairs: set[tuple[int, int]] = {(0, 0)}
    last = last_game or {}
    kc, opp = last.get("kcScore"), last.get("oppScore")
    pair = _int_pair(kc, opp)
    if pair:
        pairs.add(pair)
        pairs.add((pair[1], pair[0]))
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        play_pair = _int_pair(play.get("kcScore"), play.get("oppScore"))
        if play_pair:
            pairs.add(play_pair)
            pairs.add((play_pair[1], play_pair[0]))
        after = play.get("scoreAfter") or ""
        match = re.search(r"(\d+)\s*[–-]\s*(\d+)", after)
        if match:
            after_pair = _int_pair(match.group(1), match.group(2))
            if after_pair:
                pairs.add(after_pair)
                pairs.add((after_pair[1], after_pair[0]))
    return pairs


def official_score_yards(recap: dict | None) -> set[int]:
    """Yardage numbers on ESPN scoring plays (TD and FG)."""
    yards: set[int] = set()
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        value = play.get("yards")
        if value is None or value == "":
            continue
        try:
            yards.add(int(value))
        except (TypeError, ValueError):
            continue
    return yards


def official_td_yards(recap: dict | None) -> set[int]:
    yards: set[int] = set()
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        typ = (play.get("type") or "").strip().lower()
        if typ not in _TD_TYPES and "touchdown" not in typ and typ != "td":
            continue
        value = play.get("yards")
        if value is None or value == "":
            continue
        try:
            yards.add(int(value))
        except (TypeError, ValueError):
            continue
    return yards


def _check_scores(text: str, last_game: dict, recap: dict) -> list[str]:
    pairs = allowed_score_pairs(last_game, recap)
    if not pairs:
        return []
    masked = _mask_stat_lookalikes(text)
    issues = []
    seen: set[tuple[int, int]] = set()

    def _consider(match) -> None:
        pair = _int_pair(match.group(1), match.group(2))
        if not pair or pair in seen:
            return
        if _pair_allowed(pair, pairs):
            seen.add(pair)
            return
        seen.add(pair)
        issues.append(
            f"score {pair[0]}-{pair[1]} is not the official final "
            f"or an ESPN score-after ({match.group(0)!r})"
        )

    for rx in _SCORE_PATTERNS:
        for match in rx.finditer(masked):
            _consider(match)
    # Bare won/lost is a score claim only when the pair is a known score
    # (either order). Unknown pairs stay out — "lost 18-19" is first downs.
    for match in _LOOSE_WON_LOST.finditer(masked):
        pair = _int_pair(match.group(1), match.group(2))
        if not pair or pair in seen:
            continue
        if _pair_allowed(pair, pairs):
            seen.add(pair)
    # "14-10 game" is a score-after when that pair is official. "3-0 game"
    # is the slate record (one side is 0, not on the ESPN score-after list).
    for match in _GAME_AS_SCORE.finditer(masked):
        pair = _int_pair(match.group(1), match.group(2))
        if not pair or pair in seen:
            continue
        if _pair_allowed(pair, pairs):
            seen.add(pair)
            continue
        if 0 in pair and max(pair) <= 16:
            continue
        _consider(match)
    return issues


def _check_td_yards(
    text: str, recap: dict, last_game: dict | None = None
) -> list[str]:
    allowed = official_score_yards(recap)
    if not allowed:
        return []
    by_team = official_yards_by_team(recap, "td")
    aliases = _team_aliases(last_game, recap)
    players = _player_teams(recap)
    issues = []
    seen: set[tuple[str, int]] = set()
    for match in _TD_YARDS.finditer(text):
        raw = match.group(1) or match.group(2)
        try:
            yards = int(raw)
        except (TypeError, ValueError):
            continue
        if match.group(1):
            window = text[match.start() : min(len(text), match.end() + 56)]
            if re.search(
                r"field goal|instead of a touchdown|rush(?:ing)? yards|passing yards",
                window,
                re.I,
            ):
                continue
            # 'from the 18 first downs' is volume, not a scoring-play mark.
            if re.match(r"\s+first downs?\b", text[match.end() :], re.I):
                continue
            # 'from the 9-9 tape' is a final score, not a yard line.
            if re.match(r"-\d+", text[match.end() :]):
                continue
            sentence = _sentence_at(text, match.start())
            if re.search(
                r"\b(?:not to kick|not kick|if it is|4th-and-goal|"
                r"from the \d+-?in|plan from the|number from the|"
                r"last resort|red-zone|mesh|no hero ball|"
                r"(?:first|second|third|fourth)-and)\b",
                sentence,
                re.I,
            ):
                continue
        bound = _bound_team(text, match.start(), match.end(), aliases, players)
        team = bound or _score_team_for_yards(recap, "td", yards)
        key = (team or "*", yards)
        if key in seen:
            continue
        seen.add(key)
        if team:
            team_td = by_team.get(team, set())
            if yards not in team_td:
                issues.append(
                    f"scoring yardage {yards} is not a {team} ESPN touchdown "
                    f"({match.group(0)!r}; {team} TD yards "
                    f"{sorted(team_td) or 'none'})"
                )
            continue
        if yards not in allowed:
            issues.append(
                f"scoring yardage {yards} is not on the ESPN scoring-play list "
                f"({match.group(0)!r}; official yards {sorted(allowed)})"
            )
    return issues


def _check_fg_claims(
    text: str, recap: dict, last_game: dict | None = None
) -> list[str]:
    by_team = official_yards_by_team(recap, "fg")
    if not by_team:
        return []
    aliases = _team_aliases(last_game, recap)
    players = _player_teams(recap)
    issues = []

    for match in _FG_LIST.finditer(text):
        yards = []
        for raw in re.findall(r"\d{1,2}", match.group(1) or ""):
            try:
                yards.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not yards:
            continue
        missed = official_missed_fg_yards(recap)
        team = _bound_team(text, match.start(), match.end(), aliases, players)
        sentence = _sentence_at(text, match.start())
        if re.search(r"\bbutker\b", sentence, re.I):
            team = "KC"
        owners = []
        for value in yards:
            hit = [abbr for abbr, bag in by_team.items() if value in bag]
            if value in missed:
                hit.append("MISS")
            owners.append(set(hit))
        shared = owners[0]
        for bag in owners[1:]:
            shared &= bag
        if team:
            team_fg = by_team.get(team, set()) | missed
            bad = [v for v in yards if v not in team_fg]
            if bad:
                issues.append(
                    f"field goals {yards} credited to {team} are not that "
                    f"team's ESPN kicks ({match.group(0)!r}; {team} FG yards "
                    f"{sorted(team_fg) or 'none'})"
                )
        elif not shared:
            issues.append(
                f"field-goal yardage {yards} mixes teams or is not on the "
                f"ESPN FG list ({match.group(0)!r})"
            )

    for match in _FG_YARD.finditer(text):
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            continue
        bound = _bound_team(text, match.start(), match.end(), aliases, players)
        team = bound or _score_team_for_yards(recap, "fg", yards)
        sentence = _sentence_at(text, match.start())
        if re.search(
            r"\blet a \d+-yard field goal stand\b|"
            r"\bjason myers\b",
            sentence,
            re.I,
        ):
            continue
        last_abbr = ((recap or {}).get("oppAbbr") or "").strip().upper()
        if (
            last_abbr
            and re.search(
                rf"\b(?:replace\s+the\s+)?{re.escape(last_abbr)}"
                r"|seattle|seahawks",
                sentence,
                re.I,
            )
            and yards in by_team.get("KC", set())
            and re.search(
                r"\b(?:replace|the seattle \d+-yard|against seattle|"
                r"butker from)\b",
                sentence,
                re.I,
            )
        ):
            team = "KC"
        all_fg = set().union(*by_team.values()) if by_team else set()
        missed = official_missed_fg_yards(recap)
        all_fg |= missed
        if yards in missed:
            continue
        if team:
            if yards not in by_team.get(team, set()) and yards not in missed:
                issues.append(
                    f"field-goal yardage {yards} is not a {team} ESPN kick "
                    f"({match.group(0)!r})"
                )
        elif yards not in all_fg:
            issues.append(
                f"field-goal yardage {yards} is not on the ESPN FG list "
                f"({match.group(0)!r})"
            )

    for match in _FG_COUNT.finditer(text):
        issue = _count_issue(
            text, match, recap, "fg", aliases, "field-goal count"
        )
        if issue:
            issues.append(issue)

    for match in _TD_COUNT.finditer(text):
        issue = _count_issue(
            text, match, recap, "td", aliases, "touchdown count"
        )
        if issue:
            issues.append(issue)
    return issues


def _count_issue(
    text: str,
    match,
    recap: dict,
    kind: str,
    aliases: dict[str, str],
    label: str,
):
    claimed = _parse_count_word(match.group(1))
    team = _alias_team(match.group(2) or "", aliases)
    if claimed is None or not team:
        return None
    scope = _count_scope(text, match.start(), match.end())
    if scope == "skip":
        return None
    official = official_count_by_team(recap, kind, quarters=scope).get(team, 0)
    if claimed == official:
        return None
    return (
        f"{team} {label} {claimed} disagrees with ESPN "
        f"{official} ({match.group(0)!r})"
    )


def _check_part_of_day(
    text: str, last_game: dict | None, recap: dict | None = None
) -> list[str]:
    part = collect.kickoff_part_of_day((last_game or {}).get("date") or "")
    if part not in _DAYTIME or not text:
        return []
    prior_labels = _prior_game_labels(recap)
    issues = []
    seen: set[str] = set()
    last_labels = _last_game_labels(last_game, recap)
    for match in _NIGHT_WORD.finditer(text):
        sentence = _sentence_at(text, match.start())
        low = sentence.lower()
        if _NIGHT_IDIOM.search(low):
            continue
        if prior_labels and any(
            re.search(rf"\b{re.escape(lab)}\b", low) for lab in prior_labels
        ):
            continue
        if _foreign_team_sentence(sentence, last_game, recap):
            continue
        about_this = bool(_NIGHT_THIS_GAME.search(low)) or any(
            re.search(rf"\b{re.escape(lab)}\b", low) for lab in last_labels
        )
        if not about_this:
            continue
        snippet = match.group(0)
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            f"kickoff is {part}; do not write {snippet!r} about the game"
        )
    return issues


_FOREIGN_TEAMS = frozenset(
    {
        "chargers",
        "lac",
        "bills",
        "ravens",
        "bengals",
        "browns",
        "steelers",
        "texans",
        "colts",
        "jaguars",
        "titans",
        "broncos",
        "raiders",
        "patriots",
        "jets",
        "dolphins",
        "eagles",
        "cowboys",
        "giants",
        "commanders",
        "bears",
        "lions",
        "packers",
        "vikings",
        "falcons",
        "panthers",
        "saints",
        "buccaneers",
        "cardinals",
        "rams",
        "49ers",
        "seahawks",
    }
)


def _foreign_team_sentence(
    sentence: str, last_game: dict | None, recap: dict | None
) -> bool:
    """True when the sentence is about some other club, not this tape."""
    low = (sentence or "").lower()
    known = {"kc", "chiefs", "kansas"}
    for token in re.findall(r"[A-Za-z]+", (last_game or {}).get("opponent") or ""):
        if len(token) >= 3:
            known.add(token.lower())
    opp = ((recap or {}).get("oppAbbr") or "").strip().lower()
    if opp:
        known.add(opp)
    known |= _prior_game_labels(recap)
    hits = [name for name in _FOREIGN_TEAMS if re.search(rf"\b{re.escape(name)}\b", low)]
    if not hits:
        return False
    return not any(name in known for name in hits)


def _check_echoed_instructions(text: str) -> list[str]:
    if not text:
        return []
    issues = []
    seen: set[str] = set()
    for match in _ECHO_INSTRUCTION.finditer(text):
        snippet = match.group(0)
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        issues.append(f"echoed writer instruction ({snippet!r})")
    return issues


def _check_record_phrasing(text: str) -> list[str]:
    if not text:
        return []
    issues = []
    for match in _GARBLED_RECORD.finditer(text):
        issues.append(
            f"garbled record/ordinal phrasing ({match.group(0)!r})"
        )
    return issues


def _parse_pass_line(value: str):
    match = _PASS_LINE.search(value or "")
    if not match:
        return None
    try:
        comp, att = int(match.group(1)), int(match.group(2))
    except (TypeError, ValueError):
        return None
    yds = None
    if match.group(3):
        try:
            yds = int(match.group(3))
        except (TypeError, ValueError):
            yds = None
    return comp, att, yds


def _final_score_pairs(last_game: dict | None) -> set[tuple[int, int]]:
    pair = _int_pair((last_game or {}).get("kcScore"), (last_game or {}).get("oppScore"))
    if not pair:
        return set()
    return {pair, (pair[1], pair[0])}


def _down_context(text: str, start: int, end: int, pad: int = 48) -> bool:
    window = text[max(0, start - pad) : min(len(text), end + pad)]
    return bool(_DOWN_CTX.search(window))


def _bound_pass_matches(text: str, last_name: str):
    """Yield (match, comp, att, yds) only when the line is bound to last_name."""
    if not last_name or not text:
        return
    name = re.escape(last_name)
    token = (
        r"(\d{1,2})\s*(?:/|-of-)\s*(\d{1,2})"
        r"(?:\s*,\s*(\d{2,3})\s*(?:YDS|yards))?"
    )
    patterns = (
        # "Mahomes went 20-of-24" / "Mahomes was 20/24" / "Mahomes' 20-of-24"
        rf"{name}(?:['’]s)?(?:\s+{_BIND_VERBS})?\s+{token}",
        # "Mahomes (20/24, 246 YDS)"
        rf"{name}\s*\(\s*{token}",
        # "20-of-24 from Mahomes" / "20-of-24, Mahomes finished"
        rf"{token}\s+(?:from\s+)?(?:{_BIND_VERBS}\s+)?{name}",
    )
    for raw in patterns:
        rx = re.compile(raw, re.IGNORECASE)
        for match in rx.finditer(text):
            claimed = _parse_pass_line(match.group(0))
            if claimed:
                yield match, claimed


def _check_stat_lines(text: str, recap: dict, last_game: dict | None = None) -> list[str]:
    issues = []
    finals = _final_score_pairs(last_game)
    for leader in (recap or {}).get("leaders") or []:
        if not isinstance(leader, dict):
            continue
        player = (leader.get("player") or "").strip()
        value = leader.get("value") or ""
        last = player.split()[-1] if player else ""
        if len(last) < 3:
            continue
        official = _parse_pass_line(value)
        if not official:
            continue
        seen: set[tuple[int, int]] = set()
        for match, claimed in _bound_pass_matches(text, last):
            if _down_context(text, match.start(), match.end()):
                continue
            pair = (claimed[0], claimed[1])
            if pair in finals:
                continue
            if pair in seen:
                continue
            seen.add(pair)
            if pair != (official[0], official[1]):
                issues.append(
                    f"{player} passing line {claimed[0]}-of-{claimed[1]} "
                    f"disagrees with ESPN {official[0]}/{official[1]}"
                )
            if (
                claimed[2] is not None
                and official[2] is not None
                and claimed[2] != official[2]
            ):
                issues.append(
                    f"{player} passing yards {claimed[2]} disagree with "
                    f"ESPN {official[2]}"
                )
    return issues


def _clock_seconds(raw) -> int:
    text = str(raw or "0:00")
    parts = [p for p in text.replace(".", ":").split(":") if p != ""]
    try:
        if len(parts) >= 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0])
    except (TypeError, ValueError):
        return 0


def _event_kind(raw: str) -> str:
    token = (raw or "").strip().lower()
    if token in _TD_TYPES or token == "td" or "touchdown" in token:
        return "td"
    if token in _FG_TYPES or "field goal" in token:
        return "fg"
    if token in {"int", "interception"} or "intercept" in token:
        return "int"
    if "fumble" in token:
        return "fumble"
    if "miss" in token:
        return "missed fg"
    if "down" in token:
        return "turnover on downs"
    return token


def _event_sort_key(event: dict) -> tuple:
    try:
        quarter = int(event.get("quarter") or 0)
    except (TypeError, ValueError):
        quarter = 0
    return (quarter, -_clock_seconds(event.get("clock")))


def _timeline(recap: dict | None) -> list[dict]:
    events: list[dict] = []
    for play in (recap or {}).get("scoringPlays") or []:
        if not isinstance(play, dict):
            continue
        kind = _event_kind(_play_kind(play) or play.get("type") or "")
        if not kind:
            continue
        pair = _int_pair(play.get("kcScore"), play.get("oppScore"))
        after = play.get("scoreAfter") or ""
        match = re.search(r"(\d+)\s*[–-]\s*(\d+)", after)
        if not pair and match:
            pair = _int_pair(match.group(1), match.group(2))
        player = (play.get("player") or "").strip()
        events.append(
            {
                "kind": kind,
                "yards": _play_yards(play),
                "player": player,
                "quarter": play.get("quarter"),
                "clock": play.get("clock") or "",
                "pair": pair,
                "blob": " ".join(
                    p for p in (player, kind, str(play.get("yards") or ""), after)
                    if p
                ).lower(),
                "label": (
                    f"{player or kind} {play.get('yards') or ''}yd {kind} "
                    f"Q{play.get('quarter')} {play.get('clock') or ''}"
                ).strip(),
            }
        )
    for row in (recap or {}).get("driveResults") or []:
        if not isinstance(row, dict):
            continue
        kind = _event_kind(row.get("result") or "")
        if not kind:
            continue
        player = (row.get("player") or "").strip()
        detail = (row.get("detail") or "").strip()
        events.append(
            {
                "kind": kind,
                "yards": row.get("yards"),
                "player": player,
                "quarter": row.get("quarter"),
                "clock": row.get("clock") or "",
                "pair": None,
                "blob": " ".join(p for p in (player, detail, kind) if p).lower(),
                "label": (
                    f"{player or detail or kind} {kind} "
                    f"Q{row.get('quarter')} {row.get('clock') or ''}"
                ).strip(),
            }
        )
    for play in _plays(recap):
        kind = _event_kind(play.get("kind") or "")
        text = play.get("text") or ""
        if not kind:
            if "pass" in text.lower() and "incomplete" not in text.lower():
                kind = "completion"
            elif play.get("kind") == "rush" or "end" in (play.get("direction") or ""):
                kind = "rush"
            else:
                continue
        player = (
            play.get("target")
            or play.get("interceptedBy")
            or play.get("forcedBy")
            or ""
        )
        events.append(
            {
                "kind": kind,
                "yards": play.get("yards"),
                "player": player,
                "quarter": play.get("quarter"),
                "clock": play.get("clock") or "",
                "pair": None,
                "blob": " ".join(
                    p
                    for p in (player, text, kind, str(play.get("yards") or ""))
                    if p
                ).lower(),
                "label": (
                    f"{player or text[:48] or kind} {play.get('yards') or ''}yd "
                    f"{kind} Q{play.get('quarter')} {play.get('clock') or ''}"
                ).strip(),
            }
        )
    events.sort(key=_event_sort_key)
    deduped = []
    seen: set[tuple] = set()
    for event in events:
        key = (
            event.get("kind"),
            event.get("quarter"),
            event.get("clock"),
            event.get("yards"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _span_names(span: str) -> list[str]:
    names = []
    for hit in _SEQUENCE_NAME.finditer(span or ""):
        token = hit.group(0).lower().replace("’", "'")
        if token in _SEQUENCE_STOP:
            continue
        names.append(token)
    return names


def _resolve_timeline_event(span: str, events: list[dict], prefer_last: bool = False):
    """Map a prose span to one ESPN timeline event, or None if ambiguous."""
    if not span or not events:
        return None
    yards = None
    want_kind = ""
    pair = None
    score = None
    if prefer_last:
        for hit in _SEQUENCE_SCORE.finditer(span):
            score = hit
    else:
        score = _SEQUENCE_SCORE.search(span)
    if score:
        raw_yards = score.group(1) or score.group(2)
        if raw_yards:
            try:
                yards = int(raw_yards)
            except (TypeError, ValueError):
                yards = None
            low = score.group(0).lower()
            if "field goal" in low:
                want_kind = "fg"
            elif "td" in low or "touchdown" in low or score.group(1):
                want_kind = "td"
        elif score.group(3) and score.group(4):
            pair = _int_pair(score.group(3), score.group(4))
        else:
            pair = None
    else:
        pair = None
    if yards is None:
        gain = None
        if prefer_last:
            for hit in _SEQUENCE_GAIN.finditer(span):
                gain = hit
        else:
            gain = _SEQUENCE_GAIN.search(span)
        if gain:
            try:
                yards = int(gain.group(1) or gain.group(2))
            except (TypeError, ValueError):
                yards = None
    turn = _SEQUENCE_TURNOVER.search(span)
    # A described gain (Kelce 48) is not the nearest turnover just because
    # a passer name also appears on an INT play.
    if turn and yards is None:
        want_kind = _event_kind(turn.group(1))
        pair = None
    names = _span_names(span)
    hits = []
    for event in events:
        if want_kind and event["kind"] != want_kind:
            continue
        if yards is not None and event.get("yards") != yards:
            continue
        if pair and event.get("pair"):
            if not _pair_allowed(pair, {event["pair"], (event["pair"][1], event["pair"][0])}):
                continue
        elif pair and not event.get("pair"):
            continue
        if names and not any(name in event["blob"] for name in names):
            # Scoring yardage / score-after can stand alone.
            if not (yards is not None or (pair and event.get("pair"))):
                continue
        hits.append(event)
    if len(hits) == 1:
        return hits[0]
    if names:
        named = [event for event in hits if any(name in event["blob"] for name in names)]
        if len(named) == 1:
            return named[0]
    return None


def _right_span(text: str, start: int) -> str:
    chunk = text[start : min(len(text), start + 80)]
    return re.split(r"[.;,!?]|—", chunk, maxsplit=1)[0].strip()


def _left_span(text: str, start: int) -> str:
    chunk = text[max(0, start - 120) : start]
    return re.split(r"[.;!?]|—", chunk)[-1].strip()


def _sequence_source_span(text: str, match: re.Match, left: str, right: str) -> str:
    """Exact source slice covering the after/following claim.

    Reconstructing ``left + match + right`` glued the stripped left span
    onto ``after`` (run 37030574826: ``nobodyafter``), so salvage could
    not find the sentence and left an inverted Q1-after-Q2 claim.
    """
    start = match.start()
    if left:
        found = text.rfind(left, max(0, start - 160), start)
        if found >= 0:
            start = found
    end = match.end()
    if right:
        found = text.find(right, end, end + 160)
        if found >= 0:
            end = found + len(right)
    return text[start:end]


def _check_play_sequence(text: str, recap: dict | None) -> list[str]:
    """'A after B' / 'A following B' must match ESPN play order."""
    events = _timeline(recap)
    if len(events) < 2 or not text:
        return []
    issues = []
    seen: set[str] = set()
    for match in _AFTER_FOLLOWING.finditer(text):
        left = _left_span(text, match.start())
        right = _right_span(text, match.end())
        if _AFTER_NON_PLAY.search(right):
            continue
        first = _resolve_timeline_event(left, events, prefer_last=True)
        second = _resolve_timeline_event(right, events)
        if not first or not second or first is second:
            continue
        # One side must be a scoring play; the other a turnover or another play.
        kinds = {first["kind"], second["kind"]}
        if not kinds & {"td", "fg"}:
            continue
        if _event_sort_key(first) > _event_sort_key(second):
            continue
        snippet = re.sub(r"\s+", " ", _sequence_source_span(text, match, left, right)).strip()
        key = snippet.lower()
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            f"play order: {first['label']} is not after {second['label']} "
            f"({snippet!r})"
        )
    return issues


def _last_name_token(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        return ""
    token = token.replace(".", " ").replace("'", "")
    parts = [p for p in re.split(r"[^A-Za-z]+", token) if p]
    if not parts:
        return ""
    return parts[-1].lower()


def _plays(recap: dict | None) -> list[dict]:
    out = []
    for play in (recap or {}).get("plays") or []:
        if isinstance(play, dict):
            out.append(play)
    return out


def _official_names(plays: list[dict], *fields: str) -> set[str]:
    names: set[str] = set()
    for play in plays:
        for field in fields:
            token = _last_name_token(play.get(field) or "")
            if token:
                names.add(token)
        blob = (play.get("text") or "").lower()
        for field in fields:
            raw = play.get(field) or ""
            last = _last_name_token(raw)
            if last and last in blob:
                names.add(last)
    return names


def _parse_count(token: str | None):
    raw = (token or "").strip().lower().replace(",", "")
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if raw in {"a", "an"}:
        return 1
    if raw in _NUMBER_WORDS:
        return _NUMBER_WORDS[raw]
    if raw == "once":
        return 1
    if raw == "twice":
        return 2
    if "-" in raw:
        tens, ones = raw.split("-", 1)
        left = _NUMBER_WORDS.get(tens)
        right = _NUMBER_WORDS.get(ones)
        if left is not None and right is not None and left % 10 == 0 and 1 <= right <= 9:
            return left + right
    return None


def _match_count(match) -> int | None:
    for value in match.groups():
        parsed = _parse_count(value)
        if parsed is not None:
            return parsed
    return None


def _is_initial_dot(text: str, pos: int) -> bool:
    """True for the period in 'H. Nourzad' / 'Tr. Smith', not a sentence end."""
    if pos <= 0 or pos >= len(text) or text[pos] != ".":
        return False
    if text[pos - 1].isupper() and (pos < 2 or not text[pos - 2].isalpha()):
        return True
    # NFL two-letter initials: Tr.Smith / Tr. Smith. "later." stays a stop.
    if (
        pos >= 2
        and text[pos - 1].islower()
        and text[pos - 2].isupper()
        and (pos < 3 or not text[pos - 3].isalpha())
    ):
        nxt = text[pos + 1] if pos + 1 < len(text) else ""
        return nxt == "" or nxt.isspace() or nxt.isupper()
    return False


def _sentence_span(text: str, index: int) -> tuple[int, int]:
    start = 0
    pos = text.rfind(".", 0, index)
    while pos >= 0 and _is_initial_dot(text, pos):
        pos = text.rfind(".", 0, pos)
    if pos >= 0:
        start = pos + 1
    end = len(text)
    pos = text.find(".", index)
    while 0 <= pos < len(text) and _is_initial_dot(text, pos):
        pos = text.find(".", pos + 1)
    if pos >= 0:
        end = pos
    return start, end


def _sentence_at(text: str, index: int) -> str:
    start, end = _sentence_span(text, index)
    return text[start:end].strip()


def _check_turnover_credit(text: str, recap: dict | None) -> list[str]:
    """Credit for forcing/recovering a turnover must match the play text."""
    plays = _plays(recap)
    fumbles = [p for p in plays if p.get("kind") == "fumble" or "fumble" in (p.get("text") or "").lower()]
    ints = [p for p in plays if p.get("kind") == "int" or "intercept" in (p.get("text") or "").lower()]
    if not text or (not fumbles and not ints):
        return []
    official_force = _official_names(fumbles, "forcedBy", "recoveredBy")
    official_int = _official_names(ints, "interceptedBy")
    team_names = {
        "kc": "KC",
        "chiefs": "KC",
        "kansas": "KC",
        "mia": "MIA",
        "miami": "MIA",
        "dolphins": "MIA",
    }
    opp = ((recap or {}).get("oppAbbr") or "").strip().upper()
    if opp:
        team_names[opp.lower()] = opp
    int_teams = set()
    for play in ints:
        offense = (play.get("team") or "").upper()
        if offense == "KC":
            int_teams.add(opp or "OPP")
        elif offense:
            int_teams.add("KC")
        who = _last_name_token(play.get("interceptedBy") or "")
        if who:
            official_int.add(who)
    fumblers = set()
    for play in fumbles:
        head = re.split(r"FUMBLES", play.get("text") or "", maxsplit=1, flags=re.I)[0]
        names = re.findall(r"\b([A-Z]\.[A-Za-z\-']+)\b", head)
        if names:
            fumblers.add(_last_name_token(names[0]))
    issues = []
    seen: set[str] = set()

    passers = set()
    for play in ints:
        head = re.split(
            r"INTERCEPTED", play.get("text") or "", maxsplit=1, flags=re.I
        )[0]
        names = re.findall(r"\b([A-Z]\.[A-Za-z\-']+)\b", head)
        if names:
            passers.add(_last_name_token(names[0]))

    def _reject(name: str, role: str, snippet: str) -> None:
        last = _last_name_token(name)
        if (
            not last
            or last in fumblers
            or last in passers
            or last in _NOT_PLAYER_TOKENS
        ):
            return
        key = f"{role}:{last}:{snippet.lower()}"
        if key in seen:
            return
        seen.add(key)
        allowed = official_force if role == "fumble" else official_int
        if last in allowed:
            return
        if role == "int" and last in team_names:
            if team_names[last] in int_teams:
                return
            issues.append(
                f"turnover credit: {name} is not the team that intercepted "
                f"({snippet!r})"
            )
            return
        issues.append(
            f"turnover credit: {name} is not who ESPN lists as "
            f"{'forcing/recovering the fumble' if role == 'fumble' else 'the interceptor'} "
            f"({snippet!r})"
        )

    for match in _FUMBLE_PAREN.finditer(text):
        _reject(match.group(1), "fumble", match.group(0))
    for match in _FUMBLE_FORCED.finditer(text):
        name = match.group(1) or match.group(2)
        _reject(name, "fumble", match.group(0))
    for match in _INT_CREDIT.finditer(text):
        name = match.group(1) or match.group(2)
        if (name or "").lower() in {"int", "interception", "intercepted"}:
            continue
        sentence = _sentence_at(text, match.start())
        if re.search(r"\bwiped\b|\bnullified\b|\bno play\b", sentence, re.I):
            continue
        _reject(name, "int", match.group(0))
    return issues


def _deep_plays(recap: dict | None, team: str = "KC") -> list[dict]:
    out = []
    for play in _plays(recap):
        if (play.get("team") or "").upper() != team:
            continue
        direction = (play.get("direction") or "").lower()
        text = (play.get("text") or "").lower()
        if "deep" not in direction and "deep" not in text:
            continue
        out.append(play)
    return out


def _is_incompletion(play: dict) -> bool:
    kind = (play.get("kind") or "").lower()
    text = (play.get("text") or "").lower()
    return kind in {"incompletion", "int"} or "incomplete" in text or "intercept" in text


def _is_completion(play: dict) -> bool:
    if _is_incompletion(play):
        return False
    kind = (play.get("kind") or "").lower()
    text = (play.get("text") or "").lower()
    return kind == "completion" or ("pass" in text and "incomplete" not in text)


def _check_absolute_claims(text: str, recap: dict | None) -> list[str]:
    """only/first/never/lone game-event claims must match the play-by-play.

    Unverifiable uniqueness claims are rejected rather than waved through.
    """
    if not text:
        return []
    plays = _plays(recap)
    if not plays:
        issues = []
        for rx in (_ABSOLUTE_CLAIM, _FIRST_PLAY_CLAIM, _NEVER_PLAY_CLAIM):
            for match in rx.finditer(text):
                issues.append(
                    f"absolute claim cannot be verified against the play-by-play "
                    f"({_sentence_at(text, match.start())!r})"
                )
        return issues
    issues = []
    seen: set[str] = set()
    deep = _deep_plays(recap, "KC")
    deep_comp = [p for p in deep if _is_completion(p)]
    deep_middle_miss = [
        p
        for p in deep
        if _is_incompletion(p)
        and "middle" in ((p.get("direction") or "") + " " + (p.get("text") or "")).lower()
    ]

    def _add(snippet: str, reason: str) -> None:
        key = snippet.lower()
        if key in seen:
            return
        seen.add(key)
        issues.append(f"{reason} ({snippet!r})")

    for match in _ABSOLUTE_CLAIM.finditer(text):
        sentence = _sentence_at(text, match.start())
        low = sentence.lower()
        if re.search(r"\bonly\s+\d", low) or re.search(r"\bonly\s+after\b", low) or re.search(r"\bonly\s+if\b", low):
            continue
        if "vertical" in low or (
            "deep" in low and ("carry" in low or "drive" in low or "shot" in low)
            and "miss" not in low and "intercept" not in low
        ):
            if len(deep_comp) != 1:
                _add(
                    sentence,
                    f"absolute claim: ESPN has {len(deep_comp)} KC deep/vertical "
                    f"completions, not a unique one",
                )
            continue
        if "deep" in low and (
            "miss" in low or "incomplete" in low or "intercept" in low
        ):
            if len(deep_middle_miss) != 1:
                _add(
                    sentence,
                    f"absolute claim: ESPN has {len(deep_middle_miss)} KC "
                    f"deep-middle misses, not a unique one",
                )
            continue
        _add(sentence, "absolute claim cannot be verified against the play-by-play")

    for match in _FIRST_PLAY_CLAIM.finditer(text):
        sentence = _sentence_at(text, match.start())
        if _FIRST_ORDINAL.search(sentence):
            continue
        _add(sentence, "absolute first-claim cannot be verified against the play-by-play")

    for match in _NEVER_PLAY_CLAIM.finditer(text):
        sentence = _sentence_at(text, match.start())
        if re.search(r"\bpreseason never found\b", sentence, re.I):
            continue
        if re.search(
            r"never found the (?:end zone|paint|endzone)\b",
            sentence,
            re.I,
        ):
            if re.search(
                r"\b(?:preseason|dress rehearsal|seahawks)\b",
                sentence,
                re.I,
            ):
                continue
            if (recap or {}).get("scoringPlays") or (recap or {}).get("scoring"):
                kc_td = official_count_by_team(recap, "td").get("KC", 0)
                if kc_td == 0:
                    continue
        _add(sentence, "absolute never-claim cannot be verified against the play-by-play")
    return issues


def _check_team_yards(
    text: str,
    recap: dict | None,
    last_game: dict | None,
    schedule=None,
) -> list[str]:
    """Team rushing/passing totals vs last-game and prior-game boxes."""
    if not text or not recap:
        return []
    last_rush = official_box_yards_by_team(recap, "rush", "last")
    prior_rush = official_box_yards_by_team(recap, "rush", "prior")
    last_pass = official_box_yards_by_team(recap, "pass", "last")
    prior_pass = official_box_yards_by_team(recap, "pass", "prior")
    if not last_rush and not prior_rush and not last_pass and not prior_pass:
        return []
    prior_labels = _prior_game_labels(recap)
    last_labels = _last_game_labels(last_game, recap)
    aliases = _team_aliases(last_game, recap, schedule)
    players = _player_aliases(recap)
    player_rush = _leader_stat_yards(recap, "rushing")
    player_pass = _leader_stat_yards(recap, "passing")
    issues = []

    def _bare_code(subject: str) -> str:
        compact = _norm_team_phrase(subject).replace(".", "").replace(" ", "")
        if compact.upper() in _NFL_ABBR and compact.isalpha() and len(compact) <= 3:
            return compact.upper()
        return ""

    def _expected_rush(subject: str) -> tuple[str, int | None]:
        token = " ".join((subject or "").lower().split()).strip(" '")
        code = _bare_code(subject)
        if code and code in last_rush:
            return ("KC" if code == "KC" else code), last_rush.get(code)
        if code and code in prior_rush:
            return f"prior {code}", prior_rush.get(code)
        if token in prior_labels or any(
            re.search(rf"\b{re.escape(lab)}\b", token) for lab in prior_labels
        ):
            return "prior KC", prior_rush.get("KC")
        if token in last_labels or any(
            re.search(rf"\b{re.escape(lab)}\b", token) for lab in last_labels
        ):
            named = _team_code(token, aliases)
            if named and named != "KC" and named in last_rush:
                return named, last_rush.get(named)
            return "last KC", last_rush.get("KC")
        team = _team_code(token, aliases)
        if team == "KC":
            return "KC", last_rush.get("KC")
        if team and team in last_rush:
            return team, last_rush.get(team)
        if team and team in prior_rush:
            return f"prior {team}", prior_rush.get(team)
        return token or "team", None

    def _expected_pass(subject: str) -> tuple[str, int | None]:
        token = " ".join((subject or "").lower().split()).strip(" '")
        code = _bare_code(subject)
        team = code or _team_code(token, aliases)
        if code and code in last_pass:
            return ("KC" if code == "KC" else code), last_pass.get(code)
        if code and code in prior_pass:
            return f"prior {code}", prior_pass.get(code)
        if token in prior_labels:
            return "prior KC", prior_pass.get("KC")
        if token in last_labels or any(
            re.search(rf"\b{re.escape(lab)}\b", token) for lab in last_labels
        ):
            if team and team != "KC" and team in last_pass:
                return team, last_pass.get(team)
            return "last KC", last_pass.get("KC")
        if team == "KC":
            return "KC", last_pass.get("KC")
        if team and team in last_pass:
            return team, last_pass.get(team)
        if team and team in prior_pass:
            return f"prior {team}", prior_pass.get(team)
        return token or "team", None

    for match in _TEAM_GROUND.finditer(text):
        try:
            yards = int(match.group(2))
        except (TypeError, ValueError):
            continue
        if _player_last(match.group(1)) in players:
            continue
        subject = _ground_subject(match.group(1), aliases)
        if not subject:
            continue
        scope = _claim_game_scope(
            text, match.start(), recap, last_game, schedule
        )
        if scope == "other":
            continue
        if scope == "prior":
            label, official = "prior KC", prior_rush.get("KC")
            named = _alias_team(subject, aliases)
            if named and named != "KC" and named in prior_rush:
                label, official = f"prior {named}", prior_rush.get(named)
        else:
            label, official = _expected_rush(subject)
        if official is None:
            continue
        if yards != official:
            issues.append(
                f"team rushing {yards} disagrees with ESPN {official} for "
                f"{label} ({match.group(0)!r})"
            )

    for match in list(_TEAM_RUSH_YARDS.finditer(text)) + list(
        _RAN_FOR_YARDS.finditer(text)
    ):
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if not re.search(r"\bteam\s+rush", match.group(0), re.I):
            if _player_owns_stat(text, match.start(), players, aliases):
                continue
            sentence = _sentence_at(text, match.start())
            if yards in player_rush.values() and not re.search(
                r"\b(?:kansas city|the chiefs|\bkc\b)\s+"
                r"(?:had|finished|was|were)\b",
                sentence,
                re.I,
            ):
                continue
            if re.search(
                r"\b(?:the\s+)?miami\s+box\b|"
                r"\bmiami['’]s\s+\d+\s+rush\b",
                sentence,
                re.I,
            ) and yards == last_rush.get("KC"):
                continue
        scope = _claim_game_scope(
            text, match.start(), recap, last_game, schedule
        )
        if scope == "other":
            continue
        clause = _box_clause_at(text, match.start())
        explicit = _explicit_box_subject(clause, aliases)
        if not explicit:
            lead = _RUSH_SUBJECT_LEAD.match(clause.strip()) or _HAD_BOX_LEAD.match(
                clause.strip()
            )
            if lead:
                explicit = _alias_team(lead.group(1), aliases)
        team = explicit or _bound_box_team(text, match.start(), aliases)
        if scope == "prior":
            label, official = "prior KC", prior_rush.get("KC")
            if team and team != "KC" and team in prior_rush:
                label, official = f"prior {team}", prior_rush.get(team)
        else:
            label, official = _expected_rush(team) if team else ("team", None)
        if official is None:
            if team and team != "KC" and team in last_rush:
                official = last_rush[team]
                label = team
            elif team and team in prior_rush and team != "KC":
                official = prior_rush[team]
                label = f"prior {team}"
            else:
                official = last_rush.get("KC")
                label = "KC"
        if official is None:
            continue
        window = text[max(0, match.start() - 96) : match.end() + 96]
        names_prior = bool(
            re.search(r"indianapolis|colts|\bprior\b", window, re.I)
        ) or any(
            re.search(rf"\b{re.escape(lab)}\b", window, re.I)
            for lab in prior_labels
        )
        last_named = any(
            re.search(rf"\b{re.escape(lab)}\b", clause, re.I)
            for lab in last_labels
        )
        if names_prior and yards == prior_rush.get("KC"):
            official = prior_rush.get("KC")
            label = "prior KC"
        elif (
            names_prior
            and yards != last_rush.get("KC")
            and not last_named
            and not (
                explicit and explicit != "KC" and explicit in last_rush
            )
        ):
            official = prior_rush.get("KC")
            label = "prior KC"
            if official is None:
                continue
        if re.search(
            r"\b(?:saw|allowed|gave up|yielded)\b",
            clause,
            re.I,
        ) and yards in {last_rush.get("KC"), prior_rush.get("KC")}:
            continue
        if yards == official:
            continue
        if team and team in last_rush and yards == last_rush[team]:
            continue
        if team and team in prior_rush and yards == prior_rush[team]:
            continue
        # A number that is another club's official total stays theirs when
        # the subject is the other club or unknown — never when it is KC,
        # and never when the number is only a prior KC leftover.
        if not explicit and label in {"KC", "last KC", "team"}:
            other_totals = {
                value
                for key, value in {**last_rush, **prior_rush}.items()
                if key and key != "KC" and value is not None
            }
            if yards in other_totals:
                continue
        issues.append(
            f"team rushing {yards} disagrees with ESPN {official} for "
            f"{label} ({_claim_quote(text, match)!r})"
        )

    for match in _TEAM_PASS_YARDS.finditer(text):
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            continue
        # Player passing lines ("246 yards") sit next to a completion mark.
        window = text[max(0, match.start() - 40) : match.end()]
        if _PASS_LINE.search(window) or re.search(r"\d+\s*-\s*of\s*-\s*\d+", window, re.I):
            continue
        subject = _subject_clause(text, match.start())
        label, official = _expected_pass(subject)
        if official is None:
            team = _bound_team(text, match.start(), match.end(), aliases, {})
            if team and team in last_pass:
                official = last_pass[team]
                label = team
            else:
                official = last_pass.get("KC")
                label = "KC"
        if official is None or yards == official:
            continue
        if yards in player_pass.values():
            continue
        if subject and _team_code(subject, aliases) == "KC":
            pass
        elif yards in last_pass.values() or yards in prior_pass.values():
            continue
        issues.append(
            f"team passing {yards} disagrees with ESPN {official} for "
            f"{label} ({match.group(0)!r})"
        )
    return issues


def _box_int(block: dict | None, key: str):
    raw = (block or {}).get(key) or ""
    match = re.search(r"\d+", str(raw))
    if not match:
        return None
    try:
        return int(match.group(0))
    except (TypeError, ValueError):
        return None


def _box_clocks(recap: dict | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {
        "KC": set(),
        "OPP": set(),
        "prior KC": set(),
        "prior OPP": set(),
    }
    kc = (recap or {}).get("kc") or {}
    opp = (recap or {}).get("opp") or {}
    prior_block = (recap or {}).get("prior") or {}
    prior_kc = prior_block.get("kc") or {}
    prior_opp = prior_block.get("opp") or {}
    if kc.get("possessionTime"):
        out["KC"].add(str(kc["possessionTime"]).strip())
    if opp.get("possessionTime"):
        out["OPP"].add(str(opp["possessionTime"]).strip())
    if prior_kc.get("possessionTime"):
        out["prior KC"].add(str(prior_kc["possessionTime"]).strip())
    if prior_opp.get("possessionTime"):
        out["prior OPP"].add(str(prior_opp["possessionTime"]).strip())
    return out


def _possession_clock_is_game_or_segment(text: str, match: re.Match) -> bool:
    """True for run-of-show ranges and game-clock phrases — never TOP."""
    clock = match.group(1)
    start = match.start(1)
    end = match.end(1)
    if clock == "0:00":
        return True
    around = text[max(0, start - 12) : min(len(text), end + 12)]
    if _SEGMENT_CLOCK_RANGE.search(around):
        return True
    after = text[end : end + 48]
    if _GAME_CLOCK_AFTER.match(after):
        return True
    try:
        minutes, seconds = clock.split(":")
        clock_secs = int(minutes) * 60 + int(seconds)
    except (TypeError, ValueError):
        clock_secs = None
    short_clock = clock_secs is not None and clock_secs <= 15 * 60
    if short_clock and re.match(r"\s*drive\b", after, re.I):
        return True
    if short_clock and re.search(
        r"(?:of\s+)?possession\s+in\s+the\s+"
        r"(?:first|second|third|fourth|opening)\s+quarter\b",
        after,
        re.I,
    ):
        return True
    before = text[max(0, start - 24) : start]
    if re.search(r"\b(?:Q[1-4]|quarter\s+[1-4])\s*$", before, re.I):
        return True
    if re.search(r"\bat\s+the\s*$", before, re.I) and re.match(
        r"^\s*mark\b", after, re.I
    ):
        return True
    if re.search(r"\bwith\s*$", before, re.I) and re.match(
        r"^\s*(?:to\s+go|left|remaining)\b", after, re.I
    ):
        return True
    return False


def _line_at(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end < 0:
        end = len(text)
    return text[start:end]


def _kc_owns_possession_verb(
    text: str, match: re.Match, aliases: dict[str, str]
) -> bool:
    """whatWorked TOP lines and 'sat on the football' with a KC subject."""
    sent = _sentence_at(text, match.start())
    line = _line_at(text, match.start()).strip()
    for blob in (sent, line):
        clipped = re.sub(
            r"^\s*against\s+(?:the\s+)?[A-Za-z][A-Za-z'’.-]*(?:\s+[A-Za-z][A-Za-z'’.-]*){0,2}\s+",
            "",
            blob or "",
            flags=re.I,
        )
        lead = re.match(
            r"\s*(?:the\s+)?([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,2})",
            clipped,
        )
        if lead:
            named = _alias_team(lead.group(1), aliases)
            if named and named != "KC":
                return False
    held_subj = re.search(
        r"((?:the\s+)?[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,2})\s+"
        r"(?:held|hold(?:s|ing)?)\s+(?:the ball|it)\b",
        sent,
    )
    if held_subj:
        named = _alias_team(held_subj.group(1), aliases)
        if named and named != "KC":
            return False
    if re.search(r"\band held the ball\b", sent, re.I):
        lead = re.match(
            r"\s*(?:the\s+)?([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,2})",
            sent,
        )
        if lead:
            named = _alias_team(lead.group(1), aliases)
            if named and named != "KC":
                return False
    line = _line_at(text, match.start()).strip()
    if _KC_VOICED_TOP.match(line) or _KC_VOICED_TOP.match("\n" + line):
        return True
    window = _box_clause_at(text, match.start())
    blob = (window or line).strip()
    if re.match(r"against\s+", blob, re.I) and re.search(
        r"\b(?:sat on the (?:ball|football)|held the ball|"
        r"of clock|of possession)\b",
        blob,
        re.I,
    ):
        return True
    if re.search(
        r"\bsat on\s+(?:the\s+)?(?!ball\b|football\b)[A-Za-z]{3,}",
        window or sent,
        re.I,
    ):
        return True
    sat_on_ball = bool(
        _SAT_ON_BALL.search(window or "") or _SAT_ON_BALL.search(sent or "")
    )
    if sat_on_ball or re.search(
        r"\bheld the ball\b|\bheld it\b|\bhold(?:s|ing)? the ball\b",
        window,
        re.I,
    ):
        held_subj = re.search(
            r"((?:the\s+)?[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,2})\s+"
            r"(?:held|hold(?:s|ing)?)\s+(?:the ball|it)\b",
            window,
        )
        if held_subj:
            named = _alias_team(held_subj.group(1), aliases)
            if named and named != "KC":
                return False
        if re.search(
            r"\b(?:kansas city|the chiefs)\b",
            _scrub_box_labels(window),
            re.I,
        ):
            return True
        if re.match(r"against\s+", window.strip(), re.I):
            return True
        if sat_on_ball:
            return True
    if re.match(r"against\s+", (sent or "").strip(), re.I) and re.search(
        r"\b(?:they|of clock|of possession|sat on|held the ball)\b",
        sent,
        re.I,
    ):
        return True
    return False


def _possession_held_by_named_team(text: str, match: re.Match) -> bool:
    """True when the same clause claims TOP with possession wording."""
    start, end = _stat_clause_span(text, match.start())
    sent_start, sent_end = _sentence_span(text, match.start())
    clause_start, clause_end = _box_clause_span(text, match.start())
    left = max(start, sent_start, clause_start)
    # 37:00's colon is a clause break, so keep a short tail after the clock,
    # but never leave this sentence or this box clause.
    right = min(sent_end, clause_end, max(end, match.end() + 48))
    if right <= left:
        return False
    return bool(_POSSESSION_HELD.search(text[left:right]))


def _clock_tied_to_subject(text: str, match: re.Match) -> bool:
    """True when this clock is a team TOP, not a scoring or segment time.

    An M:SS is a possession claim only when possession wording sits in the
    same clause, or when the clock is in a first-downs / yards stat list.
    """
    if _possession_clock_is_game_or_segment(text, match):
        return False
    after = text[match.end() : match.end() + 48]
    if re.match(r"\s*drive\b", after, re.I):
        return True
    if re.search(
        r"(?:of\s+)?possession\s+in\s+the\s+"
        r"(?:first|second|third|fourth|opening)\s+quarter\b",
        after,
        re.I,
    ):
        return True
    if re.search(r"possession|clock", match.group(0), re.I):
        return True
    sent_start, sent_end = _sentence_span(text, match.start())
    clause_start, clause_end = _box_clause_span(text, match.start())
    left = max(sent_start, clause_start, match.start() - 48)
    prefix = text[left : match.start()]
    if _FIRST_DOWNS_AND_CLOCK.search(prefix):
        return True
    if re.search(
        r"(?:rush(?:ing)?(?:\s+yards)?|pass(?:ing)?(?:\s+yards)?|"
        r"total yards)\s+and\s*$",
        prefix,
        re.I,
    ):
        return True
    clause = text[max(clause_start, sent_start) : min(clause_end, sent_end)]
    if _HAD_BOX_LEAD.match(clause.strip()) and (
        _FIRST_DOWNS.search(clause) or _BOX_RUSH.search(clause)
    ):
        return True
    if _possession_held_by_named_team(text, match):
        return True
    # "Miami held the ball 34:21 and Kansas City 28:55" carries TOP.
    prev = text[max(sent_start, 0) : max(clause_start, sent_start)]
    if _POSSESSION_HELD.search(prev) and re.search(
        r"\b(?:the\s+)?[A-Z][A-Za-z. '’-]+\s*$",
        clause[: max(0, match.start() - max(clause_start, sent_start))],
    ):
        return True
    return False


def _stat_phrase_window(text: str, match: re.Match) -> str:
    """Local phrase around this number, split so an average does not
    poison a later 'had N on Sunday'."""
    start = match.start()
    end = match.end()
    left = max(0, start - 56)
    prefix = text[left:start]
    for sep in (
        ";",
        ". ",
        ", and ",
        " and had ",
        " and finished ",
        " but only ",
        " coming in",
    ):
        idx = prefix.rfind(sep)
        if idx >= 0:
            left = left + idx + len(sep)
    right = min(len(text), end + 56)
    suffix = text[end:right]
    for sep in (";", ". ", ", and "):
        idx = suffix.find(sep)
        if idx >= 0:
            right = end + idx
    return text[left:right]


def _against_non_recap_team(
    text: str,
    recap: dict | None,
    last_game: dict | None,
    schedule=None,
) -> bool:
    """True for 'against Denver' when that club is not last or prior."""
    hit = _AGAINST_OTHER_TEAM.search(text or "")
    if not hit:
        return False
    named = _alias_leading_phrase(
        re.sub(r"(?i)^against\s+(?:the\s+)?", "", hit.group(0)),
        _team_aliases(last_game, recap, schedule),
    )
    if not named or named == "KC":
        return False
    return named not in _recap_box_teams(last_game, recap)


def _named_week_average_is_box(
    text: str,
    match: re.Match,
    recap: dict | None,
    last_game: dict | None,
    schedule=None,
) -> bool:
    """True when 'averaged' is the only rate word and Week N is a recap game.

    'Las Vegas averaged 18 first downs in Week 4' is that week's box, not
    a season rate. Per-game / 'a game' / through-N-weeks stay rates.
    """
    window = _stat_phrase_window(text, match)
    if re.search(
        r"per[-\s]?game|(?:first downs?|averaged?)\s+a game|"
        r"through\s+\w+\s+weeks?|coming in\b",
        window,
        re.I,
    ):
        return False
    if not re.search(r"\baveraged?\b", window, re.I):
        return False
    sentence = _sentence_at(text, match.start())
    if not re.search(r"\bweek\s+\d+\b", sentence, re.I):
        return False
    return _claim_game_scope(
        text, match.start(), recap, last_game, schedule
    ) in {"last", "prior"}


def _stat_is_rate_or_other_game(
    text: str,
    match: re.Match,
    recap: dict | None = None,
    last_game: dict | None = None,
    schedule=None,
) -> bool:
    """True for per-game / average rates, or a box from some other club.

    Week N and 'season low' stay checkable when we have that game's box.
    'Against Indianapolis' is a prior-box label, not a skip.
    A named played week plus 'averaged' is still that week's box.
    """
    if _RATE_OR_OTHER_GAME.search(_stat_phrase_window(text, match)):
        if _named_week_average_is_box(
            text, match, recap, last_game, schedule
        ):
            return _against_non_recap_team(
                _sentence_at(text, match.start()), recap, last_game, schedule
            )
        return True
    return _against_non_recap_team(
        _sentence_at(text, match.start()), recap, last_game, schedule
    )


def _possession_owner_after_clock(
    text: str, match: re.Match, aliases: dict[str, str]
) -> str:
    """'37:00 of possession stays on Indianapolis' names the owner after TOP."""
    tail = text[match.end() : match.end() + 64]
    stayed = re.search(
        r"\bstays?\s+on\s+(?:the\s+)?([A-Za-z][A-Za-z '’-]+)",
        tail,
        re.I,
    )
    if not stayed:
        return ""
    return _alias_team(stayed.group(1), aliases)


def _check_box_clocks(
    text: str,
    recap: dict | None,
    last_game: dict | None,
    schedule=None,
) -> list[str]:
    """Possession, first downs, and game-yard totals vs the right box."""
    if not text:
        return []
    issues = []
    clocks = _box_clocks(recap)
    allowed_clocks = set().union(*clocks.values()) if clocks else set()
    aliases = _team_aliases(last_game, recap, schedule)
    prior_labels = _prior_game_labels(recap)
    kc = (recap or {}).get("kc") or {}
    prior = ((recap or {}).get("prior") or {}).get("kc") or {}
    prior_opp = ((recap or {}).get("prior") or {}).get("opp") or {}

    for match in _POSSESSION_CLOCK.finditer(text):
        if _possession_clock_is_game_or_segment(text, match):
            continue
        sentence = _sentence_at(text, match.start())
        if re.search(r"\bchant\b|\bbefore the\b.*\bpossession edge\b", sentence, re.I):
            continue
        if re.search(r"\blast week against\b", sentence, re.I):
            continue
        clock = match.group(1)
        scope = _claim_game_scope(
            text, match.start(), recap, last_game, schedule
        )
        if scope == "other":
            continue
        if not allowed_clocks:
            continue
        if clock not in allowed_clocks and clock.replace(":", ".") not in allowed_clocks:
            # Bare clock that is nobody's TOP — only flag when it claims possession.
            # Look behind for 'possession 8:42'; the regex already eats
            # '8:42 of possession' after the time. Do not treat kickoff
            # '12:00 PM' plus later 'the clock' as a TOP claim.
            prefix = text[max(0, match.start() - 40) : match.start()]
            if re.search(r"\b(?:AM|PM)\b", text[match.end() : match.end() + 8], re.I):
                continue
            if re.search(r"possession of the\b", prefix, re.I):
                continue
            if (
                re.search(r"possession|clock", match.group(0), re.I)
                or re.search(r"(?:of\s+)?possession\b(?!\s+of\b)", prefix, re.I)
                or _clock_tied_to_subject(text, match)
            ):
                issues.append(
                    f"possession {clock} is not on the ESPN box "
                    f"({match.group(0)!r}; official {sorted(allowed_clocks)})"
                )
            continue
        sentence = _sentence_at(text, match.start())
        clause = _box_clause_at(text, match.start())
        explicit = _explicit_box_subject(clause, aliases)
        if _kc_owns_possession_verb(text, match, aliases):
            explicit = "KC"
        if scope == "prior":
            team = explicit or _bound_possession_in_clause(
                text, match, aliases
            ) or (
                _possession_owner_after_clock(text, match, aliases)
            )
            prior_opp_abbr = (
                ((recap or {}).get("prior") or {}).get("oppAbbr") or ""
            ).strip().upper()
            prior_allowed = clocks["prior KC"] | clocks["prior OPP"]
            if clock in prior_allowed or clock.replace(":", ".") in prior_allowed:
                if (
                    team == "KC"
                    and clock not in clocks["prior KC"]
                    and clock in clocks["prior OPP"]
                ):
                    issues.append(
                        f"possession {clock} is prior "
                        f"{prior_opp_abbr or 'OPP'} clock, not KC "
                        f"({_clock_quote(text, match)!r})"
                    )
                elif (
                    team
                    and prior_opp_abbr
                    and team == prior_opp_abbr
                    and clock not in clocks["prior OPP"]
                    and clock in clocks["prior KC"]
                    and _clock_tied_to_subject(text, match)
                ):
                    issues.append(
                        f"possession {clock} is prior KC clock, not "
                        f"{prior_opp_abbr} ({_clock_quote(text, match)!r})"
                    )
                continue
            if prior_allowed and re.search(
                r"possession|clock", match.group(0), re.I
            ):
                issues.append(
                    f"possession {clock} is not on the prior-game ESPN box "
                    f"({match.group(0)!r}; official "
                    f"{sorted(prior_allowed)})"
                )
            continue
        team = explicit or _bound_possession_in_clause(text, match, aliases)
        after = text[match.end() : match.end() + 48]
        if (
            team == "KC"
            and clock not in clocks["KC"]
            and _clock_tied_to_subject(text, match)
            and (
                re.match(r"\s*drive\b", after, re.I)
                or re.search(
                    r"(?:(?:of\s+)?possession\s+)?in\s+the\s+"
                    r"(?:first|second|third|fourth|opening)\s+quarter\b",
                    after,
                    re.I,
                )
            )
        ):
            issues.append(
                f"possession {clock} is not a KC game clock "
                f"({_clock_quote(text, match)!r})"
            )
            continue
        owners = [side for side, bag in clocks.items() if clock in bag]
        # A clock that only appears on the opponent box binds to them
        # unless the local subject is explicitly KC.
        if owners == ["OPP"] and team != "KC":
            continue
        opp_label = (
            ((recap or {}).get("oppAbbr") or "OPP").strip() or "OPP"
        )
        quoted = _clock_quote(text, match)
        if team == "KC" and clock not in clocks["KC"] and clock in clocks["OPP"]:
            issues.append(
                f"possession {clock} is {opp_label} clock, not KC "
                f"({quoted!r})"
            )
            continue
        if (
            team
            and team == opp_label.upper()
            and clock in clocks["KC"]
            and clock not in clocks["OPP"]
            and _clock_tied_to_subject(text, match)
        ):
            issues.append(
                f"possession {clock} is KC clock, not {opp_label} "
                f"({quoted!r})"
            )

    kc_fd = _box_int(kc, "firstDowns")
    opp_fd = _box_int((recap or {}).get("opp"), "firstDowns")
    prior_fd = _box_int(prior, "firstDowns")
    prior_opp_fd = _box_int(prior_opp, "firstDowns")
    opp_abbr = ((recap or {}).get("oppAbbr") or "").strip().upper()
    prior_abbr = (
        ((recap or {}).get("prior") or {}).get("oppAbbr") or ""
    ).strip().upper()
    fd_hits = list(_FIRST_DOWNS.finditer(text))
    fd_hits.extend(_FIRST_DOWN_VOLUME.finditer(text))
    seen_fd = {(hit.start(), hit.end()) for hit in fd_hits}
    for match in _HAD_ON_GAMEDAY.finditer(text):
        if not _FIRST_DOWNS.search(_sentence_at(text, match.start())):
            continue
        if any(start <= match.start() < end for start, end in seen_fd):
            continue
        fd_hits.append(match)
    for match in fd_hits:
        try:
            claimed = int(match.group(1))
        except (TypeError, ValueError):
            continue
        sentence = _sentence_at(text, match.start())
        if re.search(r"\bchasing\b", sentence, re.I):
            continue
        if _stat_is_rate_or_other_game(
            text, match, recap, last_game, schedule
        ):
            continue
        clause = _box_clause_at(text, match.start())
        team = _bound_box_team(text, match.start(), aliases)
        explicit = _explicit_box_subject(clause, aliases)
        if _KC_VOICED_TOP.search(_line_at(text, match.start())):
            explicit = "KC"
        if explicit:
            team = explicit
        if (
            not explicit
            and team
            and opp_abbr
            and team == opp_abbr
            and kc_fd is not None
            and claimed == kc_fd
            and re.search(r"\bagainst\b", sentence, re.I)
        ):
            team = "KC"
        scope = _claim_game_scope(
            text, match.start(), recap, last_game, schedule
        )
        official = None
        label = team or "KC"
        if not explicit and _kc_owned_box(clause):
            if scope == "prior" or any(
                re.search(rf"\b{re.escape(lab)}\b", sentence.lower())
                for lab in prior_labels
            ):
                official, label = prior_fd, "prior KC"
            else:
                official, label = kc_fd, "KC"
            if official is None or claimed == official:
                continue
            issues.append(
                f"first downs {claimed} disagrees with ESPN {official} for "
                f"{label} ({_claim_quote(text, match)!r})"
            )
            continue
        if scope == "other":
            continue
        if scope == "prior":
            if team and prior_abbr and team == prior_abbr:
                official, label = prior_opp_fd, f"prior {team}"
            else:
                official, label = prior_fd, "prior KC"
            if official is None:
                continue
        elif team and team == opp_abbr:
            official, label = opp_fd, team
        elif team == "KC":
            official, label = kc_fd, "KC"
        elif any(
            re.search(rf"\b{re.escape(lab)}\b", sentence.lower())
            for lab in prior_labels
        ):
            official, label = prior_fd, "prior KC"
            if official is None:
                continue
        elif claimed in {kc_fd, opp_fd, prior_fd}:
            if (
                explicit
                and explicit not in {"", "KC", opp_abbr, prior_abbr}
                and re.search(
                    r"\bagainst\s+(?:kansas city|the chiefs|\bkc\b)",
                    sentence,
                    re.I,
                )
                and opp_fd is not None
                and claimed != opp_fd
            ):
                official, label = opp_fd, explicit
            else:
                continue
        else:
            official = kc_fd
        if official is None or claimed == official:
            continue
        issues.append(
            f"first downs {claimed} disagrees with ESPN {official} for "
            f"{label} ({_claim_quote(text, match)!r})"
        )

    for hit in _COMPARISON_TAIL.finditer(text):
        if not _FIRST_DOWNS.search(_sentence_at(text, hit.start())):
            continue
        if _claim_game_scope(
            text, hit.start(), recap, last_game, schedule
        ) == "other":
            continue
        team = _alias_team(hit.group(1), aliases)
        if not team:
            continue
        try:
            claimed_fd = int(hit.group(2))
        except (TypeError, ValueError):
            continue
        clock = hit.group(3)
        if team == "KC":
            official_fd, official_clock = kc_fd, clocks.get("KC") or set()
            fd_label, clock_label = "KC", opp_abbr or "OPP"
        elif team == opp_abbr:
            official_fd, official_clock = opp_fd, clocks.get("OPP") or set()
            fd_label, clock_label = team, "KC"
        elif team == prior_abbr:
            official_fd, official_clock = (
                prior_opp_fd,
                clocks.get("prior OPP") or set(),
            )
            fd_label, clock_label = f"prior {team}", "prior KC"
        else:
            continue
        quoted = _sentence_at(text, hit.start()).strip()
        if official_fd is not None and claimed_fd != official_fd:
            issues.append(
                f"first downs {claimed_fd} disagrees with ESPN {official_fd} "
                f"for {fd_label} ({quoted!r})"
            )
        if (
            official_clock
            and clock not in official_clock
            and clock.replace(":", ".") not in official_clock
        ):
            issues.append(
                f"possession {clock} is {clock_label} clock, not {team} "
                f"({quoted!r})"
            )

    kc_total = _box_int(kc, "totalYards")
    prior_total = _box_int(prior, "totalYards")
    prior_pass = _box_int(prior, "netPassingYards")
    kc_pass = _box_int(kc, "netPassingYards")
    for match in _GAME_YARDS.finditer(text):
        try:
            yards = int(match.group(1))
        except (TypeError, ValueError):
            continue
        sentence = _sentence_at(text, match.start()).lower()
        tail = text[match.end() : match.end() + 16].lower()
        if "passing" in sentence or "rush" in sentence or tail.startswith(" rushing"):
            continue
        if re.search(
            r"\b(?:carries|carry|walker|touches|threw for|mahomes|kelce|rice)\b",
            sentence,
        ):
            continue
        scope = _claim_game_scope(
            text, match.start(), recap, last_game, schedule
        )
        if scope == "other":
            continue
        if scope == "prior" or any(
            re.search(rf"\b{re.escape(lab)}\b", sentence) for lab in prior_labels
        ):
            official = prior_total
            label = "prior KC"
            prior_rush = _box_int(prior, "rushingYards")
            # 371 is Indy team net passing; 152 is Indy rushing — not the game total.
            if official and yards != official and yards == prior_pass:
                issues.append(
                    f"game yards {yards} is prior passing, not the "
                    f"{official}-yard Indianapolis total ({match.group(0)!r})"
                )
            elif official and yards != official and yards not in {
                kc_total,
                kc_pass,
                prior_pass,
                prior_rush,
                _box_int(kc, "rushingYards"),
            }:
                issues.append(
                    f"game yards {yards} disagrees with ESPN {official} for "
                    f"{label} ({match.group(0)!r})"
                )
    return issues


def _box_drive_count(block: dict | None, recap_drives=None, side: str = ""):
    official = _box_int(block, "totalDrives")
    if official is not None:
        return official
    if recap_drives and side:
        raw = recap_drives.get(side)
        try:
            return int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None
    return None


def _check_drive_counts(
    text: str,
    recap: dict | None,
    last_game: dict | None,
    schedule=None,
) -> list[str]:
    """Total Drives from the ESPN team box, if the copy names a count."""
    if not text:
        return []
    kc = (recap or {}).get("kc") or {}
    opp = (recap or {}).get("opp") or {}
    prior_block = (recap or {}).get("prior") or {}
    drives = (recap or {}).get("drives") or {}
    prior_drives = prior_block.get("drives") or {}
    kc_n = _box_drive_count(kc, drives, "KC")
    opp_n = _box_drive_count(opp, drives, "OPP")
    prior_kc_n = _box_drive_count(prior_block.get("kc") or {}, prior_drives, "KC")
    prior_opp_n = _box_drive_count(prior_block.get("opp") or {}, prior_drives, "OPP")
    if kc_n is None and opp_n is None and prior_kc_n is None and prior_opp_n is None:
        return []
    aliases = _team_aliases(last_game, recap, schedule)
    opp_abbr = ((recap or {}).get("oppAbbr") or "").strip().upper()
    prior_abbr = (prior_block.get("oppAbbr") or "").strip().upper()
    issues = []
    for match in _TOTAL_DRIVES.finditer(text):
        claimed = _match_count(match)
        if claimed is None:
            continue
        sentence = _sentence_at(text, match.start())
        if re.search(
            r"\b(?:if the first|when the first|first two|first ten)\b",
            sentence,
            re.I,
        ):
            continue
        team = _bound_box_team(text, match.start(), aliases)
        scope = _claim_game_scope(
            text, match.start(), recap, last_game, schedule
        )
        official = None
        label = team or "KC"
        if scope == "other":
            continue
        if scope == "prior":
            if team and prior_abbr and team == prior_abbr:
                official, label = prior_opp_n, f"prior {team}"
            else:
                official, label = prior_kc_n, "prior KC"
            if official is None:
                continue
        elif team and team == opp_abbr:
            official, label = opp_n, team
        elif team == "KC":
            official, label = kc_n, "KC"
        elif claimed in {n for n in (kc_n, opp_n, prior_kc_n) if n is not None}:
            continue
        else:
            official, label = kc_n, "KC"
        if official is None or claimed == official:
            continue
        issues.append(
            f"total drives {claimed} disagrees with ESPN {official} for "
            f"{label} ({match.group(0)!r})"
        )
    return issues


def _player_last(name: str) -> str:
    token = (name or "").replace(".", " ").strip()
    parts = [p for p in re.split(r"[^A-Za-z]+", token) if p]
    skip = {"ii", "iii", "iv", "jr", "sr", "the"}
    parts = [p for p in parts if p.lower() not in skip]
    return parts[-1].lower() if parts else ""


def _bound_touch_row(text: str, claim_at: int, rows: list[dict]) -> dict | None:
    """Bind N touches to the nearest named ESPN player, not recap order.

    collect.parse_player_usage sorts touches alphabetically, so Emmett
    Johnson and Malik Willis land before Kenneth Walker. Scanning rows
    and taking the first last name anywhere in the sentence then treated
    Walker's 20 touches as Johnson's 6 or Willis's 9.
    Prefer the rightmost ESPN last name before the claim. If none, take
    a name in the same clause after it ("20 touches for Walker").
    A closer non-usage name (Kelce's two touches next to Walker's 20)
    is not Walker's game total — do not skip over it to the fixture back.
    """
    aliases: dict[str, str] = {}
    by_last: dict[str, dict] = {}
    for row in rows:
        last = _player_last(row.get("player") or "")
        if last:
            aliases[last] = last
            by_last[last] = row
    if not aliases:
        return rows[0] if len(rows) == 1 else None
    start, end = _stat_clause_span(text, claim_at)
    before = text[start:claim_at]
    after = text[claim_at:end]
    usage_pos, last = _last_name_pos(before, aliases)
    other_pos, _ = _rightmost_other_person(before, aliases)
    if other_pos > usage_pos:
        return None
    if last:
        return by_last.get(last)
    after_pos, last = _last_name_pos(after, aliases)
    first_other, _ = _first_other_person(after, aliases)
    if last and first_other >= 0 and (after_pos < 0 or first_other < after_pos):
        return None
    if last:
        return by_last.get(last)
    # A named person who is not in the usage table is not the lone listed back.
    # --check-edition's Walker-only fixture used to take '1 touches' / '6
    # touches' on Kelce or Rice and score them against Walker's 20.
    if _clause_names_other_person(text[start:end], aliases):
        return None
    if len(rows) == 1:
        return rows[0]
    return None


_OTHER_PERSON_SKIP = _KC_SCOPE | {
    "miami",
    "dolphins",
    "las",
    "vegas",
    "raiders",
    "indianapolis",
    "colts",
    "denver",
    "broncos",
    "sunday",
    "monday",
    "week",
    "espn",
    "arrowhead",
    "hard",
    "rock",
    "stadium",
}


def _other_person_hits(clause: str, aliases: dict[str, str]):
    """Yield (start, last) for proper names that are not usage last names."""
    for hit in re.finditer(r"\b([A-Z][a-z]{2,})\b", clause or ""):
        token = hit.group(1).lower()
        if token in aliases or token in _OTHER_PERSON_SKIP:
            continue
        yield hit.start(), token


def _rightmost_other_person(clause: str, aliases: dict[str, str]) -> tuple[int, str]:
    best_pos = -1
    best = ""
    for pos, token in _other_person_hits(clause, aliases):
        if pos >= best_pos:
            best_pos = pos
            best = token
    return best_pos, best


def _first_other_person(clause: str, aliases: dict[str, str]) -> tuple[int, str]:
    for pos, token in _other_person_hits(clause, aliases):
        return pos, token
    return -1, ""


def _clause_names_other_person(clause: str, aliases: dict[str, str]) -> bool:
    """True when the clause names someone who is not a usage last name."""
    return _first_other_person(clause, aliases)[0] >= 0


def _touch_claim_is_windowed(text: str, match: re.Match) -> bool:
    """True for red-zone / drive / quarter touch counts, not game totals."""
    start, end = _stat_clause_span(text, match.start())
    clause = text[start:end]
    rel_start = match.start() - start
    rel_end = match.end() - start
    if _count_scope(clause, rel_start, rel_end) is not None:
        return True
    return bool(_TOUCH_WINDOW.search(clause))


def _check_touch_counts(text: str, recap: dict | None) -> list[str]:
    rows = [
        row
        for row in (recap or {}).get("touches") or []
        if isinstance(row, dict) and row.get("touches") is not None
    ]
    if not text or not rows:
        return []
    issues = []
    for match in _TOUCH_COUNT.finditer(text):
        claimed = _match_count(match)
        if claimed is None:
            continue
        if _touch_claim_is_windowed(text, match):
            continue
        sentence = _sentence_at(text, match.start())
        if re.search(
            r"\b(?:cut him over to|if you|do not|don't)\b",
            sentence,
            re.I,
        ):
            continue
        row = _bound_touch_row(text, match.start(), rows)
        if row is None:
            continue
        official = int(row["touches"])
        label = row.get("player") or "player"
        if claimed != official:
            issues.append(
                f"touches {claimed} disagrees with ESPN {official} for "
                f"{label} ({_claim_quote(text, match)!r})"
            )
    return issues


def _check_attempt_counts(
    text: str, recap: dict | None, last_game: dict | None = None
) -> list[str]:
    passing = [
        row
        for row in (recap or {}).get("passing") or []
        if isinstance(row, dict) and row.get("attempts") is not None
    ]
    if not text or not passing:
        return []
    kc = next((row for row in passing if (row.get("team") or "").upper() == "KC"), None)
    if not kc:
        return []
    by_last = {}
    for row in passing:
        last = _player_last(row.get("player") or "")
        if last:
            by_last[last] = row
    aliases = _team_aliases(last_game, recap)
    opp_abbr = ((recap or {}).get("oppAbbr") or "").strip().upper()
    rush_att = {
        "KC": _box_int((recap or {}).get("kc"), "rushingAttempts"),
    }
    if opp_abbr:
        rush_att[opp_abbr] = _box_int((recap or {}).get("opp"), "rushingAttempts")
    issues = []
    for match in _DROP_ATTEMPT.finditer(text):
        claimed = _match_count(match)
        if claimed is None:
            continue
        kind = _attempt_kind(text, match)
        sentence = _sentence_at(text, match.start())
        if re.search(
            r"\b(?:first\s+two|first\s+ten|if the first|when the first|"
            r"pass-set for|instead of \d+|need \d+ attempts|"
            r"will need \d+|dropback script|dropback game|"
            r"not a\s+\d+-?dropback|dropback scramble)\b",
            sentence,
            re.I,
        ):
            continue
        if re.search(
            r"\b(?:walker|feature back|a pop|carries)\b",
            sentence,
            re.I,
        ) and "drop" not in (match.group(0) or "").lower():
            continue
        owner = _last_name_in(
            text[_stat_clause_span(text, match.start())[0] : match.start()],
            {last: last for last in by_last},
        )
        row = by_last.get(owner) or kc
        if kind == "pass" and not _explicit_pass_attempt(match):
            try:
                completions = int(row.get("completions"))
            except (TypeError, ValueError):
                completions = None
            if completions is not None and claimed == completions:
                continue
            bound = _bound_box_team(text, match.start(), aliases)
            if bound and rush_att.get(bound) == claimed:
                kind = "rush"
            elif claimed in {n for n in rush_att.values() if n is not None}:
                kind = "rush"
        if kind == "rush":
            team = _bound_box_team(text, match.start(), aliases) or "KC"
            official = rush_att.get(team)
            if official != claimed:
                owners = [t for t, n in rush_att.items() if n == claimed]
                if not _bound_box_team(text, match.start(), aliases) and owners:
                    team = owners[0]
                    official = rush_att.get(team)
            if official is None:
                continue
            if claimed == official:
                continue
            issues.append(
                f"rush attempts {claimed} disagrees with ESPN {official} for "
                f"{team} ({match.group(0)!r})"
            )
            continue
        official = int(row["attempts"])
        if claimed == official:
            continue
        issues.append(
            f"pass attempts {claimed} disagrees with ESPN {official} for "
            f"{row.get('player') or 'KC'} ({match.group(0)!r})"
        )
    return issues


# A comma starts the next flag only when the next item is a penalty type.
# "Karlaftis, not Sneed" stays one clause.
_PENALTY_LIST_BREAK = re.compile(
    r"[,;]\s*(?=(?:and\s+)?(?:"
    r"defensive|offensive|illegal|holding|offside|ineligible|"
    r"roughing|unnecessary|face\s+mask|neutral\s+zone|"
    r"pass\s+interference|illegal\s+contact|illegal\s+block"
    r"))",
    re.IGNORECASE,
)


def _penalty_item_span(text: str, index: int) -> tuple[int, int]:
    """List-item span so 'holding on Sneed' does not own a nearby illegal-use."""
    sent_start, sent_end = _sentence_span(text, index)
    local = text[sent_start:sent_end]
    rel = index - sent_start
    item_start = 0
    item_end = len(local)
    for hit in _PENALTY_LIST_BREAK.finditer(local):
        if hit.end() <= rel:
            item_start = hit.end()
        elif hit.start() >= rel:
            item_end = hit.start()
            break
    return sent_start + item_start, sent_start + item_end


def _check_penalty_attribution(text: str, recap: dict | None) -> list[str]:
    penalties = [
        row
        for row in (recap or {}).get("penalties") or []
        if isinstance(row, dict)
    ]
    if not text or not penalties:
        return []
    issues = []
    for match in _ILLEGAL_USE.finditer(text):
        item_start, item_end = _penalty_item_span(text, match.start())
        low = text[item_start:item_end].lower()
        sentence = _sentence_at(text, match.start())
        official = [
            row
            for row in penalties
            if "illegal use" in (row.get("type") or "").lower()
        ]
        if not official:
            continue
        players = {_player_last(row.get("player") or "") for row in official}
        players.discard("")
        wiped = {(row.get("wiped") or "").lower() for row in official}
        credited = any(
            name and re.search(rf"\b{re.escape(name)}\b", low) for name in players
        )
        if (
            not credited
            and re.search(r"\bsneed\b", low)
            and "sneed" not in players
            and not re.search(r"\bnot sneed\b", low)
        ):
            who = ", ".join(sorted(players)) or "the flagged player"
            issues.append(
                f"illegal-use flag was on {who}, not sneed ({sentence!r})"
            )
        if re.search(r"\bmiami interception\b", low) and any(
            "roland" in item or "interception" in item for item in wiped
        ):
            issues.append(
                f"illegal-use flag wiped a KC interception, not a Miami one "
                f"({sentence!r})"
            )
    return issues


def _check_same_look_snaps(text: str, recap: dict | None) -> list[str]:
    eligible = [
        row
        for row in (recap or {}).get("eligible") or []
        if isinstance(row, dict)
    ]
    if not text or not eligible:
        return []
    nourzad_clocks = {
        (row.get("quarter"), row.get("clock"))
        for row in eligible
        if "nourzad" in (row.get("player") or "").lower()
    }
    issues = []
    for match in _SAME_LOOK.finditer(text):
        sentence = _sentence_at(text, match.start())
        low = sentence.lower()
        if "nourzad" not in low and "eligible" not in low and "jumbo" not in low:
            continue
        # The two Walker stuffs at the Miami 12 were Q2 6:58 (no report)
        # and Q2 6:15 (Nourzad). Same-look / both-snaps claims are false.
        if (2, "6:58") not in nourzad_clocks and (
            "both" in low or "same" in low or "each" in low
        ):
            issues.append(
                "Nourzad was reported eligible only on the Q2 6:15 snap, "
                f"not on 6:58 ({sentence!r})"
            )
    return issues


def _first_score_elapsed(recap: dict | None):
    plays = (recap or {}).get("scoringPlays") or []
    if not plays:
        return None
    first = plays[0]
    if int(first.get("quarter") or 0) != 1:
        return None
    remaining = _clock_seconds(first.get("clock"))
    return 15 * 60 - remaining


def _clause_at(text: str, start: int, end: int) -> str:
    """One claim: stop at newlines and sentence punctuation so later fields
    cannot turn a kickoff-clock note into an opening-score claim."""
    left_start = 0
    for idx in range(start - 1, -1, -1):
        char = text[idx]
        if char in "\n!?;" or (char == "." and not _is_initial_dot(text, idx)):
            left_start = idx + 1
            break
    right_end = len(text)
    for idx in range(end, len(text)):
        char = text[idx]
        if char in "\n!?;" or (char == "." and not _is_initial_dot(text, idx)):
            right_end = idx
            break
    return text[left_start:right_end]


def _check_first_minutes(text: str, recap: dict | None) -> list[str]:
    elapsed = _first_score_elapsed(recap)
    if not text or elapsed is None:
        return []
    issues = []
    for match in _FIRST_MINUTES.finditer(text):
        phrase = match.group(0)
        clause = _clause_at(text, match.start(), match.end())
        if not _SCORE_CLAIM.search(clause):
            continue
        claimed = _match_count(match)
        if claimed is None:
            continue
        window = claimed
        unit = "minutes"
        if re.search(r"\bseconds?\b", phrase, re.I):
            unit = "seconds"
        else:
            window = claimed * 60
        if elapsed > window:
            mm, ss = divmod(elapsed, 60)
            issues.append(
                f"opening score came at {mm}:{ss:02d}, not in the first "
                f"{claimed} {unit} ({phrase!r})"
            )
    return issues


def _pressure_owner_name(sentence: str, match) -> str:
    groups = match.groupdict() if getattr(match, "re", None) and match.re.groupindex else {}
    named = (groups.get("sack_owner") or groups.get("hit_owner") or "").strip()
    if named:
        return named
    token = match.group(0) or ""
    rel = sentence.lower().rfind(token.lower()) if token else -1
    prefix = sentence[:rel] if rel >= 0 else sentence
    # Possessive owners sit on the stat token itself ("Crosby's two sacks").
    poss_window = prefix + token
    poss = list(_POSSESSIVE_PRESSURE.finditer(poss_window))
    if poss:
        return poss[-1].group(1).strip()
    others = list(_PRESSURE_OWNER.finditer(prefix))
    if others:
        return (others[-1].group(1) or "").strip()
    return ""


def _owner_tokens(owner: str) -> set[str]:
    return {part.lower() for part in re.split(r"[^A-Za-z]+", owner) if part}


def _skip_pressure_claim(sentence: str, match) -> bool:
    if _SEASON_SPAN.search(sentence):
        return True
    if _PRESSURE_WINDOW.search(sentence):
        return True
    owner = _pressure_owner_name(sentence, match)
    if not owner:
        return False
    tokens = _owner_tokens(owner)
    if tokens.intersection(_TEAM_SIDES) or tokens.intersection(_QB_ALIASES):
        return False
    return True


def _pressure_side(sentence: str, match) -> str:
    groups = match.groupdict() if getattr(match, "re", None) and match.re.groupindex else {}
    qb = (groups.get("sack_qb") or groups.get("hit_qb") or "").strip().lower()
    if qb in _QB_ALIASES:
        return _QB_ALIASES[qb]
    owner = _pressure_owner_name(sentence, match)
    for token in _owner_tokens(owner):
        if token in _TEAM_SIDES:
            return _TEAM_SIDES[token]
    low = sentence.lower()
    if re.search(
        r"\b(?:sacked|hit)\s+willis\b|\b(?:sacks?|hits?)\s+(?:of|on)\s+willis\b",
        low,
    ):
        return "opp_qb"
    if "willis" in low and "mahomes" not in low:
        return "opp_qb"
    return "kc_qb"


def _official_sacks(recap: dict | None, side: str):
    passing = [
        row
        for row in (recap or {}).get("passing") or []
        if isinstance(row, dict) and row.get("sacks") is not None
    ]
    if side == "kc_qb":
        row = next(
            (item for item in passing if (item.get("team") or "").upper() == "KC"),
            None,
        )
        return None if row is None else int(row["sacks"])
    row = next(
        (item for item in passing if (item.get("team") or "").upper() != "KC"),
        None,
    )
    return None if row is None else int(row["sacks"])


def _official_qb_hits(recap: dict | None, side: str):
    hits = (recap or {}).get("qbHits") or {}
    key = "OPP" if side == "kc_qb" else "KC"
    value = hits.get(key)
    return None if value is None else int(value)


def _check_sack_counts(text: str, recap: dict | None) -> list[str]:
    if not text:
        return []
    issues = []
    for match in _SACK_COUNT.finditer(text):
        sentence = _sentence_at(text, match.start())
        if _skip_pressure_claim(sentence, match):
            continue
        claimed = _match_count(match)
        official = _official_sacks(recap, _pressure_side(sentence, match))
        if claimed is None or official is None or claimed == official:
            continue
        issues.append(
            f"sacks {claimed} disagrees with ESPN {official} "
            f"({match.group(0)!r})"
        )
    return issues


def _check_qb_hit_counts(text: str, recap: dict | None) -> list[str]:
    if not text:
        return []
    issues = []
    for match in _QB_HIT_COUNT.finditer(text):
        sentence = _sentence_at(text, match.start())
        if _skip_pressure_claim(sentence, match):
            continue
        claimed = _match_count(match)
        official = _official_qb_hits(recap, _pressure_side(sentence, match))
        if claimed is None or official is None or claimed == official:
            continue
        issues.append(
            f"QB hits {claimed} disagrees with ESPN {official} "
            f"({match.group(0)!r})"
        )
    return issues


def _parse_elapsed_claim(raw: str):
    text = (raw or "").strip().lower()
    if re.match(r"\d{1,2}:\d{2}$", text):
        return _clock_seconds(text)
    match = re.search(rf"({_NUM_TOKEN})\s+minutes?", text, re.I)
    if not match:
        return None
    minutes = _parse_count(match.group(1))
    return None if minutes is None else minutes * 60


def _check_opening_drive(text: str, recap: dict | None) -> list[str]:
    elapsed = _first_score_elapsed(recap)
    if not text or elapsed is None:
        return []
    issues = []
    for match in _OPENING_DRIVE.finditer(text):
        claimed = _parse_elapsed_claim(match.group(1))
        if claimed is None or claimed == elapsed:
            continue
        mm, ss = divmod(elapsed, 60)
        issues.append(
            f"opening drive lasted {mm}:{ss:02d}, not {match.group(1)} "
            f"({match.group(0)!r})"
        )
    return issues


def _check_eligible_on_score(text: str, recap: dict | None) -> list[str]:
    eligible = [
        row
        for row in (recap or {}).get("eligible") or []
        if isinstance(row, dict)
    ]
    scores = [
        play
        for play in (recap or {}).get("scoringPlays") or []
        if isinstance(play, dict)
    ]
    if not text or not eligible or not scores:
        return []
    score_clocks = {
        (int(play.get("quarter") or 0), str(play.get("clock") or ""))
        for play in scores
    }
    issues = []
    for match in re.finditer(r"\beligible\b", text, re.I):
        clause = _clause_at(text, match.start(), match.end())
        low = clause.lower()
        if not re.search(
            r"\b(?:touchdown|td|score)\b.{0,48}\bwith\b.{0,40}\beligible\b",
            low,
        ):
            continue
        if re.search(r"\bbut\b.{0,80}\beligible\b", low):
            continue
        for row in eligible:
            last = _player_last(row.get("player") or "")
            if not last or not re.search(rf"\b{re.escape(last)}\b", low):
                continue
            clock = (int(row.get("quarter") or 0), str(row.get("clock") or ""))
            if clock not in score_clocks:
                issues.append(
                    f"{row.get('player') or last} was not eligible on that "
                    f"scoring play ({clause!r})"
                )
    return issues


def _official_int_clocks(recap: dict | None) -> set[tuple[str, int, str]]:
    out = set()
    for play in _plays(recap):
        if play.get("kind") != "int" and "intercept" not in (play.get("text") or "").lower():
            continue
        last = _player_last(play.get("interceptedBy") or "")
        if not last:
            continue
        out.add((last, int(play.get("quarter") or 0), str(play.get("clock") or "")))
    for row in (recap or {}).get("driveResults") or []:
        if not isinstance(row, dict):
            continue
        if (row.get("result") or "").upper() != "INT":
            continue
        detail = row.get("detail") or ""
        who = re.search(r"\b([A-Z]\.[A-Za-z''-]+)\s+intercept", detail, re.I)
        last = _player_last(who.group(1) if who else "")
        if last:
            out.add((last, int(row.get("quarter") or 0), str(row.get("clock") or "")))
    return out


def _check_int_clocks(text: str, recap: dict | None) -> list[str]:
    official = _official_int_clocks(recap)
    if not text or not official:
        return []
    issues = []
    official_names = {name for name, _, _ in official}
    for match in _INT_AT_CLOCK.finditer(text):
        raw = match.group(1) or ""
        if not raw[:1].isupper():
            continue
        last = _player_last(raw)
        quarter = int(match.group(2))
        clock = match.group(3)
        if not last or last not in official_names:
            continue
        if (last, quarter, clock) in official:
            continue
        issues.append(
            f"{match.group(1)} interception was not at Q{quarter} {clock} "
            f"({match.group(0)!r})"
        )
    return issues


def _official_pass_tds(recap: dict | None):
    passing = [
        row
        for row in (recap or {}).get("passing") or []
        if isinstance(row, dict) and (row.get("team") or "").upper() == "KC"
    ]
    if not passing:
        return None
    row = passing[0]
    if row.get("touchdowns") is None:
        return None
    return int(row["touchdowns"])


def _check_pass_touchdowns(text: str, recap: dict | None) -> list[str]:
    official = _official_pass_tds(recap)
    if not text or official is None:
        return []
    issues = []
    for match in _PASS_TD_COUNT.finditer(text):
        sentence = _sentence_at(text, match.start())
        low = sentence.lower()
        if (
            "mahomes" not in low
            and "touchdown passes" not in low
            and not re.search(r"\d{1,2}-of-\d{1,2}", low)
        ):
            continue
        claimed = _match_count(match)
        if claimed is None or claimed == official:
            continue
        issues.append(
            f"passing touchdowns {claimed} disagrees with ESPN {official} "
            f"({match.group(0)!r})"
        )
    return issues


# In-season offline editions from the Grok path have been 3,929–4,726 words.
# The 2026-09-29 fallback was ~1,925 and must not auto-publish.
OFFLINE_WORD_FLOOR = 3000
_MIN_DUP_CHARS = 80
_RECORD_TOKEN = re.compile(r"\b(\d+)-(\d+)(?:-(\d+))?\b")
_STALE_AUGUST = re.compile(r"\bAugust\b", re.IGNORECASE)
_STALE_PRESEASON = re.compile(r"\bpreseason\b", re.IGNORECASE)
# Contrast with the preseason, not "this edition is preseason".
_PRESEASON_COMPARE = re.compile(
    r"(?:ahead of|better than|worse than|more than|less than|than|"
    r"before|after|versus|vs\.?)\s+(?:the\s+)?preseason"
    r"|preseason\s+(?:forecast|script|expectation|expectations)"
    r"|preseason\s+did\s+not"
    r"|problem\s+the\s+preseason",
    re.IGNORECASE,
)
_STALE_CURRENT_SEASON = re.compile(
    r"\blooks?\s+most\s+2025\b"
    r"|\bthe\s+2025\s+Chiefs\s+are\b"
    r"|\bKC\s+2025\s+record\b"
    r"|\bthis\s+2025\s+season\b"
    r"|\bcurrent\s+2025\b",
    re.IGNORECASE,
)


def edition_word_count(narrative: dict | None) -> int:
    """Whitespace-separated words across generated edition sections."""
    return len(edition_text(narrative).split())


def record_token(value: str) -> str:
    """Normalize '3-0', '3-0-0', or 'Preseason 0-1' for comparison."""
    text = (value or "").strip()
    text = re.sub(r"^preseason\s+", "", text, flags=re.IGNORECASE)
    match = _RECORD_TOKEN.search(text)
    if not match:
        return text.lower()
    wins, losses, ties = match.group(1), match.group(2), match.group(3) or "0"
    if ties == "0":
        return f"{wins}-{losses}"
    return f"{wins}-{losses}-{ties}"


def _check_record_match(narrative: dict | None, schedule) -> list[str]:
    if schedule is None:
        return []
    phase = (narrative or {}).get("phase") or {}
    expected = phase_mod.current_record(schedule, phase)
    actual = ((narrative or {}).get("record") or "").strip()
    ptype = phase.get("type") or ""
    if ptype in ("regular", "postseason") and not expected:
        return [
            "in-season record missing from the schedule; refusing last-season fallback"
        ]
    if not expected:
        return []
    if record_token(actual) != record_token(expected):
        return [
            f"record {actual or '(empty)'} disagrees with schedule record {expected}"
        ]
    return []


def _in_season_phase(narrative: dict | None) -> bool:
    ptype = ((narrative or {}).get("phase") or {}).get("type") or ""
    return ptype in ("regular", "postseason")


def _stale_preseason_claim(text: str) -> bool:
    """True when in-season copy treats now as preseason, not a comparison."""
    if not _STALE_PRESEASON.search(text or ""):
        return False
    for match in _STALE_PRESEASON.finditer(text or ""):
        window = (text or "")[max(0, match.start() - 48) : match.end() + 32]
        if not _PRESEASON_COMPARE.search(window):
            return True
    return False


def _check_stale_season_copy(narrative: dict | None) -> list[str]:
    if not _in_season_phase(narrative):
        return []
    text = edition_text(narrative)
    if not text.strip():
        return []
    issues = []
    if _STALE_AUGUST.search(text):
        issues.append("in-season edition contains 'August'")
    if _stale_preseason_claim(text):
        issues.append("in-season edition contains 'preseason'")
    for match in _STALE_CURRENT_SEASON.finditer(text):
        issues.append(
            f"in-season edition treats 2025 as the current season ({match.group(0)!r})"
        )
        break
    return issues


def _section_paragraphs(narrative: dict | None) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    for key in (
        "storyline",
        "lastGameReview",
        "currentState",
        "gamePlan",
        "spotlight",
    ):
        parts: list[str] = []
        _walk_prose((narrative or {}).get(key), parts)
        paras = []
        for part in parts:
            text = " ".join(str(part).lower().split())
            if len(text) >= _MIN_DUP_CHARS:
                paras.append(text)
        sections[key] = paras
    return sections


def _check_duplicate_copy(narrative: dict | None) -> list[str]:
    seen: dict[str, list[str]] = {}
    for section, paras in _section_paragraphs(narrative).items():
        for para in paras:
            seen.setdefault(para, []).append(section)
    issues = []
    reported = set()
    for _text, sections in seen.items():
        key = tuple(sorted(set(sections)))
        if len(set(sections)) >= 2 and key not in reported:
            reported.add(key)
            issues.append(
                "duplicated paragraph across " + ", ".join(sorted(set(sections)))
            )
        elif len(sections) >= 3 and "repeat" not in reported:
            reported.add("repeat")
            issues.append("duplicated paragraph repeated 3+ times")
    return issues


def _check_offline_word_floor(narrative: dict | None) -> list[str]:
    generator = ((narrative or {}).get("generator") or "").lower()
    if not generator.startswith("offline"):
        return []
    if not _in_season_phase(narrative):
        return []
    count = edition_word_count(narrative)
    if count < OFFLINE_WORD_FLOOR:
        return [
            f"offline edition is {count} words; floor is {OFFLINE_WORD_FLOOR} "
            "(refusing auto-publish)"
        ]
    return []


def _upcoming_reg_post(schedule) -> bool:
    for game in schedule or []:
        if not isinstance(game, dict):
            continue
        if game.get("seasonType") not in ("reg", "post"):
            continue
        if game.get("completed"):
            continue
        return True
    return False


def _offseason_while_slate_open(narrative: dict | None, schedule=None) -> bool:
    ptype = ((narrative or {}).get("phase") or {}).get("type")
    return ptype == "offseason" and _upcoming_reg_post(schedule)


def _check_offseason_slate(narrative: dict | None, schedule=None) -> list[str]:
    """Hold offseason editions while regular/post games are still ahead."""
    if _offseason_while_slate_open(narrative, schedule):
        return [
            "offseason phase while the slate still has upcoming "
            "regular/postseason games"
        ]
    return []


def _playoff_last_game(narrative: dict | None) -> dict | None:
    phase = ((narrative or {}).get("phase") or {})
    last = phase.get("lastGame") or (narrative or {}).get("lastGame")
    return last if isinstance(last, dict) else None


def _playoff_next_unknown(narrative: dict | None, schedule=None) -> bool:
    """True after a playoff win when ESPN has not posted the next row."""
    last = _playoff_last_game(narrative)
    if not last or last.get("seasonType") != "post":
        return False
    if not phase_mod.is_final(last):
        return False
    kc, opp = last.get("kcScore"), last.get("oppScore")
    if kc is None or opp is None or kc <= opp:
        return False
    for game in schedule or []:
        if not isinstance(game, dict):
            continue
        if game.get("seasonType") not in ("reg", "post"):
            continue
        if phase_mod.is_upcoming(game):
            return False
    return True


def _check_playoff_next_unknown(
    narrative: dict | None, schedule=None
) -> list[str]:
    """Hold a playoff bye / unposted opponent instead of treating it as over."""
    if _playoff_next_unknown(narrative, schedule):
        return ["playoff next game is unknown; holding"]
    return []


def check_copy_gates(narrative: dict | None, schedule=None) -> list[str]:
    """Record, stale-season, duplication, and offline word-count gates."""
    issues = _check_record_match(narrative, schedule)
    issues.extend(_check_stale_season_copy(narrative))
    issues.extend(_check_duplicate_copy(narrative))
    issues.extend(_check_offline_word_floor(narrative))
    return issues


def check_diagram_captions(narrative: dict | None) -> list[str]:
    """Visible SVG footer text must come from the card's why."""
    issues = []
    for xo in (narrative or {}).get("xsandos") or []:
        if not isinstance(xo, dict):
            continue
        rel = (xo.get("diagram") or "").strip()
        why = (xo.get("why") or "").strip()
        if not rel or not why:
            continue
        path = config.PUBLIC_DIR / rel
        if not path.is_file():
            issues.append(
                f"XO diagram missing for {xo.get('concept') or rel}: {rel}"
            )
            continue
        visible = diagrams.visible_caption(path.read_text(encoding="utf-8"))
        if not diagrams.caption_matches_why(visible, why):
            issues.append(
                f"XO caption does not match why for "
                f"{xo.get('concept') or rel}"
            )
    return issues


def check_review(
    narrative: dict | None,
    last_game: dict | None,
    recap: dict | None,
    *,
    schedule=None,
    copy_gates: bool = False,
) -> list[str]:
    """Return human-readable violations, or an empty list when the edition is clean.

    Scans every generated section (review, story, currentState, gamePlan,
    xsandos, matchups, strategies, …). If the recap is empty, only
    final-score contexts are checked against ``lastGame`` scores.
    Completions (3-of-7), records (6-11, 3-0), and similar non-score
    numbers are ignored unless ``copy_gates`` is on — then the top-level
    record must match the slate, in-season copy cannot say August /
    preseason / 2025-as-current, offline editions must clear the word
    floor, and heavy duplicated paragraphs fail.
    """
    issues: list[str] = []
    if last_game and phase_mod.is_final(last_game):
        text = edition_text(narrative)
        if not text.strip():
            text = review_text(narrative)
        if text.strip():
            recap = recap or {}
            issues.extend(_check_scores(text, last_game, recap))
            issues.extend(_check_part_of_day(text, last_game, recap))
            issues.extend(_check_echoed_instructions(text))
            issues.extend(_check_record_phrasing(text))
            if recap.get("scoringPlays") or recap.get("leaders") or recap.get("kc") or recap.get("plays"):
                issues.extend(_check_td_yards(text, recap, last_game))
                issues.extend(_check_fg_claims(text, recap, last_game))
                issues.extend(_check_stat_lines(text, recap, last_game))
                issues.extend(_check_play_sequence(text, recap))
                issues.extend(_check_turnover_credit(text, recap))
                issues.extend(_check_absolute_claims(prose_text(narrative), recap))
                issues.extend(_check_team_yards(text, recap, last_game, schedule))
                issues.extend(_check_garbled_initials(text, recap))
                issues.extend(_check_scheme_claims(text, recap))
                issues.extend(_check_box_clocks(text, recap, last_game, schedule))
                issues.extend(_check_unknown_was_box(text, last_game, recap, schedule))
                issues.extend(_check_drive_counts(text, recap, last_game, schedule))
                issues.extend(_check_touch_counts(text, recap))
                issues.extend(_check_attempt_counts(text, recap, last_game))
                issues.extend(_check_sack_counts(text, recap))
                issues.extend(_check_qb_hit_counts(text, recap))
                issues.extend(_check_penalty_attribution(text, recap))
                issues.extend(_check_same_look_snaps(text, recap))
                issues.extend(_check_first_minutes(text, recap))
                issues.extend(_check_opening_drive(text, recap))
                issues.extend(_check_eligible_on_score(text, recap))
                issues.extend(_check_int_clocks(text, recap))
                issues.extend(_check_pass_touchdowns(text, recap))
    issues.extend(_check_offseason_slate(narrative, schedule))
    issues.extend(_check_playoff_next_unknown(narrative, schedule))
    if copy_gates:
        issues.extend(check_copy_gates(narrative, schedule))
    # Dedup while keeping order.
    out = []
    seen = set()
    for item in issues:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _retry_stat_table(recap: dict | None) -> str:
    recap = recap or {}
    kc = recap.get("kc") or {}
    opp = recap.get("opp") or {}
    prior = recap.get("prior") or {}
    lines = [
        "PER-GAME STAT TABLE (do not blend last-game and prior-game numbers):",
        "  LAST vs "
        + (recap.get("oppAbbr") or "OPP")
        + f": KC rush={kc.get('rushingYards') or '—'} pass={kc.get('netPassingYards') or '—'} "
        f"total={kc.get('totalYards') or '—'} firstDowns={kc.get('firstDowns') or '—'} "
        f"thirdDown={kc.get('thirdDownEff') or '—'} poss={kc.get('possessionTime') or '—'} "
        f"drives={kc.get('totalDrives') or '—'}; "
        f"OPP rush={opp.get('rushingYards') or '—'} poss={opp.get('possessionTime') or '—'} "
        f"drives={opp.get('totalDrives') or '—'}.",
    ]
    if prior.get("kc"):
        pk = prior["kc"]
        po = prior.get("opp") or {}
        lines.append(
            "  PRIOR vs "
            + (prior.get("oppAbbr") or "prior")
            + f": KC rush={pk.get('rushingYards') or '—'} pass={pk.get('netPassingYards') or '—'} "
            f"total={pk.get('totalYards') or '—'} firstDowns={pk.get('firstDowns') or '—'} "
            f"thirdDown={pk.get('thirdDownEff') or '—'} poss={pk.get('possessionTime') or '—'} "
            f"drives={pk.get('totalDrives') or '—'}; "
            f"OPP poss={po.get('possessionTime') or '—'} "
            f"drives={po.get('totalDrives') or '—'}."
        )
    return "\n".join(lines)


def retry_instruction(violations: list[str], recap: dict | None = None) -> str:
    bullets = "\n".join(f"- {v}" for v in violations)
    return (
        "FACT CHECK RETRY: the generated edition disagrees with the ESPN box "
        "and scoring plays we supplied. Each bullet is a specific rejection "
        "you must fix before rewriting — do not repeat the flagged wording:\n"
        f"{bullets}\n"
        f"{_retry_stat_table(recap)}\n"
        "Rewrite every section (lastGameReview, storyline, currentState, "
        "gamePlan, xsandos, matchups, strategies) so every final score, "
        "in-game score, player stat line, TD/FG yardage, and FG/TD count "
        "matches those ESPN facts by team. Credit the kicking team. Do not "
        "call a morning/midday/afternoon kickoff a night unless you are "
        "writing about a prior night game by name. Credit only the "
        "player ESPN lists as forcing or recovering a fumble. Team rushing "
        "is 88 in Miami, not 18. Use last names (Karlaftis, not George). "
        "Do not write only/first/never/lone play claims the play-by-play "
        "cannot support. A score 'after' or 'following' a play must bind "
        "to the play that clause actually names. Possession clocks stay "
        "with the team on that box line — do not give the opponent TOP "
        "to Kansas City or mix last-game and prior-game clocks."
    )


def sentence_repair_instruction(violations: list[str]) -> str:
    bullets = "\n".join(f"- {v}" for v in violations)
    return (
        "SENTENCE REPAIR: do not rewrite the edition. Replace only the "
        "sentences that triggered these violations. Drop a sentence if you "
        "cannot make it agree with the ESPN scoring table.\n"
        f"{bullets}"
    )


def violation_snippets(violations: list[str]) -> list[str]:
    """Quoted match text from a violation, e.g. 'lost 18-19'.

    Scheme leftovers used to ship with no quotes, so the drop pass could
    not find 'between the tackles' / 'zone blitz'. Parenthetical quotes
    from check_review come first. ASCII possessives like Miami's used to
    swallow ('34:21') and leave the clock sentence in the edition.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(snippet: str) -> None:
        token = (snippet or "").strip()
        if not token or token in seen:
            return
        # Miami's clock ('34:21') → leftover 's clock, not KC ('
        if token.startswith("s ") or token.endswith("("):
            return
        token = token.replace("\\n", "\n")
        seen.add(token)
        out.append(token)
        if "\n" in token:
            _add(token.split("\n")[-1])
        # left.strip()+after used to emit 'nobodyafter' (run 37030574826).
        unglued = re.sub(
            r"(?<=[A-Za-z])(?=(?:after|following)\b)",
            " ",
            token,
            flags=re.I,
        )
        if unglued != token:
            _add(unglued)

    for item in violations or []:
        # official ['25:39', '34:21'] is the allowed set, not the claim.
        cleaned = re.sub(r";\s*official\s*\[[^\]]*\]", "", item or "")
        for hit in re.findall(r"\('([^']+)'\)", cleaned):
            _add(hit)
        for hit in re.findall(r"'([^']+)'", cleaned):
            _add(hit)
        for hit in re.findall(r'"([^"]+)"', cleaned):
            _add(hit)
        claimed = re.match(r"possession (\d{1,2}:\d{2})\b", cleaned, re.I)
        if claimed:
            clock = claimed.group(1)
            # A sentence-length quote already identifies the offending
            # line. Do not also add the bare clock — that used to drop
            # every correct 34:21 attribution in the edition.
            if not any(clock in token and token != clock for token in out):
                _add(clock)
    return out


def _split_sentences(text: str) -> list[str]:
    """Split on sentence punctuation, but keep initials like 'J. Rodriguez'."""
    chunks = []
    for block in re.split(r"\n+", (text or "").strip()):
        protected = re.sub(r"\b([A-Z])\.\s+", r"\1.<INI> ", block.strip())
        parts = re.split(r"(?<=[.!?])\s+", protected)
        chunks.extend(p.replace(".<INI> ", ". ") for p in parts if p.strip())
    return chunks


def analysis_sentences(narrative: dict | None) -> list[str]:
    review = (narrative or {}).get("lastGameReview") or {}
    out: list[str] = []
    for para in review.get("analysis") or []:
        if isinstance(para, dict):
            out.extend(_split_sentences(str(para.get("body") or "")))
        elif para:
            out.extend(_split_sentences(str(para)))
    return [s for s in out if s]


def _review_prose(narrative: dict | None) -> str:
    review = (narrative or {}).get("lastGameReview") or {}
    parts = [
        review.get("lede") or "",
    ]
    for para in review.get("analysis") or []:
        parts.append(para.get("body") if isinstance(para, dict) else str(para or ""))
    for key in ("whatWorked", "whatDidnt"):
        for item in review.get(key) or []:
            parts.append(str(item or ""))
    return "\n".join(p for p in parts if p)


def _orphan_issue(sentence: str) -> str | None:
    words = sentence.split()
    if not words:
        return None
    if _ORPHAN_OPENER.match(sentence):
        return f"orphan opener after repair ({sentence!r})"
    if _DANGLING_NAME.match(sentence):
        return f"dangling name after repair ({sentence!r})"
    if len(words) <= 4 and not _ORPHAN_VERB.search(sentence):
        return f"fragment after repair ({sentence!r})"
    return None


def _is_repair_orphan(sentence: str) -> bool:
    """True when a leftover sentence cannot stand after its antecedent dropped."""
    return _orphan_issue(sentence) is not None


def _live_heading_sentences(value) -> set[str]:
    """Headings that still have body copy — not salvage fragments."""
    out: set[str] = set()
    if isinstance(value, dict):
        head = _card_heading(value)
        if head and _card_body_sentences(value):
            out.add(head)
        for item in value.values():
            out.update(_live_heading_sentences(item))
    elif isinstance(value, list):
        for item in value:
            out.update(_live_heading_sentences(item))
    return out


def check_repair_orphans(
    narrative: dict | None,
    dropped: list[str],
    before: dict | None = None,
) -> list[str]:
    """Fail leftovers that lost their antecedent in a salvage drop."""
    if not dropped:
        return []
    dropped_set = {s.strip() for s in dropped if s and s.strip()}
    issues = []
    if before is None:
        for sentence in _split_sentences(_review_prose(narrative)):
            note = _orphan_issue(sentence)
            if note:
                issues.append(note)
        return issues
    for key in _EDITION_KEYS:
        old_sents = _prose_sentences(before.get(key))
        new_sents = {s.strip() for s in _prose_sentences((narrative or {}).get(key))}
        live_heads = _live_heading_sentences((narrative or {}).get(key))
        for i, sent in enumerate(old_sents):
            if sent.strip() not in dropped_set:
                continue
            for nxt in old_sents[i + 1 :]:
                if nxt.strip() not in new_sents:
                    continue
                if nxt.strip() in live_heads:
                    break
                note = _orphan_issue(nxt)
                if note:
                    issues.append(note)
                break
    return issues


def _drop_orphan_text(text: str) -> str:
    sentences = _split_sentences(text)
    if not sentences:
        return text
    kept = [item for item in sentences if not _is_repair_orphan(item)]
    if len(kept) == len(sentences):
        return text
    return " ".join(kept).strip()


def _prose_sentences(value) -> list[str]:
    parts: list[str] = []
    _walk_prose(value, parts)
    out: list[str] = []
    for part in parts:
        out.extend(_split_sentences(part))
    return [s for s in out if s]


def _card_heading(card: dict) -> str:
    for key in _CARD_HEADING_KEYS:
        if key in card:
            return str(card.get(key) or "").strip()
    return ""


def _drop_orphan_text_aware(before: str, after: str) -> str:
    """Drop leftover openers whose previous sentence was salvaged."""
    if not after:
        return after
    old = _split_sentences(before or "")
    new = _split_sentences(after)
    if not old or not new or old == new:
        return after
    dropped = {s.strip() for s in old} - {s.strip() for s in new}
    if not dropped:
        return after
    kept = []
    for sent in new:
        if not _is_repair_orphan(sent):
            kept.append(sent)
            continue
        orig_i = next(
            (j for j, item in enumerate(old) if item.strip() == sent.strip()),
            -1,
        )
        prev_dropped = orig_i > 0 and old[orig_i - 1].strip() in dropped
        next_dropped = (
            orig_i >= 0
            and orig_i + 1 < len(old)
            and old[orig_i + 1].strip() in dropped
        )
        heading_left = next_dropped and _is_heading_leftover(sent)
        if prev_dropped or heading_left:
            continue
        kept.append(sent)
    if len(kept) == len(new):
        return after
    return " ".join(kept).strip()


def _drop_script_leftover(before: str, after: str) -> str:
    """A script line that lost its down-and-distance opener is an orphan."""
    if not after or not before:
        return after
    if _SCRIPT_OPENER.match(before.strip()) and not _SCRIPT_OPENER.match(
        after.strip()
    ):
        return ""
    return after


def _is_heading_leftover(sentence: str) -> bool:
    """True for a short title/heading that cannot stand as leftover copy."""
    words = (sentence or "").split()
    if not words:
        return True
    return len(words) <= 4 and not _ORPHAN_VERB.search(sentence)


def _card_body_sentences(card: dict) -> list[str]:
    """Prose left on a card after headings (title/topic/unit/segment)."""
    if not isinstance(card, dict):
        return []
    out: list[str] = []
    for key, val in card.items():
        if key in _CARD_HEADING_KEYS:
            continue
        out.extend(_prose_sentences(val))
    return out


def _should_drop_thinned_card(before, after) -> bool:
    """Drop a card only when salvage emptied it to a heading (or no heading).

    A card that still has one correct sentence must stay. Run 37042175518
    dropped ~25 good lines because after_n <= 1 treated the leftover
    body as a thinned card.
    """
    if not isinstance(after, dict):
        return False
    if isinstance(before, dict):
        for key in _CARD_HEADING_KEYS:
            if (
                key in before
                and str(before.get(key) or "").strip()
                and not str(after.get(key) or "").strip()
            ):
                return True
    heading = _card_heading(after)
    if heading and not _card_body_sentences(after):
        return True
    return False


def _prune_salvage_value(before, after):
    """Strip leftover fragments and cards emptied by salvage, in every section."""
    if isinstance(after, list) and isinstance(before, list):
        out = []
        used: set[int] = set()
        for i, item in enumerate(after):
            prev = before[i] if i < len(before) else None
            if isinstance(item, dict):
                head = _card_heading(item)
                if head:
                    for j, cand in enumerate(before):
                        if j in used or not isinstance(cand, dict):
                            continue
                        if _card_heading(cand) == head:
                            prev = cand
                            used.add(j)
                            break
                if prev is not None:
                    item = _prune_salvage_value(prev, item)
                if _should_drop_thinned_card(prev, item):
                    continue
            elif isinstance(item, str):
                if isinstance(prev, str):
                    item = _drop_orphan_text_aware(prev, item)
                    item = _drop_script_leftover(prev, item)
                if not item:
                    continue
            if item not in (None, "", [], {}):
                out.append(item)
        return out
    if isinstance(after, dict) and isinstance(before, dict):
        out = {}
        for key, item in after.items():
            kept = _prune_salvage_value(before.get(key), item)
            if kept not in (None, "", [], {}):
                out[key] = kept
        return out
    if isinstance(after, str) and isinstance(before, str):
        kept = _drop_orphan_text_aware(before, after)
        return _drop_script_leftover(before, kept)
    return after


def _strip_empty_review_cards(review: dict) -> dict:
    """Drop lastGameReview cards whose body was emptied, leaving a heading.

    Do not strip standalone fragments or 'That…' sentences that still
    have no flagged snippet — run 37042175518 dropped 'No invented
    window.' and 'That split is the film…' that way.
    """
    analysis = []
    for para in review.get("analysis") or []:
        if isinstance(para, dict):
            body = str(para.get("body") or "").strip()
            if not body:
                continue
            analysis.append(para)
        elif str(para or "").strip():
            analysis.append(para)
    review["analysis"] = analysis
    takeaways = []
    for item in review.get("takeaways") or []:
        if isinstance(item, dict):
            if not str(item.get("body") or "").strip():
                continue
            takeaways.append(item)
        elif str(item or "").strip():
            takeaways.append(item)
    if "takeaways" in review:
        review["takeaways"] = takeaways
    return review


def drop_repair_orphans(narrative: dict | None) -> dict:
    """Remove review sentences that check_repair_orphans would veto."""
    payload = copy.deepcopy(narrative or {})
    review = payload.get("lastGameReview")
    if isinstance(review, dict):
        payload["lastGameReview"] = _strip_empty_review_cards(review)
    return payload


def _check_garbled_initials(text: str, recap: dict | None) -> list[str]:
    """'L’Sneed' is a broken initial; 'L. Sneed' / 'L'Jarius Sneed' are fine."""
    if not text:
        return []
    last_names = set()
    for play in _plays(recap):
        for field in ("forcedBy", "recoveredBy", "interceptedBy", "target"):
            token = _last_name_token(play.get(field) or "")
            if token:
                last_names.add(token)
    if not last_names:
        return []
    issues = []
    for match in _GARBLED_INITIAL.finditer(text):
        last = match.group(2).lower()
        if last in last_names:
            issues.append(
                f"garbled player initial ({match.group(0)!r}; use "
                f"{match.group(1)}. {match.group(2)} or the full first name)"
            )
    return issues


def _check_scheme_claims(text: str, recap: dict | None) -> list[str]:
    """Unverifiable scheme color must be rewritten, not salvaged later.

    Repair notes and FACT CHECK RETRY echoes are not edition claims — the
    leftover veto on run 36619750276 was the checker re-flagging those
    notes after drop, which could not see them because they had no quotes.
    """
    if not text:
        return []
    issues = []
    rushes = [
        p
        for p in _plays(recap)
        if "end" in ((p.get("direction") or "") + " " + (p.get("text") or "")).lower()
        and "walker" in (p.get("text") or "").lower()
    ]
    if rushes:
        for match in _BETWEEN_TACKLES.finditer(text):
            sentence = _sentence_at(text, match.start())
            if _SCHEME_NOTE.search(sentence):
                continue
            issues.append(
                "Walker also had end runs; do not write that the 70 were "
                f"between the tackles ({match.group(0)!r})"
            )
    int_texts = " ".join(
        (p.get("text") or "")
        for p in _plays(recap)
        if p.get("kind") == "int" or "intercept" in (p.get("text") or "").lower()
    ).lower()
    if "blitz" not in int_texts:
        for match in _ZONE_BLITZ_INT.finditer(text):
            sentence = _sentence_at(text, match.start())
            if _SCHEME_NOTE.search(sentence):
                continue
            issues.append(
                "play-by-play does not back a zone blitz producing the "
                f"interception ({match.group(0)!r})"
            )
    for match in _SNAP_LATER.finditer(text):
        sentence = _sentence_at(text, match.start())
        if _SCHEME_NOTE.search(sentence):
            continue
        issues.append(
            "snap-count later-claim cannot be verified against the "
            f"play-by-play ({match.group(0)!r})"
        )
    return issues


def _sentence_has_snippet(sentence: str, snip: str) -> bool:
    """True when this sentence contains the quoted violation match.

    Clock snippets such as 34:21 must still drop even when the colon
    sits against punctuation that a word-boundary search can miss.
    """
    if not snip:
        return False
    if re.search(rf"(?<!\w){re.escape(snip)}(?!\w)", sentence, re.I):
        return True
    return bool(_CLOCK_SNIPPET.match(snip) and snip in sentence)


def sentence_fails_review(
    sentence: str,
    last_game: dict | None,
    recap: dict | None,
    schedule=None,
    phase: dict | None = None,
) -> bool:
    """True when this sentence, alone, still disagrees with ESPN."""
    text = (sentence or "").strip()
    if not text:
        return False
    probe = {
        "phase": phase or {"type": "regular"},
        "lastGameReview": {
            "lede": text,
            "opponent": (last_game or {}).get("opponent") or "",
            "result": (last_game or {}).get("result") or "W",
            "score": "",
        },
    }
    if check_review(probe, last_game, recap, schedule=schedule):
        return True
    return _in_season_phase(probe) and _stale_preseason_claim(text)


def _drop_text(
    text: str,
    snippets: list[str],
    last_game: dict | None = None,
    recap: dict | None = None,
    schedule=None,
) -> str:
    """Drop only sentences that contain a flagged snippet.

    A one-sentence field used to return '' whenever any snippet was a
    substring of the whole block, which swept in neighbors that did
    not contain the hit (run 37042175518). When a recap is present,
    the snippet must also fail as its own claim — official clocks in
    a 8:42 note must not delete every correct 25:39 sentence.
    """
    needles = [s for s in snippets if s]
    if not text or not needles:
        return text
    sentences = _split_sentences(text)
    kept = []
    for sent in sentences:
        if not any(_sentence_has_snippet(sent, snip) for snip in needles):
            kept.append(sent)
            continue
        if recap is not None and not sentence_fails_review(
            sent, last_game, recap, schedule
        ):
            kept.append(sent)
            continue
    if len(kept) == len(sentences):
        return text
    return " ".join(kept).strip()


def _drop_value(
    value,
    snippets: list[str],
    last_game: dict | None = None,
    recap: dict | None = None,
    schedule=None,
):
    if isinstance(value, str):
        return _drop_text(value, snippets, last_game, recap, schedule)
    if isinstance(value, list):
        out = []
        for item in value:
            kept = _drop_value(item, snippets, last_game, recap, schedule)
            if kept in (None, "", [], {}):
                continue
            out.append(kept)
        return out
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in _PROTECTED_KEYS:
                out[key] = item
                continue
            kept = _drop_value(item, snippets, last_game, recap, schedule)
            if kept not in (None, "", [], {}):
                out[key] = kept
            elif key in ("lede", "body", "why", "coaching", "note"):
                out[key] = kept if isinstance(kept, str) else ""
            # Emptied titles (and the Hugo alt="Diagram: " they produce)
            # are omitted, not kept as "".
        return out
    return value


def _drop_inverted_play_order_text(text: str, recap: dict | None) -> str:
    """Drop leftover sentences whose after/following order still fails ESPN."""
    if not text or not recap:
        return text
    issues = _check_play_sequence(text, recap)
    if not issues:
        return text
    snippets = violation_snippets(issues)
    if snippets:
        dropped = _drop_text(text, snippets)
        if dropped != text:
            return dropped
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return ""
    kept = [sent for sent in sentences if not _check_play_sequence(sent, recap)]
    return " ".join(kept).strip()


def _drop_inverted_play_order(value, recap: dict | None):
    """Walk every section and remove inverted play-order claims after salvage."""
    if not recap:
        return value
    if isinstance(value, str):
        return _drop_inverted_play_order_text(value, recap)
    if isinstance(value, list):
        out = []
        for item in value:
            kept = _drop_inverted_play_order(item, recap)
            if kept in (None, "", [], {}):
                continue
            out.append(kept)
        return out
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in _PROTECTED_KEYS:
                out[key] = item
                continue
            kept = _drop_inverted_play_order(item, recap)
            if kept not in (None, "", [], {}):
                out[key] = kept
            elif key in ("lede", "body", "why", "coaching", "note"):
                out[key] = kept if isinstance(kept, str) else ""
        return out
    return value


def safe_score_lede(last_game: dict | None) -> str:
    last = last_game or {}
    opp = (last.get("opponent") or "the opponent").strip()
    pair = _int_pair(last.get("kcScore"), last.get("oppScore"))
    if not pair:
        return f"Kansas City played {opp}."
    return f"Kansas City finished {pair[0]}–{pair[1]} against {opp}."


_STAT_DISAGREE = re.compile(
    r"(?P<kind>touches|sacks|pass attempts|rush attempts|team rushing|"
    r"game yards|total drives|QB hits|first downs)\s+"
    r"(?P<claimed>\d+)\s+disagrees with ESPN\s+(?P<official>\d+)",
    re.IGNORECASE,
)
_ILLEGAL_USE_NOT_SNEED = re.compile(
    r"illegal-use flag was on ([^,]+), not sneed",
    re.IGNORECASE,
)


def _format_count(n: int, like: str) -> str:
    """Keep digit vs word style when swapping a verified ESPN count."""
    raw = (like or "").strip()
    if raw.isdigit():
        return str(n)
    word = {v: k for k, v in _NUMBER_WORDS.items()}.get(n)
    if not word:
        return str(n)
    if raw[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def _swap_count_in_sentence(sentence: str, claimed: int, official: int) -> str | None:
    hits = [
        match
        for match in re.finditer(rf"\b({_NUM_TOKEN})\b", sentence or "", re.I)
        if _parse_count(match.group(1)) == claimed
    ]
    if len(hits) != 1:
        return None
    hit = hits[0]
    return (
        sentence[: hit.start()]
        + _format_count(official, hit.group(1))
        + sentence[hit.end() :]
    )


def _swap_sneed_for(sentence: str, official: str) -> str | None:
    name = (official or "").split(",")[0].strip()
    if not name or not re.search(r"\bsneed\b", sentence or "", re.I):
        return None
    display = name[:1].upper() + name[1:]

    def _repl(match: re.Match) -> str:
        tail = match.group(0)[5:]
        return display + tail

    return re.sub(r"\b[Ss]need(?=['’]s\b|\b)", _repl, sentence, count=1)


def _quoted_violation(item: str) -> str:
    snippets = violation_snippets([item])
    if not snippets:
        return ""
    # check_review quotes with !r, so newlines show up as the two
    # characters backslash-n instead of a real line break.
    return max(snippets, key=len).replace("\\n", "\n")


_KC_OWNED_BOX = re.compile(
    r"\b(?:kansas\s+city|kc)['’]s\s+"
    r"(?:[A-Za-z][A-Za-z'’-]*(?:\s+[A-Za-z][A-Za-z'’-]*){0,2}\s+)?"
    r"box\b"
    r"|\bthe\s+(?:miami|seattle|denver|indianapolis|las vegas)\s+box\b",
    re.IGNORECASE,
)
_LET_THE_BOX = re.compile(
    r"\blet the\s+([A-Za-z][A-Za-z '’-]+?)\s+box\b",
    re.IGNORECASE,
)
_HAD_STAT_SUBJECT = re.compile(
    r"\b([A-Za-z][A-Za-z '’-]+?)\s+(?:had|finished with)\s+\d+\s+first downs\b",
    re.IGNORECASE,
)
_TEAM_LEAD = r"(?:the\s+)?(?P<team>[A-Za-z][A-Za-z .''’-]{0,32}?)"
_WAS_BOX_LEAD = re.compile(
    rf"^{_TEAM_LEAD}\s+(?:was|were)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_HAD_BOX_LEAD = re.compile(
    rf"^{_TEAM_LEAD}\s+(?:had|finished with)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_HELD_BOX_LEAD = re.compile(
    rf"^{_TEAM_LEAD}\s+held (?:the ball|it)(?:\s+for)?\s+"
    rf"(?P<clock>\d{{1,2}}:\d{{2}})\b",
    re.IGNORECASE,
)
_BOX_CLAUSE_SPLIT = re.compile(
    r";|"
    r"(?i:\bwhile\b)|"
    r"(?i:\beven though\b)|"
    r"(?i:\bbut\b)|"
    r"\band\s+(?=[A-Z][a-z])|"
    r"\bto\s+(?:the\s+)?(?=[A-Z])|"
    r",\s*(?=[A-Z][a-z])"
)
_WAS_LEAD_STOP = frozenset(
    {
        "that",
        "this",
        "it",
        "he",
        "she",
        "there",
        "what",
        "which",
        "who",
        "mahomes",
        "walker",
        "rice",
    }
)
_BOX_KIND_PATTERNS = (
    re.compile(r"\d+\s+first downs\b", re.I),
    re.compile(r"(?<!:)\d+\s+rush(?:ing)?(?:\s+yards)?\b", re.I),
    re.compile(r"(?<!:)\d+\s+(?:net\s+)?pass(?:ing)?(?:\s+yards)?\b", re.I),
    re.compile(r"\d+\s+total yards\b", re.I),
    re.compile(rf"(?:{_NUM_TOKEN})\s+drives\b", re.I),
    re.compile(r"\d{1,2}:\d{2}\s+(?:of\s+)?possession\b", re.I),
)
_BOX_FD = re.compile(r"\b(\d{1,2})\s+first downs\b", re.I)
_BOX_RUSH = re.compile(r"(?<!:)\b(\d{2,3})\s+rush(?:ing)?(?:\s+yards)?\b", re.I)
_BOX_PASS = re.compile(r"(?<!:)\b(\d{2,3})\s+(?:net\s+)?pass(?:ing)?(?:\s+yards)?\b", re.I)
_BOX_TOTAL = re.compile(r"\b(\d{2,3})\s+total yards\b", re.I)
_BOX_DRIVES = re.compile(rf"\b({_NUM_TOKEN})\s+drives\b", re.I)
_BOX_CLOCK = re.compile(r"\b(\d{1,2}:\d{2})\b(?!\s*(?:AM|PM))", re.I)


def _kc_owned_box(sentence: str) -> bool:
    """True for 'Kansas City's Miami box' / 'KC's box', not 'the other box'."""
    return bool(_KC_OWNED_BOX.search(sentence or ""))


def _box_clause_span(text: str, index: int) -> tuple[int, int]:
    """Subject clause around this claim: ; / while / and Team / but / even though / to / comma+Name."""
    start, end = _sentence_span(text, index)
    fragment = text[start:end]
    if not fragment:
        return start, end
    rel = max(0, min(index - start, len(fragment) - 1))
    clause_start, clause_end = 0, len(fragment)
    for hit in _BOX_CLAUSE_SPLIT.finditer(fragment):
        if hit.end() <= rel:
            clause_start = hit.end()
        elif hit.start() > rel:
            clause_end = hit.start()
            break
    return start + clause_start, start + clause_end


def _box_clause_at(text: str, index: int) -> str:
    left, right = _box_clause_span(text, index)
    return text[left:right]


def _bound_possession_in_clause(
    text: str, match: re.Match, aliases: dict[str, str]
) -> str:
    """Possession subject stays inside this clause — not after even though."""
    left, right = _box_clause_span(text, match.start())
    if re.search(r"\bto\s+$", text[max(0, left - 8) : left], re.I):
        before = text[max(0, left - 40) : left]
        after = text[left : left + 40]
        if not (
            _POSSESSION_CLOCK.search(before) and _POSSESSION_CLOCK.search(after)
        ):
            left, _ = _sentence_span(text, match.start())
    clip = text[left:right]
    rel_start = match.start() - left
    rel_end = match.end() - left

    class _ClipMatch:
        def start(self, group=0):
            del group
            return rel_start

        def end(self, group=0):
            del group
            return rel_end

        def group(self, group=0):
            return match.group(group)

    return _bound_possession_team(clip, _ClipMatch(), aliases)


def _explicit_box_subject(sentence: str, aliases: dict[str, str]) -> str:
    """'Miami had …' / 'let the Miami box' in this clause only.

    whatWorked bullets that open with Time of possession (CLOCK) are KC
    by convention — the opponent is the object being kept off the field.
    """
    if _KC_VOICED_TOP.match(sentence or ""):
        return "KC"
    let_hit = _LET_THE_BOX.search(sentence or "")
    if let_hit:
        named = _alias_team(let_hit.group(1), aliases)
        if named:
            return named
    had_hit = _HAD_STAT_SUBJECT.search(sentence or "")
    if had_hit:
        named = _alias_team(had_hit.group(1), aliases)
        if named:
            return named
    return ""


def _is_multi_stat_box(sentence: str) -> bool:
    """True when one line stacks two or more numbered box-score stats.

    Bare 'rush' or a kickoff clock is not a box line.
    """
    return sum(1 for pat in _BOX_KIND_PATTERNS if pat.search(sentence or "")) >= 2


_TWO_COUNT_FIRST_DOWNS = re.compile(
    r"\b\d{1,2}\s+and\s+\d{1,2}\s+first downs?\b",
    re.IGNORECASE,
)


def _is_two_team_first_down_pair(sentence: str) -> bool:
    """True for '19 and 18 first downs' or two first-down claims on one line."""
    if len(_FIRST_DOWNS.findall(sentence or "")) >= 2:
        return True
    return bool(_TWO_COUNT_FIRST_DOWNS.search(sentence or ""))


def _ingest_opponent_name_parts(names: set[str], raw: str) -> None:
    text = (raw or "").strip()
    if not text:
        return
    names.add(text.lower())
    tokens = [
        token
        for token in re.findall(r"[A-Za-z]+", text)
        if token.lower() not in {"the", "at"}
    ]
    for token in tokens:
        if len(token) >= 3 and token.lower() not in _WEAK_ALIAS_TOKENS:
            names.add(token.lower())
    if len(tokens) >= 2:
        names.add(" ".join(tokens[:-1]).lower())
        nick = tokens[-1].lower()
        if nick not in _WEAK_ALIAS_TOKENS:
            names.add(nick)


def _schedule_opponent_names(
    last_game: dict | None,
    recap: dict | None,
    schedule=None,
) -> list[str]:
    """Opponent tokens from the slate / recap, longest first."""
    names: set[str] = set()
    aliases = _team_aliases(last_game, recap, schedule)
    for alias, team in aliases.items():
        if team and team != "KC" and len(alias) >= 3:
            names.add(alias)
    for game in schedule or []:
        if not isinstance(game, dict):
            continue
        _ingest_opponent_name_parts(names, game.get("opponent") or "")
        _ingest_opponent_name_parts(names, game.get("opponentShort") or "")
        abbr = (game.get("opponentAbbr") or "").strip()
        if len(abbr) >= 2:
            names.add(abbr.lower())
    prior = (recap or {}).get("prior") or {}
    for raw in (
        (last_game or {}).get("opponent"),
        (last_game or {}).get("opponentShort"),
        prior.get("opponent"),
        (recap or {}).get("opponent"),
    ):
        _ingest_opponent_name_parts(names, raw or "")
    return sorted(names, key=len, reverse=True)


def _against_team_phrase(
    named: str,
    last_game: dict | None,
    recap: dict | None,
    schedule=None,
) -> str:
    """'the Chargers' for a nickname, 'Los Angeles' / 'Indianapolis' for a city."""
    token = (named or "").strip()
    if not token:
        return token
    if token.lower().startswith("the "):
        return token
    nicknames: set[str] = set()
    cities: set[str] = set()
    fulls: set[str] = set()

    def _ingest(name: str, short: str = "") -> None:
        raw = (name or "").strip()
        if raw:
            fulls.add(raw.lower())
        if short:
            nicknames.add(short.lower())
        tokens = [
            part
            for part in re.findall(r"[A-Za-z]+", raw)
            if part.lower() not in {"the", "at"}
        ]
        if tokens:
            nicknames.add(tokens[-1].lower())
            if len(tokens) >= 2:
                cities.add(" ".join(tokens[:-1]).lower())
            for part in tokens[:-1]:
                if part.lower() not in {"los", "las", "new", "green", "tampa", "kansas"}:
                    cities.add(part.lower())

    _ingest((last_game or {}).get("opponent") or "", (last_game or {}).get("opponentShort") or "")
    prior = (recap or {}).get("prior") or {}
    _ingest(prior.get("opponent") or "")
    for game in schedule or []:
        if isinstance(game, dict):
            _ingest(game.get("opponent") or "", game.get("opponentShort") or "")
    low = " ".join(token.lower().split())
    if low in cities and low not in nicknames:
        return token
    if low in nicknames or low in fulls:
        return f"the {token}"
    return token


def _recap_box_teams(last_game: dict | None, recap: dict | None) -> set[str]:
    """Last-game and prior-game opponents only — the boxes we can verify."""
    teams = {"KC"}
    for raw in (
        ((recap or {}).get("oppAbbr") or ""),
        (((recap or {}).get("prior") or {}).get("oppAbbr") or ""),
    ):
        abbr = str(raw).strip().upper()
        if abbr:
            teams.add(abbr)
    return teams


def _is_box_like_lead(sentence: str) -> bool:
    """Was/were/had/held lines that claim a team box, not a one-off note."""
    if _is_multi_stat_box(sentence):
        return True
    if _FIRST_DOWNS.search(sentence or "") and _BOX_CLOCK.search(sentence or ""):
        return True
    if _HAD_BOX_LEAD.match((sentence or "").strip()) and _FIRST_DOWNS.search(
        sentence or ""
    ):
        return True
    return bool(_HELD_BOX_LEAD.match((sentence or "").strip()))


def _unknown_was_opponent(
    sentence: str,
    last_game: dict | None,
    recap: dict | None,
    schedule=None,
) -> str:
    """'{Opp} was/were/had/held <box>' when Opp is not the last or prior team."""
    text = (sentence or "").strip()
    match = (
        _WAS_BOX_LEAD.match(text)
        or _HAD_BOX_LEAD.match(text)
        or _HELD_BOX_LEAD.match(text)
    )
    if not match:
        return ""
    named = (match.group("team") or "").strip()
    if not named or named.lower() in _WAS_LEAD_STOP:
        return ""
    if re.search(r"\b(?:report|injury|thursday)\b", named, re.I):
        return ""
    if re.search(
        r"\b(?:opener|in august|worth of praise|of their own)\b",
        text,
        re.I,
    ):
        return ""
    if re.search(r"\b(?:offense|times|fans|report)\b", named, re.I):
        return ""
    if not _is_box_like_lead(text):
        return ""
    fd = _FIRST_DOWNS.search(text)
    if fd and _stat_is_rate_or_other_game(
        text, fd, recap, last_game, schedule
    ):
        return ""
    rest = match.groupdict().get("rest") or ""
    if _RATE_OR_OTHER_GAME.search(rest):
        return ""
    if _AGAINST_OTHER_TEAM.search(text):
        return ""
    aliases = _team_aliases(last_game, recap, schedule)
    team = _alias_team(named, aliases)
    if team in _recap_box_teams(last_game, recap):
        return ""
    return named


def _check_unknown_was_box(
    text: str,
    last_game: dict | None,
    recap: dict | None,
    schedule=None,
) -> list[str]:
    issues = []
    for sentence in _split_sentences(text):
        named = _unknown_was_opponent(sentence, last_game, recap, schedule)
        if named:
            issues.append(
                f"opponent {named} box is unverifiable ({sentence!r})"
            )
    return issues


def _opponent_was_box_match(
    sentence: str,
    last_game: dict | None,
    recap: dict | None,
    schedule=None,
):
    """('IND', 'Indianapolis', '29 first downs…') for 'Indianapolis was/were …'."""
    match = _WAS_BOX_LEAD.match((sentence or "").strip())
    if not match:
        return None
    aliases = _team_aliases(last_game, recap, schedule)
    named = (match.group("team") or "").strip()
    team = _alias_team(named, aliases)
    if not team or team == "KC":
        return None
    return team, named, match.group("rest").strip().rstrip(".")


def _claimed_box_stats(sentence: str) -> list[tuple[str, object]]:
    """Typed numbers in a box line: first downs, rush, pass, total, drives, clock."""
    text = sentence or ""
    stats: list[tuple[str, object]] = []
    for match in _BOX_FD.finditer(text):
        stats.append(("firstDowns", int(match.group(1))))
    for match in _BOX_RUSH.finditer(text):
        stats.append(("rushingYards", int(match.group(1))))
    for match in _BOX_PASS.finditer(text):
        stats.append(("netPassingYards", int(match.group(1))))
    for match in _BOX_TOTAL.finditer(text):
        stats.append(("totalYards", int(match.group(1))))
    for match in _BOX_DRIVES.finditer(text):
        count = _parse_count(match.group(1))
        if count is not None:
            stats.append(("totalDrives", count))
    for match in _BOX_CLOCK.finditer(text):
        window = text[max(0, match.start() - 24) : match.end() + 24]
        if re.search(r"possession|held the ball|clock", window, re.I):
            stats.append(("possessionTime", match.group(1)))
    return stats


def _kc_box_value(recap: dict | None, game: str, key: str):
    if game == "prior":
        block = ((recap or {}).get("prior") or {}).get("kc") or {}
    else:
        block = (recap or {}).get("kc") or {}
    if key == "possessionTime":
        raw = str(block.get("possessionTime") or "").strip()
        return raw or None
    return _box_int(block, key)


def _box_stats_match_kc(
    sentence: str,
    recap: dict | None,
    game: str,
) -> bool:
    """True only when every typed number matches KC's box for that game."""
    claimed = _claimed_box_stats(sentence)
    if not claimed:
        return False
    for key, value in claimed:
        official = _kc_box_value(recap, game, key)
        if official is None or value != official:
            return False
    return True


def _reword_misattributed_box(
    sentence: str,
    recap: dict | None,
    last_game: dict | None = None,
    schedule=None,
) -> str | None:
    """KC's own box written as '{Opp} was …' → attribute to Kansas City."""
    parsed = _opponent_was_box_match(sentence, last_game, recap, schedule)
    if not parsed:
        return None
    team, display, rest = parsed
    prior_abbr = (
        ((recap or {}).get("prior") or {}).get("oppAbbr") or ""
    ).strip().upper()
    last_abbr = ((recap or {}).get("oppAbbr") or "").strip().upper()
    if prior_abbr and team == prior_abbr:
        game = "prior"
    elif last_abbr and team == last_abbr:
        game = "last"
    else:
        return None
    if not _box_stats_match_kc(sentence, recap, game):
        return None
    phrase = _against_team_phrase(display, last_game, recap, schedule)
    return f"Against {phrase}, Kansas City had {rest}."


def _flagged_sentence(
    blob: str,
    violation: str,
    last_game: dict | None = None,
    recap: dict | None = None,
    schedule=None,
) -> str | None:
    """The sentence that raised this violation, or None when the target is a tie.

    A short snippet in two clean sentences must not pick the longest.
    """
    snippet = _quoted_violation(violation)
    sentences = _split_sentences(blob)
    if not sentences:
        return snippet.split("\n")[-1].strip() if snippet else ""
    if snippet and snippet not in sentences and "\n" in snippet:
        snippet = snippet.split("\n")[-1].strip()
    if snippet:
        exact = [part for part in sentences if part == snippet]
        if len(exact) == 1:
            return exact[0]
        hits = [part for part in sentences if snippet in part]
    else:
        hits = []
    if not hits:
        return snippet
    if len(hits) == 1:
        return hits[0]
    failing = [
        part
        for part in hits
        if sentence_fails_review(part, last_game, recap, schedule)
    ]
    if len(failing) == 1:
        return failing[0]
    misattr = [
        part
        for part in failing
        if _opponent_was_box_match(part, last_game, recap, schedule)
    ]
    if len(misattr) == 1:
        return misattr[0]
    return None


def _sentence_stat_kind(sentence: str, claimed: int) -> str:
    """Which stat the claimed number is actually naming in this sentence."""
    token = re.escape(str(claimed))
    low = sentence or ""
    if re.search(rf"\b{token}\s+rush", low, re.I) or re.search(
        rf"\b{token}\s+on the ground\b", low, re.I
    ):
        return "team rushing"
    if re.search(rf"\b{token}-dropbacks?\b", low, re.I) or re.search(
        rf"\b{token}\s+dropbacks?\b", low, re.I
    ):
        return "pass attempts"
    if re.search(rf"\b{token}-drop\b", low, re.I):
        return ""
    if re.search(rf"\b{token}(?:\s+total)?\s+yards\b", low, re.I):
        return "game yards"
    return ""


def _refuse_rush_swap(
    sentence: str, claimed: int, official: int, recap: dict | None
) -> bool:
    """Do not replace prior-game rushing with last-game (or opp) rushing."""
    prior_rush = _box_int(
        ((recap or {}).get("prior") or {}).get("kc"), "rushingYards"
    )
    last_rush = _box_int((recap or {}).get("kc"), "rushingYards")
    last_opp = _box_int((recap or {}).get("opp"), "rushingYards")
    if re.search(r"indianapolis|colts|\bprior\b", sentence or "", re.I):
        if prior_rush is not None and prior_rush == claimed:
            return True
        if official in {last_rush, last_opp} and official != prior_rush:
            return True
    return False


_REWRITE_BOX_KINDS = frozenset({"first downs", "team rushing", "team passing"})
_REWRITE_PLAYER_KINDS = frozenset(
    {"pass attempts", "rush attempts", "sacks", "touches"}
)
_REWRITE_REFUSE = re.compile(
    r"\b(?:dropbacks?|carries|carry|attempts?|sacks?|"
    r"first\s+two|first\s+ten|if the first|when the first|"
    r"chasing|ended up with)\b",
    re.IGNORECASE,
)


def _correct_sentence(
    sentence: str,
    violation: str,
    recap: dict | None = None,
    last_game: dict | None = None,
) -> str | None:
    """Rewrite one ESPN disagreement, or None when the swap is not clean.

    A rewrite is only legal when the subject team, the stat type, and this
    game's box field are all certain and the number sits in that clause.
    Player counts, ordinals, and hypotheticals are never rewritten from a
    team total.
    """
    if not sentence:
        return None
    num = _STAT_DISAGREE.search(violation or "")
    if num:
        kind = (num.group("kind") or "").lower()
        claimed = int(num.group("claimed"))
        official = int(num.group("official"))
        if kind == "touches":
            named = re.search(
                r"\sfor\s+([A-Za-z. '’-]+?)\s+\(",
                violation or "",
            )
            player = _player_last((named.group(1) if named else "") or "")
            if not player or not re.search(
                rf"\b{re.escape(player)}\b",
                sentence or "",
                re.I,
            ):
                return None
            return _swap_count_in_sentence(sentence, claimed, official)
        elif kind in _REWRITE_PLAYER_KINDS:
            return None
        elif kind not in _REWRITE_BOX_KINDS:
            return None
        elif _REWRITE_REFUSE.search(sentence or ""):
            return None
        if re.search(
            r"\b(?:walker|mahomes|rice|kelce|butker)\b",
            sentence or "",
            re.I,
        ) and kind in {"team rushing", "team passing"}:
            return None
        sent_kind = _sentence_stat_kind(sentence, claimed)
        if sent_kind and sent_kind != kind:
            return None
        if kind == "game yards" and re.search(r"\brush", sentence, re.I):
            return None
        if kind == "team rushing" and _refuse_rush_swap(
            sentence, claimed, official, recap
        ):
            return None
        if _is_multi_stat_box(sentence):
            return None
        if kind == "first downs" and _is_two_team_first_down_pair(sentence):
            return None
        claim_at = 0
        hit = re.search(rf"\b{claimed}\b", sentence or "")
        if hit:
            claim_at = hit.start()
            if _stat_is_rate_or_other_game(sentence, hit, recap, last_game):
                return None
        clause = _box_clause_at(sentence, claim_at)
        if str(claimed) not in clause:
            return None
        aliases = _team_aliases(last_game, recap)
        explicit = _explicit_box_subject(clause, aliases)
        lead = re.match(
            r"^(?:the\s+)?([A-Za-z][A-Za-z .’-]+?)\s+"
            r"(?:had|finished with|was|were|averaged|posted)\b",
            clause.strip(),
            re.I,
        )
        if lead:
            explicit = explicit or _alias_team(lead.group(1), aliases)
        if not explicit:
            return None
        last_rush = official_box_yards_by_team(recap, "rush", "last")
        prior_rush = official_box_yards_by_team(recap, "rush", "prior")
        last_pass = official_box_yards_by_team(recap, "pass", "last")
        prior_pass = official_box_yards_by_team(recap, "pass", "prior")
        last_fd = {
            "KC": _box_int((recap or {}).get("kc"), "firstDowns"),
            ((recap or {}).get("oppAbbr") or "").strip().upper(): _box_int(
                (recap or {}).get("opp"), "firstDowns"
            ),
        }
        prior_block = (recap or {}).get("prior") or {}
        prior_fd = {
            "KC": _box_int(prior_block.get("kc"), "firstDowns"),
            (prior_block.get("oppAbbr") or "").strip().upper(): _box_int(
                prior_block.get("opp"), "firstDowns"
            ),
        }
        prior_named = any(
            re.search(rf"\b{re.escape(lab)}\b", sentence or "", re.I)
            for lab in _prior_game_labels(recap)
        ) or bool(re.search(r"indianapolis|colts|\bprior\b", sentence or "", re.I))
        last_named = any(
            re.search(rf"\b{re.escape(lab)}\b", sentence or "", re.I)
            for lab in _last_game_labels(last_game, recap)
        )
        use_prior = prior_named and not last_named
        bound_official = None
        if kind == "team rushing":
            bound_official = (
                prior_rush.get(explicit) if use_prior else last_rush.get(explicit)
            )
        elif kind == "team passing":
            bound_official = (
                prior_pass.get(explicit) if use_prior else last_pass.get(explicit)
            )
        elif kind == "first downs":
            bound_official = (
                prior_fd.get(explicit) if use_prior else last_fd.get(explicit)
            )
        if bound_official is None or official != bound_official:
            return None
        return _swap_count_in_sentence(sentence, claimed, official)
    name = _ILLEGAL_USE_NOT_SNEED.search(violation or "")
    if name:
        return _swap_sneed_for(sentence, name.group(1))
    return None


def _replace_sentence_value(value, old: str, new: str):
    if isinstance(value, str):
        if old and old in _split_sentences(value):
            return value.replace(old, new, 1)
        return value
    if isinstance(value, list):
        return [_replace_sentence_value(item, old, new) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_sentence_value(item, old, new)
            for key, item in value.items()
        }
    return value


def apply_fact_corrections(
    narrative: dict | None,
    violations: list[str],
    recap: dict | None = None,
    last_game: dict | None = None,
    schedule=None,
) -> tuple[dict, list[str]]:
    """Rewrite only the flagged sentence. Log each swap.

    A snippet like '29 first downs' must not retarget an earlier correct
    line. Multi-stat box lines are reworded as a whole or left for HOLD.
    An ambiguous snippet that hits two clean sentences is skipped + held.
    """
    payload = copy.deepcopy(narrative or {})
    logs: list[str] = []
    blob = edition_text(payload)
    for item in violations or []:
        sentence = _flagged_sentence(
            blob, item, last_game, recap, schedule
        )
        if sentence is None:
            snippet = _quoted_violation(item)
            logs.append(f"ambiguous snippet {snippet!r}; holding")
            continue
        unknown = _unknown_was_opponent(sentence, last_game, recap, schedule)
        if unknown:
            logs.append(f"unverifiable opponent {unknown}; holding")
            continue
        rewritten = _reword_misattributed_box(
            sentence, recap, last_game, schedule
        )
        if not rewritten:
            rewritten = _correct_sentence(sentence, item, recap, last_game)
        if not rewritten or rewritten == sentence:
            continue
        if sentence_fails_review(rewritten, last_game, recap, schedule):
            continue
        next_payload = copy.deepcopy(payload)
        for key in _EDITION_KEYS:
            if key in next_payload:
                next_payload[key] = _replace_sentence_value(
                    next_payload[key], sentence, rewritten
                )
        if edition_text(next_payload) == blob:
            continue
        payload = next_payload
        blob = edition_text(payload)
        logs.append(f"{sentence} → {rewritten}")
    return payload, logs


def repair_offending_copy(
    narrative: dict | None,
    violations: list[str],
    last_game: dict | None = None,
    recap: dict | None = None,
) -> dict:
    """Drop offending sentences, then leftover fragments in every section."""
    payload = copy.deepcopy(narrative or {})
    snippets = violation_snippets(violations)
    if snippets:
        for key in _EDITION_KEYS:
            if key in payload:
                payload[key] = _drop_value(
                    payload[key], snippets, last_game, recap
                )
    # A glued play-order quote (nobodyafter) can miss the sentence. Re-check
    # ESPN chronology and drop any leftover inverted after/following claim.
    if recap:
        for key in _EDITION_KEYS:
            if key in payload:
                payload[key] = _drop_inverted_play_order(payload[key], recap)
    if dropped_sentences(narrative or {}, payload):
        payload = _prune_salvage_value(narrative or {}, payload)
        review = payload.get("lastGameReview")
        if isinstance(review, dict):
            payload["lastGameReview"] = _strip_empty_review_cards(review)
    review = payload.get("lastGameReview")
    if isinstance(review, dict) and not (review.get("lede") or "").strip():
        review["lede"] = safe_score_lede(last_game)
        payload["lastGameReview"] = review
    for key in _REQUIRED_COPY_KEYS:
        current = str(payload.get(key) or "").strip()
        if current and not _required_copy_fails(
            current, last_game, recap
        ):
            continue
        payload[key] = _safe_required_copy(key, last_game)
    return payload


def _required_copy_fails(
    text: str,
    last_game: dict | None,
    recap: dict | None,
    schedule=None,
) -> bool:
    if recap is None:
        return False
    return sentence_fails_review(text, last_game, recap, schedule)


def _safe_required_copy(key: str, last_game: dict | None) -> str:
    """Offline stand-in so headline/dek/theEdge are never empty."""
    del key
    return safe_score_lede(last_game)


def missing_required_copy(narrative: dict | None) -> list[str]:
    """Top-level fields salvage must never empty."""
    missing = []
    for key in _REQUIRED_COPY_KEYS:
        if not str((narrative or {}).get(key) or "").strip():
            missing.append(key)
    return missing


def restore_required_copy(
    target: dict | None,
    *sources,
    last_game: dict | None = None,
    recap: dict | None = None,
    schedule=None,
) -> dict:
    """Fill empty or still-wrong headline/dek/theEdge from a clean draft."""
    payload = copy.deepcopy(target or {})
    for key in _REQUIRED_COPY_KEYS:
        current = str(payload.get(key) or "").strip()
        if current and not _required_copy_fails(
            current, last_game, recap, schedule
        ):
            continue
        restored = False
        for src in sources:
            val = (src or {}).get(key)
            text = str(val or "").strip()
            if not text:
                continue
            if _required_copy_fails(text, last_game, recap, schedule):
                continue
            payload[key] = val
            restored = True
            break
        if not restored and not current:
            payload[key] = _safe_required_copy(key, last_game)
        elif not restored and _required_copy_fails(
            current, last_game, recap, schedule
        ):
            payload[key] = _safe_required_copy(key, last_game)
    return payload


def should_hold_automerge(
    drops: list[str],
    narrative: dict | None,
    leftover: list[str] | None = None,
    corrections: list[str] | None = None,
    schedule=None,
    missing_headline: bool = False,
) -> bool:
    """Hold whenever salvage rewrote or dropped copy, leftover, or thin.

    A missing headline with no recorded drop must hold. Never automerge
    the edition label as the published title.
    """
    if leftover:
        return True
    if drops:
        return True
    if corrections:
        return True
    if missing_headline:
        return True
    if not str((narrative or {}).get("headline") or "").strip():
        return True
    if _offseason_while_slate_open(narrative, schedule):
        return True
    if _playoff_next_unknown(narrative, schedule):
        return True
    return edition_word_count(narrative) < PUBLISH_WORD_FLOOR


def repair_publish_blockers(
    leftover: list[str],
    repaired: dict | None,
    orphans: list[str] | None = None,
    *,
    before: dict | None = None,
) -> list[str]:
    """Reasons a salvage cannot publish. Empty means the leftover copy is live.

    Analysis sentences and drop count are not blockers. A full edition
    that started at or above OFFLINE_WORD_FLOOR must still clear it
    after the drops. Thin drafts that were already under the floor are
    not failed for length here — leftover ESPN disagreements still are.
    """
    if leftover:
        return [
            "Chiefs Narrative fact-check failed after repair: "
            + "; ".join(leftover)
            + ". Refusing to publish a review that disagrees with ESPN."
        ]
    after_count = edition_word_count(repaired)
    before_count = (
        edition_word_count(before) if before is not None else after_count
    )
    if before_count >= OFFLINE_WORD_FLOOR and after_count < OFFLINE_WORD_FLOOR:
        return [
            "Chiefs Narrative fact-check repair left "
            f"{after_count} words; floor is {OFFLINE_WORD_FLOOR}."
        ]
    if orphans:
        return [
            "Chiefs Narrative fact-check left fragments after repair: "
            + "; ".join(orphans)
        ]
    return []


def dropped_sentences(before, after) -> list[str]:
    """Sentences present in ``before`` that are gone after a repair."""
    old = _split_sentences(edition_text(before))
    new = {s.strip() for s in _split_sentences(edition_text(after))}
    out = []
    seen: set[str] = set()
    for sentence in old:
        key = sentence.strip()
        if not key or key in new or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
