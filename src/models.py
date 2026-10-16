from pydantic import BaseModel


class GenerateKeyRequest(BaseModel):
    wallet: str


class InitiatePaymentRequest(BaseModel):
    order_id: str
    amount_value: str
    sender_wallet: str
    receiver_wallet: str


class CreateLoanRequest(BaseModel):
    loan_id: str
    borrower_wallet: (
        str  # the borrower's own wallet address (they approve the loan grant there)
    )
    lender_wallet: str
    amount_value: str  # total debt in minor units
    weekly_cap_value: str  # max repaid per week (enforced by the app)
    term_minutes: int  # 5 for testing, 10080 = 1 week, 43200 = 30 days.
