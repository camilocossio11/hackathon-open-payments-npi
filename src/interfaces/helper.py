import json
import os
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from open_payments_sdk.models.resource import Amount, IncomingPayment

from models import IncomingPaymentInfo

CHECKPOINT_PATH = "checkpoint.json"


def load_last_date() -> datetime | None:
    if not os.path.exists(CHECKPOINT_PATH):
        return None
    with open(CHECKPOINT_PATH) as f:
        checkpoint = json.load(f)
    last_date = checkpoint.get("last_date")
    return datetime.fromisoformat(last_date) if last_date else None


def save_last_date(last_date: str) -> None:
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump({"last_date": last_date}, f, indent=2)


def to_decimal(amount: Amount) -> Decimal:
    return Decimal(amount.value).scaleb(-amount.assetScale.root)


def format_amount(amount: Amount) -> str:
    return f"{to_decimal(amount)} {amount.assetCode.root}"


def get_last_date(incoming_payments: list[IncomingPaymentInfo]) -> str | None:
    if not incoming_payments:
        return None
    last_payment = max(incoming_payments, key=lambda p: p.received_date)
    return str(last_payment.received_date)


def filter_by_date(
    incoming_payments: list[IncomingPaymentInfo],
    start_date: datetime,
) -> list[IncomingPaymentInfo]:
    return [payment for payment in incoming_payments if start_date < payment.received_date]


def filter_by_completed(incoming_payments: list[IncomingPaymentInfo]) -> list[IncomingPaymentInfo]:
    return [payment for payment in incoming_payments if payment.completed]


def get_info_from_incoming_payment(incoming_payment: IncomingPayment) -> IncomingPaymentInfo:
    return IncomingPaymentInfo(
        payment_id=str(incoming_payment.id).split("/")[-1],
        amount_value=to_decimal(incoming_payment.receivedAmount),
        amount_asset_code=incoming_payment.receivedAmount.assetCode.root,
        amount_asset_scale=incoming_payment.receivedAmount.assetScale.root,
        received_date=incoming_payment.createdAt,
        completed=incoming_payment.completed,
    )


def compute_loan_payment(
    incoming_payment: IncomingPaymentInfo, percentage_to_pay_loan: Decimal | None
) -> IncomingPaymentInfo:
    if percentage_to_pay_loan is None:
        return incoming_payment
    step = Decimal(1).scaleb(-incoming_payment.amount_asset_scale)
    to_pay_loan = (incoming_payment.amount_value * percentage_to_pay_loan).quantize(
        step, rounding=ROUND_DOWN
    )
    incoming_payment.percentage_to_pay_loan = percentage_to_pay_loan
    incoming_payment.to_pay_loan = to_pay_loan
    return incoming_payment
