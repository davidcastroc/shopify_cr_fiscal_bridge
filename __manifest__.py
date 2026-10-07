{
    'name': 'Shopify CR Fiscal Bridge',
    'version': '19.0.1.0.1',
    'summary': 'Normalización fiscal de pedidos Shopify/Emipro para Costa Rica',
    'category': 'Sales',
    'author': 'Castro Li',
    'license': 'LGPL-3',
    'depends': ['sale', 'sale_stock', 'account', 'shopify_ept', 'l10n_cr_invoice'],
    'data': ['data/decimal_precision.xml', 'views/sale_order_views.xml'],
    'installable': True,
    'application': False,
}
