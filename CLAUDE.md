# CLAUDE.md — Hackathon Open Payments NPI

## 1. Project Summary

**Event:** Open Payments Hackathon — Medellín 2026
- **Dates:** Oct 16–17, 2026, 8:30–18:00, Ruta N, Medellín (in person)
- **Theme:** make payments easier, simpler, or more accessible; real impact, financial inclusion, fintech innovation
- **Awards** (one per team max): Impact (Ruta N), Inclusion (Puntored), Innovation (FIND EAFIT), Community (This Week in Fintech)
- **Submission:** Google Slides template uploaded to the shared folder (see the event PDFs)
- **Rules that matter:**
  - AI use is governed by the event guidelines, so AI-made code changes are logged in `USAGE_AI.md`.

**Repo:** https://github.com/camilocossio11/hackathon-open-payments-npi (local: `hackathon-open-payments-npi/`). Migrated from `gzuluagal/npi-hackathon-interledger` on 2026-10-16.

**What we're building:** **Abbo**, a sales-based microloan app for tiendas de barrio (concept doc: `docs/Hackaton/cuota-viva-hackathon-mvp.md`, written under the old working name Cuota Viva). A share of each digital sale repays the tendero's loan to the bank over ILP, within a daily cap the tendero approves once in their own wallet.

**Roles:** Abbo is the Open Payments client (its own wallet `https://ilp.interledger-test.dev/abbo` and the only developer key). The tendero's wallet pays and the bank's wallet receives. Neither needs a key.

**Architecture overview (Open Payments flow):**
```
Client ──► Sender wallet address  (GET wallet address → auth server + resource server URLs)
       ──► Receiver wallet address
1. Grant (non-interactive)  → incoming-payment access on the receiver's AS
2. Create Incoming Payment  on the receiver's RS
3. Grant (non-interactive)  → quote access on the sender's AS
4. Create Quote             on the sender's RS (debit/receive amounts, FX)
5. Grant (INTERACTIVE)      → outgoing-payment access; the user approves at the redirect URL
6. Continue grant           → finalized access token
7. Create Outgoing Payment  on the sender's RS → ILP settles
```
- Auth: GNAP grants and HTTP Message Signatures (Ed25519 key pair; the key ID comes from the test wallet's developer keys).
- Amounts: `{value, assetCode, assetScale}`. The value is an integer string, so 10.00 USD = `"1000"` with scale 2.

**Test environment:**
- Test Wallet: https://wallet.interledger-test.dev/auth/login
- Wallet addresses: `https://ilp.interledger-test.dev/<name>`. Put the real ones in `.env`, never in this file.
- Developer keys: Test Wallet → Settings → Developer Keys (private key + key ID)
- Spec/docs: https://openpayments.dev

**App:** FastAPI service in `src/` (`app.py`, `models.py`, `interfaces/`). Run with `uv run uvicorn app:app --app-dir src`, or the VS Code "FastAPI" launch config.

**SDK:** `open-payments-sdk` (import `open_payments_sdk`), installed from GitHub via `[tool.uv.sources]`: https://github.com/interledger/open-payments-python-sdk. It is not on PyPI. Known model mismatches with the test network (e.g. `updatedAt`) are patched at runtime. Verify behaviour against the SDK source, not assumptions.

## 2. Development & Linting Rules

```bash
uv sync                 # install from uv.lock
ruff check .            # lint
ruff check . --fix      # lint + autofix
ruff format .           # format
ruff format --check .   # CI-style check, no writes
```
- Run `ruff check` and `ruff format --check` before every commit.
- Secrets (private keys, key IDs, wallet addresses) live in `.env` and are git-ignored. Never commit `*.pem` or `*.key`.

## 3. Agent Boundaries

- Claude is an **advisor/consultant**: concepts, architecture review, brainstorming, debugging help, and frontend support when asked.
- Claude is **not** the primary builder, executor, or lead designer. The team owns core implementation and decisions.
- Claude doesn't write core pipeline code unless the team explicitly asks for it.
- Log in `USAGE_AI.md` only when Claude changes code in project scripts (file, intervention level, description). Q&A and suggestions are not logged.
- Claude keeps this file updated as the project evolves.

## 4. Task Backlog

### Milestones
- [ ] **Pre-event (until Oct 15):** test wallet accounts and developer keys for each member; local SDK installed and importable; initial client that tests the workflow end to end; read the event AI guidelines and submission PDFs
- [ ] **Day 1 AM:** define the problem statement and target user (inclusion/impact angle)
- [ ] **Day 1:** end-to-end happy path wallet A → wallet B on the test network (steps 1–7 above)
- [ ] **Day 1 PM:** handle the interactive grant redirect and callback in the client
- [ ] **Day 2 AM:** use-case layer and UI on top of the pipeline
- [ ] **Day 2 PM:** demo script, Google Slides submission, final `USAGE_AI.md` pass

### Active Ideas
- Abbo: one standing outgoing-payment grant on the tendero's wallet (`debitAmount` = daily cap, `interval` P1D, `receiver` = the bank's per-loan incoming payment), then one quote and one outgoing payment per sale.

### Open Payments Implementation Notes
- The outgoing payment grant **requires user interaction**. Plan the UX for the redirect and approval step early because it's the usual blocker.
- Quotes expire. Create the outgoing payment soon after quoting.
- Incoming payments can be open (no `incomingAmount`) or fixed. Pick one per use case.
- Check `assetCode`/`assetScale` on both wallets. Cross-currency transfers go through the quote.
- Keep grant access tokens per resource and per wallet. Tokens can be rotated or revoked.
