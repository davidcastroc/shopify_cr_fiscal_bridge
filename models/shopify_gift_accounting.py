from odoo import _, Command, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = 'account.move'

    shopify_gift_invoice_id = fields.Many2one(
        'account.move', copy=False, readonly=True, ondelete='restrict',
    )
    shopify_gift_order_id = fields.Many2one('sale.order', copy=False, readonly=True, ondelete='restrict')
    _shopify_gift_order_unique = models.Constraint(
        'UNIQUE(shopify_gift_order_id)',
        'A Shopify gift card settlement already exists for this order.',
    )


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _shopify_gift_liability(self):
        company = self.company_id
        code = self.env['ir.config_parameter'].sudo().get_param(
            f'shopify_cr_fiscal_bridge.gift_liability_code_{company.id}', '210009.000.001',
        )
        account = self.env['account.account'].with_company(company).search([
            ('company_ids', 'in', company.ids), ('code', '=', code),
            ('account_type', '=', 'liability_current'), ('reconcile', '=', True),
        ], limit=1)
        if not account:
            raise UserError(_('Configure the Shopify gift card liability account for %s.', company.name))
        return account

    def _shopify_gift_issuance_lines(self):
        return self.order_line.filtered(
            lambda line: line.is_gift_card_line and line.price_unit > 0
        )

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        if self.shopify_instance_id and self._shopify_gift_issuance_lines():
            commercial = self.order_line.filtered(
                lambda line: not line.display_type
                and not line._is_shopify_gift_card_redemption_line()
                and not line._is_shopify_discount_technical_line()
            )
            if commercial - self._shopify_gift_issuance_lines():
                raise UserError(_('Invoice Shopify gift card issuance separately from merchandise.'))
            journals = self.env['account.journal'].search([
                ('company_id', '=', self.company_id.id), ('type', '=', 'sale'),
                ('l10n_cr_fiscal_journal', '=', False), ('code', '=', 'GIFT'),
            ])
            if len(journals) != 1:
                raise UserError(_('Configure the non-fiscal sales journal GIFT for Shopify gift card issuance.'))
            vals['journal_id'] = journals.id
        return vals

    def _shopify_gift_tender(self):
        self.ensure_one()
        if self.is_shopify_multi_payment:
            return self.currency_id.round(sum(self.shopify_payment_ids.filtered(
                lambda payment: payment.payment_gateway_id.code == 'gift_card'
            ).mapped('amount')))
        if self.shopify_payment_gateway_id.code == 'gift_card':
            lines = self.order_line.filtered(lambda line: line._is_shopify_gift_card_redemption_line())
            return self.currency_id.round(sum(-line.price_unit * line.product_uom_qty for line in lines))
        return 0.0

    def _shopify_gift_cash_payments(self):
        return self.shopify_payment_ids.filtered(lambda p: p.payment_gateway_id.code != 'gift_card')

    def _shopify_gift_invoice(self, invoice):
        currency = invoice.currency_id
        if (invoice.state != 'posted' or invoice.move_type != 'out_invoice'
                or currency != self.currency_id or invoice.company_id != self.company_id
                or invoice.invoice_line_ids.sale_line_ids.order_id != self):
            raise UserError(_('Shopify gift cards require a posted invoice for a single matching order.'))
        amount = self._shopify_gift_tender()
        payments = self._shopify_gift_cash_payments()
        cash = currency.round(sum(payments.mapped('amount')))
        if amount <= 0 or currency.compare_amounts(invoice.amount_total, amount + cash):
            raise UserError(_('Shopify gift card and other tenders must cover exactly one full invoice.'))
        move_model = self.env['account.move'].with_company(invoice.company_id)
        existing = move_model.search([('shopify_gift_order_id', '=', self.id)], limit=1)
        if existing:
            if (existing.state != 'posted' or existing.shopify_gift_invoice_id != invoice
                    or not currency.is_zero(invoice.amount_residual)):
                raise UserError(_('Review the existing Shopify gift card settlement before retrying.'))
            return
        receivable = invoice.line_ids.filtered(lambda line: line.account_type == 'asset_receivable')
        if len(receivable.account_id) != 1:
            raise UserError(_('The invoice must have a single receivable account.'))
        journal = self.env['account.journal'].search([
            ('company_id', '=', invoice.company_id.id), ('type', '=', 'general'), ('code', '=', 'MISC'),
        ], order='sequence, id', limit=1)
        if not journal:
            raise UserError(_('Configure a miscellaneous journal for gift card settlements.'))
        if not currency.compare_amounts(invoice.amount_residual, amount + cash):
            post_cash = True
        elif cash > 0 and not currency.compare_amounts(invoice.amount_residual, amount):
            previous = invoice._get_reconciled_payments()
            actual = sorted((p.journal_id.id, currency.round(p.amount)) for p in previous
                            if p.currency_id == currency)
            expected = sorted((p.workflow_id.journal_id.id, currency.round(p.amount)) for p in payments)
            if actual != expected:
                raise UserError(_('Existing payments do not match the other Shopify tenders.'))
            post_cash = False
        else:
            raise UserError(_('Review the existing payments before applying the Shopify gift card.'))
        company = invoice.company_id
        balance = currency._convert(amount, company.currency_id, company, invoice.date)
        common = {'partner_id': invoice.commercial_partner_id.id, 'currency_id': currency.id}
        settlement = move_model.create({
            'move_type': 'entry', 'journal_id': journal.id, 'date': invoice.date,
            'ref': _('Shopify gift card redemption for %s', invoice.name),
            'shopify_gift_invoice_id': invoice.id, 'shopify_gift_order_id': self.id,
            'line_ids': [
                Command.create(dict(common, account_id=self._shopify_gift_liability().id,
                                    amount_currency=amount, debit=balance)),
                Command.create(dict(common, account_id=receivable.account_id.id,
                                    amount_currency=-amount, credit=balance,
                                    date_maturity=invoice.invoice_date_due or invoice.date)),
            ],
        })
        settlement.action_post()
        (receivable + settlement.line_ids.filtered(
            lambda line: line.account_id == receivable.account_id
        )).filtered(lambda line: not line.reconciled).reconcile()
        if post_cash:
            for payment in payments:
                self._shopify_post_cash_tender(invoice, payment, receivable)
        if not currency.is_zero(invoice.amount_residual):
            raise UserError(_('The Shopify gift card and other tenders did not settle the invoice.'))

    def _shopify_post_cash_tender(self, invoice, tender, receivable):
        workflow = tender.workflow_id
        vals = invoice.prepare_payment_dict(workflow)
        payment_method = workflow.journal_id.inbound_payment_method_line_ids.filtered(
            lambda line: line.payment_method_id == workflow.inbound_payment_method_id
        )[:1]
        if not payment_method or tender.amount <= 0:
            raise UserError(_('Check the Shopify tender amount and inbound payment configuration.'))
        vals.pop('payment_method_id', None)
        vals.update(amount=tender.amount, payment_method_line_id=payment_method.id,
                    destination_account_id=receivable.account_id.id)
        payment = self.env['account.payment'].create(vals)
        payment.action_post()
        counterpart = payment.move_id.line_ids.filtered(
            lambda line: line.account_id == receivable.account_id and not line.reconciled
        )
        if not counterpart:
            raise UserError(_('Configure an outstanding receipts account for %s.', workflow.journal_id.display_name))
        (counterpart + receivable.filtered(lambda line: not line.reconciled)).reconcile()

    def paid_invoice_ept(self, invoices):
        self.ensure_one()
        if not self.shopify_instance_id:
            return super().paid_invoice_ept(invoices)
        if not self._shopify_gift_tender():
            if self.shopify_payment_gateway_id.code == 'gift_card' or self.order_line.filtered(
                lambda line: line._is_shopify_gift_card_redemption_line()
            ):
                raise UserError(_('The Shopify gift card amount is missing. Check the imported transactions.'))
            return super().paid_invoice_ept(invoices)
        with self.env.cr.savepoint():
            for invoice in invoices:
                self._shopify_gift_invoice(invoice)
        return True


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _prepare_invoice_line(self, **optional_values):
        vals = super()._prepare_invoice_line(**optional_values)
        if self.order_id.shopify_instance_id and self.is_gift_card_line and self.price_unit > 0:
            vals.update(account_id=self.order_id._shopify_gift_liability().id,
                        tax_ids=[Command.clear()])
        return vals


class ElectronicDocument(models.Model):
    _inherit = 'ce.document'

    def get_payment_way(self, cedoc=None, classdoc=None):
        self.ensure_one()
        invoice = self.invoice_id
        if invoice.move_type != 'out_invoice':
            return super().get_payment_way(cedoc, classdoc)
        orders = invoice.invoice_line_ids.sale_line_ids.order_id
        gift_orders = orders.filtered(lambda order: order.shopify_instance_id and order._shopify_gift_tender())
        if not gift_orders:
            return super().get_payment_way(cedoc, classdoc)
        if len(orders) != 1:
            raise UserError(_('Shopify gift card redemptions require a separate invoice for each order.'))
        currency = invoice.currency_id
        gift = orders._shopify_gift_tender()
        other = orders._shopify_gift_cash_payments()
        cash = currency.round(sum(other.mapped('amount')))
        if gift <= 0 or currency.compare_amounts(abs(self.xml_amount_total), gift + cash):
            raise UserError(_('Shopify tenders must match the electronic invoice total.'))
        methods = [classdoc.MedioPago(TipoMedioPago='99', MedioPagoOtros='Tarjeta de regalo Shopify',
                                      TotalMedioPago=gift)]
        if other:
            if any('tilopay' not in (p.payment_gateway_id.code or '').lower() for p in other):
                raise UserError(_('Configure the fiscal payment code for the other Shopify tender.'))
            methods.append(classdoc.MedioPago(TipoMedioPago='02', TotalMedioPago=cash))
        return methods
