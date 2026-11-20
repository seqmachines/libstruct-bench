# Benchmark configurations

Configurations are grouped by the tasks they evaluate:

| Folder | Contents |
| --- | --- |
| [task1/](task1/README.md) | Final-library reconstruction: PDF/API and native-agent configurations, name-only API control, and the earlier input inventory used to verify exclusions. |
| [tasks2_3/](tasks2_3/README.md) | Linked oligo-family and molecular-workflow reconstruction: protocol/source inventory, rules, network assets, name-only native-agent configs, cost snapshot and historical planning provenance. |
| [shared/](shared/) | Protocol-processing instructions consumed by Task 1 and the retained legacy extraction generator. |

Selected predictions, panel membership, final scores, statistical tests and
reference identities are in [`paper/`](../paper/README.md). This folder contains
execution configurations, not a second copy of those results.

Generated tasks, downloaded source files, verifier ground truth, experiment
locks, credentials and logs are local/ignored artifacts. The superseded
standalone oligo-extraction benchmark and obsolete model list were archived
locally; their legacy generator accepts explicit `--protocols` and `--out`.

The directory reorganization did not change agent prompts, scientific weights,
protocol selection, model versions, saved run paths, or C30. Repository-path
references were updated. Historical `paper/` manifests retain their original
paths and hashes; [the relocation inventory](../docs/release/benchmark_layout.json)
records where retained benchmark files now live. Historical locks are not
silently rewritten to authorize resuming an old experiment with changed code.
