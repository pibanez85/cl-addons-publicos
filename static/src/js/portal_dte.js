/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.PortalDTE = publicWidget.Widget.extend({
    selector: '.l10n_cl_fe',
    events: {
        "keyup .l10n_cl_rut": '_formatRUT',
        "change .l10n_cl_partner_search": '_setPartner',
        "change .l10n_cl_email": '_fillDTEEmail',
        "change .l10n_cl_state, change select[name='state_id']": '_populateCities',
        "change .l10n_cl_city, change select[name='city_id']": '_onChangeCity',
    },

    init() {
        this._super(...arguments);
        this.rpc = this.bindService("rpc");
    },

    clean: function(rut) {
        return typeof rut === 'string'
            ? rut.replace(/^0+|[^0-9kK]+/g, '').toUpperCase()
            : '';
    },

    validate: function(rut) {
        if (typeof rut !== 'string') {
            return false;
        }
        if (!/^0*(\d{1,3}(\.?\d{3})*)-?([\dkK])$/.test(rut)) {
            return false;
        }
        rut = this.clean(rut);
        let t = parseInt(rut.slice(0, -1), 10);
        let m = 0;
        let s = 1;
        while (t > 0) {
            s = (s + (t % 10) * (9 - m++ % 6)) % 11;
            t = Math.floor(t / 10);
        }
        const v = s > 0 ? String(s - 1) : 'K';
        return v === rut.slice(-1);
    },

    _formatRUT: function(ev) {
        const rut = this.clean(ev.target.value);
        let result = rut.slice(-4, -1);
        if (result === "") {
            result = rut;
        } else {
            result += '-' + rut.substr(rut.length - 1);
        }
        for (let i = 4; i < rut.length; i += 3) {
            result = rut.slice(-3 - i, -i) + '.' + result;
        }
        ev.target.value = result;
    },

    _setPartner: async function(ev) {
        const rut = ev.target.value;
        const data = await this.rpc("/portal/partner_remote", { rut: rut });
        if (!data) {
            return;
        }
        if (data.razon_social) {
            const nameInput = this.el.querySelector(".l10n_cl_name");
            if (nameInput) {
                nameInput.value = data.razon_social;
            }
        }
        if (data.email) {
            const emailInput = this.el.querySelector(".l10n_cl_email");
            if (emailInput) {
                emailInput.value = data.email;
            }
        }
        if (data.dte_email) {
            const dteEmailInput = this.el.querySelector(".l10n_cl_dte_email");
            if (dteEmailInput) {
                dteEmailInput.value = data.dte_email;
            }
        }
        if (data.glosa_giro) {
            const giroInput = this.el.querySelector(".l10n_cl_giro");
            if (giroInput) {
                giroInput.value = data.glosa_giro;
            }
        }
        if (data.direccion) {
            const streetInput = this.el.querySelector(".l10n_cl_street");
            if (streetInput) {
                streetInput.value = data.direccion;
            }
        }
        if (data.phone) {
            const phoneInput = this.el.querySelector(".l10n_cl_phone");
            if (phoneInput) {
                phoneInput.value = data.phone;
            }
        }
    },

    _fillDTEEmail: function(ev) {
        const dteEmailInput = this.el.querySelector(".l10n_cl_dte_email");
        if (dteEmailInput && !dteEmailInput.value) {
            dteEmailInput.value = ev.target.value;
        }
    },

    _populateCities: async function(ev) {
        const value = ev.target.value;
        const selectCity = this.el.querySelector("select[name='city_id']") || this.el.querySelector(".l10n_cl_city");
        if (!selectCity || !value) {
            return;
        }
        let data;
        try {
            data = await this.rpc("/portal/state_infos/" + value, { mode: "billing" });
        } catch (err) {
            console.warn("l10n_cl_fe: fallo al cargar comunas", err);
            return;
        }
        while (selectCity.firstChild) {
            selectCity.removeChild(selectCity.firstChild);
        }
        const defaultOpt = document.createElement('option');
        defaultOpt.textContent = 'Seleccione Comuna...';
        defaultOpt.value = '';
        selectCity.add(defaultOpt);
        const parentDiv = selectCity.closest('div');
        if (data && data.cities && data.cities.length) {
            data.cities.forEach(function(city) {
                const opt = document.createElement('option');
                opt.textContent = city.name;
                opt.value = city.id;
                if (city.code) {
                    opt.dataset.code = city.code;
                }
                if (city.zipcode) {
                    opt.dataset.zipcode = city.zipcode;
                }
                selectCity.add(opt);
            });
            if (parentDiv) {
                parentDiv.style.display = '';
            }
        } else if (parentDiv) {
            parentDiv.style.display = 'none';
        }
    },

    _onChangeCity: function(ev) {
        const selectCity = ev.currentTarget;
        const selectedOption = selectCity.options[selectCity.selectedIndex];
        const zipcode = selectedOption ? selectedOption.dataset.zipcode : null;
        if (zipcode) {
            const zipInput = this.el.querySelector(".l10n_cl_zipcode");
            if (zipInput) {
                zipInput.value = zipcode;
            }
        }
    }
});
