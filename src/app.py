import base64
import hashlib
import hmac
import math
import os
import secrets
from datetime import UTC, datetime, timedelta

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

from interfaces.get_keys import generate_keys, get_key_for_wallet
from interfaces.open_payment import OpenPaymentsInterface
from models import (
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
REPAYMENT_RATE_PERCENT = 10  # share of each sale that repays the loan


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


@app.post("/loans")
def create_loan(req: CreateLoanRequest):
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

    client = make_client(ABBO_WALLET_ADDRESS)
    borrower = client.discover_receiver_servers(req.borrower_wallet)
    lender = client.discover_receiver_servers(req.lender_wallet)

    start = datetime.now(UTC)
    term_end = start + timedelta(minutes=req.term_minutes)

    # 1. The loan's incoming payment on the lender's wallet: accepts at most the debt, until term_end
    incoming_token = client.request_incoming_payment_grant(req.lender_wallet)
    lender_incoming_url, _ = client.create_incoming_payment(
        receiver_wallet=req.lender_wallet,
        resource_server=lender["resource_server"],
        access_token=incoming_token,
        amount_value=req.amount_value,
        asset_code=lender["asset_code"],
        asset_scale=lender["asset_scale"],
        expires_at=term_end,
    )

    # 2. The capped permission on the borrower's wallet (approved once, interactively)
    nonce = secrets.token_urlsafe(16)
    interval = build_interval(start, req.term_minutes, req.period_minutes)
    redirect_url, pending_grant = client.request_loan_grant(
        borrower_wallet=req.borrower_wallet,
        lender_incoming_url=lender_incoming_url,
        cap_value=str(cap),
        interval=interval,
        asset_code=borrower["asset_code"],
        asset_scale=borrower["asset_scale"],
        finish_uri=f"{CALLBACK_BASE_URL}/loan/{req.loan_id}",
        nonce=nonce,
    )

    loans[req.loan_id] = {
        "borrower_wallet": req.borrower_wallet,
        "lender_wallet": req.lender_wallet,
        "borrower_auth_server": borrower["auth_server"],
        "lender_auth_server": lender["auth_server"],
        "lender_incoming_url": lender_incoming_url,
        "asset_code": borrower["asset_code"],
        "asset_scale": borrower["asset_scale"],
        "debt": req.amount_value,
        "balance": req.amount_value,
        "weekly_cap": str(cap),
        "min_cap": str(minimum_cap),
        "interval": interval,
        "term_start": start.isoformat(),
        "term_end": term_end.isoformat(),
        "nonce": nonce,
        "pending_grant": pending_grant,
        "status": "waiting_approval",
    }
    return {
        "loan_id": req.loan_id,
        "approve_url": str(redirect_url),
        "lender_incoming_url": lender_incoming_url,
        "term_end": term_end.isoformat(),
        "cap_per_period": str(cap),
        "min_cap": str(minimum_cap),
        "interval": interval,
    }


@app.get("/callback/loan/{loan_id}")
def loan_callback(loan_id: str, interact_ref: str = Query(...), hash: str = Query(...)):
    loan = loans.get(loan_id)
    if loan is None or loan["status"] != "waiting_approval":
        raise HTTPException(
            status_code=404, detail=f"No loan waiting approval: '{loan_id}'"
        )

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
    loan["status"] = "active"
    return {"loan_id": loan_id, "status": "active", "term_end": loan["term_end"]}


@app.post("/loans/{loan_id}/disburse")
def disburse_loan(loan_id: str):
    loan = loans.get(loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail=f"Loan '{loan_id}' not found")
    # The borrower must approve the repayment grant before the lender releases the money.
    # "disbursing" is allowed so the lender can get a fresh approval link.
    if loan["status"] not in ("active", "disbursing"):
        raise HTTPException(
            status_code=409,
            detail=f"Loan '{loan_id}' is '{loan['status']}': it can be disbursed only when 'active'",
        )

    # One-off payment from the lender's wallet to the borrower's wallet; the lender approves it.
    # The amount lands in the borrower's currency, so this assumes both wallets share a currency.
    client = make_client(ABBO_WALLET_ADDRESS)
    redirect_url, outgoing_grant, lender_resource_server, nonce, quote_id = (
        client.create_payment_request(
            receiver_wallet=loan["borrower_wallet"],
            amount_value=loan["debt"],
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
    loan["status"] = "disbursing"
    return {"loan_id": loan_id, "approve_url": str(redirect_url), "status": "disbursing"}


@app.get("/callback/disburse/{loan_id}")
def disburse_callback(loan_id: str, interact_ref: str = Query(...), hash: str = Query(...)):
    loan = loans.get(loan_id)
    if loan is None or loan["status"] != "disbursing":
        raise HTTPException(
            status_code=404, detail=f"No loan waiting for disbursement: '{loan_id}'"
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
    loan["status"] = "disbursed"
    return {"loan_id": loan_id, "status": "disbursed", "payment_id": str(payment_id)}


@app.post("/loans/{loan_id}/sales")
def record_sale(loan_id: str, req: SaleRequest):
    loan = loans.get(loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail=f"Loan '{loan_id}' not found")
    if loan["status"] != "disbursed":
        raise HTTPException(
            status_code=409,
            detail=f"Loan '{loan_id}' is '{loan['status']}': sales repay it only when 'disbursed'",
        )

    repayments = loan.setdefault("repayments", [])
    if any(r["sale_id"] == req.sale_id for r in repayments):
        return {"status": "duplicate", "sale_id": req.sale_id, "deduction": "0"}

    balance = int(loan["balance"])
    deduction = min(int(req.amount_value) * REPAYMENT_RATE_PERCENT // 100, balance)
    if deduction <= 0:
        return {"status": "nothing_to_pay", "sale_id": req.sale_id, "deduction": "0"}

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
            "sale_id": req.sale_id,
            "deduction": str(deduction),
            "received": str(received),
            "payment_id": payment_id,
            "paid_at": datetime.now(UTC).isoformat(),
        }
    )
    if loan["balance"] == "0":
        loan["status"] = "paid_off"
    return {
        "status": "paid",
        "sale_id": req.sale_id,
        "deduction": str(deduction),
        "received": str(received),
        "balance": loan["balance"],
        "payment_id": payment_id,
    }


@app.get("/loans/{loan_id}")
def get_loan(loan_id: str):
    loan = loans.get(loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail=f"Loan '{loan_id}' not found")
    hidden = {
        "pending_grant",
        "pending_disbursement",
        "nonce",
        "access_token",
        "manage_url",
        "continue_token",
    }
    return {"loan_id": loan_id, **{k: v for k, v in loan.items() if k not in hidden}}
