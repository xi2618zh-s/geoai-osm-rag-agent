# Development benchmark split

Visible development tasks belong here from M11 onward. They may be used for
debugging and prompt/tool development, so reports must label their metrics as
development results rather than hidden-test results.

M11a adds:

- `m11a_tasks_v1.jsonl`: 24 visible synthetic tasks spanning all seven GeoDSL operations;
- `m11a_calibration_predictions_v1.jsonl`: deterministic fixture predictions with deliberate errors that exercise every M11a failure category.

The `required_steps` values are semantic evaluation labels, not claims that M04
tools with those names are already registered. Do not use the 15/24 calibration
pass count as system-quality evidence.
