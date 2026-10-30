# Evidence: what has been tested, and what it showed

This page collects every study of whether the library's outputs mean something in the real
world, with what worked and what did not. Each study's questions and the rules for reading its
results were written down before any result was seen; results are reported whatever they showed.
Whether the code computes what it claims (exactness, tiling, invariants) is a different question,
answered in [Verification](verification.md).

AUC below is the chance that a place with a flood report scores higher than the place of a
randomly chosen resident (0.5 = no better than where people live). Intervals are 95% spatial
block-bootstrap intervals. A result is "supported" or "contradicted" only when the whole interval
is on one side.

## At a glance

| Question | Answer | Strength |
|---|---|---|
| Do 311 flood reports gather on ground whose depression fills in a 2-year storm? | Yes in New York and Houston 2015; not shown in four other cities | Mixed |
| Do landlocked depressions gather more reports than drained ones? | No: the opposite in Houston and Austin, the expected way in New Orleans | Contradicted where clear |
| Does predicted standing water at a storm's peak mark where reports come from? | Yes, modestly (AUC 0.575 in four held-out cities); hardly in Chicago | Supported, weak |
| Does following the storm hour by hour beat the storm total? | No measurable difference | Inconclusive |
| Do assumed storm drains help? | No: every uniform drain rule made the ranking worse | Clear, on calibration cities |
| Curve numbers (A) or Green-Ampt (B)? | Equal | No difference |
| Does finer terrain help? | 10 m better than 30 m against lidar and, slightly, against reports; 1 m not better than 10 m against reports | 10 m: supported; 1 m: inconclusive |
| Does machine learning beat the physical score in unseen cities? | Not the model tested; a simple logistic baseline did, as an untested lead | Not kept |

## Study 1: the susceptibility layer against flood reports

New York City and Houston (two floods), the published 30 m layer. Fills in a 2-year storm
against rarer or never: supported in New York (lift 1.28) and Houston 2015 (9.9), inconclusive in
Houston 2016. Landlocked against drained: contradicted in both Houston floods, inconclusive in New
York. Full study: [Validation, study 1](validation.md#study-the-susceptibility-layer-against-flood-reports-new-york-city-and-houston).

**Rerun in four more cities** (Austin, New Orleans, Chicago, Baltimore): neither finding
replicated cleanly. The 2-year class was contradicted in New Orleans and inconclusive elsewhere;
landlocked against drained was contradicted in Austin, supported in New Orleans and inconclusive
in Chicago and Baltimore ([details](validation.md#study-1-in-four-more-cities)).

## Study 2: storms in time against flood reports

Ten real storms with hourly radar rain in six cities; settings chosen on New York and Houston,
hypotheses tested on Austin, New Orleans, Chicago and Baltimore. Predicted standing water at the
peak: AUC 0.575 (0.549 to 0.600), supported. The hour-by-hour model against the storm total:
-0.0006 (-0.0018 to 0.0003), inconclusive. Drains lowered the AUC at every level tried. 10 m
terrain beat 30 m by 0.012 (0.003 to 0.022); 1 m lidar against 10 m was inconclusive. Full study:
[Validation, study 2](validation.md#study-2-storms-in-time-against-flood-reports-six-cities).

What follows for use: the storm model's value is depth, timing and how long water stands, not a
better ranking of places; defaults stay method A and no drains ([Storms in time](storms.md)).

## Terrain quality: 30 m, 10 m and 1 m lidar

Ten 5 × 5 km windows compared with 1 m lidar (depressed area per H3 cell, area-weighted F1):
outside New York, 30 m finds 14% to 56% of the depressed area lidar finds and 10 m 32% to 83%,
and what both find is mostly real (precision 0.53 to 0.85 at 30 m, 0.87 to 0.95 at 10 m).
10 m beat 30 m by at least 0.10 in F1 in 8 of 10 windows. In New York neither resembles lidar
(F1 0.05 to 0.32): street-scale hollows between buildings and curbs need lidar.

Against independent maps on three 1-degree boxes (Houston, the prairie potholes, the Central
Valley), depressed area lies in mapped wetlands (US National Wetlands Inventory) 1.7 to 5.1 times
more often than chance, and in an independent 10 m depression map (Wu et al., National Surface
Depressions) 3.4 to 7.6 times.

A national 10 m run would take on the order of 110 to 130 hours and 250 GB of disk on a laptop
(an estimate scaled from the measured boxes), so the national layer stays at 30 m; 10 m is
available for any box (`h3lowpoints build --from 3dep10`).

## Machine learning

Does a learned model predict where reports occur in a city it never saw better than the physical
score? Ten storms, six cities, each city scored by models trained on the other five. Boosted trees
(forced to rise with the score, rain and depressed area): +0.013 (-0.009 to 0.032) over the
physical score, inconclusive, so **not kept**; it was unstable from city to city. A simple
logistic model on the same inputs did better (+0.045, 0.029 to 0.060), but it was a baseline, not
the model under test, and part of its gain may come from where people report (paved streets)
rather than where water stands. It is a lead for a future study, not a result. No prediction
layer is published.

## What none of this shows

- Reports and claims record where people noticed and reported flooding, not where water stood.
- Storm drains, sewers and pumps are in none of the layers; in cities they decide much of what
  floods.
- Ten storms in six cities are few; readings were made without correction for multiple tests.
- Association is not cause. See [Accuracy and limits](limitations.md).
