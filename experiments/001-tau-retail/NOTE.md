# Same customer, same request, four runs

*Where a support agent's runs split, and when the split decides the outcome.*

Ashwin, Crucible Science

**In short.** We read 216 published runs of a customer-service agent (Sierra's τ²-bench: 18 tasks × 3 models × 4 runs with identical settings) without running any model ourselves.

- On 10 of 15 tasks, Claude 3.7 Sonnet passed some runs and failed others; o4-mini did on 7 of 15. These tasks were chosen for instability; across all 114 tasks (all 1,368 published runs), the rates are 33% and 46%.
- Two runs made the same ten tool calls in the same order. One passed. The other told the customer it had found the cheapest T-shirt when a lookup it had made moments earlier listed an available one 19 cents cheaper, and wrote the pricier item to the order.
- 13 of 32 first splits between runs are only a difference in which record is read first. The splits that matter are where a run decides to act on the customer's record, and on what.
- In one task where every run succeeded, the most expensive run cost about 7 times the cheapest; in the other same-outcome cells the median spread was 1.28x.
- Of five pre-registered hypotheses, one failed. The bars for the others were modest; section 3 reports each against its bar and under sensitivity checks. All data and code are public.

![Trajectory tree for task 37 on Claude 3.7 Sonnet](exhibits/task37_claude37.png)

*Task 37 on Claude 3.7 Sonnet, four runs of one request. Run #1 leaves after four shared steps and cancels the order (fail); run #0 carries on past step 10 (pass); runs #2 and #3 share all ten tool calls and part only at the ending, one pass and one fail. It is the only such case on the two models we scored.*

## 1. Why repeat runs matter

Agent tests often run each request once and check the result. In production the same request arrives thousands of times, and a language-model agent does not always handle it the same way, even at temperature zero; batching on shared inference servers is one known reason [7]. A test that runs once sees one of the possible outcomes.

Banks are being asked to look at exactly this. RBI's draft guidance on model risk management asks regulated entities to ensure that "model outputs under similar inputs should not exhibit excessive or unexplained variation" (para 54(6)) [10]. NPCI's IndicBankBench, published in September, reports a similar gap on Indian retail banking cases: across eleven models, the share of cases passed on all three tries ranged from 43.7% to 58.2%, against 60–74% for passing at least once [8].

This note asks where, in the sequence of tool calls, repeated runs of the same request part ways, and when that split decides whether the customer's request is handled correctly.

## 2. What we looked at, and how to read a trajectory tree

The runs are Sierra's own. τ²-bench [2], a benchmark in which an agent serves a simulated customer using a retail store's tools and policy, publishes its baseline results in its repository under the MIT licence. We used the retail results at commit 5bfa7e37: 114 tasks, four runs each, for three models, 1,368 runs in all. We used Sierra's stored grades, computed by its evaluator at commit c30d59aa, and re-graded nothing. We made no model calls ourselves.

Settings were identical across each task's four runs. Claude 3.7 Sonnet and GPT-4.1 ran at temperature 0; o4-mini, which takes no temperature setting, ran with reasoning effort high. The customer is simulated by GPT-4.1 at temperature 0. "Same customer" in the title means the same scripted instructions to that simulated customer; its wording varies between runs. A run passes when τ's grader gives it full reward: the database ends in the expected state, and the agent told the customer what the task requires.

Before scoring anything, we wrote down five hypotheses with pass and kill thresholds, and a fixed rule for choosing 18 tasks from GPT-4.1's results only: the ten tasks whose four runs flipped most between pass and fail; six tasks that handed off to a human on some runs but not others; and two controls that always passed with the fewest distinct paths (tasks 88 and 10). The hypotheses are scored on the other two models. The record is our own: experiment.yaml, which carries the date 30 September 2026 in the file itself. That date is self-attested, not held by an external registry, and our working history before publication is not public; only GPT-4.1's results were examined first. We also checked each task against the current benchmark: task 18's expected answer was corrected after these runs, so it is excluded from counts that depend on grading.

Each trajectory tree shows one task on one model. Every run starts at the left, and each step is a tool call, compared by the tool's name only, not its arguments. Each node carries a number, and the key under the tree gives the tool it stands for. Runs that make the same calls in the same order share a branch; where one run calls a different tool, the branch splits. A branch ends teal if its runs passed and red if they failed. Because only names are compared, two runs can share a branch and still differ in what they passed to a tool; section 3.2 is such a case.

## 3. Findings

### 3.1 Same request, different outcome (H1)

We scored H1 on 15 tasks: the 18 we selected, minus the two controls and task 18. Claude 3.7 Sonnet passed some runs and failed others on 10 of the 15 (67%). o4-mini did so on 7 of 15 (47%). The pre-registered bar was 25% on both models, so H1 is supported. It was a low bar: the tasks were picked for instability, and the benchmark-wide rates below already exceed it. Customer instructions for two of the 15, tasks 62 and 100, were clarified after the runs; with both removed, the counts are 9 of 13 (69%) and 6 of 13 (46%).

These 15 tasks are not a random sample, so they overstate how often a typical request flips. Across all 114 retail tasks, the share of tasks that flip is 38 of 114 (33%) on Claude 3.7, 43 of 114 (38%) on GPT-4.1 and 52 of 114 (46%) on o4-mini. Those figures include task 18 and other tasks that τ³-bench later fixed, so they may overstate flips too. We do not quote GPT-4.1's rate on the 15, since they were picked on its behaviour.

A flip belongs to the whole system: the agent and a simulated customer that is itself a language model. The recorded runs show whether the customer's words differed before a split, but not whether that difference caused it. Separating the two needs each run replayed from its split point with the customer's messages held fixed, which we have not done (section 5).

### 3.2 Same ten tools, same order, one different argument (H5)

In task 37, the customer's card has $1,150 of credit left and the order is over $1,160. After asking about splitting the payment and removing the priciest item, the customer asks to switch all the items to their cheapest options to bring the total down. On Claude 3.7 Sonnet, runs #2 and #3 called the same ten tools in the same order. Run #2 passed. Run #3 failed. The simulated customer worded its messages differently in the two runs, but asked for the same thing both times; in run #3 it said "switch all the items in my order to their cheapest options".

The difference is one argument. Run #3 looked up the T-shirt's variants, and the result listed item 3234800602 (red, L, v-neck), marked available, at $46.66. One lookup later, the agent told the customer "here are the cheapest available options for each item" and listed a different T-shirt: item 9354168549 (red, XXL, crew neck) at $46.85. The customer confirmed, and the agent made the change. The new total, $1,131.04, was under $1,150, so the customer's limit was met. Their instruction was not: the order ended in a different state from the one the task expects, and τ's database check scored 0.

Nothing in the conversation shows the mistake. Run #3's T-shirt happens to keep the customer's original size and style, but the agent never gave that as a reason, and it was not preserving options elsewhere: in the same list it changed the desk lamp's brightness and power source to reach the lamp's cheapest variant. It said it had found the cheapest. τ's communication check passed, but it only asks whether the agent named the most expensive item and its price. To catch the error, a reviewer would have had to compare the agent's list against the raw lookup result.

The money is 19 cents. What matters is the shape: a confident claim, contradicted by data the agent had read one call earlier, at the step that writes to the customer's record.

Because the trajectory tree keys on tool names only, it draws runs #2 and #3 as a single branch through all ten calls, splitting only at the ending, into a pass and a fail. A monitor that compares only tool sequences could not tell these runs apart.

We pre-registered this as H5 (same path, different outcome). On the two confirmation models it occurred in exactly one cell, this one; the exploration model, GPT-4.1, showed one more (task 19). We show it because it is the clearest case, not because it is common. We checked the pair against Sierra's raw results file, and the task's definition is unchanged in the current τ³-bench task set. The lookup result, the agent's list and both runs side by side are in [exhibits/task37_pair.md](exhibits/task37_pair.md).

### 3.3 Where runs split: our hypothesis was wrong (H2, killed)

We expected the first split to be mostly about stopping or escalating: one run ends or hands off while another carries on. The pre-registered kill rule was that splits between two tools would equal or outnumber stopping and escalation splits combined. Across the 32 cells on the two confirmation models where runs diverge, 25 first splits are a choice between tools and 7 involve the hand-off tool, so H2 is killed.

What the splits are matters more than the verdict. 13 are between two lookups, most often whether to fetch the order or the customer's profile first (8 cells); at that point the runs differ only in which record they read first, which matches what Rabanser et al. report about agents taking similar actions in a different order [3]. Whether those differences matter later, we did not test. 12 involve a step that writes to the customer's record: in 9, one run acts while another looks something up first; in 3, runs choose different operations, such as exchange versus return. The remaining 7 are hand-offs. A split is not an alarm in itself. The ones worth watching are where a run decides to act, and on what.

### 3.4 Failing runs leave the path earlier (H3)

The 18 cells on the confirmation models where outcomes differ give 108 pairs of runs. For each pair we counted the tool steps shared before their paths part (identical paths count as their length plus one). Pass/fail pairs share 4.75 steps on average (61 pairs); pass/pass pairs share 6.1 (31 pairs); the other 16 are fail/fail. That is the pre-registered direction, so H3 is supported, but on direction only: the pairs overlap, since each run appears in several, and we ran no test, so treat it as descriptive. Ten pass/pass pairs followed identical paths, which the scoring rule favours. Dropping every identical pair narrows the gap but keeps its direction: 4.65 shared steps for pass/fail (60 pairs) against 5.33 for pass/pass (21), with fail/fail pairs at 2.91 (11). The direction matches Mehta's finding that earlier divergence goes with lower accuracy [4], here in a multi-turn setting with a simulated customer and writes to a database.

### 3.5 Cost (H4)

All figures are the agent's API cost as τ recorded it; the simulated customer's cost is excluded. We pre-registered that at least 10% of the 18 tasks, that is two tasks, would cost twice as much on one run as another, on at least one confirmation model. o4-mini reached 2x on 3 of 18 tasks (17%) and Claude 3.7 on 2 of 18 (11%), so H4 is supported. The largest spread, 8.38x on o4-mini, is task 62, where the cheapest run handed off on its first tool call and failed; task 62's instructions were later clarified, so we do not lead with it. With tasks 62 and 100 set aside, o4-mini reaches 2x on 2 of 16 (13%) and Claude 3.7 on 1 of 16 (6%): the result holds on o4-mini and fails on Claude 3.7.

**Same outcome, different bill.** In task 50 on o4-mini, all four runs passed by handing the customer to a human, as the task expects. Two did so on their first tool call, at $0.00441 and $0.00613; the other two made one and three lookups first, at $0.02594 and $0.03153. The most expensive cost about 7 times the cheapest. In the other cells where every run had the same outcome, spreads ran from 1.06x to 1.72x, with a median of 1.28x.

**The cheapest run can be a failure.** In task 36 on Claude 3.7, the two passing runs cost $0.42 and $0.46. The cheapest run, $0.25, failed by cancelling the order; the most expensive, $0.93, failed after 19 tool calls ending in a hand-off. Per resolved request, the task cost $1.03, against an average of $0.52 per run. In task 47 on o4-mini, three runs passed at $0.044 to $0.048; the fourth handed off on its fourth tool call and failed, at $0.026, the cheapest run in the cell. Per resolved request, it cost $0.054, against $0.041 per run.

## 4. What a bank would have to show

This is a retail servicing agent, not a bank's. But RBI's draft guidance on model risk management, issued on 24 June 2026, asks regulated entities to validate every model they use, including a vendor's, and a bank's servicing agent would face the same questions this note asks. Comments closed on 24 July, and the draft has no effective date yet. What follows is what the draft asks [10], with paragraph numbers, and what evidence would answer it.

| What the draft asks | Where this note touches it | Evidence a vendor could hand over |
|---|---|---|
| Outputs "under similar inputs should not exhibit excessive or unexplained variation" (para 54(6)) | 3.1 (flip rates), 3.2 (task 37), 3.5 (task 47) | Repeat-run pass rates per request type, and a trajectory tree for every request that flips, with each split explained |
| "Independent validation by the RE … notwithstanding any validation, certification, or assurance provided by the third-party provider" (para 46(i)) | The whole note: the vendor's own tests are not enough | Raw traces in an open format the bank can re-analyse itself, not a summary |
| Deployed models, including third-party ones, "should be subject to ongoing monitoring" (para 37) | 3.3, 3.4: which splits to watch | Scheduled repeat runs on a fixed task set, with alerts on splits at steps that write to the customer's record |
| "Enhanced documentation … to enable traceability, reproducibility, and auditability" (para 57) | Section 7 | OpenTelemetry traces, input checksums, a dated pre-registration |
| "Override, suspension, or deactivation mechanisms, including kill-switch arrangements" (para 60(ii)) | 3.2, 3.3 | A proposal, untested here: hold for review any write whose arguments disagree with what the agent just read |
| "Any change to a model, should be preceded by a documented impact assessment" (para 40) | 3.1: the three models flip on 33%, 38% and 46% of the same tasks (o4-mini samples without a temperature setting) | Before-and-after trajectory trees on the same tasks whenever the underlying model changes |

None of this is a certificate. The draft is explicit that validation is the bank's own job (paras 29 and 46); the most a vendor can do is make it fast.

## 5. Limitations

Four runs per task is thin: per-task rates are rough, and no result here carries a significance claim. The models are mid-2025 releases, so this is a study of method and failure shape, not a model ranking. o4-mini samples without a temperature setting, so comparisons across the three models mix decoding regimes. The agent serves a retail store, not a bank; a banking run is planned separately.

The customer is simulated by a language model, so part of any split may come from the customer, not the agent. Recorded runs cannot separate the two; that needs each run replayed from its split point, which we plan next.

The runs and grades are Sierra's, from before τ³-bench fixed 26 retail tasks [9]. Task 18 is excluded because its expected answer was later corrected. Customer instructions for tasks 62 and 100 were later clarified, and we show H1 and H4 with and without them. Task 10's instruction was clarified too; it is a control, outside H1, and its cost spreads (1.48x and 1.40x) are under the H4 bar either way. Task 10 is also why we checked: on Claude 3.7 it passed twice and failed twice, and its instruction was later amended to add "Make sure to return BOTH orders." An unclear instruction to the simulated customer can produce a flip that is not the agent's fault, and we dropped an earlier finding that rested on this task.

The tasks were chosen on one model's behaviour, which may favour tasks that are unstable for it. Trajectory trees compare tool names, not arguments; section 3.2 shows what that misses. Our published run files cap each tool result at 4,000 characters; Sierra's raw files, fetched by the reproduction script, are complete. Costs are τ's recorded agent costs at the prices of the time.

We did not register the hypotheses with an external registry, test any result for significance, recompute the benchmark-wide rates without the later-fixed tasks, or compare the customer's words before every split. Each would strengthen a follow-up.

## 6. Prior art

τ-bench introduced pass^k, which asks whether an agent succeeds on every one of k tries [1]; τ²-bench, the source of these runs, extends it [2]. Rabanser et al. break agent reliability into consistency, robustness, predictability and safety, and measure how much trajectories and costs vary across repeated runs [3]. Mehta finds that tasks whose runs diverge early are answered correctly less often [4]. Khatchadourian separates determinism of an agent's trajectory from determinism of its final decision in financial services, framed around regulatory audit replay [5]; task 37 is a case where the two come apart. Khanal et al. show reliability and capability diverging as task duration grows [6], and Thinking Machines explain why temperature-zero inference still varies [7]. NPCI's IndicBankBench measures repeat-run reliability on Indian retail banking [8]. SABER finds errors concentrated in steps that change state, and its fixes to τ-bench are the source of the clarifications in section 5 [9]. Commercial and open-source tools test agents before release, among them Coval, Bluejay, Cekura, Hamming, Roark, SuperBryn, Cyara and hotato; this note reads recorded runs rather than generating tests.

Others have shown that runs differ (pass^k, Rabanser et al.), measured how much trajectories differ (Rabanser et al., Khatchadourian), and found that earlier divergence goes with lower accuracy (Mehta). This note does three narrower things in a multi-turn, state-changing customer-service setting: it reads pass/fail pairs of the same request side by side, classifies each first split by kind (many are only differences of order), and documents a case where the tool sequence is identical and the outcome still differs because of one argument. It does this on the benchmark's own published runs, pre-registered, reports a hypothesis that failed, and maps each finding to the RBI draft.

**References**

1. Yao, Shinn, Razavi, Narasimhan. τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains. arXiv:2406.12045, 2024.
2. Barres, Dong, Ray, Si, Narasimhan. τ²-Bench: Evaluating Conversational Agents in a Dual-Control Environment. arXiv:2506.07982, 2025. Results: github.com/sierra-research/tau2-bench
3. Rabanser, Kapoor, Kirgis, Liu, Utpala, Narayanan. Towards a Science of AI Agent Reliability. ICML 2026. arXiv:2602.16666.
4. Mehta. When Agents Disagree With Themselves: Behavioral Consistency as an Uncertainty Signal for LLM Agents. arXiv:2602.11619, 2026.
5. Khatchadourian. Replayable Financial Agents: A Determinism-Faithfulness Assurance Harness for Tool-Using LLM Agents. arXiv:2601.15322, 2026.
6. Khanal, Tao, Zhou. Beyond pass@1: A Reliability Science Framework for Long-Horizon LLM Agents. arXiv:2603.29231, 2026.
7. He and Thinking Machines Lab. Defeating Nondeterminism in LLM Inference. Connectionism, September 2025. thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference
8. Paul, Bhushan, Sharma, Kukreja, Dedhia, Doshi, Devadiga. IndicBankBench: Evaluating Safety and Reliability of Language Model Assistants in Indian Retail Banking. arXiv:2609.29167, 2026.
9. Cuadron, Yu, Liu, Gupta. SABER: Small Actions, Big Errors — Safeguarding Mutating Steps in LLM Agents. ICLR 2026 Workshop on Memory for LLM-Based Agentic Systems. arXiv:2512.07850.
10. Reserve Bank of India. Draft Guidance on Regulatory Principles for Model Risk Management, 2026. Press Release 2026-2027/528, 24 June 2026. rbi.org.in/Scripts/bs_viewcontent.aspx?Id=5089

## 7. Reproduce it

From the repository at tag `001-tau-retail-v1`:

```
bash experiments/001-tau-retail/fetch_raw.sh     # Sierra's files at 5bfa7e37, sha256-checked
python divergence/importers/tau.py experiments/001-tau-retail --clean
python divergence/analyse.py experiments/001-tau-retail
python -m http.server                            # open /viewer/?exp=001-tau-retail
```

`manifest.json` lists the sha256 of every input; `experiment.yaml` is the dated pre-registration. `checks.sh` recomputes the numbers that are not in analysis.json.

## 8. Run this on your own agent

The method works on any agent that logs its tool calls. If you run a support or collections agent, send us redacted traces of the same requests handled several times, and we will show you your trajectory trees: where your runs split, and which splits change the outcome. ashwin@cruciblescience.com

Crucible Science sells this analysis, applied to a client's own agent; this note is also how we show the method. It was self-funded. No vendor paid for it, saw it before publication, or reviewed it.