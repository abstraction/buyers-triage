# buyers-triage

An automated pipeline for researching, scoring, and ranking Android phones for sale-day purchases using web search and Gemini via the `agy` CLI.

## Background

When evaluating hardware candidates, pairwise tournament brackets (putting phones head-to-head in single elimination) suffer from the "group of death" problem: strong contenders often knock each other out in early rounds.

This pipeline avoids brackets. It extracts verifiable hardware data for each candidate, evaluates them independently against an objective 100-point rubric, and sends the top survivors to a simulated 3-person technical board for final debate and ranking.

## How to Re-Run (Quick Reference)

To run this again for a future purchase cycle:

1. **Update candidates**: Add current models to `config/phone_list.txt`.
2. **Set budgets and criteria**: Adjust `TARGET_BUDGET`, `CEILING_BUDGET`, and point weights in `src/research_pipeline.py`.
3. **Run the pipeline**:
   ```bash
   uv run ./research_pipeline.py
   ```
4. **Check output**: Read the debate transcript and ranking table in `results/ULTIMATE_RANKING.md`.

### Useful Commands

```bash
# Check current state of all phones
uv run ./research_pipeline.py --status

# Re-run extraction for one phone
uv run ./research_pipeline.py --reset-phase2 "Google Pixel 10a"

# Re-run scoring without re-extracting specs
uv run python calculate_scores.py

# Inspect spec summary in terminal
uv run python analyze_phones.py
```

## How It Works

The script processes candidates through five stages:

1. **Phase 1 (Reality Gate)**: Checks availability in India, confirms official warranty, checks SoC whitelist, and verifies storage speed (UFS 3.1 minimum).
2. **Phase 2 (Spec Extraction)**: Uses `agy` and web search to extract unmarketed hardware details: camera sensor part numbers, GCam/Camera2 API access, proximity sensor type (physical vs. virtual), and background app killing behavior (`dontkillmyapp` score, Tasker non-root survival).
3. **Phase 2.5 (Pruning)**: Drops phones that exceed the price ceiling or block third-party camera access.
4. **Phase 2.7 (Triage Scorecard)**: Scores each phone out of 100 points:
   - Camera hardware & GCam support (50 pts)
   - Background service survival (10 pts)
   - Physical hardware quality (15 pts)
   - SoC tier and RAM standard (25 pts)
5. **Phase 3 (Technical Board Debate)**: Sends the top-scoring survivors to a simulated technical review board (Photography, Background Services, Audio/Hardware) to argue trade-offs and produce a final recommendation with fallback choices.

## Adapting to Other Hardware

The pipeline is currently tailored to Android phones, but the five-stage structure is generic:

`Filter availability -> Extract specs via web -> Hard prune dealbreakers -> Score via rubric -> Debate trade-offs`

To adapt this to laptops, monitors, or other hardware, replace:
- The input list in `config/`
- The extraction fields and prompt in Phase 2
- The scoring weights in `calculate_triage_score`
- The board member personas in Phase 3

## Requirements

- Python 3.11+
- `uv`
- `agy` CLI in PATH
