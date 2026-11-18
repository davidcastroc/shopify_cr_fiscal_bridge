# Run with odoo-bin shell on Pura Prote staging. All fixtures are rolled back.
from io import BytesIO
from xml.etree import ElementTree

from odoo import Command
from odoo.addons.l10n_cr_invoice.models import FE


def check(env):
    company = env.company
    instance = env['shopify.instance.ept'].search([('gift_card_product_id', '!=', False)], limit=1)
    gift_gateway = env['shopify.payment.gateway.ept'].search([('code', '=', 'gift_card')], limit=1)
    card_gateway = env['shopify.payment.gateway.ept'].search([('code', 'ilike', 'tilopay')], limit=1)
    workflow = env['sale.workflow.process.ept'].search([('name', '=', 'Card')], limit=1)
    assert instance and gift_gateway and card_gateway and workflow
    product = instance.gift_card_product_id
    partner = env['res.partner'].create({'name': 'Shopify gift card staging test'})
    journal_domain = [('type', '=', 'sale'), ('company_id', '=', company.id),
                      ('l10n_cr_fiscal_journal', '=', False), ('code', '=', 'GIFT')]
    journal = env['account.journal'].search(journal_domain) or env['account.journal'].create({
        'name': 'Gift card test only', 'type': 'sale', 'code': 'GIFT', 'company_id': company.id,
        'l10n_cr_fiscal_journal': False,
    })
    assert len(journal) == 1
    income = env['account.account'].search([
        ('account_type', '=', 'income'), ('company_ids', 'in', company.ids),
    ], limit=1)

    def order(gateway):
        return env['sale.order'].create({
            'partner_id': partner.id, 'company_id': company.id,
            'shopify_instance_id': instance.id, 'shopify_payment_gateway_id': gateway.id,
        })

    def line(sale, price, **kwargs):
        return env['sale.order.line'].create(dict(
            order_id=sale.id, product_id=product.id, price_unit=price, product_uom_qty=1, **kwargs,
        ))

    def invoice(sale_line, amount, account):
        return env['account.move'].create({
            'move_type': 'out_invoice', 'company_id': company.id, 'partner_id': partner.id,
            'journal_id': journal.id, 'currency_id': sale_line.currency_id.id,
            'invoice_line_ids': [Command.create({
                'name': 'Gift card staging test', 'quantity': 1, 'price_unit': amount,
                'account_id': account.id, 'tax_ids': [Command.clear()],
                'sale_line_ids': [Command.link(sale_line.id)],
            })],
        })

    issuance = order(card_gateway)
    issue_line = line(issuance, 50000, is_gift_card_line=True)
    liability = issuance._shopify_gift_liability()
    assert issuance._prepare_invoice()['journal_id'] == journal.id
    assert issue_line._prepare_invoice_line()['account_id'] == liability.id
    issuance.state = 'sale'
    issue_invoice = issuance._create_invoices()
    issue_invoice.action_post()
    assert issue_invoice.invoice_line_ids.credit == 50000 and not issue_invoice.document_id
    print('PASS: issuance credits the liability and creates no fiscal document')

    for cash, already_paid in ((0, False), (115.50, False), (115.50, True)):
        sale = order(card_gateway if cash else gift_gateway)
        goods = line(sale, 50000 + cash)
        redemption = line(sale, -50000)
        assert redemption._is_shopify_gift_card_redemption_line()
        assert not redemption._prepare_invoice_lines_vals_list()
        if cash:
            sale.write({'is_shopify_multi_payment': True, 'shopify_payment_ids': [
                Command.create({'payment_gateway_id': gift_gateway.id, 'workflow_id': workflow.id,
                                'payment_transaction_id': 'test-gift', 'amount': 50000}),
                Command.create({'payment_gateway_id': card_gateway.id, 'workflow_id': workflow.id,
                                'payment_transaction_id': 'test-card', 'amount': cash}),
            ]})
        bill = invoice(goods, 50000 + cash, income)
        bill.action_post()
        doc = env['ce.document'].new({'invoice_id': bill.id})
        methods = doc.get_payment_way(None, FE)
        assert [m.TipoMedioPago for m in methods] == (['99', '02'] if cash else ['99'])
        assert sum(m.TotalMedioPago for m in methods) == bill.amount_total
        buffer = BytesIO()
        methods[0].export(buffer, 0, name_='MedioPago')
        node = ElementTree.fromstring(buffer.getvalue())
        assert node.findtext('MedioPagoOtros') == 'Tarjeta de regalo Shopify'
        if already_paid:
            receivable = bill.line_ids.filtered(lambda l: l.account_type == 'asset_receivable')
            sale._shopify_post_cash_tender(bill, sale._shopify_gift_cash_payments(), receivable)
        for attempt in range(2):
            sale.paid_invoice_ept(bill)
            assert bill.currency_id.is_zero(bill.amount_residual)
        entries = env['account.move'].search([('shopify_gift_order_id', '=', sale.id)])
        assert len(entries) == 1 and entries.line_ids.filtered(lambda l: l.account_id == liability).debit == 50000
        assert len(bill._get_reconciled_payments()) == bool(cash)
        print('PASS: redemption, XML and retry with cash amount', cash, 'previously paid', already_paid)


try:
    check(env)
finally:
    env.cr.rollback()
