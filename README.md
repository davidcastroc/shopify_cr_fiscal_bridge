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
