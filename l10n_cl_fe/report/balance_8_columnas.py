from odoo import _, api, models
from odoo.exceptions import UserError


class ReportBalance8Columnas(models.AbstractModel):
    _name = "report.l10n_cl_fe.report_balance_8_columnas_document"
    _description = "Balance de 8 Columnas (Chile) - QWeb"

    def _get_wizard(self, docids, data):
        if data and data.get("form", {}).get("wizard_id"):
            return self.env["l10n_cl_fe.balance8col.wizard"].browse(
                data["form"]["wizard_id"]
            )
        if docids:
            return self.env["l10n_cl_fe.balance8col.wizard"].browse(docids)
        raise UserError(_("Debe abrir el reporte desde el asistente."))

    def _serialize_lines(self, wizard):
        rows = []
        for line in wizard.line_ids:
            rows.append({
                "code": line.code,
                "name": line.name,
                "account_type": line.account_type,
                "debit": line.debit,
                "credit": line.credit,
                "deudor": line.deudor,
                "acreedor": line.acreedor,
                "activo": line.activo,
                "pasivo": line.pasivo,
                "perdida": line.perdida,
                "ganancia": line.ganancia,
            })
        return rows

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self._get_wizard(docids, data)
        # Sin escribir en el asistente si no hay líneas: la ruta /report es de solo lectura.
        wizard._ensure_lines()
        rows = self._serialize_lines(wizard)
        totals = {key: 0.0 for key in ("debit", "credit", "deudor", "acreedor",
                                       "activo", "pasivo", "perdida", "ganancia")}
        for row in rows:
            for key in totals:
                totals[key] += row[key]
        resultado_inv = totals["activo"] - totals["pasivo"]
        resultado_res = totals["ganancia"] - totals["perdida"]
        currency = wizard.company_id.currency_id
        descuadre_sumas = currency.round(totals["debit"] - totals["credit"])
        descuadre_saldos = currency.round(totals["deudor"] - totals["acreedor"])
        descuadre_resultado = currency.round(resultado_inv + resultado_res)
        descuadre = max(abs(descuadre_sumas), abs(descuadre_saldos), abs(descuadre_resultado))
        return {
            "doc_ids": wizard.ids,
            "doc_model": "l10n_cl_fe.balance8col.wizard",
            "docs": wizard,
            "data": wizard._build_data(),
            "company": wizard.company_id,
            "rows": rows,
            "totals": totals,
            "resultado_inv": resultado_inv,
            "resultado_res": resultado_res,
            "descuadre": descuadre if descuadre > 0 else 0,
        }


class ReportBalance8ColumnasXlsx(models.AbstractModel):
    _name = "report.l10n_cl_fe.report_balance_8_columnas_xlsx"
    _description = "Balance de 8 Columnas (Chile) - XLSX"
    _inherit = "report.l10n_cl_fe.abstract"

    def _get_wizard(self, docids, data):
        if data and data.get("form", {}).get("wizard_id"):
            return self.env["l10n_cl_fe.balance8col.wizard"].browse(
                data["form"]["wizard_id"]
            )
        if docids:
            return self.env["l10n_cl_fe.balance8col.wizard"].browse(docids)
        raise UserError(_("No se pudo determinar el asistente origen."))

    def generate_xlsx_report(self, workbook, data, objs):
        wizard = self._get_wizard(objs.ids if objs else None, data)
        # Sin escribir en el asistente si no hay líneas: la ruta /report es de solo lectura.
        wizard._ensure_lines()
        sheet = workbook.add_worksheet("Balance 8 Columnas")
        bold = workbook.add_format({"bold": True})
        title = workbook.add_format({"bold": True, "align": "center", "font_size": 14})
        header_lbl = workbook.add_format({"bold": True, "bg_color": "#EEEEEE"})
        money = workbook.add_format({"num_format": "#,##0"})
        money_bold = workbook.add_format({"bold": True, "num_format": "#,##0",
                                          "top": 2})

        sheet.set_column(0, 0, 12)
        sheet.set_column(1, 1, 40)
        sheet.set_column(2, 9, 14)

        company = wizard.company_id
        partner = company.partner_id
        sheet.merge_range(0, 0, 0, 9, "BALANCE TRIBUTARIO DE 8 COLUMNAS", title)
        sheet.merge_range(1, 0, 1, 9, partner.name or company.name, title)
        rut = partner.rut() if partner and hasattr(partner, "rut") else (partner.vat or "")
        sheet.write(3, 0, "RUT:", header_lbl)
        sheet.write(3, 1, rut)
        sheet.write(3, 4, "Período:", header_lbl)
        sheet.write(3, 5, "Del %s al %s" % (wizard.date_from, wizard.date_to))
        sheet.write(4, 0, "Giro:", header_lbl)
        sheet.write(4, 1, company.activity_description.name if company.activity_description else "")
        sheet.write(4, 4, "Moneda:", header_lbl)
        sheet.write(4, 5, company.currency_id.name)
        sheet.write(5, 0, "Domicilio:", header_lbl)
        domicilio = " ".join(filter(None, [
            partner.street or "", partner.street2 or "",
            ("," + partner.city) if partner.city else "",
        ])).strip()
        sheet.merge_range(5, 1, 5, 9, domicilio)

        headers_top = ["Código", "Cuenta", "Sumas", "", "Saldos", "",
                       "Inventario", "", "Resultados", ""]
        for col, value in enumerate(headers_top):
            sheet.write(7, col, value, bold)
        sheet.merge_range(7, 2, 7, 3, "Sumas", bold)
        sheet.merge_range(7, 4, 7, 5, "Saldos", bold)
        sheet.merge_range(7, 6, 7, 7, "Inventario", bold)
        sheet.merge_range(7, 8, 7, 9, "Resultados", bold)
        headers_bot = ["", "", "Debe", "Haber", "Deudor", "Acreedor",
                       "Activo", "Pasivo", "Pérdida", "Ganancia"]
        for col, value in enumerate(headers_bot):
            sheet.write(8, col, value, bold)

        totals = {key: 0.0 for key in ("debit", "credit", "deudor", "acreedor",
                                       "activo", "pasivo", "perdida", "ganancia")}
        row = 9
        for line in wizard.line_ids:
            sheet.write(row, 0, line.code or "")
            sheet.write(row, 1, line.name or "")
            sheet.write_number(row, 2, line.debit, money)
            sheet.write_number(row, 3, line.credit, money)
            sheet.write_number(row, 4, line.deudor, money)
            sheet.write_number(row, 5, line.acreedor, money)
            sheet.write_number(row, 6, line.activo, money)
            sheet.write_number(row, 7, line.pasivo, money)
            sheet.write_number(row, 8, line.perdida, money)
            sheet.write_number(row, 9, line.ganancia, money)
            for key, val in (("debit", line.debit), ("credit", line.credit),
                             ("deudor", line.deudor), ("acreedor", line.acreedor),
                             ("activo", line.activo), ("pasivo", line.pasivo),
                             ("perdida", line.perdida), ("ganancia", line.ganancia)):
                totals[key] += val
            row += 1

        sheet.write(row, 1, "Sumas", bold)
        sheet.write_number(row, 2, totals["debit"], money_bold)
        sheet.write_number(row, 3, totals["credit"], money_bold)
        sheet.write_number(row, 4, totals["deudor"], money_bold)
        sheet.write_number(row, 5, totals["acreedor"], money_bold)
        sheet.write_number(row, 6, totals["activo"], money_bold)
        sheet.write_number(row, 7, totals["pasivo"], money_bold)
        sheet.write_number(row, 8, totals["perdida"], money_bold)
        sheet.write_number(row, 9, totals["ganancia"], money_bold)
        row += 1

        resultado_inv = totals["activo"] - totals["pasivo"]
        resultado_res = totals["ganancia"] - totals["perdida"]
        sheet.write(row, 1, "Resultado del Ejercicio", bold)
        if resultado_inv < 0:
            sheet.write_number(row, 6, -resultado_inv, money_bold)
        elif resultado_inv > 0:
            sheet.write_number(row, 7, resultado_inv, money_bold)
        if resultado_res > 0:
            sheet.write_number(row, 8, resultado_res, money_bold)
        elif resultado_res < 0:
            sheet.write_number(row, 9, -resultado_res, money_bold)
        row += 1

        total_inv = max(totals["activo"], totals["pasivo"])
        total_res = max(totals["perdida"], totals["ganancia"])
        sheet.write(row, 1, "SUMAS IGUALES", bold)
        sheet.write_number(row, 2, totals["debit"], money_bold)
        sheet.write_number(row, 3, totals["credit"], money_bold)
        sheet.write_number(row, 4, totals["deudor"], money_bold)
        sheet.write_number(row, 5, totals["acreedor"], money_bold)
        sheet.write_number(row, 6, total_inv, money_bold)
        sheet.write_number(row, 7, total_inv, money_bold)
        sheet.write_number(row, 8, total_res, money_bold)
        sheet.write_number(row, 9, total_res, money_bold)
        row += 3

        sheet.write(row, 1, wizard.representante_name or "", bold)
        sheet.write(row, 6, wizard.contador_name or "", bold)
        row += 1
        sheet.write(row, 1, "RUT: %s" % (wizard.representante_rut or ""))
        sheet.write(row, 6, "RUT: %s" % (wizard.contador_rut or ""))
        row += 1
        sheet.write(row, 1, "Representante Legal")
        registro = wizard.contador_registro or ""
        sheet.write(row, 6, "Contador" + (" — Reg. N° %s" % registro if registro else ""))
        row += 2
        sheet.write(row, 1, "%s, %s" % (wizard.lugar_emision or "", wizard.date_to))
