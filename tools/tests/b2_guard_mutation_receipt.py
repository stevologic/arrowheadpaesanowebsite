#!/usr/bin/env python3
"""Drop-one mutants of B2 / N3 / hold guards vs isolating repository tests.

Not named test_*.py so unittest discover does not run mutations.
Control and mutant each run 6x. Captures AssertionError actual vs expected
from the mutant run, plus file:line of the dropped production fragment.

Rerun:
  python3 tools/tests/b2_guard_mutation_receipt.py tools/tests/b2_class_coverage
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
N = 6
OUT_DEFAULT = ROOT / "tools" / "tests" / "b2_class_coverage"

WIN = "tools.tests.test_window_scoring_counts.WindowScoringCounts"
B2 = "tools.tests.test_b2_check_edition"
NARR = "tools.tests.test_narrative.FactCheck"
PRESS = "tools.tests.test_press_conferences.PressConferenceVerification"

MEMBERS: list[dict] = [
    # --- I1 score-window regexes ---
    {
        "item": "I1",
        "id": "win_after_score_reached",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '_WIN_AFTER_SCORE_REACHED = re.compile(\n'
            '    r"(?<!non-)(?<!un-)\\bafter\\s+the\\s+score\\s+reached\\s+"\n'
            '    r"(\\d{1,2})\\s*-\\s*(\\d{1,2})\\b",\n'
            "    re.I,\n"
            ")"
        ),
        "new": '_WIN_AFTER_SCORE_REACHED = re.compile(r"(?!x)x", re.I)',
        "test": f"{WIN}.test_win_after_score_reached",
        "fixture": "five TDs after the score reached 14-12",
    },
    {
        "item": "I1",
        "id": "win_after_score_reached_endash",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '_WIN_AFTER_SCORE_REACHED_ENDASH = re.compile(\n'
            '    r"(?<!non-)(?<!un-)\\bafter\\s+the\\s+score\\s+reached\\s+"\n'
            '    r"(\\d{1,2})\\s*–\\s*(\\d{1,2})\\b",\n'
            "    re.I,\n"
            ")"
        ),
        "new": '_WIN_AFTER_SCORE_REACHED_ENDASH = re.compile(r"(?!x)x", re.I)',
        "test": f"{WIN}.test_win_after_score_reached_endash",
        "fixture": "five TDs after the score reached 14–12",
    },
    {
        "item": "I1",
        "id": "win_after_trailing",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '_WIN_AFTER_TRAILING = re.compile(\n'
            '    r"(?<!non-)(?<!un-)\\bafter\\s+trailing\\s+"\n'
            '    r"(\\d{1,2})\\s*-\\s*(\\d{1,2})\\b",\n'
            "    re.I,\n"
            ")"
        ),
        "new": '_WIN_AFTER_TRAILING = re.compile(r"(?!x)x", re.I)',
        "test": f"{WIN}.test_win_after_trailing",
        "fixture": "four TDs after trailing 6-0",
    },
    {
        "item": "I1",
        "id": "win_after_trailing_endash",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '_WIN_AFTER_TRAILING_ENDASH = re.compile(\n'
            '    r"(?<!non-)(?<!un-)\\bafter\\s+trailing\\s+"\n'
            '    r"(\\d{1,2})\\s*–\\s*(\\d{1,2})\\b",\n'
            "    re.I,\n"
            ")"
        ),
        "new": '_WIN_AFTER_TRAILING_ENDASH = re.compile(r"(?!x)x", re.I)',
        "test": f"{WIN}.test_win_after_trailing_endash",
        "fixture": "four TDs after trailing 6–0",
    },
    {
        "item": "I1",
        "id": "win_after_a_start",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '_WIN_AFTER_A_START = re.compile(\n'
            '    r"(?<!non-)(?<!un-)\\bafter\\s+a\\s+"\n'
            '    r"(\\d{1,2})\\s*-\\s*(\\d{1,2})\\s+start\\b",\n'
            "    re.I,\n"
            ")"
        ),
        "new": '_WIN_AFTER_A_START = re.compile(r"(?!x)x", re.I)',
        "test": f"{WIN}.test_win_after_a_start",
        "fixture": "four TDs after a 0-6 start",
    },
    {
        "item": "I1",
        "id": "win_after_a_start_endash",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '_WIN_AFTER_A_START_ENDASH = re.compile(\n'
            '    r"(?<!non-)(?<!un-)\\bafter\\s+a\\s+"\n'
            '    r"(\\d{1,2})\\s*–\\s*(\\d{1,2})\\s+start\\b",\n'
            "    re.I,\n"
            ")"
        ),
        "new": '_WIN_AFTER_A_START_ENDASH = re.compile(r"(?!x)x", re.I)',
        "test": f"{WIN}.test_win_after_a_start_endash",
        "fixture": "four TDs after a 0–6 start",
    },
    {
        "item": "I1",
        "id": "window_specs_trailing_not_reached",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '    ("after_trailing", _WIN_AFTER_TRAILING, _SCORE_WINDOW_TRAILING),\n'
        ),
        "new": (
            '    ("after_trailing", _WIN_AFTER_TRAILING, _SCORE_WINDOW_REACHED),\n'
        ),
        "test": f"{WIN}.test_i2_trailing_skips_kc_ahead",
        "fixture": "after trailing 14-12 (KC ahead) must stay unverifiable",
    },
    # --- I2 conjuncts ---
    {
        "item": "I2",
        "id": "trailing_skip_kc_ahead",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "        if trailing and pair[0] >= pair[1]:\n            continue\n",
        "new": "",
        "test": f"{WIN}.test_i2_trailing_skips_kc_ahead",
        "fixture": "trailing and pair[0] >= pair[1] skip",
    },
    {
        "item": "I2",
        "id": "swapped_pair",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "        if pair == (left, right) or pair == (right, left):\n",
        "new": "        if pair == (left, right):\n",
        "test": f"{WIN}.test_i2_swapped_pair_12_14",
        "fixture": "12-14 matches ESPN 14-12",
    },
    {
        "item": "I2",
        "id": "off_by_one_idx_plus_one",
        "file": "tools/chiefs_narrative/facts.py",
        "old": '    sliced["scoringPlays"] = plays[idx + 1 :]\n',
        "new": '    sliced["scoringPlays"] = plays[idx :]\n',
        "test": f"{WIN}.test_i2_off_by_one_includes_state_play",
        "fixture": "plays[idx+1:] excludes the 21-12 creating KC TD",
    },
    {
        "item": "I2",
        "id": "mode_set_trailing_start",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "    trailing = mode in {_SCORE_WINDOW_TRAILING, _SCORE_WINDOW_START}\n"
        ),
        "new": "    trailing = mode in {_SCORE_WINDOW_START}\n",
        "test": f"{WIN}.test_i2_trailing_skips_kc_ahead",
        "fixture": "TRAILING is in the mode set that skips KC-ahead",
    },
    {
        "item": "I2",
        "id": "missing_score_none",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "    if idx is None:\n        return None\n",
        "new": "    if idx is None:\n        return 0\n",
        "test": f"{WIN}.test_i2_missing_score_state_unverifiable",
        "fixture": "99-99 is unverifiable, not official 0",
    },
    # --- I3 COUNT_SCOPE + skip ---
    {
        "item": "I3",
        "id": "count_scope_score_reached",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            r'    r"after\s+the\s+score\s+reached\s+\d{1,2}\s*[–-]\s*\d{1,2}|"'
            "\n"
        ),
        "new": "",
        "test": f"{WIN}.test_scope_score_reached_three_empty_issues",
        "fixture": "three TDs after the score reached 14-12 leftover-empty",
    },
    {
        "item": "I3",
        "id": "count_scope_trailing",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            r'    r"after\s+trailing\s+\d{1,2}\s*[–-]\s*\d{1,2}|"'
            "\n"
        ),
        "new": "",
        "test": f"{WIN}.test_i3_scope_trailing_three_empty_leftover",
        "fixture": "three TDs after trailing 0-6 leftover-empty",
    },
    {
        "item": "I3",
        "id": "count_scope_start",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            r'    r"after\s+a\s+\d{1,2}\s*[–-]\s*\d{1,2}\s+start|"'
            "\n"
        ),
        "new": "",
        "test": f"{WIN}.test_i3_scope_start_three_empty_leftover",
        "fixture": "three TDs after a 0-6 start leftover-empty",
    },
    {
        "item": "I3",
        "id": "count_scope_skip_branch",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '        r"score\\s+reached|after\\s+trailing|after\\s+a\\s+\\d{1,2}\\s*-\\s*\\d{1,2}\\s+start",\n'
            "        raw,\n"
            "        re.I,\n"
            "    ):\n"
            '        return "skip"\n'
        ),
        "new": (
            '        r"score\\s+reached|after\\s+trailing|after\\s+a\\s+\\d{1,2}\\s*-\\s*\\d{1,2}\\s+start",\n'
            "        raw,\n"
            "        re.I,\n"
            "    ):\n"
            "        return None\n"
        ),
        "test": f"{WIN}.test_scope_score_reached_three_empty_issues",
        "fixture": "skip branch (ASCII hyphen) vs leftover whole-game",
    },
    # --- I4 digits / singular ---
    {
        "item": "I4",
        "id": "window_count_digit",
        "file": "tools/chiefs_narrative/facts.py",
        "old": '_WINDOW_COUNT_DIGIT = r"\\d+"\n',
        "new": '_WINDOW_COUNT_DIGIT = r"(?!x)x"\n',
        "test": f"{WIN}.test_i4_digit_five_after_reached",
        "fixture": "5 touchdowns after the score reached 14-12",
    },
    {
        "item": "I4",
        "id": "window_count_singular_td",
        "file": "tools/chiefs_narrative/facts.py",
        "old": '    "touchdowns",\n    "touchdown",\n',
        "new": '    "touchdowns",\n',
        "test": f"{WIN}.test_i4_singular_touchdown_after_reached",
        "fixture": "4 touchdown (singular) after trailing 0-6",
    },
    # --- I5 player-TD family ---
    {
        "item": "I5",
        "id": "player_scored_td",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_PLAYER_SCORED_TD = re.compile(\n",
        "new": "_PLAYER_SCORED_TD = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.PlayerTdTotals.test_player_scored_a_touchdown_flags",
        "fixture": "Walker scored a touchdown all day",
    },
    {
        "item": "I5",
        "id": "typed_player_td",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_TYPED_PLAYER_TD = re.compile(\n",
        "new": "_TYPED_PLAYER_TD = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.PlayerTdTotals.test_typed_rushing_td_flags",
        "fixture": "Walker had two rushing touchdowns",
    },
    {
        "item": "I5",
        "id": "bare_player_td",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_BARE_PLAYER_TD = re.compile(\n",
        "new": "_BARE_PLAYER_TD = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.PlayerTdTotals.test_bare_player_td_all_day_flags",
        "fixture": "Walker scored one touchdown all day",
    },
    {
        "item": "I5",
        "id": "headline_td_perf",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_HEADLINE_TD_PERF = re.compile(\n",
        "new": "_HEADLINE_TD_PERF = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.PlayerTdTotals.test_headline_td_perf_two_flags",
        "fixture": "Walker's two-touchdown performance",
    },
    {
        "item": "I5",
        "id": "headline_td_bare",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_HEADLINE_TD_BARE = re.compile(\n",
        "new": "_HEADLINE_TD_BARE = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.PlayerTdTotals.test_headline_td_bare_four_flags",
        "fixture": "Four-touchdown performance from Walker",
    },
    {
        "item": "I5",
        "id": "whole_game_td_scope",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            r'    r"\b(?:all\s+day|on\s+the\s+day|in\s+(?:the|this)\s+game|all\s+game)\b",'
            "\n"
        ),
        "new": (
            r'    r"\b(?:all\s+day|on\s+the\s+day|in\s+(?:the|this)\s+game)\b",'
            "\n"
        ),
        "test": f"{B2}.PlayerTdTotals.test_whole_game_td_scope_all_game",
        "fixture": "Walker had one touchdown all game",
    },
    {
        "item": "I5",
        "id": "parse_td_count_a_an",
        "file": "tools/chiefs_narrative/facts.py",
        "old": '    if token in {"a", "an"}:\n        return 1\n',
        "new": '    if token in {"a", "an"}:\n        return None\n',
        "test": f"{B2}.PlayerTdTotals.test_player_scored_a_touchdown_flags",
        "fixture": "_parse_td_count_word('a') == 1",
    },
    {
        "item": "I5",
        "id": "scoring_td_line",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_SCORING_TD_LINE = re.compile(\n",
        "new": "_SCORING_TD_LINE = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.PlayerTdTotals.test_scoring_td_line_pass_is_rec",
        "fixture": "— KC Hunt 12 Yd pass",
    },
    {
        "item": "I5",
        "id": "pbp_pass_td",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_PBP_PASS_TD = re.compile(\n",
        "new": "_PBP_PASS_TD = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.PlayerTdTotals.test_pbp_pass_td_is_rec",
        "fixture": "pass to H.Hunt ... TOUCHDOWN",
    },
    {
        "item": "I5",
        "id": "pbp_rush_td",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_PBP_RUSH_TD = re.compile(\n",
        "new": "_PBP_RUSH_TD = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.PlayerTdTotals.test_pbp_rush_td_is_rush",
        "fixture": "K.Hunt 1 Yd run TOUCHDOWN",
    },
    {
        "item": "I5",
        "id": "add_player_td_rush_vs_rec",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '    if kind == "rec":\n'
            '        slot["rec"] += 1\n'
            '    elif kind == "rush":\n'
            '        slot["rush"] += 1\n'
        ),
        "new": (
            '    if kind == "rec":\n'
            '        slot["rush"] += 1\n'
            '    elif kind == "rush":\n'
            '        slot["rec"] += 1\n'
        ),
        "test": f"{B2}.PlayerTdTotals.test_add_player_td_rush_vs_rec",
        "fixture": "_add_player_td rush vs rec slots",
    },
    {
        "item": "I5",
        "id": "player_td_sum_rush_plus_rec",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '    return int(row.get("rush") or 0) + int(row.get("rec") or 0)\n'
        ),
        "new": '    return int(row.get("rush") or 0)\n',
        "test": f"{B2}.PlayerTdTotals.test_m_total_flags_one_vs_espn_three",
        "fixture": "Walker scored one touchdown all day vs ESPN 3",
    },
    # --- I6 red zone ---
    {
        "item": "I6",
        "id": "red_zone_eff_regex",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_RED_ZONE_EFF = re.compile(\n",
        "new": "_RED_ZONE_EFF = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.RedZoneThirdDown.test_red_zone_eff_5_of_4_flags",
        "fixture": "Kansas City was 5-of-4 in the red zone",
    },
    {
        "item": "I6",
        "id": "red_zone_trips_regex",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_RED_ZONE_TRIPS = re.compile(\n",
        "new": "_RED_ZONE_TRIPS = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.RedZoneThirdDown.test_red_zone_trips_five_flags",
        "fixture": "Five red-zone trips",
    },
    {
        "item": "I6",
        "id": "red_zone_trips_fn",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "def _red_zone_trips(pair: tuple[int, int]) -> int:\n    return int(pair[1])\n",
        "new": "def _red_zone_trips(pair: tuple[int, int]) -> int:\n    return 5\n",
        "test": f"{B2}.RedZoneThirdDown.test_red_zone_trips_five_flags",
        "fixture": "_red_zone_trips returns att, not 5",
    },
    {
        "item": "I6",
        "id": "red_zone_tds_not_trips",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '    """TD (made) side of ESPN redZoneEff. Mutant: return trips instead."""\n'
            "    return int(pair[0])\n"
        ),
        "new": (
            '    """TD (made) side of ESPN redZoneEff. Mutant: return trips instead."""\n'
            "    return int(pair[1])\n"
        ),
        "test": f"{B2}.RedZoneThirdDown.test_red_zone_tds_not_trips",
        "fixture": "four TDs vs 4-6 efficiency uses made not trips",
    },
    {
        "item": "I6",
        "id": "red_zone_kind_td",
        "file": "tools/chiefs_narrative/facts.py",
        "old": '            if kind not in {"td", "score"}:\n                continue\n',
        "new": '            if kind not in {"score"}:\n                continue\n',
        "test": f"{B2}.RedZoneThirdDown.test_five_red_zone_tds_flags",
        "fixture": "kind set includes td",
    },
    {
        "item": "I6",
        "id": "red_zone_kind_score",
        "file": "tools/chiefs_narrative/facts.py",
        "old": '            if kind not in {"td", "score"}:\n                continue\n',
        "new": '            if kind not in {"td"}:\n                continue\n',
        "test": f"{B2}.RedZoneThirdDown.test_red_zone_score_kind_flags",
        "fixture": "kind set includes score",
    },
    {
        "item": "I6",
        "id": "red_zone_opponent_bind",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '            if captured and captured != "KC":\n'
            "                continue\n"
        ),
        "new": "",
        "test": f"{B2}.RedZoneThirdDown.test_opponent_red_zone_is_not_kc",
        "fixture": "Five Jets touchdowns in the red zone",
    },
    {
        "item": "I6",
        "id": "red_zone_space_separator",
        "file": "tools/chiefs_narrative/facts.py",
        "old": r'_RED_ZONE = re.compile(r"\bred[- ]zone\b", re.I)',
        "new": r'_RED_ZONE = re.compile(r"\bred-zone\b", re.I)',
        "test": f"{B2}.RedZoneThirdDown.test_red_zone_space_separator_flags",
        "fixture": "red zone (space) finish that produced five touchdowns",
    },
    # --- I7 third down ---
    {
        "item": "I7",
        "id": "third_down_n_of_m_words",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "_THIRD_DOWN_BOUND = re.compile(\n",
        "new": "_THIRD_DOWN_BOUND = re.compile(r'(?!x)x', re.I) or re.compile(\n",
        "test": f"{B2}.RedZoneThirdDown.test_third_down_n_of_m_words",
        "fixture": "7 of 11 third downs",
    },
    {
        "item": "I7",
        "id": "third_down_n_hyphen_m",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            r'(?P<pre>\d+)\s*(?:of|for|-|–)\s*(?P<pre_att>\d+)[ \t]+(?:on[ \t]+)?'
        ),
        "new": (
            r'(?P<pre>\d+)\s*(?:of|for|–)\s*(?P<pre_att>\d+)[ \t]+(?:on[ \t]+)?'
        ),
        "test": f"{B2}.RedZoneThirdDown.test_third_down_n_hyphen_m",
        "fixture": "7-11 on third down",
    },
    {
        "item": "I7",
        "id": "third_down_singular",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '    r"third[- ]downs?"\n'
            '    r"|"\n'
            '    r"third[- ]downs?[ \\t]+'
        ),
        "new": (
            '    r"third[- ]downs"\n'
            '    r"|"\n'
            '    r"third[- ]downs[ \\t]+'
        ),
        "test": f"{B2}.RedZoneThirdDown.test_third_down_singular",
        "fixture": "7-of-11 on third down (singular)",
    },
    {
        "item": "I7",
        "id": "third_down_same_line",
        "file": "tools/chiefs_narrative/facts.py",
        "old": r"(?P<pre_att>\d+)[ \t]+(?:on[ \t]+)?",
        "new": r"(?P<pre_att>\d+)\s+(?:on\s+)?",
        "test": f"{B2}.RedZoneThirdDown.test_third_down_not_across_newline",
        "fixture": "7 of 11\\nthird downs is not same-line",
    },
    {
        "item": "I7",
        "id": "third_down_rightmost_team",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "def _rightmost_team(span: str, aliases: dict[str, str]) -> str:\n",
        "new": (
            "def _rightmost_team(span: str, aliases: dict[str, str]) -> str:\n"
            '    return "KC"\n'
        ),
        "test": f"{B2}.RedZoneThirdDown.test_rightmost_team_seattle_third_down",
        "fixture": "Seattle's 4 of 11 vs ESPN 4-12",
    },
    {
        "item": "I7",
        "id": "third_down_to_pair",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            r"(?:[ \t]+to[ \t]+(?P<to>\d+)\s*(?:of|for|-|–)\s*(?P<to_att>\d+))?"
        ),
        "new": "",
        "test": f"{B2}.RedZoneThirdDown.test_third_down_multiple_pairs",
        "fixture": "6 of 11 to 3 of 12",
    },
    # --- I8 kickoff / TV ---
    {
        "item": "I8",
        "id": "tv_matches_always_true",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "    if left_c not in right_c and right_c not in left_c:\n"
            "        return False\n"
        ),
        "new": (
            "    if left_c not in right_c and right_c not in left_c:\n"
            "        return True\n"
        ),
        "test": f"{B2}.NextKickoffTv.test_m_fox_flags_tv",
        "fixture": "nextGame.tv FOX vs ESPN CBS",
    },
    {
        "item": "I8",
        "id": "tv_espn_not_espn2",
        "file": "tools/chiefs_narrative/facts.py",
        "old": "    if after.isdigit():\n        return False\n    return True\n",
        "new": "    if after.isdigit():\n        return True\n    return True\n",
        "test": f"{B2}.NextKickoffTv.test_tv_espn_is_not_espn2_substring",
        "fixture": "ESPN is not ESPN2",
    },
    {
        "item": "I8",
        "id": "kickoff_always_true",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "    if left is None or right is None:\n"
            "        return True\n"
            "    return left == right\n"
        ),
        "new": (
            "    if left is None or right is None:\n"
            "        return True\n"
            "    return True\n"
        ),
        "test": f"{B2}.NextKickoffTv.test_m_fox_flags_kickoff",
        "fixture": "3:25 PM CT vs ESPN 12:00 PM CT",
    },
    {
        "item": "I8",
        "id": "kickoff_am_midnight",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            '    if ampm.startswith("a") and hour == 12:\n'
            "        hour = 0\n"
        ),
        "new": "",
        "test": f"{B2}.NextKickoffTv.test_kickoff_am_pm_variants",
        "fixture": "12:00 AM CT is not 12:00 PM CT",
    },
    {
        "item": "I8",
        "id": "tv_alias_peacock",
        "file": "tools/chiefs_narrative/facts.py",
        "old": '    "peacock": "nbc",\n',
        "new": "",
        "test": f"{B2}.NextKickoffTv.test_tv_aliases_nbc_peacock_and_prime_video",
        "fixture": "Peacock aliases to NBC",
    },
    {
        "item": "I8",
        "id": "tv_alias_amazon",
        "file": "tools/chiefs_narrative/facts.py",
        "old": '    "amazon": "prime",\n',
        "new": "",
        "test": f"{B2}.NextKickoffTv.test_tv_aliases_nbc_peacock_and_prime_video",
        "fixture": "Amazon aliases to Prime Video",
    },
    {
        "item": "I8",
        "id": "slate_preseason",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "        week_rows = [game for game in matches if game.get(\"week\") == wanted_week]\n"
        ),
        "new": "        week_rows = list(matches)\n",
        "test": f"{B2}.NextKickoffTv.test_preseason_week_does_not_use_regular_rematch",
        "fixture": "preseason Seattle is not the regular rematch",
    },
    {
        "item": "I8",
        "id": "slate_completed_week",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "        week_rows = [game for game in matches if game.get(\"week\") == wanted_week]\n"
        ),
        "new": "        week_rows = list(matches)\n",
        "test": f"{B2}.NextKickoffTv.test_completed_week_keeps_its_own_slate_row",
        "fixture": "completed Week 4 Raiders keeps its slate row",
    },
    # --- I9 hold branches ---
    {
        "item": "I9",
        "id": "inspect_pr_hold",
        "file": "tools/chiefs_narrative/edition_merge.py",
        "old": "    if repair_holds_automerge(root):\n",
        "new": "    if False and repair_holds_automerge(root):\n",
        "test": f"{B2}.HoldAutomergePaths.test_inspect_pr_refuses_hold_automerge",
        "fixture": "holdAutomerge true refuses edition_merge --pr",
    },
    {
        "item": "I9",
        "id": "hold_repair_cli",
        "file": "tools/chiefs_narrative/review_gate.py",
        "old": "        if args.hold_repair:\n",
        "new": "        if False and args.hold_repair:\n",
        "test": f"{B2}.HoldAutomergePaths.test_hold_repair_refuses",
        "fixture": "review_gate --hold-repair rc 1 vs 0",
    },
    {
        "item": "I9",
        "id": "automerge_hold_cli",
        "file": "tools/chiefs_narrative/review_gate.py",
        "old": "        if args.automerge:\n",
        "new": "        if args.automerge and False:\n",
        "test": f"{B2}.HoldAutomergePaths.test_automerge_refuses_hold",
        "fixture": "review_gate --automerge refuses hold",
    },
    {
        "item": "I9",
        "id": "load_repair_missing",
        "file": "tools/chiefs_narrative/edition_merge.py",
        "old": "    if not repair_path.is_file():\n        return held\n",
        "new": "    if not repair_path.is_file():\n        return {}\n",
        "test": f"{B2}.HoldAutomergePaths.test_load_repair_missing_is_held",
        "fixture": "missing narrative_repair.json is held",
    },
    {
        "item": "I9",
        "id": "load_repair_invalid",
        "file": "tools/chiefs_narrative/edition_merge.py",
        "old": "    except (OSError, json.JSONDecodeError):\n        return held\n",
        "new": "    except (OSError, json.JSONDecodeError):\n        return {}\n",
        "test": f"{B2}.HoldAutomergePaths.test_load_repair_invalid_json_is_held",
        "fixture": "invalid JSON is held",
    },
    {
        "item": "I9",
        "id": "load_repair_non_object",
        "file": "tools/chiefs_narrative/edition_merge.py",
        "old": "    if not isinstance(loaded, dict):\n        return held\n",
        "new": "    if not isinstance(loaded, dict):\n        return {}\n",
        "test": f"{B2}.HoldAutomergePaths.test_load_repair_non_object_is_held",
        "fixture": "non-object JSON is held",
    },
    {
        "item": "I9",
        "id": "ci_gates_hold_repair",
        "file": ".github/workflows/ci.yml",
        "old": (
            "          python -m tools.chiefs_narrative.review_gate --hold-repair\n"
        ),
        "new": "          true\n",
        "test": f"{B2}.HoldAutomergePaths.test_ci_gates_runs_hold_repair",
        "fixture": "CI gates job runs --hold-repair after --pages",
    },
    {
        "item": "I9",
        "id": "pages_yml_hold_repair",
        "file": ".github/workflows/pages.yml",
        "old": (
            "          python -m tools.chiefs_narrative.review_gate --hold-repair\n"
        ),
        "new": "          true\n",
        "test": f"{NARR}.test_automerge_and_pages_refuse_unsigned_reviews",
        "fixture": "pages.yml Refuse held edition before Publish",
    },
    # --- I10 N3 ---
    {
        "item": "I10",
        "id": "n3_changed_archive",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "def _fact_check_changed_editions() -> list[str]:\n"
            '    """Fact-check changed archive edition JSON files. Mutant: return []."""\n'
        ),
        "new": (
            "def _fact_check_changed_editions() -> list[str]:\n"
            '    """Fact-check changed archive edition JSON files. Mutant: return []."""\n'
            "    return []\n"
        ),
        "test": f"{B2}.N3ChangedEditions.test_n3_archived_false_count_fails_check_edition",
        "fixture": "false count only in a changed archived edition",
    },
    {
        "item": "I10",
        "id": "n3_live_equals_latest",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "def _live_matches_latest_edition() -> list[str]:\n"
            '    """Assert live narrative.json equals the latest edition file. Mutant: skip."""\n'
        ),
        "new": (
            "def _live_matches_latest_edition() -> list[str]:\n"
            '    """Assert live narrative.json equals the latest edition file. Mutant: skip."""\n'
            "    return []\n"
        ),
        "test": f"{B2}.N3ChangedEditions.test_n3_live_differs_from_latest_fails",
        "fixture": "live JSON differs from the latest edition file",
    },
    {
        "item": "I10",
        "id": "n3_unknown_git_fail_closed",
        "file": "tools/chiefs_narrative/generate.py",
        "old": "    if paths is None:\n        return sorted(\n",
        "new": "    if paths is None:\n        return []\n        return sorted(\n",
        "test": f"{B2}.N3ChangedEditions.test_n3_unknown_git_scans_archives",
        "fixture": "unknown git scans every on-disk archive",
    },
    {
        "item": "I10",
        "id": "n3_bare_name",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            '        elif posix.endswith(".json") and "/" not in posix:\n'
            "            wanted.add(posix)\n"
        ),
        "new": "",
        "test": f"{B2}.N3ChangedEditions.test_n3_bare_name_changed_path_is_scanned",
        "fixture": "bare filename.json is still scanned",
    },
    {
        "item": "I10",
        "id": "n3_live_twin_skip",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "        if (\n"
            "            live_payload is not None\n"
            '            and path.name == f"{live_slug}.json"\n'
            "            and payload == live_payload\n"
            "        ):\n"
            "            continue\n"
        ),
        "new": (
            "        if (\n"
            "            live_payload is not None\n"
            '            and path.name == f"{live_slug}.json"\n'
            "            and False\n"
            "        ):\n"
            "            continue\n"
        ),
        "test": f"{B2}.N3ChangedEditions.test_n3_live_twin_skip_when_equal",
        "fixture": "equal live twin is not double-scanned",
    },
    {
        "item": "I10",
        "id": "n3_paths_aligned_raises",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "    try:\n"
            "        config.NARRATIVE_JSON.resolve().relative_to(config.DATA_DIR.resolve())\n"
            "        config.EDITIONS_DIR.resolve().relative_to(config.DATA_DIR.resolve())\n"
            "    except ValueError as exc:\n"
            "        raise EditionPathError(\n"
            '            "NARRATIVE_JSON and EDITIONS_DIR must sit under DATA_DIR"\n'
            "        ) from exc\n"
            "    return True\n"
        ),
        "new": "    return True\n",
        "test": f"{B2}.N3ChangedEditions.test_edition_paths_aligned_raises_off_data_dir",
        "fixture": "_edition_paths_aligned raises off DATA_DIR",
    },
    {
        "item": "I10",
        "id": "n3_latest_z_vs_offset",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "        stamp = facts._parse_generated_at(payload.get(\"generatedAt\")) or fallback\n"
        ),
        "new": (
            "        stamp = str(payload.get(\"generatedAt\") or \"\")\n"
        ),
        "test": f"{B2}.N3ChangedEditions.test_latest_edition_z_vs_offset_sort",
        "fixture": "Z vs +01:00 generatedAt sort",
    },
    {
        "item": "I10",
        "id": "n3_unreadable",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            '            found.append(f"{path.name}: unreadable edition JSON")\n'
        ),
        "new": "            continue\n",
        "test": f"{B2}.N3ChangedEditions.test_n3_unreadable_edition_fails",
        "fixture": "unreadable changed edition fails closed",
    },
    {
        "item": "I10",
        "id": "n3_grandfather_empty_diff",
        "file": "tools/chiefs_narrative/generate.py",
        "old": "    if paths is None:\n        return sorted(\n",
        "new": "    if paths is None or not paths:\n        return sorted(\n",
        "test": f"{B2}.N3ChangedEditions.test_n3_unchanged_archive_lie_is_grandfathered",
        "fixture": "unchanged archive lie stays rc 0 (grandfather)",
    },
    {
        "item": "I10",
        "id": "n3_latest_only_lie",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "def _fact_check_changed_editions() -> list[str]:\n"
            '    """Fact-check changed archive edition JSON files. Mutant: return []."""\n'
        ),
        "new": (
            "def _fact_check_changed_editions() -> list[str]:\n"
            '    """Fact-check changed archive edition JSON files. Mutant: return []."""\n'
            "    return []\n"
        ),
        "test": f"{B2}.N3ChangedEditions.test_n3_latest_edition_false_count_fails",
        "fixture": "false count only in the latest archived edition",
    },
    # --- I11 like-with-like pair ---
    {
        "item": "I11",
        "id": "n1_like_with_like_true_neg",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "        else:\n"
            "            row = totals.get(player) or {}\n"
            "            official = _player_td_sum(row) if row else None\n"
            '            label = "total TDs"\n'
        ),
        "new": (
            "        else:\n"
            "            official = rush_only.get(player)\n"
            '            label = "total TDs"\n'
        ),
        "test": f"{B2}.SourceTdScope.test_rushing_leader_vs_all_purpose_is_not_a_conflict",
        "fixture": "three-touchdown vs rushing-leader 1 TD is not a conflict",
    },
    {
        "item": "I11",
        "id": "n1_all_purpose_positive",
        "file": "tools/chiefs_narrative/facts.py",
        "old": (
            "        issues.append(\n"
            '            f"{publisher} {player} {label} {claimed} disagrees with ESPN "\n'
            '            f"{official} ({title!r})"\n'
            "        )\n"
        ),
        "new": "        pass\n",
        "test": f"{B2}.SourceTdScope.test_all_purpose_four_is_a_conflict",
        "fixture": "Walker's Four-Touchdown Performance vs ESPN 3",
    },
    # --- N4 presser oEmbed transport vs content ---
    {
        "item": "N4",
        "id": "n4_oembed_retry_attempts",
        "file": "tools/chiefs_narrative/press_conferences.py",
        "old": "OEMBED_ATTEMPTS = 3",
        "new": "OEMBED_ATTEMPTS = 1",
        "test": f"{PRESS}.test_n4_transient_transport_then_success_does_not_hold",
        "fixture": "Timeout then 200: retry recovers, no hold",
    },
    {
        "item": "N4",
        "id": "n4_transport_not_unverified",
        "file": "tools/chiefs_narrative/press_conferences.py",
        "old": (
            '        if result.kind == "transport":\n'
            "            issues.append(\n"
            '                f"{PRESS_TRANSPORT_PREFIX} {result.detail} for {item.get(\'url\')}"\n'
            "            )\n"
            "            continue\n"
        ),
        "new": (
            '        if result.kind == "transport":\n'
            "            issues.append(\n"
            '                f"press conference: unverified embed {item.get(\'url\')}"\n'
            "            )\n"
            "            continue\n"
        ),
        "test": f"{PRESS}.test_n4_persistent_transport_classified_logged_not_held",
        "fixture": "persistent ConnectionError is transport, logged, not held",
    },
    {
        "item": "N4",
        "id": "n4_check_edition_transport_no_hold",
        "file": "tools/chiefs_narrative/generate.py",
        "old": (
            "            if item not in press_issues and item not in transport_issues"
        ),
        "new": "            if item not in press_issues",
        "test": f"{PRESS}.test_n4_check_edition_transport_does_not_hold",
        "fixture": "--check-edition rc 0 on transport-only issues",
    },
]


def _line_of(text: str, old: str) -> int:
    idx = text.find(old)
    if idx < 0:
        return 0
    return text[:idx].count("\n") + 1


def _run(selector: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PYTHON, "-m", "unittest", selector, "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _repeat(selector: str, n: int) -> tuple[int, int, dict[str, str]]:
    ok = fail = 0
    parsed = {"assert_text": "", "actual": "", "expected": ""}
    for _ in range(n):
        rc, out = _run(selector)
        if rc == 0:
            ok += 1
        else:
            fail += 1
            parsed = _parse_assert(out)
    return ok, fail, parsed


def _apply(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _count_in(text: str, old: str, unique_hint: str | None) -> int:
    if unique_hint and unique_hint not in text:
        return 0
    return text.count(old)


def _parse_assert(out: str) -> dict[str, str]:
    fail = re.search(
        r"^(FAIL|ERROR): .*?\n-+\n(.*?)(?:\n(?:={5,}|FAIL: |ERROR: )|\nRan \d+ |\Z)",
        out,
        re.S | re.M,
    )
    block = fail.group(2) if fail else out
    m = re.search(r"AssertionError: (.*)", block, re.S)
    blob = " ".join((m.group(1) if m else block).split())
    blob = re.sub(r" -+ Ran \d+ test.*", "", blob)
    blob = re.sub(r" -{5,}.*$", "", blob).strip()[:400]
    actual = ""
    expected = ""
    lists = re.search(r"Lists differ: (.*) != (.*?)(?: First list|$)", blob)
    if lists:
        actual, expected = lists.group(1).strip(), lists.group(2).strip()
    else:
        neq = re.search(r"^(.*?) != (.*?)(?: : |$)", blob)
        if neq and "is not" not in neq.group(1):
            actual, expected = neq.group(1).strip()[:200], neq.group(2).strip()[:200]
        elif "is not true" in blob:
            expected = "True (non-empty issues)"
            actual = blob.split(" : ", 1)[-1][:200] if " : " in blob else "False"
        elif "is not false" in blob:
            actual, expected = "True", "False"
        elif "not found in" in blob:
            actual, expected = blob[:200], "needle present"
        elif "unexpectedly found in" in blob:
            actual, expected = blob[:200], "needle absent"
        elif "not raised" in blob:
            actual, expected = blob[:200], "exception raised"
    if not actual:
        actual = blob[:200] or "(no AssertionError body)"
    if not expected:
        expected = "assertion-pass"
    return {"assert_text": blob, "actual": actual, "expected": expected}


def _run_adversarial() -> list[dict]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.tests.test_window_scoring_counts import (
        ADVERSARIAL_ROWS,
        WindowScoringCounts,
    )

    case = WindowScoringCounts("test_adversarial_table_head")
    case.setUp()
    rows = []
    for row in ADVERSARIAL_ROWS:
        got = case._classify(row["sentence"])
        rows.append(
            {
                "id": row["id"],
                "sentence": row["sentence"],
                "expected": row["head"],
                "got": got,
                "develop": row["develop"],
                "note": row["note"],
                "pass": got == row["head"],
            }
        )
    return rows


def main() -> int:
    originals: dict[Path, str] = {}
    for member in MEMBERS:
        path = ROOT / member["file"]
        originals.setdefault(path, path.read_text(encoding="utf-8"))
        text = originals[path]
        n = _count_in(text, member["old"], member.get("unique"))
        if n != 1:
            print(
                f"ANCHOR {member['id']} count={n} in {path}",
                file=sys.stderr,
            )
            print(member["old"][:240], file=sys.stderr)
            return 2

    rows = []
    try:
        for member in MEMBERS:
            path = ROOT / member["file"]
            text = originals[path]
            line = _line_of(text, member["old"])
            selector = member["test"]
            ctrl_ok, ctrl_fail, _ = _repeat(selector, N)
            if ctrl_fail:
                print("CONTROL FAIL", member["id"], file=sys.stderr)
                return 1
            _apply(path, member["old"], member["new"])
            try:
                _mut_ok, mut_fail, parsed = _repeat(selector, N)
            finally:
                path.write_text(originals[path], encoding="utf-8")
            vacuous = mut_fail == 0
            change = member["new"].strip().replace("\n", " / ")[:120] or "(deleted)"
            rows.append(
                {
                    "item": member["item"],
                    "id": member["id"],
                    "test": selector.rsplit(".", 1)[-1],
                    "test_full": selector,
                    "mutant": f"{member['file']}:{line} {change}",
                    "file": member["file"],
                    "line": line,
                    "fixture": member["fixture"],
                    "control": f"{ctrl_ok}/{N} pass",
                    "kills": f"{mut_fail}/{N}",
                    "actual": parsed["actual"] if mut_fail else "PASS (control-like)",
                    "expected": parsed["expected"] if mut_fail else "FAIL",
                    "assert_text": parsed["assert_text"] if mut_fail else "",
                    "vacuous": vacuous,
                }
            )
            flag = "VACUOUS" if vacuous else "KILLED"
            print(
                f"{flag:8} {member['item']:4} {member['id']:32} "
                f"{selector.rsplit('.', 1)[-1]} {mut_fail}/{N}"
            )
            if mut_fail:
                print(
                    f"         {member['file']}:{line} actual={parsed['actual'][:120]!r} "
                    f"expected={parsed['expected'][:80]!r}"
                )
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")

    adv = _run_adversarial()
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DEFAULT
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "class_coverage.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "adversarial.json").write_text(
        json.dumps(adv, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# B2 I1–I12 class-coverage receipt",
        "",
        "Each row drops one production fragment, then runs the isolating",
        "repository test 6× against a 6× unmutated control. `actual` /",
        "`expected` are parsed from the mutant run's AssertionError, not a",
        "hard-coded FAIL bit.",
        "",
        "| item | test | mutant (file:line) | actual vs expected | n/6 | vacuous? |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        actual_vs = f"{row['actual']} vs {row['expected']}"
        lines.append(
            f"| {row['item']}/{row['id']} | `{row['test']}` | "
            f"`{row['mutant']}` | {actual_vs} | {row['kills']} | "
            f"{'yes' if row['vacuous'] else 'no'} |"
        )
    (out_dir / "receipt.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    adv_lines = [
        "# I12 adversarial table (run on this HEAD)",
        "",
        "| id | sentence | expected | got | develop | pass? | note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in adv:
        sentence = row["sentence"].replace("|", "\\|").replace("\n", " ")
        adv_lines.append(
            f"| {row['id']} | {sentence} | {row['expected']} | {row['got']} | "
            f"{row['develop']} | {'yes' if row['pass'] else 'NO'} | {row['note']} |"
        )
    (out_dir / "adversarial.md").write_text(
        "\n".join(adv_lines) + "\n", encoding="utf-8"
    )
    print(out_dir / "receipt.md")
    print(out_dir / "adversarial.md")
    vacuous = [row["id"] for row in rows if row["vacuous"]]
    adv_fail = [row["id"] for row in adv if not row["pass"]]
    if vacuous:
        print("VACUOUS MEMBERS", vacuous)
        return 1
    if adv_fail:
        print("ADVERSARIAL FAIL", adv_fail)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
