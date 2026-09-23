import logging

from odoo import models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _compute_invoice_status(self):
        """
        Las líneas técnicas utilizadas por Shopify para representar descuentos
        no son artículos facturables independientes.

        El descuento ya se incorpora en la línea real del producto, por lo que
        estas líneas técnicas no deben impedir que una orden completamente
        facturada quede en estado "Facturado".
        """
        super()._compute_invoice_status()

        for order in self.filtered(
            lambda so: so.state == "sale" and so.shopify_instance_id
        ):
            relevant_lines = order.order_line.filtered(
                lambda line: (
                    not line.display_type
                    and not line.is_downpayment
                    and not line._is_shopify_discount_technical_line()
                )
            )

            if not relevant_lines:
                continue

            statuses = relevant_lines.mapped("invoice_status")

            if all(status == "invoiced" for status in statuses):
                order.invoice_status = "invoiced"
            elif all(
                status in ("invoiced", "upselling")
                for status in statuses
            ):
                order.invoice_status = "upselling"

    def _prepare_invoice(self):
        self.ensure_one()
        vals = super()._prepare_invoice()

        if not self.shopify_instance_id:
            return vals

        gateway_name = (self.shopify_payment_gateway_id.display_name or "").strip().lower()

        if "tilopay" in gateway_name:
            payment_method = self.env.ref(
                "l10n_cr_invoice.l10n_cr_payment_method_02",
                raise_if_not_found=False,
            )
            if not payment_method:
                payment_method = self.env["l10n_cr.payment_method"].search(
                    [("code", "=", "02")],
                    limit=1,
                )

            if payment_method:
                vals["l10n_cr_payment_method_id"] = payment_method.id
            else:
                _logger.warning(
                    "Shopify CR Fiscal Bridge: no se encontró el medio de pago CR 02 para %s",
                    self.name,
                )

        return vals
