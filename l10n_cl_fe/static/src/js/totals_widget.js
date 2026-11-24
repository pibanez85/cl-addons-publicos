/** @odoo-module */
import { TaxTotalsComponent } from "@account/components/tax_totals/tax_totals";
import { formatMonetary } from "@web/views/fields/formatters";
import { patch } from "@web/core/utils/patch";

function _retencion(totals) {
    let r = 0;
    for (const name of Object.keys(totals.groups_by_subtotal || {})) {
        for (const g of totals.groups_by_subtotal[name]) {
            r += g.tax_group_amount_retencion || 0;
        }
    }
    return r;
}

patch(TaxTotalsComponent.prototype, {
    formatData(props) {
        super.formatData(...arguments);
        const totals = this.totals;
        if (!totals || !totals.groups_by_subtotal) {
            return;
        }
        const ret = _retencion(totals);
        if (!ret) {
            return;
        }
        const currencyFmtOpts = {
            currencyId: props.record.data.currency_id && props.record.data.currency_id[0],
        };
        totals.amount_tax_retencion = ret;
        totals.formatted_amount_retencion = formatMonetary(-ret, currencyFmtOpts);
        totals.amount_total = totals.amount_total - ret;
        totals.formatted_amount_total = formatMonetary(totals.amount_total, currencyFmtOpts);
    },
});
