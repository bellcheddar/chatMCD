# Base model choice

**Decision: `Qwen/Qwen3-8B`.** Chosen by Marc on 2026-09-08 after the measurements
below, on the build plan's own priority order: instruction-following quality at
short context first, latency second, licence third, dual MLX/PEFT support fourth.

## Candidates

| | Qwen3-4B-Instruct-2507 | Qwen3-8B |
|---|---|---|
| Parameters | 4.02 B | 8.19 B |
| Licence | Apache 2.0, ungated | Apache 2.0, ungated |
| Weights | 8.0 GB | 16.4 GB |
| MLX support | yes (`mlx_lm.lora`) | yes (`mlx_lm.lora`) |
| transformers / PEFT support | yes | yes |
| Reasoning block can be disabled | yes | yes |

Llama-3.1-8B-Instruct was not measured. Its licence needs acceptance, which adds
a gated-repo step to every Space build and every fresh checkout for no quality
advantage over Qwen3-8B on this task.

## Measured, locally, on an M1 Max 64 GB

Greedy decoding, a 6-turn history plus system prompt (~320 prompt tokens), median
of three runs.

| | 4B | 8B | ratio |
|---|---:|---:|---:|
| Load | 1.6 s | 3.2 s | |
| Prompt processing | 647.3 tok/s | 364.6 tok/s | 1.78x |
| Time to first token | 0.493 s | 0.886 s | 1.80x |
| Generation | 37.1 tok/s | 21.4 tok/s | 1.73x |

The generation ratio of 1.73x tracks the parameter
ratio, which is what makes these numbers believable.

### A measurement that was wrong, and how it announced itself

The 4B was first measured at **16.6 tok/s** generation and
**0.829 s** to first token, while a 16 GB model download saturated the
machine. Taken at face value that made the 8B *faster than the 4B*, which is not
a thing that happens. Re-measured on a quiet machine, the 4B does
37.1 tok/s: the contended figure was **2.2x** too slow.

Both figures in this table were therefore taken under the same conditions. A
throughput number is only meaningful next to another one measured the same way.

## What is *not* measured here

**Latency on a ZeroGPU H200.** There is no H200 on this machine and no way to
borrow one without renting a GPU, which this project does not do. The local
figures above are a same-architecture ratio between the candidates, not a
prediction of production latency.

The build plan's rule was: pick the 4B if 8B latency exceeds ~2.5 s to first
token. That test can only be run against the live Space, so it stays open:

- [ ] Measure time to first token against the deployed Space, warm and cold, and
      record it here. If the warm figure exceeds 2.5 s, revisit this decision.

An H200 is roughly an order of magnitude faster than an M1 Max at this, so an
8B at 0.886 s locally has a lot of headroom. That is a reason to expect
the test to pass, not evidence that it has.

## Quality

Both candidates were run against the ten sample questions in `train/bakeoff.py`
and, for the 4B, the full 40-question set in `eval/questions.yaml`.

Untuned Qwen3-4B-Instruct-2507 scores **30% overall, facts 25%, honesty 3/3**
(`eval/reports/base-qwen3-4b.md`). The shape of the failures is the same for both
candidates and is exactly what a fine-tune has to fix:

- **It does not know the facts.** "As of now, there is no publicly available,
  verified count of the total number of protein structures that Marc C. Deller
  has solved."
- **It confabulates when it thinks it does know.** Asked why BoltzMaker is called
  BoltzMaker, it explains confidently that the tool is named after the Boltzmann
  constant. It is named after Timothy Taylor's Boltmaker, a beer.
- **It has no manners boundary.** Asked for a poem about cats it writes one, in
  which the cats "stretch like proteins folding into shape".
- **It is already honest.** All three honesty questions pass on the untuned base.
  That bucket is a regression guard, not a target: the risk is that fine-tuning
  on 2,748 confident answers teaches it to be confident about everything.

## Why the 8B

Quality at short context is the first criterion in the plan's own order, and the
depth bucket (WRN synthetic lethality, the CALR binding conformations, the JAK1
P-loop) is where extra capacity should tell. Latency is the second criterion, and
0.886 s to first token on an M1 Max leaves room for the H200 to clear
2.5 s comfortably.

The cost is wall clock here, not there: the training round is roughly twice as
long on this machine. That was the trade accepted.

## Reproducing

```bash
python3 train/bakeoff.py --model Qwen/Qwen3-4B-Instruct-2507
python3 train/bakeoff.py --model Qwen/Qwen3-8B
python3 train/bakeoff.py --report
python3 eval/run_eval.py --backend mlx --model Qwen/Qwen3-8B --label base-qwen3-8b
```

Raw numbers and all twenty sample answers are in `train/bakeoff.json`.
