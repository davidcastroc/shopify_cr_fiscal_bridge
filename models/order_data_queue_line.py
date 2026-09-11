import json
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class ShopifyOrderDataQueueLine(models.Model):
    _inherit = "shopify.order.data.queue.line.ept"

    def process_import_order_queue_data(self, update_order=False):
        """Process paid updates found by the scheduled Shopify import.

        Emipro's normal scheduled import considers an already existing Shopify
        sale order as processed and discards the payload. If Shopify changed an
        order from ``pending`` to ``paid`` and the paid webhook was not received,
        the sale order therefore remains on the pending workflow and no invoice
        is created.

        For scheduled queue lines whose current Shopify financial status is
        ``paid``, this bridge checks whether the corresponding sale order already
        exists and still has no effective invoice. In that specific case it uses
        Emipro's own ``update_shopify_order`` path, which resolves the paid
        workflow and performs the native invoice/payment processing.

        Every other queue line continues through Emipro unchanged.
        """
        if update_order:
            return super().process_import_order_queue_data(update_order=True)

        sale_order_model = self.env["sale.order"]
        normal_lines = self.env["shopify.order.data.queue.line.ept"]

        for line in self:
            queue = line.shopify_order_data_queue_id

            # Only provide a fallback for scheduled imports that still carry
            # their Shopify payload.
            if (
                not queue
                or queue.created_by != "scheduled_action"
                or not line.order_data
            ):
                normal_lines |= line
                continue

            try:
                order_data = json.loads(line.order_data)
            except (TypeError, ValueError, json.JSONDecodeError):
                normal_lines |= line
                continue

            if order_data.get("financial_status") != "paid":
                normal_lines |= line
                continue

            instance = line.shopify_instance_id or queue.shopify_instance_id
            if not instance:
                normal_lines |= line
                continue

            existing_order = sale_order_model.search_existing_shopify_order(
                order_data,
                instance,
                order_data.get("order_number"),
            )

            # New order: let Emipro create it through its standard import path.
            if not existing_order:
                normal_lines |= line
                continue

            # Idempotency guard. If an effective invoice already exists, do not
            # run the paid workflow again. Let Emipro consume the duplicate
            # scheduled payload normally.
            effective_invoices = existing_order.invoice_ids.filtered(
                lambda invoice: invoice.state != "cancel"
            )
            if effective_invoices:
                _logger.info(
                    "Shopify CR Fiscal Bridge: order %s already has invoice(s) %s; "
                    "scheduled paid queue line %s continues through normal processing.",
                    existing_order.name,
                    ", ".join(effective_invoices.mapped("name")),
                    line.id,
                )
                normal_lines |= line
                continue

            _logger.info(
                "Shopify CR Fiscal Bridge: existing order %s is paid in Shopify "
                "but has no invoice. Processing scheduled queue line %s through "
                "Emipro's update flow.",
                existing_order.name,
                line.id,
            )

            sale_order_model.update_shopify_order(
                line,
                "Scheduled Action",
                instance,
            )

        if normal_lines:
            return super(
                ShopifyOrderDataQueueLine,
                normal_lines,
            ).process_import_order_queue_data(update_order=False)

        return True
