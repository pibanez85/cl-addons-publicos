/** @odoo-module **/

import { registry } from "@web/core/registry";
import { booleanField, BooleanField } from "@web/views/fields/boolean/boolean_field";
import { useService } from "@web/core/utils/hooks";
import { Dialog } from "@web/core/dialog/dialog";

import { Component } from "@odoo/owl";



export class L10nCLFEUpgradeDialog extends Component {
    setup() {
        this.orm = useService("orm");
        this.router = useService("router");
    }
    async _confirmUpgrade() {
        const usersCount = await this.orm.call("res.users", "search_count", [
            [["share", "=", false]],
        ]);
        window.open("https://globalresponse.cl/upgrade?num_users=" + usersCount, "_blank");
        this.props.close();
    }
}
L10nCLFEUpgradeDialog.template = "l10n_cl_fe.ProUpgrade";
L10nCLFEUpgradeDialog.components = { Dialog };


export class UpgradeBooleanField extends BooleanField {
    setup() {
        super.setup();
        this.dialogService = useService("dialog");
        this.isEnterprise = odoo.info && odoo.info.isEnterprise;
    }

    async onChange(newValue) {
        if (!this.isEnterprise) {
            this.dialogService.add(
                L10nCLFEUpgradeDialog,
                {},
                {
                    onClose: () => {
                        this.props.record.update({ [this.props.name]: false });
                    },
                }
            );
        } else {
            super.onChange(...arguments);
        }
    }
}

export const upgradeBooleanField = {
    ...booleanField,
    component: UpgradeBooleanField,
    isUpgradeField: true,
    additionalClasses: [...(booleanField.additionalClasses || []), "o_field_boolean"],
};



registry.category("fields").add("upgrade_odoocoop_boolean", upgradeBooleanField)//.add("upgrade_odoocoop_radio", UpgradeRadio);
