#!/usr/bin/env python3
"""Drop-one mutants of the window-bound scoring guard vs repository tests.

Meta-tests and source-text greps are not in this module. Each isolating
test is a behavioural unittest. Control and mutant each run 6x.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTS = ROOT / "tools" / "chiefs_narrative" / "facts.py"
PYTHON = sys.executable
N = 6
TEST_MOD = "tools.tests.test_window_scoring_counts.WindowScoringCounts"

# Isolating members: drop only that source fragment, run only that test.
MEMBERS: list[dict] = [
    {
        "id": "win_after_halftime",
        "old": '_WIN_AFTER_HALFTIME = re.compile(r"(?<!non-)(?<!un-)\\bafter\\s+halftime\\b", re.I)',
        "new": '_WIN_AFTER_HALFTIME = re.compile(r"(?!x)x", re.I)',
        "test": "test_win_after_halftime",
        "fixture": "Five Kansas City touchdowns after halftime.",
    },
    {
        "id": "win_after_the_half",
        "old": '_WIN_AFTER_THE_HALF = re.compile(r"(?<!non-)(?<!un-)\\bafter\\s+the\\s+half\\b", re.I)',
        "new": '_WIN_AFTER_THE_HALF = re.compile(r"(?!x)x", re.I)',
        "test": "test_win_after_the_half",
        "fixture": "Five Kansas City touchdowns after the half.",
    },
    {
        "id": "win_after_the_break",
        "old": '_WIN_AFTER_THE_BREAK = re.compile(r"(?<!non-)(?<!un-)\\bafter\\s+the\\s+break\\b", re.I)',
        "new": '_WIN_AFTER_THE_BREAK = re.compile(r"(?!x)x", re.I)',
        "test": "test_win_after_the_break",
        "fixture": "Five Kansas City touchdowns after the break.",
    },
    {
        "id": "win_after_a_nn_hyphen",
        "old": r'(?<!non-)(?<!un-)\bafter\s+a\s+\d{1,2}\s*-\s*\d{1,2}\s+halftime\b',
        "new": r"(?!x)x",
        "test": "test_win_after_a_nn_hyphen",
        "fixture": "Five Kansas City touchdowns after a 14-12 halftime.",
    },
    {
        "id": "win_after_a_nn_endash",
        "old": r'(?<!non-)(?<!un-)\bafter\s+a\s+\d{1,2}\s*–\s*\d{1,2}\s+halftime\b',
        "new": r"(?!x)x",
        "test": "test_win_after_a_nn_endash",
        "fixture": "Five Kansas City touchdowns after a 14–12 halftime.",
    },
    {
        "id": "win_since_halftime",
        "old": '_WIN_SINCE_HALFTIME = re.compile(r"(?<!non-)(?<!un-)\\bsince\\s+halftime\\b", re.I)',
        "new": '_WIN_SINCE_HALFTIME = re.compile(r"(?!x)x", re.I)',
        "test": "test_win_since_halftime",
        "fixture": "Five Kansas City touchdowns since halftime.",
    },
    {
        "id": "win_since_the_half",
        "old": '_WIN_SINCE_THE_HALF = re.compile(r"(?<!non-)(?<!un-)\\bsince\\s+the\\s+half\\b", re.I)',
        "new": '_WIN_SINCE_THE_HALF = re.compile(r"(?!x)x", re.I)',
        "test": "test_win_since_the_half",
        "fixture": "Five Kansas City touchdowns since the half.",
    },
    {
        "id": "win_since_the_break",
        "old": '_WIN_SINCE_THE_BREAK = re.compile(r"(?<!non-)(?<!un-)\\bsince\\s+the\\s+break\\b", re.I)',
        "new": '_WIN_SINCE_THE_BREAK = re.compile(r"(?!x)x", re.I)',
        "test": "test_win_since_the_break",
        "fixture": "Five Kansas City touchdowns since the break.",
    },
    {
        "id": "win_in_the_second_half",
        "old": r'(?<!non-)(?<!un-)\bin\s+the\s+second[- ]half\b',
        "new": r"(?!x)x",
        "test": "test_win_in_the_second_half",
        "fixture": "Five Kansas City touchdowns in the second half.",
    },
    {
        "id": "win_the_second_half",
        "old": r'(?<!non-)(?<!un-)(?<!in\s)the\s+second[- ]half\b',
        "new": r"(?!x)x",
        "test": "test_win_the_second_half",
        "fixture": "The second half was five Kansas City touchdowns.",
    },
    {
        "id": "win_second_half",
        "old": r'(?<!non-)(?<!un-)(?<!the\s)(?<!the-)second[- ]half\b',
        "new": r"(?!x)x",
        "test": "test_win_second_half",
        "fixture": "A second-half five Kansas City touchdowns closed it.",
    },
    {
        "id": "win_following_halftime",
        "old": r'(?<!non-)(?<!un-)\bfollowing\s+halftime\b',
        "new": r"(?!x)x",
        "test": "test_win_following_halftime",
        "fixture": "Five Kansas City touchdowns following halftime.",
    },
    {
        "id": "win_following_the_half",
        "old": r'(?<!non-)(?<!un-)\bfollowing\s+the\s+half\b',
        "new": r"(?!x)x",
        "test": "test_win_following_the_half",
        "fixture": "Five Kansas City touchdowns following the half.",
    },
    {
        "id": "win_following_the_break",
        "old": r'(?<!non-)(?<!un-)\bfollowing\s+the\s+break\b',
        "new": r"(?!x)x",
        "test": "test_win_following_the_break",
        "fixture": "Five Kansas City touchdowns following the break.",
    },
    {
        "id": "win_following_a_nn",
        "old": r'(?<!non-)(?<!un-)\bfollowing\s+a\s+\d{1,2}\s*[–-]\s*\d{1,2}\s+halftime\b',
        "new": r"(?!x)x",
        "test": "test_win_following_a_nn",
        "fixture": "Five Kansas City touchdowns following a 14-12 halftime.",
    },
    {
        "id": "win_turned_halftime",
        "old": r'\bturned\s+a\s+\d{1,2}\s*[–-]\s*\d{1,2}\s+halftime\s+into\b',
        "new": r"(?!x)x",
        "test": "test_win_turned_halftime",
        "fixture": "Five Kansas City touchdowns turned a 14-12 halftime into 35-12.",
    },
    {
        "id": "win_flipped_score",
        "old": r'\bflipped\s+a\s+game\s+that\s+was\s+\d{1,2}\s*[–-]\s*\d{1,2}\b',
        "new": r"(?!x)x",
        "test": "test_win_flipped_score",
        "fixture": "Five Kansas City touchdowns flipped a game that was 14-12.",
    },
    {
        "id": "win_fourth_quarter",
        "old": r'(?<!non-)(?<!un-)\b(?:in\s+the\s+fourth[- ]quarter|fourth-quarter)\b',
        "new": r"(?!x)x",
        "test": "test_win_fourth_quarter",
        "fixture": "Five Kansas City touchdowns in the fourth quarter.",
    },
    {
        "id": "win_after_a_score",
        "old": '_AFTER_A_SCORE_WINDOW = re.compile(r"\\bafter a score\\b", re.IGNORECASE)',
        "new": '_AFTER_A_SCORE_WINDOW = re.compile(r"(?!x)x", re.IGNORECASE)',
        "test": "test_win_after_a_score",
        "fixture": "Five Kansas City touchdowns after a score.",
    },
    {
        "id": "fold_zwsp",
        "old": '    text = text.replace(_WINDOW_FOLD_ZWSP, "")\n',
        "new": "",
        "test": "test_fold_zwsp",
        "fixture": "14 ZWSP-ZWSP 12 after-a-NN",
    },
    {
        "id": "fold_soft_hyphen",
        "old": '    text = text.replace(_WINDOW_FOLD_SOFT_HYPHEN, "")\n',
        "new": "",
        "test": "test_fold_soft_hyphen",
        "fixture": "14 SH-SH 12 after-a-NN",
    },
    {
        "id": "fold_em_dash",
        "old": '    text = text.replace(_WINDOW_FOLD_EM_DASH, "-")\n',
        "new": "",
        "test": "test_fold_em_dash",
        "fixture": "14—12 after-a-NN",
    },
    {
        "id": "fold_minus",
        "old": '    text = text.replace(_WINDOW_FOLD_MINUS, "-")\n',
        "new": "",
        "test": "test_fold_minus",
        "fixture": "14−12 after-a-NN",
    },
    {
        "id": "num_one",
        "old": '    "one",\n    "two",',
        "new": '    "two",',
        "test": "test_num_one",
        "fixture": "One Kansas City field goal after halftime.",
    },
    {
        "id": "num_two",
        "old": '    "two",\n    "three",',
        "new": '    "three",',
        "test": "test_num_two",
        "fixture": "Two Kansas City field goals after halftime.",
    },
    {
        "id": "num_three",
        "old": '    "three",\n    "four",',
        "new": '    "four",',
        "test": "test_num_three",
        "fixture": "Three Kansas City field goals after halftime.",
    },
    {
        "id": "num_four",
        "old": '    "four",\n    "five",',
        "new": '    "five",',
        "test": "test_num_four",
        "fixture": "Four Kansas City field goals after halftime.",
    },
    {
        "id": "num_five",
        "old": '    "five",\n    "six",',
        "new": '    "six",',
        "test": "test_num_five",
        "fixture": "Five Kansas City field goals after halftime.",
    },
    {
        "id": "num_six",
        "old": '    "six",\n    "seven",',
        "new": '    "seven",',
        "test": "test_num_six",
        "fixture": "Six Kansas City field goals after halftime.",
    },
    {
        "id": "num_seven",
        "old": '    "seven",\n    "eight",',
        "new": '    "eight",',
        "test": "test_num_seven",
        "fixture": "Seven Kansas City field goals after halftime.",
    },
    {
        "id": "num_eight",
        "old": '    "eight",\n    "nine",',
        "new": '    "nine",',
        "test": "test_num_eight",
        "fixture": "Eight Kansas City field goals after halftime.",
    },
    {
        "id": "num_nine",
        "old": '    "nine",\n    "ten",',
        "new": '    "ten",',
        "test": "test_num_nine",
        "fixture": "Nine Kansas City field goals after halftime.",
    },
    {
        "id": "num_ten",
        "old": '    "nine",\n    "ten",\n)',
        "new": '    "nine",\n)',
        "test": "test_num_ten",
        "fixture": "Ten Kansas City field goals after halftime.",
    },
    {
        "id": "num_digit",
        "old": '_WINDOW_COUNT_DIGIT = r"\\d+"',
        "new": '_WINDOW_COUNT_DIGIT = r"(?!x)x"',
        "test": "test_num_digit",
        "fixture": "8 Kansas City field goals after halftime.",
    },
    {
        "id": "noun_touchdowns",
        "old": '    "touchdowns",\n    "touchdown",',
        "new": '    "touchdown",',
        "test": "test_noun_touchdowns",
        "fixture": "Five Kansas City touchdowns after halftime.",
    },
    {
        "id": "noun_touchdown",
        "old": '    "touchdowns",\n    "touchdown",\n    "TDs",',
        "new": '    "touchdowns",\n    "TDs",',
        "test": "test_noun_touchdown",
        "fixture": "One Kansas City touchdown after halftime.",
    },
    {
        "id": "noun_tds",
        "old": '    "TDs",\n    "TD",',
        "new": '    "TD",',
        "test": "test_noun_tds",
        "fixture": "Five Kansas City TDs after the half.",
    },
    {
        "id": "noun_td",
        "old": '    "TDs",\n    "TD",\n    "field[ \\t]+goals",',
        "new": '    "TDs",\n    "field[ \\t]+goals",',
        "test": "test_noun_td",
        "fixture": "Five Kansas City TD after the half.",
    },
    {
        "id": "noun_field_goals",
        "old": '    "field[ \\t]+goals",\n    "field[ \\t]+goal",',
        "new": '    "field[ \\t]+goal",',
        "test": "test_noun_field_goals",
        "fixture": "Two Kansas City field goals after the break.",
    },
    {
        "id": "noun_field_goal",
        "old": '    "field[ \\t]+goals",\n    "field[ \\t]+goal",\n    "scores",',
        "new": '    "field[ \\t]+goals",\n    "scores",',
        "test": "test_noun_field_goal",
        "fixture": "One Kansas City field goal after the break.",
    },
    {
        "id": "noun_scores",
        "old": '    "scores",\n    "score",',
        "new": '    "score",',
        "test": "test_noun_scores",
        "fixture": "Five Kansas City scores after halftime.",
    },
    {
        "id": "noun_score",
        "old": '    "scores",\n    "score",\n    "yards",',
        "new": '    "scores",\n    "yards",',
        "test": "test_noun_score",
        "fixture": "One Kansas City score after halftime.",
    },
    {
        "id": "noun_yards",
        "old": '    "yards",\n    "yard",',
        "new": '    "yard",',
        "test": "test_noun_yards_fail_closed",
        "fixture": "Walker's 102 yards ... turned a 14-12 halftime into 35-12",
    },
    {
        "id": "noun_yard",
        "old": '    "yard",\n    "sacks",',
        "new": '    "sacks",',
        "test": "test_noun_yard_fail_closed",
        "fixture": "One Kansas City yard after halftime.",
    },
    {
        "id": "noun_sacks",
        "old": '    "sacks",\n    "sack",',
        "new": '    "sack",',
        "test": "test_noun_sacks_fail_closed",
        "fixture": "Five Kansas City sacks after halftime.",
    },
    {
        "id": "noun_sack",
        "old": '    "sack",\n    "points",',
        "new": '    "points",',
        "test": "test_noun_sack_fail_closed",
        "fixture": "One Kansas City sack after halftime.",
    },
    {
        "id": "noun_points",
        "old": '    "points",\n    "point",',
        "new": '    "point",',
        "test": "test_noun_points_fail_closed",
        "fixture": "21 Kansas City points after halftime.",
    },
    {
        "id": "noun_point",
        "old": '    "point",\n    "takeaways",',
        "new": '    "takeaways",',
        "test": "test_noun_point_fail_closed",
        "fixture": "One Kansas City point after halftime.",
    },
    {
        "id": "noun_takeaways",
        "old": '    "takeaways",\n    "takeaway",\n)',
        "new": '    "takeaway",\n)',
        "test": "test_noun_takeaways_fail_closed",
        "fixture": "Three Kansas City takeaways after halftime.",
    },
    {
        "id": "noun_takeaway",
        "old": '    "takeaway",\n)',
        "new": ")",
        "test": "test_noun_takeaway_fail_closed",
        "fixture": "One Kansas City takeaway after halftime.",
    },
    {
        "id": "count_scope_after_halftime",
        "old": '    r"after\\s+halftime|"\n    r"after\\s+the\\s+half|"',
        "new": '    r"after\\s+the\\s+half|"',
        "test": "test_scope_after_halftime_three_empty_issues",
        "fixture": "Three Kansas City touchdowns after halftime.",
    },
    {
        "id": "count_scope_after_the_half",
        "old": '    r"after\\s+the\\s+half|"\n    r"after\\s+the\\s+break|"',
        "new": '    r"after\\s+the\\s+break|"',
        "test": "test_scope_after_the_half_three_empty_issues",
        "fixture": "Three Kansas City touchdowns after the half.",
    },
    {
        "id": "count_scope_after_the_break",
        "old": '    r"after\\s+the\\s+break|"\n    r"after\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"',
        "new": '    r"after\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"',
        "test": "test_scope_after_the_break_three_empty_issues",
        "fixture": "Three Kansas City touchdowns after the break.",
    },
    {
        "id": "count_scope_after_a_nn",
        "old": '    r"after\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"\n    r"since\\s+halftime|"',
        "new": '    r"since\\s+halftime|"',
        "test": "test_three_after_halftime_passes",
        "fixture": "Three Kansas City touchdowns after a 14-12 halftime.",
    },
    {
        "id": "count_scope_since_halftime",
        "old": '    r"since\\s+halftime|"\n    r"since\\s+the\\s+half|"',
        "new": '    r"since\\s+the\\s+half|"',
        "test": "test_scope_since_halftime_three_empty_issues",
        "fixture": "Three Kansas City touchdowns since halftime.",
    },
    {
        "id": "count_scope_since_the_half",
        "old": '    r"since\\s+the\\s+half|"\n    r"since\\s+the\\s+break|"',
        "new": '    r"since\\s+the\\s+break|"',
        "test": "test_scope_since_the_half_three_empty_issues",
        "fixture": "Three Kansas City touchdowns since the half.",
    },
    {
        "id": "count_scope_since_the_break",
        "old": '    r"since\\s+the\\s+break|"\n    r"following\\s+halftime|"',
        "new": '    r"following\\s+halftime|"',
        "test": "test_scope_since_the_break_three_empty_issues",
        "fixture": "Three Kansas City touchdowns since the break.",
    },
    {
        "id": "count_scope_following_halftime",
        "old": '    r"following\\s+halftime|"\n    r"following\\s+the\\s+half|"',
        "new": '    r"following\\s+the\\s+half|"',
        "test": "test_scope_following_halftime_three_empty_issues",
        "fixture": "Three Kansas City touchdowns following halftime.",
    },
    {
        "id": "count_scope_following_the_half",
        "old": '    r"following\\s+the\\s+half|"\n    r"following\\s+the\\s+break|"',
        "new": '    r"following\\s+the\\s+break|"',
        "test": "test_scope_following_the_half_three_empty_issues",
        "fixture": "Three Kansas City touchdowns following the half.",
    },
    {
        "id": "count_scope_following_the_break",
        "old": '    r"following\\s+the\\s+break|"\n    r"following\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"',
        "new": '    r"following\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"',
        "test": "test_scope_following_the_break_three_empty_issues",
        "fixture": "Three Kansas City touchdowns following the break.",
    },
    {
        "id": "count_scope_following_a_nn",
        "old": '    r"following\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"\n    r"turned\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"',
        "new": '    r"turned\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"',
        "test": "test_scope_following_a_nn_three_empty_issues",
        "fixture": "Three Kansas City touchdowns following a 14-12 halftime.",
    },
    {
        "id": "count_scope_turned",
        "old": '    r"turned\\s+a\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}\\s+halftime|"\n    r"flipped\\s+a\\s+game\\s+that\\s+was\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}|"',
        "new": '    r"flipped\\s+a\\s+game\\s+that\\s+was\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}|"',
        "test": "test_scope_turned_three_empty_issues",
        "fixture": "Three Kansas City touchdowns turned a 14-12 halftime into 35-12.",
    },
    {
        "id": "count_scope_flipped",
        "old": '    r"flipped\\s+a\\s+game\\s+that\\s+was\\s+\\d{1,2}\\s*[–-]\\s*\\d{1,2}|"\n    r"(?:in\\s+the\\s+)?(?:first|second|third|(?<!non-)(?<!un-)fourth|opening|final)\\s+quarter|"',
        "new": '    r"(?:in\\s+the\\s+)?(?:first|second|third|(?<!non-)(?<!un-)fourth|opening|final)\\s+quarter|"',
        "test": "test_scope_flipped_three_empty_issues",
        "fixture": "Three Kansas City touchdowns flipped a game that was 14-12.",
    },
    {
        "id": "scope_quarters_after_halftime",
        "old": '    "after halftime": frozenset({3, 4}),\n',
        "new": "",
        "test": "test_scope_quarters_after_halftime",
        "fixture": "_count_scope('... after halftime') → {3,4}",
    },
    {
        "id": "scope_quarters_after_the_half",
        "old": '    "after the half": frozenset({3, 4}),\n',
        "new": "",
        "test": "test_scope_quarters_after_the_half",
        "fixture": "_count_scope('... after the half') → {3,4}",
    },
    {
        "id": "scope_quarters_after_the_break",
        "old": '    "after the break": frozenset({3, 4}),\n',
        "new": "",
        "test": "test_scope_quarters_after_the_break",
        "fixture": "_count_scope('... after the break') → {3,4}",
    },
    {
        "id": "scope_quarters_since_halftime",
        "old": '    "since halftime": frozenset({3, 4}),\n',
        "new": "",
        "test": "test_scope_quarters_since_halftime",
        "fixture": "_count_scope('... since halftime') → {3,4}",
    },
    {
        "id": "scope_quarters_since_the_half",
        "old": '    "since the half": frozenset({3, 4}),\n',
        "new": "",
        "test": "test_scope_quarters_since_the_half",
        "fixture": "_count_scope('... since the half') → {3,4}",
    },
    {
        "id": "scope_quarters_since_the_break",
        "old": '    "since the break": frozenset({3, 4}),\n',
        "new": "",
        "test": "test_scope_quarters_since_the_break",
        "fixture": "_count_scope('... since the break') → {3,4}",
    },
    {
        "id": "normalize_after_a_nn",
        "old": """    token = re.sub(
        r"after a \\d{1,2}\\s*-\\s*\\d{1,2} halftime",
        "after halftime",
        token,
    )""",
        "new": "    token = token",
        "test": "test_normalize_scope_after_a_nn_rewrite",
        "fixture": "after a 14-12 halftime → after halftime",
    },
    {
        "id": "normalize_following",
        "old": '    token = re.sub(r"^following ", "after ", token)\n',
        "new": "",
        "test": "test_normalize_scope_following_rewrite",
        "fixture": "following halftime → after halftime",
    },
    {
        "id": "normalize_turned",
        "old": """    token = re.sub(
        r"turned a \\d{1,2}\\s*-\\s*\\d{1,2} halftime",
        "after halftime",
        token,
    )""",
        "new": "    token = token",
        "test": "test_normalize_scope_turned_rewrite",
        "fixture": "turned a 14-12 halftime → after halftime",
    },
    {
        "id": "normalize_flipped",
        "old": """    token = re.sub(
        r"flipped a game that was \\d{1,2}\\s*-\\s*\\d{1,2}",
        "after halftime",
        token,
    )""",
        "new": "    token = token",
        "test": "test_normalize_scope_flipped_rewrite",
        "fixture": "flipped a game that was 14-12 → after halftime",
    },
    {
        "id": "window_quarter_3",
        "old": "_WINDOW_QUARTERS = frozenset({_WINDOW_QUARTER_3, _WINDOW_QUARTER_4})",
        "new": "_WINDOW_QUARTERS = frozenset({_WINDOW_QUARTER_4})",
        "test": "test_window_quarter_3",
        "fixture": "One Kansas City touchdown after halftime.",
    },
    {
        "id": "window_quarter_4",
        "old": "_WINDOW_QUARTERS = frozenset({_WINDOW_QUARTER_3, _WINDOW_QUARTER_4})",
        "new": "_WINDOW_QUARTERS = frozenset({_WINDOW_QUARTER_3})",
        "test": "test_window_quarter_4",
        "fixture": "Two Kansas City touchdowns after halftime.",
    },
    {
        "id": "verifiable_recap_none",
        "old": "    if recap is None:\n        return False\n    if not isinstance(recap, dict):\n",
        "new": "    if not isinstance(recap, dict):\n",
        "test": "test_verifiable_recap_none",
        "fixture": "recap=None Five-after-halftime",
    },
    {
        "id": "verifiable_not_dict",
        "old": "    if not isinstance(recap, dict):\n        return False\n",
        "new": "",
        "test": "test_verifiable_recap_not_dict",
        "fixture": "recap=list Five-after-halftime",
    },
    {
        "id": "verifiable_missing_key",
        "old": '    if "scoringPlays" not in recap:\n        return False\n',
        "new": "",
        "test": "test_verifiable_missing_key",
        "fixture": "recap without scoringPlays",
    },
    {
        "id": "verifiable_plays_not_list",
        "old": "    if not isinstance(plays, list):\n        return False\n",
        "new": "",
        "test": "test_verifiable_plays_not_list",
        "fixture": "scoringPlays=dict",
    },
    {
        "id": "verifiable_play_not_dict",
        "old": "        if not isinstance(play, dict):\n            return False\n",
        "new": "",
        "test": "test_verifiable_play_not_dict",
        "fixture": "scoringPlays=['x']",
    },
    {
        "id": "verifiable_missing_quarter",
        "old": "        if _play_quarter(play) is None:\n            return False\n",
        "new": "",
        "test": "test_verifiable_missing_quarter",
        "fixture": "play without quarter",
    },
    {
        "id": "check_review_wiring",
        "old": """            issues.extend(
                _check_window_scoring_counts(text, last_game, recap, schedule)
            )
""",
        "new": "            pass\n",
        "test": "test_check_review_wires_window_guard",
        "fixture": "check_review(Five after a 14-12 halftime)",
    },
]

COARSE = [
    {
        "id": "noop-guard",
        "old": (
            "    Fail closed: a window-bound count with no verifiable scoringPlays /\n"
            "    quarters, bound only to the unmapped phrase 'after a score', or a\n"
            "    window-bound count noun ESPN cannot split by half (yards/sacks/points/\n"
            "    takeaways), flags.\n"
            '    """\n'
            "    if not text:\n"
            "        return []\n"
        ),
        "new": (
            "    Fail closed: a window-bound count with no verifiable scoringPlays /\n"
            "    quarters, bound only to the unmapped phrase 'after a score', or a\n"
            "    window-bound count noun ESPN cannot split by half (yards/sacks/points/\n"
            "    takeaways), flags.\n"
            '    """\n'
            "    return []\n"
            "    if not text:\n"
            "        return []\n"
        ),
    },
    {
        "id": "fail-open-unverifiable",
        "old": (
            "            if not _scoring_plays_verifiable(recap):\n"
            "                issues.append(\n"
            '                    f"window-bound {who} {label} count {claimed} cannot be "\n'
            '                    f"verified against ESPN scoring plays ({snippet!r})"\n'
            "                )\n"
            "                continue\n"
        ),
        "new": (
            "            if not _scoring_plays_verifiable(recap):\n"
            "                continue\n"
        ),
    },
    {
        "id": "wrong-half-quarters",
        "old": "_WINDOW_QUARTERS = frozenset({_WINDOW_QUARTER_3, _WINDOW_QUARTER_4})",
        "new": "_WINDOW_QUARTERS = frozenset({1, 2})",
    },
    {
        "id": "window-regex-never",
        "old": "            hits = _hits_for_count(folded, match)\n",
        "new": "            hits = []\n",
    },
]


def _run_unittest(selector: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PYTHON, "-m", "unittest", selector, "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = (proc.stderr or "") + (proc.stdout or "")
    return proc.returncode, out


def _parse_fail(out: str) -> str:
    m = re.search(
        r"AssertionError: (.*?)(?:\n\n|\n-|\n : |\n$)",
        out,
        re.S,
    )
    if m:
        return " ".join(m.group(1).split())[:240]
    m = re.search(r"(AssertionError|Error|FAIL):.*", out)
    if m:
        return m.group(0)[:240]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return " | ".join(lines[-8:])[:240]


def _apply(old: str, new: str) -> None:
    src = FACTS.read_text(encoding="utf-8")
    if old not in src:
        raise SystemExit(f"ANCHOR MISSING:\n{old[:180]!r}")
    if src.count(old) != 1:
        raise SystemExit(f"ANCHOR NOT UNIQUE ({src.count(old)}):\n{old[:180]!r}")
    FACTS.write_text(src.replace(old, new, 1), encoding="utf-8")


def _restore(original: str) -> None:
    FACTS.write_text(original, encoding="utf-8")


def _repeat(selector: str, n: int) -> tuple[int, int, str]:
    """Return (passes, fails, last_fail_detail)."""
    passes = 0
    fails = 0
    detail = ""
    for _ in range(n):
        rc, out = _run_unittest(selector)
        if rc == 0:
            passes += 1
        else:
            fails += 1
            detail = _parse_fail(out)
    return passes, fails, detail


def main() -> int:
    original = FACTS.read_text(encoding="utf-8")
    for member in MEMBERS + COARSE:
        if member["old"] not in original:
            print("ANCHOR MISSING", member["id"], file=sys.stderr)
            print(member["old"][:200], file=sys.stderr)
            return 2
        n = original.count(member["old"])
        if n != 1:
            print("ANCHOR NOT UNIQUE", member["id"], n, file=sys.stderr)
            return 2

    rows = []
    try:
        for member in MEMBERS:
            selector = f"{TEST_MOD}.{member['test']}"
            ctrl_ok, ctrl_fail, _ = _repeat(selector, N)
            _apply(member["old"], member["new"])
            try:
                mut_ok, mut_fail, detail = _repeat(selector, N)
            finally:
                _restore(original)
            kills = mut_fail
            vacuous = kills == 0
            row = {
                "id": member["id"],
                "fixture": member["fixture"],
                "mutant": f"drop {member['id']}",
                "test": member["test"],
                "control": f"{ctrl_ok}/{N} pass",
                "kills": f"{kills}/{N}",
                "actual": "FAIL" if mut_fail else "PASS",
                "expected": "FAIL",
                "detail": detail if mut_fail else "control-like pass",
                "vacuous": vacuous,
            }
            rows.append(row)
            flag = "VACUOUS" if vacuous else "KILLED"
            print(f"{flag:8} {member['id']:32} {member['test']} {kills}/{N}")

        coarse_rows = []
        skip_re = re.compile(r"(meta|source.?text|grep_source)", re.I)
        full = "tools.tests.test_window_scoring_counts"
        ctrl_ok, ctrl_fail, _ = _repeat(full, N)
        print(f"I12 control {ctrl_ok}/{N} pass (full module, skip={skip_re.pattern})")
        for mutant in COARSE:
            _apply(mutant["old"], mutant["new"])
            try:
                mut_ok, mut_fail, detail = _repeat(full, N)
                rc, out = _run_unittest(full)
            finally:
                _restore(original)
            failed_names = re.findall(r"^(FAIL|ERROR): (\S+)", out, re.M)
            names = [name for _, name in failed_names if not skip_re.search(name)]
            coarse_rows.append(
                {
                    "id": mutant["id"],
                    "kills": f"{mut_fail}/{N}",
                    "actual": "FAIL" if mut_fail else "PASS",
                    "expected": "FAIL",
                    "tests": names[:12],
                    "detail": detail,
                    "control": f"{ctrl_ok}/{N} pass",
                }
            )
            print(f"I12 {mutant['id']:24} {mut_fail}/{N} tests={names[:6]}")
    finally:
        _restore(original)

    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/window-guard-receipt")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "class_coverage.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "i12_coarse.json").write_text(
        json.dumps(coarse_rows, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Class-coverage receipt",
        "",
        "Each row drops one inventory member, then runs the isolating",
        "repository test 6x against a 6x unmutated control.",
        "",
        "| fixture | mutant | repository test | n/6 kills | actual vs expected | vacuous? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['fixture']} | {row['mutant']} | `{row['test']}` | "
            f"{row['kills']} | {row['actual']} vs {row['expected']} | "
            f"{'yes' if row['vacuous'] else 'no'} |"
        )
    lines += [
        "",
        "# I12 coarse mutants vs repository tests",
        "",
        "Meta-tests / source-text greps disabled (none in this module).",
        "",
        "| mutant | n/6 kills | actual vs expected | failing tests | detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in coarse_rows:
        tests = ", ".join(f"`{name}`" for name in row["tests"][:6]) or "(none)"
        lines.append(
            f"| {row['id']} | {row['kills']} | {row['actual']} vs {row['expected']} | "
            f"{tests} | {row['detail']} |"
        )
    (out_dir / "receipt.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_dir / "receipt.md")
    vacuous = [row["id"] for row in rows if row["vacuous"]]
    if vacuous:
        print("VACUOUS MEMBERS", vacuous)
    survived_coarse = [row["id"] for row in coarse_rows if row["actual"] != "FAIL"]
    if survived_coarse:
        print("I12 SURVIVORS", survived_coarse)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
