import base64
import hashlib
import hmac
import math
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from interfaces.get_keys import generate_keys, get_key_for_wallet
from interfaces.open_payment import OpenPaymentsInterface
from models import (
    AcceptLoanRequest,
    CreateLoanRequest,
    GenerateKeyRequest,
    InitiatePaymentRequest,
    SaleRequest,
)

load_dotenv()

CALLBACK_BASE_URL = os.getenv("CALLBACK_BASE_URL", "http://localhost:8000/callback")
print(f"[startup] CALLBACK_BASE_URL = {CALLBACK_BASE_URL}")

app = FastAPI()
pending_payments: dict[str, dict] = {}
loans: dict[str, dict] = {}  # in memory for now: lost on restart

ABBO_WALLET_ADDRESS = "https://ilp.interledger-test.dev/abbo"
REPAYMENT_RATE_PERCENT = 10  # default share of each sale that repays the loan


def make_client(sender_wallet: str) -> OpenPaymentsInterface:
    entry = get_key_for_wallet(sender_wallet)
    if entry is None:
        msg = f"No key registered for wallet '{sender_wallet}'. Call POST /keys/generate first."
        raise HTTPException(
            status_code=400,
            detail=msg,
        )
    return OpenPaymentsInterface(
        key_id=entry["kid"],
        private_key_pem=entry["private_key_pem"],
        wallet_address=sender_wallet,
    )


def back_to_ui(return_url: str | None, loan_id: str, event: str, fallback: dict):
    # After a wallet approval, send the browser back to the UI that started it (if any)
    if return_url:
        return RedirectResponse(f"{return_url}?{urlencode({'loan_id': loan_id, 'event': event})}")
    return fallback


def verify_interaction_hash(
    client_nonce: str,
    interact_nonce: str,
    interact_ref: str,
    auth_server: str,
    received_hash: str,
) -> bool:
    # Same as Rafiki: the grant URL goes in without a trailing slash (the SDK's HashManager adds one)
    data = f"{client_nonce}\n{interact_nonce}\n{interact_ref}\n{auth_server}"
    computed = base64.b64encode(hashlib.sha256(data.encode()).digest()).decode()
    return hmac.compare_digest(computed, received_hash)


def count_periods(term_minutes: int, period_minutes: int) -> int:
    # Periods that start within the term; the last one may be partial
    return max(1, math.ceil(term_minutes / period_minutes))


def min_cap(debt: int, term_minutes: int, period_minutes: int) -> int:
    # Smallest cap per period that still lets the debt be repaid within the term
    return math.ceil(debt / count_periods(term_minutes, period_minutes))


def build_interval(start: datetime, term_minutes: int, period_minutes: int) -> str:
    # ISO 8601 repeating interval, e.g. R4/2026-10-17T05:00:00Z/P7D: the grant's cap resets
    # every period and the grant stops after the last period
    count = count_periods(term_minutes, period_minutes)
    period = f"P{period_minutes // 1440}D" if period_minutes % 1440 == 0 else f"PT{period_minutes}M"
    return f"R{count}/{start:%Y-%m-%dT%H:%M:%SZ}/{period}"


# @app.post("/payments/initiate")
# def initiate_payment(req: InitiatePaymentRequest):
#     client = make_client(ABBO_WALLET_ADDRESS)
#     redirect_url, outgoing_grant, sender_resource_server, nonce, quote_id = (
#         client.create_payment_request(
#             receiver_wallet=req.receiver_wallet,
#             amount_value=req.amount_value,
#             order_id=req.order_id,
#             callback_base_url=CALLBACK_BASE_URL,
#             sender_wallet=req.sender_wallet
#         )
#     )

#     pending_payments[req.order_id] = {
#         "outgoing_grant": outgoing_grant,
#         "sender_resource_server": sender_resource_server,
#         "sender_wallet": req.sender_wallet,
#         "nonce": nonce,
#         "quote_id": quote_id,
#     }

#     return {"redirect_url": str(redirect_url), "order_id": req.order_id}


# @app.get("/callback/{order_id}")
# def payment_callback(
#     order_id: str,
#     interact_ref: str = Query(...),
#     hash: str = Query(...),
# ):
#     state = pending_payments.pop(order_id, None)
#     if state is None:
#         raise HTTPException(
#             status_code=404, detail=f"No pending payment for order '{order_id}'"
#         )

#     client = make_client(ABBO_WALLET_ADDRESS)
#     outgoing_token = client.continue_outgoing_payment_grant(
#         outgoing_grant=state["outgoing_grant"],
#         interact_ref=interact_ref,
#     )
#     payment_id = client.create_outgoing_payment(
#         resource_server=state["sender_resource_server"],
#         outgoing_token=outgoing_token,
#         quote_id=state["quote_id"],
#         sender_wallet=state["sender_wallet"]
#     )

#     return {"status": "completed", "payment_id": str(payment_id), "order_id": order_id}


def set_status(loan: dict, status: str) -> None:
    # Both UIs poll the loan, so each change is kept with its time for their timelines
    loan["status"] = status
    loan.setdefault("history", []).append({"status": status, "at": datetime.now(UTC).isoformat()})


def get_loan_in(loan_id: str, *statuses: str) -> dict:
    loan = loans.get(loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail=f"Loan '{loan_id}' not found")
    if loan["status"] not in statuses:
        raise HTTPException(
            status_code=409,
            detail=f"Loan '{loan_id}' is '{loan['status']}': expected one of {', '.join(statuses)}",
        )
    return loan


@app.post("/loans")
def create_loan(req: CreateLoanRequest):
    # The lender's offer: only the terms. Nothing happens in the wallets until the borrower accepts.
    if req.loan_id in loans:
        raise HTTPException(
            status_code=409, detail=f"Loan '{req.loan_id}' already exists"
        )

    # The cap must let the debt be repaid within the term; default to the minimum
    minimum_cap = min_cap(int(req.amount_value), req.term_minutes, req.period_minutes)
    if req.weekly_cap_value is None:
        cap = minimum_cap
    elif int(req.weekly_cap_value) < minimum_cap:
        raise HTTPException(
            status_code=422,
            detail=f"Cap {req.weekly_cap_value} is too low: repaying {req.amount_value} in "
            f"{count_periods(req.term_minutes, req.period_minutes)} periods needs at least {minimum_cap}",
        )
    else:
        cap = int(req.weekly_cap_value)

    loans[req.loan_id] = {
        "borrower_wallet": req.borrower_wallet,
        "lender_wallet": req.lender_wallet,
        "debt": req.amount_value,
        "principal": req.principal_value or req.amount_value,
        "repayment_rate_percent": req.repayment_rate_percent,
        "lender_return_url": req.lender_return_url,
        "balance": req.amount_value,
        "weekly_cap": str(cap),
        "min_cap": str(minimum_cap),
        "term_minutes": req.term_minutes,
        "period_minutes": req.period_minutes,
    }
    set_status(loans[req.loan_id], "offered")
    return public_view(req.loan_id)


@app.post("/loans/{loan_id}/reject")
def reject_loan(loan_id: str):
    set_status(get_loan_in(loan_id, "offered"), "rejected")
    return public_view(loan_id)


@app.post("/loans/{loan_id}/accept")
def accept_loan(loan_id: str, req: AcceptLoanRequest):
    # "waiting_approval" is allowed so the borrower can get a fresh approval link
    loan = get_loan_in(loan_id, "offered", "waiting_approval")
    client = make_client(ABBO_WALLET_ADDRESS)
    borrower = client.discover_receiver_servers(loan["borrower_wallet"])
    lender = client.discover_receiver_servers(loan["lender_wallet"])

    start = datetime.now(UTC)
    term_end = start + timedelta(minutes=loan["term_minutes"])

    # 1. The loan's incoming payment on the lender's wallet: accepts at most the debt, until term_end
    incoming_token = client.request_incoming_payment_grant(loan["lender_wallet"])
    lender_incoming_url, _ = client.create_incoming_payment(
        receiver_wallet=loan["lender_wallet"],
        resource_server=lender["resource_server"],
        access_token=incoming_token,
        amount_value=loan["debt"],
        asset_code=lender["asset_code"],
        asset_scale=lender["asset_scale"],
        expires_at=term_end,
    )

    # 2. The capped permission on the borrower's wallet (approved once, interactively)
    nonce = secrets.token_urlsafe(16)
    interval = build_interval(start, loan["term_minutes"], loan["period_minutes"])
    redirect_url, pending_grant = client.request_loan_grant(
        borrower_wallet=loan["borrower_wallet"],
        lender_incoming_url=lender_incoming_url,
        cap_value=loan["weekly_cap"],
        interval=interval,
        asset_code=borrower["asset_code"],
        asset_scale=borrower["asset_scale"],
        finish_uri=f"{CALLBACK_BASE_URL}/loan/{loan_id}",
        nonce=nonce,
    )

    loan.update(
        {
            "borrower_auth_server": borrower["auth_server"],
            "lender_auth_server": lender["auth_server"],
            "lender_incoming_url": lender_incoming_url,
            "asset_code": borrower["asset_code"],
            "asset_scale": borrower["asset_scale"],
            "borrower_return_url": req.borrower_return_url,
            "interval": interval,
            "term_start": start.isoformat(),
            "term_end": term_end.isoformat(),
            "nonce": nonce,
            "pending_grant": pending_grant,
        }
    )
    set_status(loan, "waiting_approval")
    return {
        "loan_id": loan_id,
        "approve_url": str(redirect_url),
        "lender_incoming_url": lender_incoming_url,
        "term_end": term_end.isoformat(),
        "cap_per_period": loan["weekly_cap"],
        "interval": interval,
    }


@app.get("/callback/loan/{loan_id}")
def loan_callback(loan_id: str, interact_ref: str | None = None, hash: str | None = None):
    loan = loans.get(loan_id)
    if loan is None or loan["status"] != "waiting_approval":
        raise HTTPException(
            status_code=404, detail=f"No loan waiting approval: '{loan_id}'"
        )
    if not interact_ref or not hash:  # the borrower rejected the grant in their wallet
        set_status(loan, "grant_rejected")
        return back_to_ui(loan["borrower_return_url"], loan_id, "grant_rejected", {"status": "grant_rejected"})

    # Check that this redirect really comes from the borrower's wallet
    valid = verify_interaction_hash(
        loan["nonce"],
        loan["pending_grant"].root.interact.finish,
        interact_ref,
        loan["borrower_auth_server"],
        hash,
    )
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid interaction hash")

    client = make_client(ABBO_WALLET_ADDRESS)
    loan.update(client.continue_loan_grant(loan.pop("pending_grant"), interact_ref))
    set_status(loan, "active")
    return back_to_ui(
        loan["borrower_return_url"],
        loan_id,
        "grant_approved",
        {"loan_id": loan_id, "status": "active", "term_end": loan["term_end"]},
    )


@app.post("/loans/{loan_id}/disburse")
def disburse_loan(loan_id: str):
    # The borrower must approve the repayment grant before the lender releases the money.
    # "disbursing" and "disburse_rejected" are allowed so the lender can try again.
    loan = get_loan_in(loan_id, "active", "disbursing", "disburse_rejected")

    # One-off payment from the lender's wallet to the borrower's wallet; the lender approves it.
    # The amount lands in the borrower's currency, so this assumes both wallets share a currency.
    client = make_client(ABBO_WALLET_ADDRESS)
    redirect_url, outgoing_grant, lender_resource_server, nonce, quote_id = (
        client.create_payment_request(
            receiver_wallet=loan["borrower_wallet"],
            amount_value=loan["principal"],
            order_id=loan_id,
            callback_base_url=f"{CALLBACK_BASE_URL}/disburse",
            sender_wallet=loan["lender_wallet"],
        )
    )

    loan["pending_disbursement"] = {
        "outgoing_grant": outgoing_grant,
        "lender_resource_server": lender_resource_server,
        "nonce": nonce,
        "quote_id": quote_id,
    }
    set_status(loan, "disbursing")
    return {"loan_id": loan_id, "approve_url": str(redirect_url), "status": "disbursing"}


@app.get("/callback/disburse/{loan_id}")
def disburse_callback(loan_id: str, interact_ref: str | None = None, hash: str | None = None):
    loan = loans.get(loan_id)
    if loan is None or loan["status"] != "disbursing":
        raise HTTPException(
            status_code=404, detail=f"No loan waiting for disbursement: '{loan_id}'"
        )
    if not interact_ref or not hash:  # the lender rejected the payment in their wallet
        set_status(loan, "disburse_rejected")  # the borrower's grant still stands: the lender can retry
        return back_to_ui(
            loan["lender_return_url"], loan_id, "disburse_rejected", {"status": "disburse_rejected"}
        )
    pending = loan["pending_disbursement"]

    # Check that this redirect really comes from the lender's wallet
    valid = verify_interaction_hash(
        pending["nonce"],
        pending["outgoing_grant"].root.interact.finish,
        interact_ref,
        loan["lender_auth_server"],
        hash,
    )
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid interaction hash")

    client = make_client(ABBO_WALLET_ADDRESS)
    outgoing_token = client.continue_outgoing_payment_grant(
        outgoing_grant=pending["outgoing_grant"],
        interact_ref=interact_ref,
    )
    payment_id = client.create_outgoing_payment(
        resource_server=pending["lender_resource_server"],
        outgoing_token=outgoing_token,
        quote_id=pending["quote_id"],
        sender_wallet=loan["lender_wallet"],
    )

    loan.pop("pending_disbursement")
    loan["disbursement_payment_id"] = str(payment_id)
    loan["disbursed_at"] = datetime.now(UTC).isoformat()
    set_status(loan, "disbursed")
    return back_to_ui(
        loan["lender_return_url"],
        loan_id,
        "disbursed",
        {"loan_id": loan_id, "status": "disbursed", "payment_id": str(payment_id)},
    )


def get_disbursed_loan(loan_id: str) -> dict:
    loan = loans.get(loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail=f"Loan '{loan_id}' not found")
    if loan["status"] != "disbursed":
        raise HTTPException(
            status_code=409,
            detail=f"Loan '{loan_id}' is '{loan['status']}': sales repay it only when 'disbursed'",
        )
    return loan


def repay_from_sale(loan: dict, sale_id: str, sale_value: int) -> dict:
    repayments = loan.setdefault("repayments", [])
    if any(r["sale_id"] == sale_id for r in repayments):
        return {"status": "duplicate", "sale_id": sale_id, "deduction": "0"}

    balance = int(loan["balance"])
    rate = loan.get("repayment_rate_percent", REPAYMENT_RATE_PERCENT)
    deduction = min(sale_value * rate // 100, balance)
    if deduction <= 0:
        return {"status": "nothing_to_pay", "sale_id": sale_id, "deduction": "0"}

    # The borrower's wallet enforces the per-period cap: over it, the payment is rejected here
    client = make_client(ABBO_WALLET_ADDRESS)
    try:
        payment_id, received = client.pay_with_grant(
            sender_wallet=loan["borrower_wallet"],
            incoming_payment_url=loan["lender_incoming_url"],
            outgoing_token=loan["access_token"],
            debit_value=str(deduction),
            asset_code=loan["asset_code"],
            asset_scale=loan["asset_scale"],
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502, detail=f"The wallet rejected the repayment: {e.response.text}"
        ) from e

    # The balance drops by what the lender received, so it matches the lender's incoming payment
    loan["balance"] = str(max(balance - received, 0))
    repayments.append(
        {
            "sale_id": sale_id,
            "deduction": str(deduction),
            "received": str(received),
            "payment_id": payment_id,
            "paid_at": datetime.now(UTC).isoformat(),
        }
    )
    if loan["balance"] == "0":
        set_status(loan, "paid_off")
    return {
        "status": "paid",
        "sale_id": sale_id,
        "deduction": str(deduction),
        "received": str(received),
        "balance": loan["balance"],
        "payment_id": payment_id,
    }


@app.post("/loans/{loan_id}/sales")
def record_sale(loan_id: str, req: SaleRequest):
    return repay_from_sale(get_disbursed_loan(loan_id), req.sale_id, int(req.amount_value))


@app.post("/loans/{loan_id}/sync-sales")
def sync_sales(loan_id: str):
    # Reads the borrower's wallet and repays from every sale received since the disbursement.
    # Already repaid sales are skipped by sale_id, so calling this again is safe.
    loan = get_disbursed_loan(loan_id)
    client = make_client(ABBO_WALLET_ADDRESS)
    try:
        incoming = client.list_incoming_payments(loan["borrower_wallet"], loan["access_token"])
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502, detail=f"Could not read the borrower's payments: {e.response.text}"
        ) from e

    # Only completed payments: the wallet credits the money when an incoming payment completes.
    # Created after the disbursement, so older sales and the loan itself are not sales here.
    disbursed_at = datetime.fromisoformat(loan["disbursed_at"])
    sales = sorted(
        (p for p in incoming if p.completed and p.createdAt > disbursed_at),
        key=lambda p: p.createdAt,
    )

    results = []
    for sale in sales:
        try:
            results.append(repay_from_sale(loan, str(sale.id), int(sale.receivedAmount.value)))
        except HTTPException as e:
            # Not recorded as repaid, so the next sync retries it (e.g. after the cap resets)
            results.append({"status": "failed", "sale_id": str(sale.id), "detail": e.detail})
    return {"loan_id": loan_id, "status": loan["status"], "balance": loan["balance"], "results": results}


HIDDEN_FIELDS = {
    "pending_grant",
    "pending_disbursement",
    "nonce",
    "access_token",
    "manage_url",
    "continue_token",
}


def public_view(loan_id: str) -> dict:
    return {"loan_id": loan_id, **{k: v for k, v in loans[loan_id].items() if k not in HIDDEN_FIELDS}}


@app.get("/loans")
def list_loans(borrower_wallet: str | None = None):
    # Oldest first; the borrower's UI shows the last one
    return [
        public_view(loan_id)
        for loan_id, loan in loans.items()
        if borrower_wallet is None or loan["borrower_wallet"] == borrower_wallet
    ]


@app.get("/loans/{loan_id}")
def get_loan(loan_id: str):
    if loan_id not in loans:
        raise HTTPException(status_code=404, detail=f"Loan '{loan_id}' not found")
    return public_view(loan_id)


# The lender and borrower UIs, served from the same origin as the API: /demo/prestador.html
app.mount("/demo", StaticFiles(directory=Path(__file__).resolve().parent.parent / "demo", html=True))
