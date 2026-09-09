import logging

from odoo import models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

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
