# USAGE_AI.md — AI Assistance Log

Log of AI-made code changes during the Open Payments Hackathon Medellín 2026.
Tool: Claude Code (Claude Opus 5.5), advisory role (see `CLAUDE.md` §3).

Only code changes to project scripts are logged. Questions, explanations and suggestions the team applied themselves are not.

**Intervention levels:** `Full Script` · `Major Refactor` · `Minor Edit / Few Lines` · `Typo / Syntax Correction`

| Date | File Path | Intervention Level | Description |
|------|-----------|--------------------|-------------|
| 2026-10-16 | src/interfaces/open_payment.py | Minor Edit / Few Lines | Added `request_loan_grant` (outgoing-payment grant on the borrower's AS, limited to the lender's incoming payment and the total debt) and `continue_loan_grant` (returns access token, manage URL, expiry, continue URI/token). Fixed `expiresAt` key and kwargs in `create_incoming_payment`. |
| 2026-10-16 | src/app.py | Minor Edit / Few Lines | Added `POST /loans` (lender incoming payment with expiry + borrower loan grant), `GET /callback/loan/{loan_id}` (hash check, continue grant, store tokens) and `GET /loans/{loan_id}` (loan status without secrets). |
| 2026-10-16 | src/models.py | Typo / Syntax Correction | Updated `borrower_wallet` comment: the borrower now uses their own wallet (no app-owned wallet). |
| 2026-10-16 | src/app.py | Minor Edit / Few Lines | Replaced SDK `HashManager` with `verify_interaction_hash` in the loan callback: Rafiki hashes the tenant grant URL without a trailing slash, the SDK adds one, so every valid callback was rejected. |
