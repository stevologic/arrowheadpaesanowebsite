# I12 adversarial table (run on this HEAD)

| id | sentence | expected | got | develop | pass? | note |
| --- | --- | --- | --- | --- | --- | --- |
| r1_five_after_14_12 | Five Kansas City touchdowns after a 14-12 halftime, including Johnson’s 6-yard score | FLAG | FLAG | pass | yes | #331 r1; ESPN 401873045 has three 2H TDs; head must FLAG |
| r2_three_second_half | Three Kansas City touchdowns in the second half, including Johnson's 6-yard score | pass | pass | pass | yes | #331 r2; BASE second-half scope already 3=3 |
| three_after_14_12 | Three Kansas City touchdowns after a 14-12 halftime, including Johnson’s 6-yard score | pass | pass | leftover | yes | develop leftover 3 vs 5; head pass is the I6 fix, not a miss |
| lede_second_half_three | The second half was three Kansas City touchdowns and a defense that kept Geno Smith off the scoreboard. | pass | pass | pass | yes | lede The second half was three... |
| whole_game_five_no_window | Five Kansas City touchdowns, four New York field goals, no Jets touchdown. | pass | pass | pass | yes | true-negative: no window word, 5=5 whole game |
| whole_game_three_no_window | Three Kansas City touchdowns came before the break. | leftover | leftover | leftover | yes | true-negative for the window guard; leftover 3 vs 5 is whole-game |
| walker_turned | Walker's 102 yards on 18 carries, plus Johnson's six-yard touchdown, turned a 14-12 halftime into 35-12 | FLAG | FLAG | pass | yes | I5/H1: yards bound to turned-halftime is fail-closed |
| flipped_14_12 | Eighteen carries and 102 yards just flipped a game that was 14-12 | FLAG | FLAG | pass | yes | I5/H1: yards bound to flipped 14-12 is fail-closed |
| following_halftime | Five Kansas City touchdowns following halftime. | FLAG | FLAG | pass | yes | following = after |
| after_the_half | Five Kansas City touchdowns after the half. | FLAG | FLAG | pass | yes | after the half |
| in_the_second_half_five | Five Kansas City touchdowns in the second half. | FLAG | FLAG | leftover | yes | develop leftover via second-half scope 5 vs 3; head also window-FLAG |
| endash_14_12 | Five Kansas City touchdowns after a 14–12 halftime. | FLAG | FLAG | pass | yes | Unicode en dash U+2013 |
| emdash_14_12 | Five Kansas City touchdowns after a 14—12 halftime. | FLAG | FLAG | pass | yes | Unicode em dash U+2014 folded to hyphen |
| minus_14_12 | Five Kansas City touchdowns after a 14−12 halftime. | FLAG | FLAG | pass | yes | U+2212 minus folded to hyphen |
| soft_hyphen_14_12 | Five Kansas City touchdowns after a 14­-­12 halftime. | FLAG | FLAG | pass | yes | soft hyphen U+00AD stripped/folded |
| zwsp_14_12 | Five Kansas City touchdowns after a 14​-​12 halftime. | FLAG | FLAG | pass | yes | ZWSP U+200B stripped |
| sep_ascii_hyphen | Five Kansas City touchdowns after a 14-12 halftime. | FLAG | FLAG | pass | yes | 14-12 |
| sep_en_dash | Five Kansas City touchdowns after a 14–12 halftime. | FLAG | FLAG | pass | yes | 14–12 |
| sep_spaced_hyphen | Five Kansas City touchdowns after a 14 - 12 halftime. | FLAG | FLAG | pass | yes | 14 - 12 |
| negation_never_five | Kansas City never five touchdowns after halftime. | pass | pass | pass | yes | negation: skip the count |
| i12_figure_dash | Five Kansas City touchdowns after a 14‒12 halftime. | FLAG | FLAG | pass | yes | U+2012 figure dash folded to hyphen |
| i12_nbsp_reached | Kansas City scored five touchdowns after the score reached 14 – 12. | FLAG | FLAG | pass | yes | NBSP inside 14–12 |
| i12_non_after_reached | Five Kansas City touchdowns non-after the score reached 14-12. | pass | pass | pass | yes | un/non lookbehind on after the score reached |
| i12_following_the_score_reached | Five Kansas City touchdowns following the score reached 14-12. | pass | pass | pass | yes | following the score reached is not after |
| i12_had_reached | Five Kansas City touchdowns after the score had reached 14-12. | pass | pass | pass | yes | had reached is not the bind |
| i12_falling_behind | Five Kansas City touchdowns after falling behind 0-6. | pass | pass | pass | yes | falling behind is not trailing |
| i12_once_trailing | Five Kansas City touchdowns once trailing 6-0. | pass | pass | pass | yes | once trailing is not after trailing |
| i12_never_trailed | Kansas City never trailed 6-0 after the score reached 14-12. | pass | pass | pass | yes | negation / no count noun |
| i12_digit_five_reached | Kansas City scored 5 touchdowns after the score reached 14-12. | FLAG | FLAG | pass | yes | digit 5 vs word five on the new score window |
| i12_td_singular_reached | Kansas City scored 5 touchdown after the score reached 14-12. | FLAG | FLAG | pass | yes | singular touchdown on the new score window |
| digit_5 | 5 Kansas City touchdowns after halftime. | FLAG | FLAG | pass | yes | 5 vs Five |
| word_five | Five Kansas City touchdowns after halftime. | FLAG | FLAG | pass | yes | Five vs 5 |
| five_tds_after_the_half | Five Kansas City TDs after the half. | FLAG | FLAG | pass | yes | TDs noun |
| two_field_goals_after_the_break | Two Kansas City field goals after the break. | FLAG | FLAG | leftover | yes | 0 second-half FGs; develop leftover via after-the-break scope |
| three_scores_since_halftime | Three Kansas City scores since halftime. | pass | pass | pass | yes | 3 scores = 3 TDs + 0 FG in 2H |
| sacks_after_halftime | Five Kansas City sacks after halftime. | FLAG | FLAG | pass | yes | fail-closed: no per-window ESPN sacks |
| points_after_halftime | Twenty-one Kansas City points after halftime. | pass | pass | pass | yes | twenty-one is not a number-word/digit member; not this guard |
| points_digit_after_halftime | 21 Kansas City points after halftime. | FLAG | FLAG | pass | yes | fail-closed: no per-window ESPN points |
| yards_after_halftime | 102 Kansas City yards after halftime. | FLAG | FLAG | pass | yes | fail-closed: no per-window ESPN yards |
| takeaways_after_halftime | Three Kansas City takeaways after halftime. | FLAG | FLAG | pass | yes | fail-closed: no per-window ESPN takeaways |
| fourth_quarter_five | Five Kansas City touchdowns in the fourth quarter. | FLAG | FLAG | leftover | yes | Q4 has 1 KC TD; develop leftover via fourth-quarter scope |
| non_second_half | Five Kansas City touchdowns in the non-second-half. | pass | pass | leftover | yes | un/non prefix must not bind second-half; develop leftover via second-half |
| one_sack_singular | One Kansas City sack after halftime. | FLAG | FLAG | pass | yes | fail-closed singular sack |
| one_yard_singular | One Kansas City yard after halftime. | FLAG | FLAG | pass | yes | fail-closed singular yard |
| one_point_singular | One Kansas City point after halftime. | FLAG | FLAG | pass | yes | fail-closed singular point |
| one_takeaway_singular | One Kansas City takeaway after halftime. | FLAG | FLAG | pass | yes | fail-closed singular takeaway |
| three_following_the_half | Three Kansas City touchdowns following the half. | pass | pass | leftover | yes | I6: true 2H count; develop leftover 3 vs 5 |
| three_following_the_break | Three Kansas City touchdowns following the break. | pass | pass | leftover | yes | I6: true 2H count; develop leftover 3 vs 5 |
| three_following_a_nn | Three Kansas City touchdowns following a 14-12 halftime. | pass | pass | leftover | yes | I6: true 2H count; develop leftover 3 vs 5 |
| three_turned | Three Kansas City touchdowns turned a 14-12 halftime into 35-12. | pass | pass | leftover | yes | I6: true 2H count; develop leftover 3 vs 5 |
| three_flipped | Three Kansas City touchdowns flipped a game that was 14-12. | pass | pass | leftover | yes | I6: true 2H count; develop leftover 3 vs 5 |
| one_catch_singular | One Kansas City catch after halftime. | FLAG | FLAG | pass | yes | fail-closed singular catch |
| two_catches | Two Kansas City catches after halftime. | FLAG | FLAG | pass | yes | fail-closed plural catches |
| one_carry_singular | One Kansas City carry after halftime. | FLAG | FLAG | pass | yes | fail-closed singular carry |
| two_carries | Two Kansas City carries after halftime. | FLAG | FLAG | pass | yes | fail-closed plural carries |
| one_interception_singular | One Kansas City interception after halftime. | FLAG | FLAG | pass | yes | fail-closed singular interception |
| two_interceptions | Two Kansas City interceptions after halftime. | FLAG | FLAG | pass | yes | fail-closed plural interceptions |
| one_fumble_singular | One Kansas City fumble after halftime. | FLAG | FLAG | pass | yes | fail-closed singular fumble |
| two_fumbles | Two Kansas City fumbles after halftime. | FLAG | FLAG | pass | yes | fail-closed plural fumbles |
| one_turnover_singular | One Kansas City turnover after halftime. | FLAG | FLAG | pass | yes | fail-closed singular turnover |
| two_turnovers | Two Kansas City turnovers after halftime. | FLAG | FLAG | pass | yes | fail-closed plural turnovers |
| one_reception_singular | One Kansas City reception after halftime. | FLAG | FLAG | pass | yes | fail-closed singular reception |
| two_receptions | Two Kansas City receptions after halftime. | FLAG | FLAG | pass | yes | fail-closed plural receptions |
| one_rush_singular | One Kansas City rush after halftime. | FLAG | FLAG | pass | yes | fail-closed singular rush |
| two_rushes | Two Kansas City rushes after halftime. | FLAG | FLAG | pass | yes | fail-closed plural rushes |
| one_completion_singular | One Kansas City completion after halftime. | FLAG | FLAG | pass | yes | fail-closed singular completion |
| two_completions | Two Kansas City completions after halftime. | FLAG | FLAG | pass | yes | fail-closed plural completions |
