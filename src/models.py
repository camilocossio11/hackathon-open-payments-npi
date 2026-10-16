from pydantic import BaseModel


class GenerateKeyRequest(BaseModel):
    wallet: str


class InitiatePaymentRequest(BaseModel):
    order_id: str
    amount_value: str
    sender_wallet: str
    receiver_wallet: str
