import os

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
