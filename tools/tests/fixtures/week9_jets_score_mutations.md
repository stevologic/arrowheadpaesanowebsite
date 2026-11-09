# Week 9 Jets score mutants

| mutant | behavioural test | actual | expected | result |
| --- | --- | --- | --- | --- |
| unwire Jets 401873045 fixture | replay_fixture_missing(week9) is True | missing=True recap_final=None | missing=True recap_final!=(35, 12) | **KILLED** |
| trust edition header 24-28 as Jets official | test_jets_week9_replay_uses_box_and_rejects_denver_final | stamped_issues=[] espn_last=35-12 espn_issues=["score 24-28 is not the official final or an ESPN score-after ('KC 24–28')", "score 24-28 is not the official final ('lost 24-28')"] | stamped 24-28 passes; ESPN 35-12 leftover still fails | **KILLED** |
| rewrite lost 24-28 to won 35-12 | drop-only _correct_sentence on official-final leftover | current=None mutant='Kansas City won 35-12 to the Jets.' | current=None mutant rewrites the count | **KILLED** |
| gate-replay returns 0 on hard leftover | held leftover still exits non-zero | live_rc=1 hold_zero_rc=0 | live HARD_REPLAY_RC=1; mutant patched HARD_REPLAY_RC=0 returns 0 | **KILLED** |
| map Jets to Denver 24-28 box | last_game_for_edition Week 9 Review is 401873045 / 35-12 | current id=401873045 score=35-12; mutant id=401873033 score=None-None | current id=401873045 score=35-12; mutant id=401873033 | **KILLED** |
| nearest Jets 35-12 steals Denver leftover 24-28 | test_broncos_subject_lost_24_28_next_to_jets_keeps_denver | current=[] mutant=["score 24-28 is not the official final ('lost 24-28')"] | current keeps 24-28; mutant flags official-final | **KILLED** |
