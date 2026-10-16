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

    def create_quote(
        self,
        resource_server: str,
        access_token: str,
        incoming_payment_url: str,
        sender_wallet: str,
        debit_amount: Amount | None = None,
    ):
        
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
            **{
                "access_token": {
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

    def continue_outgoing_payment_grant(self, outgoing_grant, interact_ref: str):
        continue_token = outgoing_grant.root.cont.access_token.value
        continue_uri = str(outgoing_grant.root.cont.uri)
        final_grant = self.client.grants.post_grant_continuation_request(
            interact_ref=InteractRef(interact_ref=interact_ref),
            continue_uri=continue_uri,
            access_token=continue_token,
        )
        return final_grant.access_token.value

    def create_outgoing_payment(
        self,
        resource_server: str,
        outgoing_token: str,
        quote_id: str,
        sender_wallet: str,
    ):
        payment = OutgoingPaymentRequestWithQuote.model_construct(
            walletAddress=WalletAddress.model_validate(sender_wallet),
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
        self, receiver_wallet: str, amount_value: str, order_id: str, callback_base_url: str, sender_wallet: str
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

    #     return {"status": "completed", "payment_id": str(payment_id), "order_id": order_id}
