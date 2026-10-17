import secrets
from datetime import datetime
from decimal import Decimal
from typing import Optional

import httpx
from open_payments_sdk.client.client import OpenPaymentsClient
from open_payments_sdk.models.auth import Grant, GrantRequest, InteractRef
from open_payments_sdk.models.resource import (
    Amount,
    IncomingPayment,
    IncomingPaymentRequest,
    OutgoingPayment,
    OutgoingPaymentRequestWithQuote,
    PaginatedIncomingPayments,
    Pagination,
    PaymentListQuery,
    QuoteRequestBase,
    WalletAddress,
)

from models import IncomingPaymentInfo

from .helper import (
    compute_loan_payment,
    filter_by_completed,
    filter_by_date,
    get_info_from_incoming_payment,
    get_last_date,
    load_last_date,
    save_last_date,
)

IncomingPayment.__annotations__["updatedAt"] = Optional[datetime]
IncomingPayment.model_fields["updatedAt"].default = None
IncomingPayment.model_rebuild(force=True)

OutgoingPayment.__annotations__["updatedAt"] = Optional[datetime]
OutgoingPayment.model_fields["updatedAt"].default = None
OutgoingPayment.model_config["extra"] = "ignore"
OutgoingPayment.model_rebuild(force=True)

for _field in ("startCursor", "endCursor", "hasNextPage", "hasPrevPage"):
    Pagination.model_fields[_field].default = None
Pagination.model_rebuild(force=True)
PaginatedIncomingPayments.model_rebuild(force=True)

PaymentListQuery.model_fields["walletAddress"].serialization_alias = "wallet-address"
PaymentListQuery.model_config["serialize_by_alias"] = True
PaymentListQuery.model_rebuild(force=True)


class OpenPaymentsInterface:
    def __init__(self, key_id: str, private_key_pem: str, wallet_address: str):
        self.client = OpenPaymentsClient(
            keyid=key_id, private_key=private_key_pem, client_wallet_address=wallet_address
        )

    def discover_receiver_servers(self, receiver_wallet: str):
        info = self.client.wallet.get_wallet_address(receiver_wallet)
        return {
            "auth_server": str(info.authServer),
            "resource_server": str(info.resourceServer),
            "asset_code": str(info.assetCode.root),
            "asset_scale": int(info.assetScale.root),
        }

    def request_incoming_payment_grant(self, receiver_wallet: str):
        auth_server = self.discover_receiver_servers(receiver_wallet)["auth_server"]
        grant_req = GrantRequest(
            **{
                "access_token": {
                    "access": [
                        {
                            "type": "incoming-payment",
                            "actions": ["create", "read"],
                            "identifier": receiver_wallet,
                        }
                    ]
                },
                "client": self.client.client_wallet_address,
            }
        )
        grant = self.client.grants.post_grant_request(grant_req, auth_server)
        return grant.root.access_token.value

    def create_incoming_payment(
        self,
        receiver_wallet: str,
        resource_server: str,
        access_token: str,
        amount_value: str,
        asset_code: str = "USD",
        asset_scale: int = 2,
    ):
        payment = IncomingPaymentRequest.model_construct(
            walletAddress=WalletAddress.model_validate(receiver_wallet),
            incomingAmount=Amount(value=amount_value, assetCode=asset_code, assetScale=asset_scale),
        )
        print("Sending body:", payment.model_dump(exclude_unset=True, mode="json"))
        try:
            incoming = self.client.incoming_payments.post_create_payment(
                payment=payment,
                resource_server_endpoint=resource_server,
                access_token=access_token,
            )
        except httpx.HTTPStatusError as e:
            print("Incoming payment error response:", e.response.text)
            raise
        return str(incoming.id), incoming

    def request_quote_grant(self, sender_wallet: str) -> str:
        auth_server = self.discover_receiver_servers(sender_wallet)["auth_server"]
        grant_req = GrantRequest(
            **{
                "access_token": {"access": [{"type": "quote", "actions": ["create", "read"]}]},
                "client": self.client.client_wallet_address,
            }
        )
        grant = self.client.grants.post_grant_request(grant_req, auth_server)
        return grant.root.access_token.value

    def create_quote(self, resource_server: str, access_token: str, incoming_payment_url: str):
        quote_req = QuoteRequestBase.model_construct(
            walletAddress=WalletAddress.model_validate(self.client.client_wallet_address),
            receiver=incoming_payment_url,
            method="ilp",
        )
        try:
            quote = self.client.quotes.post_create_quote(
                quote=quote_req,
                resource_server_endpoint=resource_server,
                access_token=access_token,
            )
        except httpx.HTTPStatusError as e:
            print("Quote error response:", e.response.text)
            raise
        print(f"Quote created: id={quote.id} debitAmount={quote.debitAmount}")
        return quote

    def request_outgoing_payment_grant(
        self,
        receiver_wallet: str,
        incoming_payment_url: str,
        finish_uri: str,
        nonce: str,
        amount_value: str,
        asset_code: str,
        asset_scale: int,
    ):
        auth_server = self.discover_receiver_servers(receiver_wallet)["auth_server"]
        outgoing_grant_req = GrantRequest(
            **{
                "access_token": {
                    "access": [
                        {
                            "type": "outgoing-payment",
                            "actions": ["create", "read"],
                            "identifier": self.client.client_wallet_address,
                            "limits": {
                                "receiver": incoming_payment_url,
                                "debitAmount": {
                                    "value": amount_value,
                                    "assetCode": asset_code,
                                    "assetScale": asset_scale,
                                },
                            },
                        }
                    ]
                },
                "client": self.client.client_wallet_address,
                "interact": {
                    "start": ["redirect"],
                    "finish": {
                        "method": "redirect",
                        "uri": finish_uri,
                        "nonce": nonce,
                    },
                },
            }
        )
        outgoing_grant = self.client.grants.post_grant_request(outgoing_grant_req, auth_server)
        redirect_url = outgoing_grant.root.interact.redirect
        return redirect_url, outgoing_grant

    def continue_grant(self, grant: Grant, interact_ref: str) -> str:
        continue_token = grant.root.cont.access_token.value
        continue_uri = str(grant.root.cont.uri)
        final_grant = self.client.grants.post_grant_continuation_request(
            interact_ref=InteractRef(interact_ref=interact_ref),
            continue_uri=continue_uri,
            access_token=continue_token,
        )
        return final_grant.access_token.value

    def continue_outgoing_payment_grant(self, outgoing_grant: Grant, interact_ref: str):
        return self.continue_grant(outgoing_grant, interact_ref)

    def create_outgoing_payment(
        self,
        resource_server: str,
        outgoing_token: str,
        quote_id: str,
    ):
        payment = OutgoingPaymentRequestWithQuote.model_construct(
            walletAddress=WalletAddress.model_validate(self.client.client_wallet_address),
            quoteId=quote_id,
        )
        print("Outgoing payment body:", payment.model_dump(exclude_unset=True, mode="json"))
        try:
            outgoing = self.client.outgoing_payments.post_create_payment(
                payment=payment,
                resource_server_endpoint=resource_server,
                access_token=outgoing_token,
            )
        except httpx.HTTPStatusError as e:
            print("Outgoing payment error response:", e.response.text)
            raise
        return outgoing.id

    def create_payment_request(
        self, receiver_wallet: str, amount_value: str, order_id: str, callback_base_url: str
    ):
        nonce = secrets.token_urlsafe(16)
        finish_uri = f"{callback_base_url}/{order_id}"
        print(f"[make_payment] finish_uri = {finish_uri}")

        access_token = self.request_incoming_payment_grant(receiver_wallet)

        receiver_info = self.discover_receiver_servers(receiver_wallet)
        incoming_payment_url, _ = self.create_incoming_payment(
            receiver_wallet=receiver_wallet,
            resource_server=receiver_info["resource_server"],
            access_token=access_token,
            amount_value=amount_value,
            asset_code=receiver_info["asset_code"],
            asset_scale=receiver_info["asset_scale"],
        )

        sender_info = self.discover_receiver_servers(self.client.client_wallet_address)
        quote_token = self.request_quote_grant(self.client.client_wallet_address)
        quote = self.create_quote(
            resource_server=sender_info["resource_server"],
            access_token=quote_token,
            incoming_payment_url=incoming_payment_url,
        )

        redirect_url, outgoing_grant = self.request_outgoing_payment_grant(
            receiver_wallet=receiver_wallet,
            incoming_payment_url=incoming_payment_url,
            finish_uri=finish_uri,
            nonce=nonce,
            amount_value=str(quote.debitAmount.value),
            asset_code=str(quote.debitAmount.assetCode.root),
            asset_scale=int(quote.debitAmount.assetScale.root),
        )

        return (
            redirect_url,
            outgoing_grant,
            sender_info["resource_server"],
            nonce,
            str(quote.id),
        )

    def make_payment(self, order_id: str, state: dict, interact_ref: str):
        outgoing_token = self.continue_outgoing_payment_grant(
            outgoing_grant=state["outgoing_grant"],
            interact_ref=interact_ref,
        )
        payment_id = self.create_outgoing_payment(
            resource_server=state["sender_resource_server"],
            outgoing_token=outgoing_token,
            quote_id=state["quote_id"],
        )

        return {"status": "completed", "payment_id": str(payment_id), "order_id": order_id}

    def request_list_incoming_payments_grant(
        self, wallet_address: str, finish_uri: str, nonce: str
    ):
        auth_server = self.discover_receiver_servers(wallet_address)["auth_server"]
        grant_req = GrantRequest(
            **{
                "access_token": {
                    "access": [
                        {
                            "type": "incoming-payment",
                            "actions": ["list-all", "read-all"],
                            "identifier": wallet_address,
                        }
                    ]
                },
                "client": self.client.client_wallet_address,
                "interact": {
                    "start": ["redirect"],
                    "finish": {
                        "method": "redirect",
                        "uri": finish_uri,
                        "nonce": nonce,
                    },
                },
            }
        )
        try:
            grant = self.client.grants.post_grant_request(grant_req, auth_server)
        except httpx.HTTPStatusError as e:
            print("List incoming payments grant error response:", e.response.text)
            raise
        return grant.root.interact.redirect, grant

    def get_incoming_payments(
        self,
        wallet_address: str,
        access_token: str,
        page_size: int = 1,
        fetch_all: bool = True,
    ) -> list[IncomingPaymentInfo]:
        resource_server = self.discover_receiver_servers(wallet_address)["resource_server"]

        payments: list[IncomingPaymentInfo] = []
        last_date = load_last_date()
        cursor = None
        while True:
            query_fields = {
                "walletAddress": WalletAddress.model_validate(wallet_address),
                "first": page_size,
            }
            if cursor:
                query_fields["cursor"] = cursor
            query = PaymentListQuery.model_construct(**query_fields)
            try:
                page = self.client.incoming_payments.get_incoming_payments(
                    query=query,
                    resource_server_endpoint=resource_server,
                    access_token=access_token,
                )
            except httpx.HTTPStatusError as e:
                print("List incoming payments error response:", e.response.text)
                raise
            formatted_payments = [
                get_info_from_incoming_payment(payment) for payment in page.result
            ]
            completed_payments = filter_by_completed(formatted_payments)
            filtered_payments = (
                filter_by_date(completed_payments, last_date) if last_date else completed_payments
            )
            payments.extend(filtered_payments)

            if (
                not fetch_all
                or not filtered_payments
                or not page.pagination.hasNextPage
                or not page.pagination.endCursor
            ):
                break

            cursor = page.pagination.endCursor

        last_date = get_last_date(payments) if payments else None
        if last_date:
            save_last_date(last_date)
        print(f"Retrieved {len(payments)} incoming payments. Last date: {last_date}")
        return payments

    def list_incoming_payments_with_loan(
        self,
        wallet_address: str,
        access_token: str,
        page_size: int = 1,
        fetch_all: bool = True,
        percentage_to_pay_loan: Decimal | None = None,
    ) -> list[IncomingPaymentInfo]:
        payments = self.get_incoming_payments(
            wallet_address,
            access_token,
            page_size,
            fetch_all,
        )
        payments_with_loans = [
            compute_loan_payment(payment, percentage_to_pay_loan) for payment in payments
        ]
        return payments_with_loans
