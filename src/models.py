from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class GenerateKeyRequest(BaseModel):
    wallet: str


class InitiatePaymentRequest(BaseModel):
    order_id: str
    amount_value: str
    sender_wallet: str
    receiver_wallet: str


@dataclass
class IncomingPaymentInfo:
    payment_id: str
    amount_value: Decimal
    amount_asset_code: str
    amount_asset_scale: int
    received_date: datetime
    completed: bool
    percentage_to_pay_loan: Decimal | None = None
    to_pay_loan: Decimal | None = None

    def model_dump(self) -> dict:
        data = {
            "payment_id": self.payment_id,
            "amount_value": str(self.amount_value),
            "amount_asset_code": self.amount_asset_code,
            "amount_asset_scale": self.amount_asset_scale,
            "received_date": self.received_date.isoformat(),
            "completed": self.completed,
        }
        if self.percentage_to_pay_loan is not None:
            data["percentage_to_pay_loan"] = str(self.percentage_to_pay_loan)
        if self.to_pay_loan is not None:
            data["to_pay_loan"] = str(self.to_pay_loan)
        return data
