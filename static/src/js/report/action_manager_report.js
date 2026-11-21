/** @odoo-module **/

import { registry } from "@web/core/registry";
import { download } from "@web/core/network/download";
import { getReportUrl } from "@web/webclient/actions/reports/utils";

registry
    .category("ir.actions.report handlers")
    .add("l10n_cl_fe_xlsx", async (action, options, env) => {
        if (action.report_type !== "xlsx") {
            return false;
        }
        const url = getReportUrl(action, "xlsx");
        const downloadContext = { ...env.services.user.context };
        if (action.context) {
            Object.assign(downloadContext, action.context);
        }
        env.services.ui.block();
        try {
            await download({
                url,
                data: {
                    data: JSON.stringify([url, action.report_type]),
                    context: JSON.stringify(downloadContext),
                },
            });
        } finally {
            env.services.ui.unblock();
        }
        return true;
    });
