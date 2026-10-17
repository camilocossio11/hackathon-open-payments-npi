import os
import secrets
import uuid
from decimal import Decimal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

from interfaces.get_keys import generate_keys, get_key_for_wallet
from interfaces.open_payment import OpenPaymentsInterface
from models import GenerateKeyRequest, InitiatePaymentRequest

load_dotenv()

CALLBACK_BASE_URL = os.getenv("CALLBACK_BASE_URL", "http://localhost:8000/callback")
print(f"[startup] CALLBACK_BASE_URL = {CALLBACK_BASE_URL}")

app = FastAPI()
pending_payments: dict[str, dict] = {}
pending_incoming_lists: dict[str, dict] = {}
incoming_list_tokens: dict[str, str] = {}


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


@app.post("/keys/generate")
def generate_key_pair(req: GenerateKeyRequest):
    key_pair = generate_keys(req.wallet)
    return {
        "kid": key_pair["kid"],
        "public_key_b64": key_pair["public_key_b64"],
        "public_key_pem": key_pair["public_key_pem"],
        "jwks": key_pair["jwks"],
        "message": f"Paste public_key_b64 into the 'Public key' field of wallet {req.wallet}.",
    }


@app.post("/payments/initiate")
def initiate_payment(req: InitiatePaymentRequest):
    client = make_client(req.sender_wallet)
    redirect_url, outgoing_grant, sender_resource_server, nonce, quote_id = (
        client.create_payment_request(
            receiver_wallet=req.receiver_wallet,
            amount_value=req.amount_value,
            order_id=req.order_id,
            callback_base_url=CALLBACK_BASE_URL,
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
        raise HTTPException(status_code=404, detail=f"No pending payment for order '{order_id}'")

    client = make_client(state["sender_wallet"])
    outgoing_token = client.continue_outgoing_payment_grant(
        outgoing_grant=state["outgoing_grant"],
        interact_ref=interact_ref,
    )
    payment_id = client.create_outgoing_payment(
        resource_server=state["sender_resource_server"],
        outgoing_token=outgoing_token,
        quote_id=state["quote_id"],
    )

    return {"status": "completed", "payment_id": str(payment_id), "order_id": order_id}


@app.get("/payments/list/incoming")
def list_incoming_payments(
    wallet_address: str,
    page_size: int = 1,
    fetch_all: bool = True,
):
    client = make_client(wallet_address)

    access_token = incoming_list_tokens.get(wallet_address)
    if access_token:
        try:
            payments = client.get_incoming_payments(
                wallet_address=wallet_address,
                access_token=access_token,
                page_size=page_size,
                fetch_all=fetch_all,
            )
            return {"payments": [payment.model_dump(mode="json") for payment in payments]}
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (401, 403):
                raise
            incoming_list_tokens.pop(wallet_address, None)

    request_id = str(uuid.uuid4())
    nonce = secrets.token_urlsafe(16)
    finish_uri = f"{CALLBACK_BASE_URL}/list-incoming/{request_id}"
    redirect_url, grant = client.request_list_incoming_payments_grant(
        wallet_address=wallet_address,
        finish_uri=finish_uri,
        nonce=nonce,
    )

    pending_incoming_lists[request_id] = {
        "grant": grant,
        "wallet_address": wallet_address,
        "nonce": nonce,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }

    return {"redirect_url": str(redirect_url), "request_id": request_id}


@app.get("/callback/list-incoming/{request_id}")
def list_incoming_payments_callback(
    request_id: str,
    interact_ref: str = Query(...),
    hash: str = Query(...),
):
    state = pending_incoming_lists.pop(request_id, None)
    if state is None:
        raise HTTPException(status_code=404, detail=f"No pending list request '{request_id}'")

    wallet_address = state["wallet_address"]
    client = make_client(wallet_address)
    access_token = client.continue_grant(grant=state["grant"], interact_ref=interact_ref)
    incoming_list_tokens[wallet_address] = access_token

    payments = client.list_incoming_payments_with_loan(
        wallet_address=wallet_address,
        access_token=access_token,
        page_size=state["page_size"],
        fetch_all=state["fetch_all"],
        percentage_to_pay_loan=Decimal(0.05),
    )
    return {"payments": [payment.model_dump() for payment in payments]}
