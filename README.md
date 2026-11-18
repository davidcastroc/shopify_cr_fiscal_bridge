# Shopify CR Fiscal Bridge

## Hace esto

- Convierte `Shopify Discount Product` en descuento real sobre la línea relacionada.
- No elimina Shipping, Custom Service, Tip, Duties, Gift Card, Refund Adjustment, etc.
- Si el gateway Shopify contiene `Tilopay`, fija el medio de pago CR en `02 - Tarjeta`.
- No modifica el core de Emipro.

## Importante

Los demás productos técnicos de Emipro se reutilizan como productos Odoo normales.
Si llegan a factura, deben tener su CABYS e impuesto configurados correctamente en Odoo.

## Prueba en staging

Crear una orden NUEVA en Shopify con producto, descuento, shipping y pago Tilopay.
Validar:

- SO total == Invoice total
- no aparece `Shopify Discount Product` en la factura
- el producto real lleva el porcentaje de descuento
- shipping sigue presente
- medio de pago = 02 Tarjeta
- los impuestos mantienen la configuración actual correcta

## Gift cards Shopify en Odoo 19

- La emisión independiente se contabiliza en el diario de ventas no fiscal GIFT. Se acredita la cuenta de pasivo conciliable 210009.000.001 y se omiten impuestos de la línea de emisión. Para otro código de pasivo, configurar el parámetro `shopify_cr_fiscal_bridge.gift_liability_code_<company_id>`.
- La redención se excluye de las líneas facturadas. El diario general MISC registra el débito al pasivo y el crédito a cuentas por cobrar, que se concilia con la factura. En pagos mixtos, se aplica el cobro de Tilopay mediante el diario y método del workflow. Si Tilopay ya se concilió, se comprueban diarios e importes antes de aplicar la tarjeta.
- Cada pedido admite un solo asiento de redención. Un reintento sobre la misma factura liquidada devuelve el resultado existente. Si falla algún cobro, Odoo revierte la liquidación completa.
- El XML registra la tarjeta de regalo como medio 99 con descripción y Tilopay como 02. Los montos deben sumar el total de la factura.
- Las redenciones requieren una factura completa del pedido. Emitir tarjetas separadamente de la mercancía. Revisar manualmente las facturas parciales, agrupadas y los reembolsos.

La prueba de staging se ejecuta así y revierte sus datos al terminar.

```sh
odoo-bin shell -d puraprote-dev-41739091 --no-http < tests/staging_gift_cards.py
```
