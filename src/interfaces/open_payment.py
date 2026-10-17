import secrets
from datetime import datetime
from typing import Optional

import httpx
from open_payments_sdk.client.client import OpenPaymentsClient
from open_payments_sdk.models.auth import GrantRequest, InteractRef
from open_payments_sdk.models.resource import (
    Amount,
    IncomingPayment,
    IncomingPaymentRequest,
    OutgoingPayment,
    OutgoingPaymentRequestWithQuote,
    PaginatedIncomingPayments,
    Pagination,
    PaymentListQuery,
    QuoteFixedSent,
    QuoteRequestBase,
    WalletAddress,
)

IncomingPayment.__annotations__["updatedAt"] = Optional[datetime]
IncomingPayment.model_fields["updatedAt"].default = None
IncomingPayment.model_rebuild(force=True)

OutgoingPayment.__annotations__["updatedAt"] = Optional[datetime]
OutgoingPayment.model_fields["updatedAt"].default = None
OutgoingPayment.model_config["extra"] = "ignore"
OutgoingPayment.model_rebuild(force=True)

# From main (list incoming payments): the wallet omits empty pagination fields,
# and the list query must send walletAddress as "wallet-address"
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
            keyid=key_id,
            private_key=private_key_pem,
            client_wallet_address=wallet_address,
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
            access_token={
                "access": [
                    {
                        "type": "incoming-payment",
                        "actions": ["create", "read"],
                        "identifier": receiver_wallet,
                    }
                ]
            },
            client=self.client.client_wallet_address,
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
        expires_at: datetime | None = None,
    ):
        extra = {"expiresAt": expires_at} if expires_at else {}
        payment = IncomingPaymentRequest.model_construct(
            walletAddress=WalletAddress.model_validate(receiver_wallet),
            incomingAmount=Amount(
                value=amount_value, assetCode=asset_code, assetScale=asset_scale
            ),
            **extra,
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
            access_token={"access": [{"type": "quote", "actions": ["create", "read"]}]},
            client=self.client.client_wallet_address,
        )
        grant = self.client.grants.post_grant_request(grant_req, auth_server)
        return grant.root.access_token.value

    def create_quote(
        self,
        resource_server: str,
        access_token: str,
        incoming_payment_url: str,
        sender_wallet: str,
        debit_amount: Amount | None = None,
    ):
        # Pass debit_amount (sender's asset) to fix what the sender pays, e.g. one sale's deduction.
        if debit_amount is None:
            quote_req = QuoteRequestBase.model_construct(
                walletAddress=WalletAddress.model_validate(sender_wallet),
                receiver=incoming_payment_url,
                method="ilp",
            )
        else:
            quote_req = QuoteFixedSent.model_construct(
                walletAddress=WalletAddress.model_validate(sender_wallet),
                receiver=incoming_payment_url,
                method="ilp",
                debitAmount=debit_amount,
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
        incoming_payment_url: str,
        finish_uri: str,
        nonce: str,
        amount_value: str,
        asset_code: str,
        asset_scale: int,
        sender_wallet: str,
    ):
        auth_server = self.discover_receiver_servers(sender_wallet)["auth_server"]
        outgoing_grant_req = GrantRequest(
            access_token={
                "access": [
                    {
                        "type": "outgoing-payment",
                        "actions": ["create", "read"],
                        "identifier": sender_wallet,
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
            client=self.client.client_wallet_address,
            interact={
                "start": ["redirect"],
                "finish": {
                    "method": "redirect",
                    "uri": finish_uri,
                    "nonce": nonce,
                },
            },
        )
        outgoing_grant = self.client.grants.post_grant_request(
            outgoing_grant_req, auth_server
        )
        redirect_url = outgoing_grant.root.interact.redirect
        return redirect_url, outgoing_grant

    def continue_outgoing_payment_grant(self, outgoing_grant, interact_ref: str):
        continue_token = outgoing_grant.root.cont.access_token.value
        continue_uri = str(outgoing_grant.root.cont.uri)
        final_grant = self.client.grants.post_grant_continuation_request(
            interact_ref=InteractRef(interact_ref=interact_ref),
            continue_uri=continue_uri,
            access_token=continue_token,
        )
        return final_grant.access_token.value

    def request_loan_grant(
        self,
        borrower_wallet: str,
        lender_incoming_url: str,
        cap_value: str,
        interval: str,
        asset_code: str,
        asset_scale: int,
        finish_uri: str,
        nonce: str,
    ):
        # The money leaves the borrower's wallet, so the grant lives on the borrower's auth server.
        # With an interval, debitAmount is the cap per period (the wallet resets it each period).
        # The total is capped by the lender's incoming payment (incomingAmount = debt).
        auth_server = self.discover_receiver_servers(borrower_wallet)["auth_server"]
        loan_grant_req = GrantRequest(
            access_token={
                "access": [
                    {
                        "type": "outgoing-payment",
                        "actions": ["create", "read"],
                        "identifier": borrower_wallet,
                        "limits": {
                            "receiver": lender_incoming_url,
                            "debitAmount": {
                                "value": cap_value,
                                "assetCode": asset_code,
                                "assetScale": asset_scale,
                            },
                            "interval": interval,
                        },
                    },
                    # Same approval also lets Abbo read the borrower's sales (incoming payments)
                    {
                        "type": "incoming-payment",
                        "actions": ["list-all", "read-all"],
                        "identifier": borrower_wallet,
                    },
                ]
            },
            client=self.client.client_wallet_address,
            interact={
                "start": ["redirect"],
                "finish": {
                    "method": "redirect",
                    "uri": finish_uri,
                    "nonce": nonce,
                },
            },
        )
        pending_grant = self.client.grants.post_grant_request(
            loan_grant_req, auth_server
        )
        redirect_url = pending_grant.root.interact.redirect
        return redirect_url, pending_grant

    def continue_loan_grant(self, pending_grant, interact_ref: str) -> dict:
        continue_token = pending_grant.root.cont.access_token.value
        continue_uri = str(pending_grant.root.cont.uri)
        final_grant = self.client.grants.post_grant_continuation_request(
            interact_ref=InteractRef(interact_ref=interact_ref),
            continue_uri=continue_uri,
            access_token=continue_token,
        )
        # The SDK drops "continue" from this response, so the revoke data comes from pending_grant
        return {
            "access_token": final_grant.access_token.value,
            "manage_url": str(final_grant.access_token.manage),
            "expires_in": final_grant.access_token.expires_in,
            "continue_uri": continue_uri,
            "continue_token": continue_token,
        }

    def create_outgoing_payment(
        self,
        resource_server: str,
        outgoing_token: str,
        quote_id: str,
        sender_wallet: str,
    ):
        payment = OutgoingPaymentRequestWithQuote.model_construct(
            walletAddress=WalletAddress.model_validate(
                sender_wallet
            ),
            quoteId=quote_id,
        )
        print(
            "Outgoing payment body:",
            payment.model_dump(exclude_unset=True, mode="json"),
        )
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

    def pay_with_grant(
        self,
        sender_wallet: str,
        incoming_payment_url: str,
        outgoing_token: str,
        debit_value: str,
        asset_code: str,
        asset_scale: int,
    ) -> tuple[str, int]:
        # Pays a fixed amount under an already approved outgoing-payment grant: no redirect.
        # Returns the payment id and what the receiver gets (after fees).
        resource_server = self.discover_receiver_servers(sender_wallet)["resource_server"]
        quote = self.create_quote(
            resource_server=resource_server,
            access_token=self.request_quote_grant(sender_wallet),
            incoming_payment_url=incoming_payment_url,
            sender_wallet=sender_wallet,
            debit_amount=Amount(value=debit_value, assetCode=asset_code, assetScale=asset_scale),
        )
        payment_id = self.create_outgoing_payment(
            resource_server=resource_server,
            outgoing_token=outgoing_token,
            quote_id=str(quote.id),
            sender_wallet=sender_wallet,
        )
        return str(payment_id), int(quote.receiveAmount.value)

    def list_incoming_payments(
        self, wallet_address: str, access_token: str
    ) -> list[IncomingPayment]:
        # Every incoming payment on the wallet, following the pagination cursor
        resource_server = self.discover_receiver_servers(wallet_address)["resource_server"]
        payments: list[IncomingPayment] = []
        cursor = None
        while True:
            fields = {"walletAddress": WalletAddress.model_validate(wallet_address), "first": 20}
            if cursor:
                fields["cursor"] = cursor
            page = self.client.incoming_payments.get_incoming_payments(
                query=PaymentListQuery.model_construct(**fields),
                resource_server_endpoint=resource_server,
                access_token=access_token,
            )
            payments.extend(page.result)
            if not page.pagination.hasNextPage or not page.pagination.endCursor:
                return payments
            cursor = page.pagination.endCursor

    def create_payment_request(
        self,
        receiver_wallet: str,
        amount_value: str,
        order_id: str,
        callback_base_url: str,
        sender_wallet: str
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

        sender_info = self.discover_receiver_servers(sender_wallet)
        quote_token = self.request_quote_grant(sender_wallet)
        quote = self.create_quote(
            resource_server=sender_info["resource_server"],
            access_token=quote_token,
            incoming_payment_url=incoming_payment_url,
            sender_wallet=sender_wallet,
        )

        redirect_url, outgoing_grant = self.request_outgoing_payment_grant(
            incoming_payment_url=incoming_payment_url,
            finish_uri=finish_uri,
            nonce=nonce,
            amount_value=str(quote.debitAmount.value),
            asset_code=str(quote.debitAmount.assetCode.root),
            asset_scale=int(quote.debitAmount.assetScale.root),
            sender_wallet=sender_wallet
        )

        return (
            redirect_url,
            outgoing_grant,
            sender_info["resource_server"],
            nonce,
            str(quote.id),
        )

    # def make_payment(self, order_id: str, state: dict, interact_ref: str):
    #     outgoing_token = self.continue_outgoing_payment_grant(
    #         outgoing_grant=state["outgoing_grant"],
    #         interact_ref=interact_ref,
    #     )
    #     payment_id = self.create_outgoing_payment(
    #         resource_server=state["sender_resource_server"],
    #         outgoing_token=outgoing_token,
    #         quote_id=state["quote_id"],
    #     )

        # return {
        #     "status": "completed",
        #     "payment_id": str(payment_id),
        #     "order_id": order_id,
        # }
