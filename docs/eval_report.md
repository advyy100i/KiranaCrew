# Evaluation report — 2026-09-18

Tables are generated: `python eval/make_report.py > docs/eval_tables.md` from `eval/results/*.json`
(each JSON records dataset, parser, provider, model, date, metrics, per-field, per-category and every miss).
Metric definitions are in the plan §11 and `backend/app/cli/eval.py`. The number defended is the **false-update rate**:
rows where the system committed but the gold said ask/reject, or committed different values.

## Datasets

| set | rows | provenance |
|---|---|---|
| `eval/dataset.jsonl` | 130, 21 categories | shipped with the reference core; written *alongside* the rules, so 100% here is a consistency check, not accuracy |
| `eval/heldout.jsonl` | 55, 11 categories | written on 2026-09-18 by reading sentences, **not** by running the parser; adversarial by design: name traps (Kumari/Kumar, Sunil/Sunita), sign traps (money *to* a customer), two-transaction sentences, per-unit vs total price, Hindi number words (`ek sau bees`, `sawa sau`, `paune do sau`), word order |

The held-out set found real bugs in the shipped core before any LLM was involved (see "What the held-out set caught").

## Results

See [`eval_tables.md`](eval_tables.md) for the full tables (per parser × dataset, plus every miss). Summary:

| parser mode | held-out e2e | dataset e2e | false updates (both sets) | p50 latency | needs network |
|---|---|---|---|---|---|
| rules only | 100% (55/55) | 100% (130/130) | **0** | 0 ms | no |
| LLM only — local `qwen2.5:3b` (Ollama, GPU) | 98.2% | 96.2% | **0** | ~5 s | no |
| LLM only — hosted `qwen/qwen3.8-27b` (Groq) | 98.2% | 100% | **0** | ~5 s* | yes |
| hybrid — rules → local LLM | 100% | 99.2% | **0** | 0 ms (p95 5.4 s) | no |
| hybrid — rules → hosted LLM | 100% | 100% | **0** | 0 ms (p95 6.1 s) | yes |

\* Groq p50 is inflated by free-tier throttling (1,000 output tokens/min → ~10 calls/min with the built-in backoff); a single
unthrottled call is 0.6–1.0 s.

Hybrid sends 13/55 and 16/130 sentences to the LLM (24% / 12%); the rest commit deterministically at zero cost. Every
LLM-mode miss in the tables is an over-cautious *ask* (`MISSING_QUANTITY`, `UNKNOWN_PRODUCT`), never a wrong commit.

Model choice: `qwen2.5:3b` (2.4 GB VRAM, ~5 s/call on an RTX 3050) is good enough for extraction once the grounding and
role guards are in place; the 27B hosted model is only marginally better and costs the offline property. Groq retired
the plan's `llama-3.3-70b-versatile`; the `gpt-oss` models fail Groq's JSON-mode validation on this prompt.

## What made the LLM safe (each was a measured false update before the fix)

The first pure-LLM run of the local model scored 61.8% e2e with **2 false updates**; the hosted model had 1. All were
the same class of error — *grounding proves a number exists, not what it means* — and were removed by three
deterministic rules in `app/domain/parser.py`, applied after the LLM and before `decide()`:

1. **Rules own number roles.** `55 rupaye kilo` is a unit price; a model filing 55 under `total_amount_rupees` passes
   grounding and books ₹55 instead of 2 × ₹55. When the LLM assigns a different role to a number the rules matched by
   literal pattern, the rules' role wins. (HO16, HO54)
2. **Rules own the transaction type.** `kharide` is a purchase, `zyada nikla` is found stock, and "paise baad mein dega"
   is not a payment mode. If the rules found a type from keywords, the LLM cannot overrule it; the LLM may assert
   CREDIT/CASH only if the keyword is in the text, otherwise it becomes SALE → "udhaar ya cash?". (HO41, PU04, AD08)
3. **Rules safety flags survive the LLM path.** `MULTIPLE_ITEMS` / `EXTRA_NUMBERS` / `UNSUPPORTED` from the rules apply
   regardless of what the LLM returned; with `EXTRA_NUMBERS` every numeric field is nulled (a question, not a guess).

Plus the plan's grounding check (numbers must appear in the normalized text; mentions must fuzzy-match a span ≥ 80),
string coercion for small models that write `"5 kg"` in a numeric field, and the `MULTIPLE_ITEMS` flag taking precedence
over a null type.

## What the held-out set caught in the shipped core (rules only)

| bug | example | before | after |
|---|---|---|---|
| number composition | `ek sau bees` (120) | `100 20` → SALE, quantity 100 | 120 |
| thousands separators | `1,000 rupaye` | `1 0 rupaye` | 1000 |
| `sawa` before a unit / `sau` | `sawa kilo`, `sawa sau` | untouched / `sawa 100` | 1.25 kg / 125 |
| lakh | `10 lakh rupaye` | lost | 1,000,000 → CONFIRM_LARGE_AMOUNT |
| fuzzy customer auto-accept | `Ramesh Kumari ne …` | **committed to Ramesh Kumar** (false update) | asks, offers 3 candidates |
| phonetic spelling | `Raamesh Kumaar`, `Sunitha Devi` | UNKNOWN | resolved via skeleton match |
| two transactions in one sentence | `… udhaar liya aur 100 rupaye chukaye` | **committed** with ₹100 as the sale total | REJECT MULTIPLE_ITEMS |
| two actors | `Rakesh ne 3 kilo … aur Ramesh ne 2 kilo` | **committed** 3 kg to Rakesh | REJECT MULTIPLE_ITEMS |
| money *to* a customer | `Ramesh ko 500 rupaye udhaar diye` | **committed as a repayment** (sign inverted) | REJECT UNSUPPORTED |
| unit mismatch | `2 kg sabun` | MISSING_QUANTITY | UNIT_MISMATCH (asks "kitne pc?") |
| per-unit price phrasing | `55 ke bhav se` | ignored → catalog price | EXPLICIT_UNIT |
| zero quantity | `0 kilo chawal` | **crash** (pydantic) | MISSING_QUANTITY |
| stray numbers | `5 5 5 kilo chawal` | committed 5 kg | EXTRA_NUMBERS → asks |

Five of these were silent false updates. The shipped README's "100% on its own dataset" was accurate and meaningless.

## STT

Only one clip exists so far (`eval/audio/tts_credit_sale.wav`, synthesized with Windows TTS), so the STT table proves the
plumbing, not accuracy — the `initial_prompt` contains that exact sentence. On this laptop:

| provider | model | latency per ~3 s clip | notes |
|---|---|---|---|
| faster-whisper (CPU int8) | `small` | 2.5 s (load 70 s incl. download) | `large-v3-turbo` int8 needs ~1.5 GB RAM, which this machine rarely has free |
| Groq | `whisper-large-v3-turbo` | 0.8 s | free tier; `verbose_json` gives `avg_logprob` / `no_speech_prob` for the confidence gate |

**To do before trusting STT numbers:** record 40 clips (8 per type, 3–4 speakers, real room), write human transcripts
to `eval/audio/transcripts.jsonl`, run `python -m app.cli.stt_bench --providers faster_whisper,groq` per model size,
and put CER / CER-after-normalize / p50 in the table above. `CER after normalize` is the number that matters: `kilo`
vs `kg` is not an error for this pipeline.

## Forecasting (StatsForecast, nightly job)

12 weeks of synthetic daily sales with weekly seasonality (seed), rolling-origin backtest, 3 folds × 7 days:

| product | AutoETS MASE | SeasonalNaive MASE | shown |
|---|---|---|---|
| Rice | 0.902 | 1.209 | AutoETS |
| Sugar | 0.652 | 0.947 | AutoETS |
| Atta | 0.649 | 0.671 | AutoETS (barely) |
| Toor Dal | 0.568 | 0.743 | AutoETS |
| Mustard Oil | 0.880 | 1.126 | AutoETS |
| Refined Oil | 0.815 | 1.073 | AutoETS |
| Milk | 0.692 | 0.817 | AutoETS |
| Salt | 0.921 | 1.318 | AutoETS |
| Soap | 0.770 | 0.914 | AutoETS |
| Biscuit | 0.761 | 0.884 | AutoETS |
| Eggs | 0.896 | 1.219 | AutoETS |

The model wins everywhere **because the seed has clean weekly seasonality plus Gaussian noise** — that is a property of
the generator, not evidence. The honest statement: the pipeline shows a forecast only where `model_mase < baseline_mase`,
and on real shop data expect the baseline to win for slow movers. Moong Dal has no price, so no sales, so no forecast.

## Reproduce

```powershell
cd backend
python -m app.cli.eval --dataset ..\eval\heldout.jsonl --catalog ..\eval\seed_catalog.json --parser rules --errors
python -m app.cli.eval --dataset ..\eval\heldout.jsonl --catalog ..\eval\seed_catalog.json --parser hybrid --provider ollama --json ..\eval\results\heldout_hybrid_ollama_qwen2.5-3b.json
python -m app.cli.eval --dataset ..\eval\heldout.jsonl --catalog ..\eval\seed_catalog.json --parser llm --provider groq --model qwen/qwen3.8-27b
python ..\eval\make_report.py > ..\docs\eval_tables.md
```

CI runs the rules mode on both sets and fails if the false-update rate is not exactly 0.0%.
