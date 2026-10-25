"""F9 step 3: versioned market rules, scoped to openfootball's 8 fixed leagues.

Only football/soccer regulation-time rules with full-time (and, where needed,
half-time) score evidence are implemented here, because that is the only fact
openfootball can supply (see docs/F9_SOURCE_FEASIBILITY.md). Everything else
observed in the real runs -- handicaps, timeline/1UP-2UP markets, statistics
markets, combined AND/OR markets, non-regulation periods beyond HT/FT -- is
registered as explicitly unsupported with a reason, per F9_RESULTS_PLAN.md
section 6: "Register every inventoried combination as supported with evidence
or explicitly unsupported." No fixture matching or network fetching happens
in this package; evaluation always takes an already-resolved score as input.
"""
