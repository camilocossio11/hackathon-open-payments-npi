# USAGE_AI.md — AI Assistance Log

Log of AI-made code changes during the Open Payments Hackathon Medellín 2026.
Tool: Claude Code (Claude Opus 5.5), advisory role (see `CLAUDE.md` §3).

Only code changes to project scripts are logged. Questions, explanations and suggestions the team applied themselves are not.

**Intervention levels:** `Full Script` · `Major Refactor` · `Minor Edit / Few Lines` · `Typo / Syntax Correction`

| Date | File Path | Intervention Level | Description |
|------|-----------|--------------------|-------------|
| 2026-10-16 | `src/interfaces/open_payment.py` | Minor Edit / Few Lines | `create_quote`: added optional `debit_amount` to create a fixed-send quote (`QuoteFixedSent`), and a comment explaining why `model_construct` is used (SDK `check_path` validator sets `receiver` to `None`). |
| 2026-10-16 | src/interfaces/open_payment.py | Minor Edit / Few Lines | Added `request_loan_grant` (outgoing-payment grant on the borrower's AS, limited to the lender's incoming payment and the total debt) and `continue_loan_grant` (returns access token, manage URL, expiry, continue URI/token). Fixed `expiresAt` key and kwargs in `create_incoming_payment`. |
| 2026-10-16 | src/app.py | Minor Edit / Few Lines | Added `POST /loans` (lender incoming payment with expiry + borrower loan grant), `GET /callback/loan/{loan_id}` (hash check, continue grant, store tokens) and `GET /loans/{loan_id}` (loan status without secrets). |
| 2026-10-16 | src/models.py | Typo / Syntax Correction | Updated `borrower_wallet` comment: the borrower now uses their own wallet (no app-owned wallet). |
| 2026-10-16 | src/app.py | Minor Edit / Few Lines | Replaced SDK `HashManager` with `verify_interaction_hash` in the loan callback: Rafiki hashes the tenant grant URL without a trailing slash, the SDK adds one, so every valid callback was rejected. |
| 2026-10-16 | `src/interfaces/open_payment.py` | Minor Edit / Few Lines | Restored `create_quote` after the `dev-simon` merge reverted it: quotes from `sender_wallet` again, optional `debit_amount` (`QuoteFixedSent`). |
| 2026-10-16 | `src/interfaces/open_payment.py` | Minor Edit / Few Lines | `request_loan_grant`: the grant now caps `debitAmount` per period (`cap_value`) and sets `limits.interval`; the total stays capped by the lender's incoming payment. |
| 2026-10-16 | `src/app.py` | Minor Edit / Few Lines | Added `build_interval` (ISO 8601 repeating interval from term and period) and passed it to `request_loan_grant` in `POST /loans`; stored the lender's auth server and the interval on the loan. |
| 2026-10-16 | `src/app.py` | Major Refactor | Added `POST /loans/{loan_id}/disburse` (lender → borrower payment via `create_payment_request`, only when the loan is `active`) and `GET /callback/disburse/{loan_id}` (hash check, continue grant, outgoing payment, status `disbursed`). |
| 2026-10-16 | `src/models.py` | Minor Edit / Few Lines | `CreateLoanRequest`: added `period_minutes` (default weekly) and updated the cap comment. |
| 2026-10-16 | `src/app.py` | Minor Edit / Few Lines | Added `count_periods` and `min_cap` (ceil(debt / periods)); `POST /loans` defaults the cap to the minimum, rejects a lower cap with 422, and returns the cap, minimum and interval. |
| 2026-10-16 | `src/models.py` | Typo / Syntax Correction | `CreateLoanRequest.weekly_cap_value` is now optional (defaults to the minimum cap). |
| 2026-10-16 | `src/interfaces/open_payment.py` | Minor Edit / Few Lines | Added `pay_with_grant`: fixed-amount quote from the sender's wallet, then an outgoing payment with an already approved grant token; returns the payment id and the received amount. |
| 2026-10-16 | `src/app.py` | Major Refactor | Added `POST /loans/{loan_id}/sales`: only for `disbursed` loans, skips duplicate `sale_id`s, deduction = 10% of the sale capped at the balance, pays via `pay_with_grant`, lowers the balance by the received amount, sets `paid_off` at 0, returns 502 if the wallet rejects (e.g. period cap reached). |
| 2026-10-16 | `src/models.py` | Minor Edit / Few Lines | Added `SaleRequest` (`sale_id`, `amount_value`). |
| 2026-10-16 | `src/app.py`, `src/interfaces/open_payment.py` | Minor Edit / Few Lines | Temporary `DEBUG` prints in `record_sale` and `pay_with_grant`, plus `debug_payment_status` (reads the outgoing payment and the lender's public incoming payment 3 s after paying) to trace why repayments did not reach the bank. Removed on 2026-10-17. |
