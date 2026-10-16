import base64
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

from interfaces.get_keys import generate_keys, get_key_for_wallet
from interfaces.open_payment import OpenPaymentsInterface
from models import CreateLoanRequest, GenerateKeyRequest, InitiatePaymentRequest

load_dotenv()

CALLBACK_BASE_URL = os.getenv("CALLBACK_BASE_URL", "http://localhost:8000/callback")
print(f"[startup] CALLBACK_BASE_URL = {CALLBACK_BASE_URL}")

app = FastAPI()
pending_payments: dict[str, dict] = {}
loans: dict[str, dict] = {}  # in memory for now: lost on restart

ABBO_WALLET_ADDRESS = "https://ilp.interledger-test.dev/abbo"


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


@app.post("/payments/initiate")
def initiate_payment(req: InitiatePaymentRequest):
    client = make_client(ABBO_WALLET_ADDRESS)
    redirect_url, outgoing_grant, sender_resource_server, nonce, quote_id = (
        client.create_payment_request(
            receiver_wallet=req.receiver_wallet,
            amount_value=req.amount_value,
            order_id=req.order_id,
            callback_base_url=CALLBACK_BASE_URL,
            sender_wallet=req.sender_wallet
        )
    )

    pending_payments[req.order_id] = {
        "outgoing_grant": outgoing_grant,
        "sender_resource_server": sender_resource_server,
        "sender_wallet": req.sender_wallet,
        "nonce": nonce,
        "quote_id": quote_id,
    }

    return {"redirect_url": str(redirect_url), "order_id": req.order_id}


@app.get("/callback/{order_id}")
def payment_callback(
    order_id: str,
    interact_ref: str = Query(...),
    hash: str = Query(...),
):
    state = pending_payments.pop(order_id, None)
    if state is None:
        raise HTTPException(
            status_code=404, detail=f"No pending payment for order '{order_id}'"
        )

    client = make_client(ABBO_WALLET_ADDRESS)
    outgoing_token = client.continue_outgoing_payment_grant(
        outgoing_grant=state["outgoing_grant"],
        interact_ref=interact_ref,
    )
    payment_id = client.create_outgoing_payment(
        resource_server=state["sender_resource_server"],
        outgoing_token=outgoing_token,
        quote_id=state["quote_id"],
        sender_wallet=state["sender_wallet"]
    )

    return {"status": "completed", "payment_id": str(payment_id), "order_id": order_id}


@app.post("/loans")
def create_loan(req: CreateLoanRequest):
    if req.loan_id in loans:
        raise HTTPException(
            status_code=409, detail=f"Loan '{req.loan_id}' already exists"
        )

    # For now the lender's key signs the requests; switch to the app's client wallet later
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
    redirect_url, pending_grant = client.request_loan_grant(
        borrower_wallet=req.borrower_wallet,
        lender_incoming_url=lender_incoming_url,
        debt_value=req.amount_value,
        asset_code=borrower["asset_code"],
        asset_scale=borrower["asset_scale"],
        finish_uri=f"{CALLBACK_BASE_URL}/loan/{req.loan_id}",
        nonce=nonce,
    )

    loans[req.loan_id] = {
        "borrower_wallet": req.borrower_wallet,
        "lender_wallet": req.lender_wallet,
        "borrower_auth_server": borrower["auth_server"],
        "lender_incoming_url": lender_incoming_url,
        "asset_code": borrower["asset_code"],
        "asset_scale": borrower["asset_scale"],
        "debt": req.amount_value,
        "balance": req.amount_value,
        "weekly_cap": req.weekly_cap_value,
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

    client = make_client(loan["lender_wallet"])
    loan.update(client.continue_loan_grant(loan.pop("pending_grant"), interact_ref))
    loan["status"] = "active"
    return {"loan_id": loan_id, "status": "active", "term_end": loan["term_end"]}


@app.get("/loans/{loan_id}")
def get_loan(loan_id: str):
    loan = loans.get(loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail=f"Loan '{loan_id}' not found")
    hidden = {"pending_grant", "nonce", "access_token", "manage_url", "continue_token"}
    return {"loan_id": loan_id, **{k: v for k, v in loan.items() if k not in hidden}}
