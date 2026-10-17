from pydantic import BaseModel


class GenerateKeyRequest(BaseModel):
    wallet: str


class InitiatePaymentRequest(BaseModel):
    order_id: str
    amount_value: str
    sender_wallet: str
    receiver_wallet: str


class SaleRequest(BaseModel):
    sale_id: str  # the sale's own id, so a resent sale is not charged twice
    amount_value: str  # the sale, in the borrower's minor units


class CreateLoanRequest(BaseModel):
    loan_id: str
    borrower_wallet: (
        str  # the borrower's own wallet address (they approve the loan grant there)
    )
    lender_wallet: str
    amount_value: str  # total debt in minor units
    # max repaid per period, in the borrower's minor units (enforced by the borrower's wallet).
    # Leave it out to use the minimum: ceil(debt / number of periods). A lower value is rejected.
    weekly_cap_value: str | None = None
    term_minutes: int  # 5 for testing, 10080 = 1 week, 43200 = 30 days.
    period_minutes: int = 10080  # how often the cap resets: 10080 = weekly, 5 for testing
