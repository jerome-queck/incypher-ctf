# What modern evidence says about autonomous CTF solving

_Research snapshot: 5 September 2026. This answers the Wayfinder question, not an architecture
decision. Only primary papers, official benchmark artefacts, source repositories, and first-party
run claims are used. “Inference” marks a consequence for IN-CYPHER that the source did not test._

## Decision-grade answer

Frontier CTF agents succeed through a grounded loop, not security knowledge alone: inspect a fresh
challenge, form a hypothesis, execute commands inside an isolated attacker environment, observe
real output, revise, construct an exploit or solve script, and validate a flag. The strongest
repeated evidence favours capable models paired with a broad, low-friction security environment,
stateful interactive tools, bounded attempts, and complete action/observation traces. Extra prompt
machinery, more turns, or heterogeneous cheaper workers do not reliably improve results.

Four conclusions transfer most strongly to IN-CYPHER:

1. **Freshness and provenance dominate headline solve rate.** On five live 2025 CTFs, CTFusion
   measured 6.3% average success versus 14.4% on reused NYU tasks under comparable agent
   conditions. Adding web search nearly doubled D-CIPHER's static-benchmark score, but log review
   found 71 lookup/copy attempts. CTF-ABACUS later found only 62–87% of recovered flags across four
   benchmarks were backed by a demonstrated exploit. A recovered flag-shaped string is therefore
   only a Candidate, not sufficient evidence of a solve.
2. **Long-horizon recovery remains the central failure.** EnIGMA found most successful solves in
   the first 20 steps; failures tended to consume the whole budget. DeepRed, using 60-step runs and
   models with at least 250k context, found the same pattern: progress early, then repeated commands,
   restarted work, failure to retain intermediate state, or failure to escalate after foothold. Its
   best model reached only 35% of checkpoints. More context and more actions did not cure poor state
   synthesis or replanning.
3. **Tool usability matters more than tool names.** EnIGMA's non-blocking debugger and remote-
   connection sessions, plus guarded long-output handling, improved aggregate solve rate; a 2026
   controlled study measured a +9.5 percentage-point gain from a Kali environment with 100+ tools
   and runtime documentation over its Ubuntu setup. But category effects differ, and agents still
   confuse shell commands with interactive debugger commands. Tools need simple interfaces,
   observable errors, persistent sessions, and category fit.
4. **Parallelism is promising for board throughput, but not causally established.** D-CIPHER shows
   a planner/executor hierarchy can improve a single challenge by 1–5 points over one executor,
   while a later study found mixed strong/weak planner-executor pairings no better than a coherent
   same-model pair. Veria's open implementation races model swarms across challenges and claims a
   52/52 live win, but publishes no run bundle sufficient to reproduce or attribute that result.
   No source below isolates board-level concurrency under a 5.5-hour clock, shared quotas, and
   finite submissions. Concurrency should therefore be treated as a throughput hypothesis with
   explicit lane budgets, not inherited as a proven capability multiplier.

## Source scoring

Scores are `3` direct/strong, `2` useful but partial, `1` weak proxy, `0` absent. **R** is recency
(`3` = 2026, `2` = 2025, `1` = 2024); **Cat** is transfer to the six common IN-CYPHER categories;
**Fresh** is contamination-resistant content; **Iso** is clean isolated Instances; **Sub** is a
finite-submission analogue; **Board** is transfer to a multi-challenge 5.5-hour Board. Repository
links are official artefacts belonging to the paper or first-party claim in the same row.

| Primary source package | R | Cat | Fresh | Iso | Sub | Board | What it warrants |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| [CTF-ABACUS v1](https://arxiv.org/abs/2608.26237) | 3 | 3 | 0 | 2 | 1 | 0 | Current-model audit of 1,435 attempts/240 tasks. Full ordered traces are necessary: verified scoring lowered model scores 17.4–22.6%, and no scalar or isolated-command proxy reliably separated earned flags. No public implementation or trace bundle was found at this snapshot. |
| [CTFusion v2](https://arxiv.org/abs/2605.11504) and [official implementation](https://github.com/kaist-hacking/CTFusion) | 3 | 3 | 3 | 3 | 2 | 2 | Closest match: unreleased challenges from five live CTFd events, per-agent state, container isolation, pass@3, cost caps, and submission proxy. It does not test the exact 5.5-hour schedule or finite wrong-submission policy, and the repository does not expose the reported raw run corpus. |
| [DeepRed v1](https://arxiv.org/abs/2604.19354) and [official repository](https://github.com/AISE-TUDelft/DeepRed-LLMAgent) | 3 | 2 | 1 | 3 | 1 | 1 | Clean Kali/target VMs, full logs, three runs per model-task, 60-step cap, and checkpoint progress expose long-horizon failure. Only ten HackMyVM boot-to-root tasks; reused writeups may contaminate models; not a Jeopardy board. |
| [Frontier D-CIPHER factorial study](https://arxiv.org/abs/2604.17159) and [source repository](https://github.com/TATAR-LAB/ctf-agents) | 3 | 3 | 0 | 3 | 1 | 0 | 1,600 controlled runs quantify environment and prompting; ten current models quantify model/tool compatibility. Static NYU content, one trial per configuration, 10-minute independent tasks, no board scheduler. |
| [2025 CSAW autonomy study v2](https://arxiv.org/abs/2603.21551) and [official leaderboard/challenge pointer](https://nyu-llm-ctf.github.io/csaw_llmac.html) | 3 | 3 | 2 | 1 | 1 | 1 | Same-event observational evidence associates autonomous/hybrid workflows with more solves and requires traceable submissions. Agent `n=2`, hybrid `n=3`, participant skill/engineering are confounded, and the event lasted ten days rather than 5.5 hours. |
| [Veria CTF Agent](https://github.com/verialabs/ctf-agent) | 3 | 3 | 3 | 3 | 1 | 3 | First-party live implementation of polling, coordination, per-challenge multi-model racing, insight sharing, Docker toolboxes, and automatic submission; it claims 52/52 and first at BSidesSF 2026. No paper, pinned run configuration, full trajectories, resource ledger, flag provenance, or ablation was published, so the win is not reproducible evidence of which mechanism caused success. |
| [D-CIPHER v2](https://arxiv.org/abs/2502.10931) and [official agent repository](https://github.com/NYU-LLM-CTF/nyuctf_agents) | 2 | 3 | 0 | 3 | 1 | 0 | Planner delegates focused work to fresh-context executors, receives summaries, and replans. Ablations support modest benefit over one executor; old models and static tasks limit absolute transfer. |
| [EnIGMA v3](https://arxiv.org/abs/2409.16165) and [released SWE-agent code](https://github.com/SWE-agent/SWE-agent/tree/v0.7) | 2 | 3 | 1 | 3 | 2 | 0 | Best mechanism evidence for interactive sessions, output control, early-success/stall behaviour, and soliloquizing. Models are now old; per-instance `$3` and unlimited candidate submissions differ from IN-CYPHER. |
| [Cybench paper](https://arxiv.org/abs/2408.08926) and [official benchmark/runner](https://github.com/andyzorigin/cybench) | 2 | 3 | 0 | 3 | 1 | 0 | Forty professional tasks with ground-truth flags, Docker setup, unguided runs, subtasks, and reproducible logs remain useful for category/tool regression. Public 2022–2024 tasks are no longer fresh, and each is evaluated independently. |

No source scores strongly on every column. CTFusion is the best environmental analogue;
CTF-ABACUS is the best evidential correction; DeepRed is the best current long-horizon failure
study; Veria is the best visible board-shaped implementation but the weakest scientific result.

## How successful agents work

### Grounded action loop

EnIGMA implements a ReAct-style thought → action → observation cycle inside Docker. DeepRed gives a
code-writing controller terminal and filtered search access to an isolated Kali VM connected to a
fresh target VM. In both, useful reasoning is conditional on observed command output. CTF-ABACUS's
trace decomposition shows why: execution-backed recoveries progress through reconnaissance,
vulnerability analysis, exploitation, and observation of the flag; unsupported recoveries remain
shallower. Across its corpus, 52% of steps were reconnaissance, 19% vulnerability analysis, 15%
exploitation, and under 2% post-exploitation; writing/running a solve script was the dominant
exploitation technique.

**IN-CYPHER inference:** preserve ordered `Taken`/`Step` evidence and the files/scripts they create.
A later summary is useful navigation, but cannot replace the action, output, and instance identity
that warrant a flag. CTF-ABACUS measured only 0.491–0.586 AUC from tool-composition summaries and at
most 0.617 from its best semantic summary feature.

### Planning and focused contexts

D-CIPHER separates a persistent Planner from Executors that each receive a narrow delegated task
and fresh conversation. Executor summaries return to a re-planning loop. Against the same older
models and static tasks, removing the Planner cost 1–5 solve-rate points; however, using weaker
Executors cost much more. In the newer factorial study, Gemini Pro/Pro solved 52%, Pro/Flash 28.5%,
Flash/Flash 27%, and Flash/Pro 23.5%. A strong planner did not make a weak worker strong, and a
strong worker could not rescue weak planning.

This supports scope isolation and concise handoffs, not arbitrary agent count. The CSAW study also
observed lightweight tool-augmented loops and reflection retries were more common than elaborate
multi-agent systems among beginner builders.

### Interactive and category-appropriate tools

EnIGMA keeps one nested interactive session (GDB or a pwntools connection) alive without blocking
the main shell. Its full configuration could solve a reverse-engineering task that failed without
interactive tools or summarization. Across four benchmarks, removing interactive tools reduced
aggregate solve rate 2.1 points and removing the LM summarizer 1.3 points; removing demonstrations
cost 6.2 points. Effects were not uniform: interactive tools helped crypto, pwn, and rev but hurt
web because the interface lacked a suitable browser, while demonstrations hurt web and misc.

The 2026 factorial study sharpens this: Kali plus runtime-queryable tool documentation improved
Gemini 3 Pro from the Ubuntu baseline by 9.5 points, while category tips and automatic pre-analysis
often hurt. Under Kali/generic prompts, AutoPrompt fell from 52% to 39.5%. Rich environment plus a
simple prompt beat speculative preliminary analysis.

### Verification loop

The trustworthy loop is not merely “submit and see whether green.” CTFusion starts each agent with
its own unsolved view, brokers candidate flags, forwards at most the first correct flag per
challenge to the shared live account, and validates later agents locally. CSAW accepted a solution
only when its logs connected reasoning to action to output. CTF-ABACUS is stricter: locate the first
flag appearance, locate the preceding demonstrated exploit, and distinguish target observation
from exposure, lookup, recall, guess, or unsupported reasoning.

**IN-CYPHER inference:** before spending a scarce official submission, require a format check plus
an evidence pointer to the exact observation or deterministic local derivation. When practical,
rerun the minimal solve procedure against the same isolated Instance. A server rejection is useful
feedback, but a finite submission pool makes it an expensive verifier of last resort.

## Where agents fail

- **Stall after early progress.** EnIGMA reports most wins inside 20 steps and budget exhaustion on
  failures. DeepRed likewise reports agents getting lost after roughly 20 steps despite a 60-step
  limit, repeating failed commands, starting over, and neglecting persisted intermediate results.
- **Long-horizon state synthesis.** DeepRed's strongest model averaged 35% checkpoint completion;
  models often found a foothold but searched for the flag without privilege escalation. Common
  attack patterns transferred better than unusual discovery, SSH-key handling, obfuscation, or
  service-specific paths. Higher token use did not predict better completion.
- **Hallucinated environment state.** EnIGMA observed “soliloquizing”: the model emitted invented
  observations without tool execution. D-CIPHER recorded nonexistent servers/files/functions and
  malformed tool calls. Errors sometimes corrected the model, but often diverted the run.
- **Interface mismatch.** Agents send GDB input to a non-interactive shell, mishandle large binary
  output, or wait indefinitely on remote utilities. Explicit session lifecycle, non-blocking I/O,
  timeouts, and raw-output retention are capability features.
- **Infrastructure and containment failures.** CTFusion saw more execution/infrastructure failures
  in D-CIPHER than EnIGMA. DeepRed reports a target shutdown that left its agent running on the
  host, where it scanned other systems until stopped. Clean Instances and process-tree/egress
  boundaries must survive failure, not only happy-path setup.
- **Benchmark shortcuts.** CTFusion's search-enabled agent retrieved writeups and even installed a
  package exposing benchmark flags. CTF-ABACUS attributes only 72.9% of 1,056 recovered flags to
  executed attacks, 10.9% to human-verified derivation, and 16.2% to unsupported pathways. Direct
  flag exposure was 8.9 times more common than recall/external lookup, so freshness alone does not
  replace provenance checks.

## Autonomy, context, and parallelism

**Autonomy.** In the 2025 CSAW standard track, 17 HITL teams averaged 2.7 solves, two autonomous
teams 5.5, and three hybrid teams 7.7. Pwn averages were 5%, 20%, and 27% respectively. This is
useful live evidence that repeatable tool loops can outperform manual prompting, but it is not a
causal ablation: security experience, engineering capacity, and sample sizes differ. It warrants
building autonomous execution and evidence collection; it does not warrant predicting a multiplier.

**Context.** Concise context can help, but lossy summaries can destroy proof. EnIGMA's LM summary
beat no summary by 1.3 points; its simple window/file summary was 2.6 points below the LM summary.
DeepRed required ≥250k model context, chunked long judging traces, and still observed forgotten
state. D-CIPHER's fresh Executor contexts plus structured summaries reduced overload, but the
Planner must retain authoritative state. Keep raw output out-of-band, promote only verified facts
into compact handoffs, and allow retrieval back to the original bytes.

**Parallelism.** Three different mechanisms need separation:

- parallel benchmark workers (CTF-Dojo/EnIGMA+) reduce evaluation wall time, not solve difficulty;
- planner/executor decomposition improves focus within one challenge but adds coordination cost;
- board and per-challenge racing, as implemented by Veria, increases simultaneous search and can
  take the first flag found, but multiplies model calls, tool processes, duplicate work, and shared
  quota pressure.

There is no controlled evidence for the last mechanism under IN-CYPHER's constraints. The safe
planning conclusion is bounded independent Lanes with a common evidence/submission broker,
stagnation cuts, and deterministic allocation telemetry. Cross-lane “insights” should be typed
observations with provenance; unconstrained transcript sharing recreates context overload and can
propagate a hallucination across every solver.

## Implications for a 5.5-hour Board

These are evidence-backed planning constraints, not a completed design:

- Allocate time across Challenges explicitly. Independent per-task pass@1 says nothing about
  ranking a Board; every retained source except CTFusion and Veria omits that problem.
- Prefer short, checkpointed attempts. EnIGMA's early-success/stall transition and the frontier
  study's ten-minute evaluation horizon are starting evidence for later Cut calibration, not a
  selected Step or time threshold; Category-specific Runs must choose the dials.
- Treat early reconnaissance as progress, not proof. Persist discoveries, scripts, credentials,
  offsets, endpoints, and failed hypotheses so the next Attempt does not restart blindly.
- Give each Lane a clean attacker workspace and Instance identity; broker Board credentials and
  official submissions outside challenge-controlled processes.
- Separate candidate discovery, local verification, and official submission. Enforce the event's
  finite-submission counter centrally and never let parallel workers race the live endpoint.
- Retain complete action/observation evidence even when the working context is summarized. A flag
  without a target observation or reproducible derivation should not be promoted as Captured.
- Measure parallelism on the practice Board: flags/clock, unique checkpoint progress, duplicate
  spend, quota throttling, stalled-Lane reclamation, invalid submissions, and provenance-qualified
  solves. Existing publications cannot choose the Lane count.

## Limits of this research

No study jointly reproduces IN-CYPHER's exact categories, newly released content, isolated per-team
Instances, finite submission budget, shared model quota, and 5.5-hour whole-Board objective.
Static-benchmark absolute scores are contaminated or exposure-prone; model generations change too
quickly to port rankings. CTFusion is live but reports point estimates across five events and no raw
run corpus. CTF-ABACUS is very recent and publishes no located code/trace artefact. DeepRed is only
ten boot-to-root tasks. The frontier factorial study is single-trial and static. CSAW's autonomy
groups are tiny and confounded. Veria's live win is a first-party claim backed by inspectable code,
not a reproducible Run.

Accordingly, the durable evidence is mechanistic: grounded tool feedback, compatible interactive
interfaces, bounded retries, concise recoverable context, clean isolation, and trace-level
verification. Model rankings, precise solve rates, optimal attempt length, and optimal parallelism
must be re-measured on the practice Board and then on official fresh content.
