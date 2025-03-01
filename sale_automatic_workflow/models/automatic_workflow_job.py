# Copyright 2011 Akretion Sébastien BEAU <sebastien.beau@akretion.com>
# Copyright 2013 Camptocamp SA (author: Guewen Baconnier)
# Copyright 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from contextlib import contextmanager

from odoo import api, fields, models
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


@contextmanager
def savepoint(cr):
    """ Open a savepoint on the cursor, then yield.

    Warning: using this method, the exceptions are logged then discarded.
    """
    try:
        with cr.savepoint():
            yield
    except Exception:
        _logger.exception("Error during an automatic workflow action.")


@contextmanager
def force_company(env, company_id):
    user_company = env.user.company_id
    env.user.update({"company_id": company_id})
    try:
        yield
    finally:
        env.user.update({"company_id": user_company})


class AutomaticWorkflowJob(models.Model):
    """ Scheduler that will play automatically the validation of
    invoices, pickings...  """

    _name = "automatic.workflow.job"
    _description = (
        "Scheduler that will play automatically the validation of"
        " invoices, pickings..."
    )

    def _do_validate_sale_order(self, sale, domain_filter):
        """Validate a sales order, filter ensure no duplication"""
        if not self.env["sale.order"].search_count(
            [("id", "=", sale.id)] + domain_filter
        ):
            return "{} {} job bypassed".format(sale.display_name, sale)
        sale.action_confirm()
        return "{} {} confirmed successfully".format(sale.display_name, sale)

    @api.model
    def _validate_sale_orders(self, order_filter):
        sale_obj = self.env["sale.order"]
        sales = sale_obj.search(order_filter)
        _logger.debug("Sale Orders to validate: %s", sales.ids)
        for sale in sales:
            with savepoint(self.env.cr), force_company(self.env, sale.company_id):
                self._do_validate_sale_order(sale, order_filter)

    def _do_create_invoice(self, sale, domain_filter):
        """Create an invoice for a sales order, filter ensure no duplication"""
        if not self.env["sale.order"].search_count(
            [("id", "=", sale.id)] + domain_filter
        ):
            return "{} {} job bypassed".format(sale.display_name, sale)
        payment = self.env["sale.advance.payment.inv"].create({})
        payment.with_context(active_ids=sale.ids).create_invoices()
        return "{} {} create invoice successfully".format(sale.display_name, sale)

    @api.model
    def _create_invoices(self, create_filter):
        sale_obj = self.env["sale.order"]
        sales = sale_obj.search(create_filter)
        _logger.debug("Sale Orders to create Invoice: %s", sales.ids)
        for sale in sales:
            with savepoint(self.env.cr), force_company(self.env, sale.company_id):
                self._do_create_invoice(sale, create_filter)

    def _do_validate_invoice(self, invoice, domain_filter):
        """Validate an invoice, filter ensure no duplication"""
        if not self.env["account.move"].search_count(
            [("id", "=", invoice.id)] + domain_filter
        ):
            return "{} {} job bypassed".format(invoice.display_name, invoice)
        invoice.with_context(force_company=invoice.company_id.id).post()
        return "{} {} validate invoice successfully".format(
            invoice.display_name, invoice
        )

    @api.model
    def _validate_invoices(self, validate_invoice_filter):
        move_obj = self.env["account.move"]
        invoices = move_obj.search(validate_invoice_filter)
        _logger.debug("Invoices to validate: %s", invoices.ids)
        for invoice in invoices:
            with savepoint(self.env.cr), force_company(self.env, invoice.company_id):
                self._do_validate_invoice(invoice, validate_invoice_filter)

    def _do_validate_picking(self, picking, domain_filter):
        """Validate a stock.picking, filter ensure no duplication"""
        if not self.env["stock.picking"].search_count(
            [("id", "=", picking.id)] + domain_filter
        ):
            return "{} {} job bypassed".format(picking.display_name, picking)
        picking.validate_picking()
        return "{} {} validate picking successfully".format(
            picking.display_name, picking
        )

    @api.model
    def _validate_pickings(self, picking_filter):
        picking_obj = self.env["stock.picking"]
        pickings = picking_obj.search(picking_filter)
        _logger.debug("Pickings to validate: %s", pickings.ids)
        for picking in pickings:
            with savepoint(self.env.cr):
                self._do_validate_picking(picking, picking_filter)

    def _do_sale_done(self, sale, domain_filter):
        """Set a sales order to done, filter ensure no duplication"""
        if not self.env["sale.order"].search_count(
            [("id", "=", sale.id)] + domain_filter
        ):
            return "{} {} job bypassed".format(sale.display_name, sale)
        sale.action_done()
        return "{} {} set done successfully".format(sale.display_name, sale)

    @api.model
    def _sale_done(self, sale_done_filter):
        sale_obj = self.env["sale.order"]
        sales = sale_obj.search(sale_done_filter)
        _logger.debug("Sale Orders to done: %s", sales.ids)
        for sale in sales:
            with savepoint(self.env.cr), force_company(self.env, sale.company_id):
                self._do_sale_done(sale, sale_done_filter)

    def _prepare_dict_account_payment(self, invoice):
        partner_type = (
            invoice.type in ("out_invoice", "out_refund") and "customer" or "supplier"
        )
        communication = (
            invoice.name
            if invoice.type in ("out_invoice", "out_refund")
            else invoice.reference
        )
        return {
            "invoice_ids": [(6, 0, invoice.ids)],
            "amount": invoice.residual,
            "payment_date": fields.Date.context_today(self),
            "communication": communication,
            "partner_id": invoice.partner_id.id,
            "partner_type": partner_type,
        }

    @api.model
    def _register_payments(self, payment_filter):
        invoice_obj = self.env["account.move"]
        invoices = invoice_obj.search(payment_filter)
        _logger.debug("Invoices to Register Payment: %s", invoices.ids)
        for invoice in invoices:
            self._register_payment_invoice(invoice)
        return

    def _register_payment_invoice(self, invoice):
        with savepoint(self.env.cr):
            payment = self.env["account.payment"].create(
                self._prepare_dict_account_payment(invoice)
            )
            payment.post()

    @api.model
    def run_with_workflow(self, sale_workflow):
        workflow_domain = [("workflow_process_id", "=", sale_workflow.id)]
        if sale_workflow.validate_order:
            self._validate_sale_orders(
                safe_eval(sale_workflow.order_filter_id.domain) + workflow_domain
            )
        if sale_workflow.validate_picking:
            self._validate_pickings(
                safe_eval(sale_workflow.picking_filter_id.domain) + workflow_domain
            )
        if sale_workflow.create_invoice:
            self._create_invoices(
                safe_eval(sale_workflow.create_invoice_filter_id.domain)
                + workflow_domain
            )
        if sale_workflow.validate_invoice:
            self._validate_invoices(
                safe_eval(sale_workflow.validate_invoice_filter_id.domain)
                + workflow_domain
            )
        if sale_workflow.sale_done:
            self._sale_done(
                safe_eval(sale_workflow.sale_done_filter_id.domain) + workflow_domain
            )

        if sale_workflow.register_payment:
            self._register_payments(
                safe_eval(sale_workflow.payment_filter_id.domain) + workflow_domain
            )

    @api.model
    def run(self):
        """ Must be called from ir.cron """
        sale_workflow_process = self.env["sale.workflow.process"]
        for sale_workflow in sale_workflow_process.search([]):
            self.run_with_workflow(sale_workflow)
        return True
