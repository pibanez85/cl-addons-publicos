/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { AccountFileUploader } from "@account/components/bills_upload/bills_upload";

const SII_DTE_RE = /<\s*(?:EnvioDTE|SetDTE|DTE)\b/;

function isSiiDteFile(file) {
    if (!file || !file.data) return false;
    if (!(file.name || "").toLowerCase().endsWith(".xml")) return false;
    try {
        const head = atob(file.data.slice(0, 2048));
        return SII_DTE_RE.test(head);
    } catch (e) {
        return false;
    }
}

patch(AccountFileUploader.prototype, {
    async onFileUploaded(file) {
        if (isSiiDteFile(file)) {
            this.dteFiles = this.dteFiles || [];
            this.dteFiles.push(file);
            return;
        }
        return super.onFileUploaded(file);
    },

    async onUploadComplete() {
        const dteList = this.dteFiles || [];
        this.dteFiles = [];
        const hasNonDte = this.attachmentIdsToProcess.length > 0;

        if (!dteList.length) {
            return super.onUploadComplete();
        }
        if (hasNonDte) {
            await super.onUploadComplete();
        }

        const journalId = this.extraContext.default_journal_id || false;
        let action = null;
        for (const f of dteList) {
            action = await this.orm.call(
                "sii.dte.upload_xml.wizard",
                "create_from_attachment",
                [f.data, f.name, journalId],
            );
        }
        if (action) {
            this.action.doAction(action);
        }
    },
});
