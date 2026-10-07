# sparql-university on GLM-4.7: two kinds of query in five runs

Exhibit for section 3.3 of [the note](../NOTE.md). Source: Harbor's leaderboard repository
`harborframework/terminal-bench-2-leaderboard` at revision
572b2614be2c0cb2527e14f5b1e4026f1072e6c1, submission folder
`submissions/terminal-bench/2.0/Terminus2__GLM-4.7/2026-01-27__12-34-00/`, the five trials
of task `sparql-university`. Run numbers follow our importer (the task's trials sorted by
start time, then name). Step numbers are positions in the step list of the trial's
`agent/trajectory.json`, as `hero_facts.py` prints them; one step can hold more than one
command. Text in code blocks and quotation marks is verbatim; `…` marks an omission.

![Trajectory trees for sparql-university on GLM-4.7 and on Claude Opus 4.6](sparql_university_glm47_opus46.png)

*Five runs of one task on each model: GLM-4.7 above (three passed, two failed), Claude
Opus 4.6 below (five passed), both on the Terminus2 harness. A number names the kind of
step, as in the key under the trees; the order of steps runs left to right. In the upper
tree the two failed endings are run 000 (upper) and run 001 (lower). This pair is an
illustration, chosen for readability; most task and model pairs are messier.*

## What the task asks

The agent is given a data file and asked to write one query. Two lines of the task matter
here:

> 2. They work in at least one department of a university located in a European Union country.

> where ?professorName is the professor's name, and ?countries lists all countries where the professor currently works in.

## The two query shapes

All five runs wrote a query. They differ in what the country column keeps.

Run 000 (failed), final query, written at step 12. The country that is listed is the same
variable that is filtered to the EU codes:

```sparql
    # Get the university and country for the department
    ?dept uni:belongsTo ?university .
    ?university uni:locatedInCountry ?country .
    
    # EU member states as of 2025-08-16
    VALUES ?euCountry { "AT" "BE" "BG" "HR" "CY" "CZ" "DK" "EE" "FI" "FR" "DE" "GR" "HU" "IE" "IT" "LV" "LT" "LU" "MT" "NL" "PL" "PT" "RO" "SK" "SI" "ES" "SE" }
    FILTER(?country = ?euCountry)
```

Run 002 (passed), final query, written at step 2. The EU test uses one variable and the
country list another:

```sparql
  ?dept uni:belongsTo ?univ .
  ?univ uni:locatedInCountry ?euCountry .
  
  # Get all countries where the professor works
  ?prof uni:worksIn ?allDept .
  ?allDept uni:belongsTo ?allUniv .
  ?allUniv uni:locatedInCountry ?country .
```

Run 001 (failed) has the first shape and runs 003 and 004 (passed) have the second.

## What the two failing runs saw when they tested

Both installed rdflib and ran their query on the data file they had been given.

Run 000, step 11, on its second version of the query:

```
Results:
  Orfeas Menis | GR
  Pepe Attanasio | GR
  Aristotle Tympas | GR
```

Run 000, step 12, after rewriting the query (the final version):

```
Results:
  Orfeas Menis | GR
  Pepe Attanasio | PT, GR
  Aristotle Tympas | GR
```

Run 001, step 10:

```
Results:
  Professor: Orfeas Menis, Countries: GR
  Professor: Pepe Attanasio, Countries: GR, PT
  Professor: Aristotle Tympas, Countries: GR
```

## What run 000 printed one step later

At step 13 run 000 listed every professor with each country they work in. Among the lines
on its own terminal:

```
Professor details:
…
  Orfeas Menis | Professor of Computer Science | GR
  Orfeas Menis | Professor of Computer Science | US
```

It did not change the query after that. It ran two more checks of student counts (steps
14 and 15), read the file once more (step 16) and declared the task complete (step 17).

## What each run said at the end

Run 000 (failed):

> Analysis: I have verified the solution thoroughly:
> …
> Tested results:
> - Orfeas Menis | GR (works in ComputerScience_NTUA with 12 students)

Run 001 (failed):

> Analysis: The SPARQL query has been verified and produces the correct results:
> …
> - Returns professor name and grouped countries where they work in EU

Run 002 (passed), which never ran its query:

> Expected results: Orfeas_Menis (GR, US), Pepe_Attanasio (PT, GR), Aristotle_Tympas (GR) - all are full professors in EU countries whose departments have >10 current students.

Run 003 (passed), which never ran its query:

> My query looks correct. It should return:
> - Orfeas Menis: "GR, US"

## Side by side

| Run | Trial | Final query (step, fingerprint) | Ran its query on the data? | Country column keeps | Orfeas Menis, on the data the agent could see¹ | Verifier reward |
|---|---|---|---|---|---|---|
| 000 | `sparql-university__7ndKpMY` | 12, `0c62f5e1` | yes, steps 11 and 12 | EU countries only | GR | 0 |
| 001 | `sparql-university__q9pqxLN` | 2, `1c762d7e` | yes, step 10 | EU countries only | GR | 0 |
| 002 | `sparql-university__HuZF8xa` | 2, `33269503` | no | all countries | GR, US | 1 |
| 003 | `sparql-university__NqBqLJb` | 2, `98b16993` | no | all countries | GR, US | 1 |
| 004 | `sparql-university__p5dZhmN` | 8, `dc1c5141` | no | all countries | GR, US | 1 |

¹ From re-running each run's final query on the task's data file with rdflib 7.1.4
(`hero_verify.py`). For runs 000 and 001 the agent's own terminal shows the same row.

The three runs that did not run their query each looked for a query tool (run 002 at
step 3, run 003 at step 4, run 004 at step 3) and installed nothing.

## Claude Opus 4.6 on the same task

Submission folders `Terminus2__Claude-Opus-4.6/2026-02-05__16-08-28/` (run 000) and
`…/2026-02-05__17-41-47/` (runs 001 to 004). All five runs passed. All five final queries
keep every country: re-run on the task's data file, each returns "Orfeas Menis | GR, US".
Three of the five ran their query before declaring the task done (run 000 at step 7, run 003 at step 11,
run 004 at step 12); runs 001 and 002 did not.

| Run | Trial | Final query (step, fingerprint) | Ran its query on the data? |
|---|---|---|---|
| 000 | `sparql-university__Sk62Rzd` | 2, `552cae60` | yes |
| 001 | `sparql-university__ym6rXgX` | 2, `3233c884` | no |
| 002 | `sparql-university__yKbyp9G` | 2, `ef28dc96` | no |
| 003 | `sparql-university__XZNyLCM` | 11, `168a8a2a` | yes |
| 004 | `sparql-university__fmkL7ch` | 12, `c75aceb7` | yes |

So testing did not separate passing from failing here: Opus's tested runs passed. What
the two failing GLM-4.7 runs have in common is the query shape, and their own test showed
them a result that agreed with it.

## What is not in this file

The verifier runs each query on a separate test graph that the agent never sees. We do not
republish that graph or the rows it expects. We re-ran the verifier's comparison on all
ten final queries (`hero_verify.py`, rdflib 7.1.4, task repository
`laude-institute/terminal-bench-2` at commit 2fd12b88) and it reproduces all ten recorded
rewards.

## Reproduce this extract

From the repository root, after `fetch.py` has downloaded the trials (see this folder's
[README](../README.md)):

```bash
python experiments/002-terminal-bench/hero_facts.py
```

It prints the task text, every write to the query file, each run's steps with the terminal
output, and the fingerprints above, and names the trial folder each came from. Its output
goes to `out/hero_facts.txt`, which is generated and not committed.