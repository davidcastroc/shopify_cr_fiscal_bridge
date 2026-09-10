import logging

from odoo import models
from odoo.tools import float_is_zero

_logger = logging.getLogger(__name__)


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def _is_shopify_discount_technical_line(self):
        self.ensure_one()
        instance = self.order_id.shopify_instance_id

        return bool(
            instance
            and instance.discount_product_id
            and self.product_id == instance.discount_product_id
            and self.shopify_related_line_id
            and self.shopify_related_line_id.startswith("discount_")
        )

    def _get_shopify_discount_lines(self):
        self.ensure_one()

        instance = self.order_id.shopify_instance_id
        if not instance or not instance.discount_product_id or not self.shopify_line_id:
            return self.env["sale.order.line"]

        related_key = "discount_%s" % self.shopify_line_id

        return self.order_id.order_line.filtered(
            lambda line: (
                line.product_id == instance.discount_product_id
                and line.shopify_related_line_id == related_key
                and not float_is_zero(line.product_uom_qty, precision_digits=4)
            )
        )

    def _get_shopify_effective_discount_percentage(self):
        self.ensure_one()

        discount_lines = self._get_shopify_discount_lines()
        if not discount_lines:
            return self.discount or 0.0

        gross_amount = abs(self.price_unit * self.product_uom_qty)

        if float_is_zero(
            gross_amount,
            precision_rounding=self.currency_id.rounding,
        ):
            return self.discount or 0.0

        existing_discount_amount = gross_amount * ((self.discount or 0.0) / 100.0)

        shopify_discount_amount = sum(
            abs(line.price_unit * line.product_uom_qty)
            for line in discount_lines
        )

        total_discount_amount = existing_discount_amount + shopify_discount_amount
        percentage = (total_discount_amount / gross_amount) * 100.0

        return min(max(percentage, 0.0), 100.0)

    def _prepare_invoice_lines_vals_list(self, **optional_values):
        self.ensure_one()

        if self._is_shopify_discount_technical_line():
            return []

        return super()._prepare_invoice_lines_vals_list(**optional_values)

    def _prepare_invoice_line(self, **optional_values):
        self.ensure_one()

        vals = super()._prepare_invoice_line(**optional_values)

        if not self.order_id.shopify_instance_id:
            return vals

        # En las líneas de envío importadas desde Shopify, Emipro puede
        # arrastrar una descripción técnica/duplicada. Para la factura
        # mostramos únicamente el nombre actual del producto en Odoo.
        if self.is_delivery and self.product_id:
            vals["name"] = self.product_id.name

        discount_lines = self._get_shopify_discount_lines()
        if not discount_lines:
            return vals

        discount_percentage = self._get_shopify_effective_discount_percentage()
        vals["discount"] = discount_percentage

        _logger.info(
            "Shopify CR Fiscal Bridge: orden %s, línea %s, descuento convertido a %.6f%%",
            self.order_id.name,
            self.display_name,
            discount_percentage,
        )

        return vals
