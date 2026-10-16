# USAGE_AI.md — AI Assistance Log

Log of AI-made code changes during the Open Payments Hackathon Medellín 2026.
Tool: Claude Code (Claude Opus 5.5), advisory role (see `CLAUDE.md` §3).

Only code changes to project scripts are logged. Questions, explanations and suggestions the team applied themselves are not.

**Intervention levels:** `Full Script` · `Major Refactor` · `Minor Edit / Few Lines` · `Typo / Syntax Correction`

| Date | File Path | Intervention Level | Description |
|------|-----------|--------------------|-------------|
| 2026-10-16 | `src/interfaces/open_payment.py` | Minor Edit / Few Lines | `create_quote`: added optional `debit_amount` to create a fixed-send quote (`QuoteFixedSent`), and a comment explaining why `model_construct` is used (SDK `check_path` validator sets `receiver` to `None`). |
