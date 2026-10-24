# Matching evals

One JSON file per case. Each names the requester and the other students (overrides on the
test defaults in `tests/factories.py`: campus, quiet, EECS year 3, CS 61A, free 5–10 PM on
weekdays), the request, a `recorded_output` (a valid model answer), and `expect`.

- Offline (`pytest tests/test_evals.py`): the time Filter runs on the fixture, the recorded
  answer must pass `validate_match`, and the pipeline must produce the expected proposal.
  This checks the fixtures and the plumbing, not the model's judgement.
- Live (`STUDYMATE_LIVE_EVALS=1 pytest tests/test_evals.py`): the same expectations run
  against the real model through Omnara. This is the run that judges the prompt.

`expect` keys: `filter_includes`, `filter_excludes` (ids with time inside the window),
`chosen` (exact ids), `not_chosen`, `no_match`, `alternative_in_other_free`, `near_tie`,
`runner_up_set`, `flagged` (ids that must appear in flags), `reason_mentions` (every phrase,
case-insensitive), `reason_mentions_any`, `place_kind`.
