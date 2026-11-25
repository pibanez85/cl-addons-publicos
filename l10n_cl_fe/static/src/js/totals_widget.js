import { TaxTotalsComponent } from "@account/components/tax_totals/tax_totals";
import { patch } from "@web/core/utils/patch";

/**
 * Odoo 20: ``tax_totals`` es el estándar (subtotals[].tax_groups[]) y no trae la retención.
 * ``account.move.amount_total`` sí la descuenta (impuestos ``sii_type`` R/A, ver
 * ``_compute_amount``): si el documento tiene retención, se muestra la fila "Retención" con la
 * diferencia y el total pasa a ser ``amount_total``, igual que en 17.0.
 */
patch(TaxTotalsComponent.prototype, {
    formatData(props) {
        const totals = super.formatData(...arguments);
        const data = props.record.data;
        if (!totals || !data.amount_tax_retencion || typeof data.amount_total !== "number") {
            return totals;
        }
        const ajuste = data.amount_total - totals.total_amount_currency;
        if (Math.abs(ajuste) < 1e-9) {
            return totals;
        }
        totals.l10n_cl_retencion_amount_currency = ajuste;
        totals.total_amount_currency = data.amount_total;
        return totals;
    },
});
