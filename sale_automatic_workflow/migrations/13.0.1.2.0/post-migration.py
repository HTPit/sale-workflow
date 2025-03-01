# Copyright 2021 Camptocamp SA
# Copyright 2021 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl)
from openupgradelib import openupgrade  # pylint: disable=W7936


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(
        env.cr, "sale_automatic_workflow", "migrations/13.0.1.2.0/noupdate_changes.xml"
    )
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE account_move am
        SET workflow_process_id = ai.workflow_process_id
        FROM account_invoice ai
        WHERE ai.id = am.old_invoice_id""",
    )
