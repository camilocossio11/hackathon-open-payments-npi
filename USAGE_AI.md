# USAGE_AI.md — AI Assistance Log

Log of AI-made code changes during the Open Payments Hackathon Medellín 2026.
Tool: Claude Code (Claude Opus 5.5), advisory role (see `CLAUDE.md` §3).

Only code changes to project scripts are logged. Questions, explanations and suggestions the team applied themselves are not.

**Intervention levels:** `Full Script` · `Major Refactor` · `Minor Edit / Few Lines` · `Typo / Syntax Correction`

| Date | File Path | Intervention Level | Description |
|------|-----------|--------------------|-------------|
| 2026-10-16 | `src/interfaces/open_payment.py` | Minor Edit / Few Lines | Changed the list incoming payments grant actions from `list-all`/`read-all` to `list`/`read`, because the `-all` actions need an interactive grant (the auth server returned 400). |
| 2026-10-16 | `src/interfaces/open_payment.py` | Minor Edit / Few Lines | Patched `PaymentListQuery` at runtime so `walletAddress` is sent as the `wallet-address` query parameter that the resource server expects. |
| 2026-10-16 | `src/interfaces/open_payment.py` | Minor Edit / Few Lines | Made the list incoming payments grant interactive (`list-all`/`read-all` + `interact` redirect), so it returns payments from every sender. Added a generic `continue_grant` and changed `get_incoming_payments` to take the access token as a parameter. |
| 2026-10-16 | `src/app.py` | Minor Edit / Few Lines | `GET /payments/list/incoming` now returns an approval `redirect_url` (or the payments, if a cached token still works). Added the `GET /callback/list-incoming/{request_id}` callback, which continues the grant, caches the token per wallet and returns all incoming payments. |
| 2026-10-16 | `src/interfaces/helper.py`, `src/interfaces/open_payment.py` | Minor Edit / Few Lines | Added `load_last_date()`, which reads `last_date` from `checkpoint.json` (returns `None` if the file or the key is missing), and used it to initialize `last_date` in `get_incoming_payments`. |
| 2026-10-16 | `src/interfaces/helper.py`, `src/interfaces/open_payment.py` | Minor Edit / Few Lines | Added `save_last_date()` and called it at the end of `get_incoming_payments` to save the latest received date to `checkpoint.json` (only when payments were found). |
| 2026-10-16 | `src/interfaces/helper.py` | Minor Edit / Few Lines | Suggested the `compute_loan_payment` logic: treat 0% as a valid share (`is not None`) and round `to_pay_loan` down to the asset scale. Claude first wrote it as a method in `src/models.py`; the team then moved it into `helper.py` as a standalone function. `get_info_from_incoming_payment` now passes plain `str`/`int` (`.root`) for the asset code and scale. |
