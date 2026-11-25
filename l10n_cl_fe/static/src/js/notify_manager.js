import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";

/**
 * Avisos de los asistentes del SII (apicaf): el servidor los envía por el bus con
 * ``bus.bus._sendone(partner, "dte.caf.apicaf/display_notification", {...})``.
 * Reemplaza al notify_manager legado de 17.0 (WebClient.include, ya desactivado).
 */
export const dteNotificationService = {
    dependencies: ["bus_service", "notification"],

    start(env, { bus_service, notification }) {
        bus_service.subscribe("dte.caf.apicaf/display_notification", (payload) => {
            const { title, message, url } = payload || {};
            const buttons = [];
            if (url && url.uri && /^https?:\/\//.test(url.uri)) {
                buttons.push({
                    name: url.name || _t("Abrir"),
                    primary: false,
                    onClick: () => browser.open(url.uri, "_blank", "noopener"),
                });
            }
            notification.add(message || "", {
                title: title || _t("Facturación electrónica"),
                type: "warning",
                sticky: payload && "sticky" in payload ? Boolean(payload.sticky) : true,
                buttons,
            });
        });
    },
};

registry.category("services").add("l10n_cl_fe_dte_notification", dteNotificationService);
