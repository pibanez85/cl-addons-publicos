# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal


class PortalDTE(http.Controller):

    @http.route(['/portal/state_infos/<model("res.country.state"):state>'], type='json', auth="public", methods=['POST'], website=True)
    def state_infos(self, state, mode='billing', **kw):
        return dict(
            cities=[{
                'id': city.id,
                'name': city.name,
                'code': city.code,
                'zipcode': city.zipcode,
            } for city in state.get_website_sale_cities(mode=mode)]
        )

    @http.route(['/portal/partner_remote'], type='json', methods=['POST'], auth="public", website=True)
    def partner_remote(self, rut, **kw):
        return request.env['res.partner'].sudo().get_remote_user_data(rut, process_data=False)


class CustomerPortalCL(CustomerPortal):

    def _get_portal_account_cities(self, partner, post):
        state_id = post.get('state_id') or partner.state_id.id
        try:
            state_id = int(state_id) if state_id else False
        except (TypeError, ValueError):
            state_id = False
        if not state_id:
            return request.env['res.city']
        state = request.env['res.country.state'].sudo().browse(state_id)
        return state.get_website_sale_cities()

    def _get_portal_account_selected_city(self, partner, post):
        city_id = post.get('city_id') or partner.city_id.id
        try:
            return int(city_id) if city_id else False
        except (TypeError, ValueError):
            return False

    @http.route()
    def account(self, redirect=None, **post):
        response = super().account(redirect=redirect, **post)
        if getattr(response, 'qcontext', None) is not None:
            partner = request.env.user.partner_id
            response.qcontext['cities'] = self._get_portal_account_cities(partner, post)
            response.qcontext['selected_city_id'] = self._get_portal_account_selected_city(partner, post)
        return response
