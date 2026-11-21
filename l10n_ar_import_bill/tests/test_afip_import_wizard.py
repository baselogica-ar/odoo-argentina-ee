from io import BytesIO
from os import path

import openpyxl
from odoo.addons.l10n_ar.tests.common import TestArCommon
from odoo.addons.l10n_ar.tools.partner_identifiers import AR_CUIT_AFIP_CODE
from odoo.addons.l10n_ar_import_bill.wizards.afip_import_wizard_line import AR_CUIT_IDENTIFIER_KEY
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestAfipImportWizard(TestArCommon):
    def _fixture(self, filename):
        """The bytes of one of the ARCA listings used by the tests."""
        with open(path.join(path.dirname(__file__), "fixtures", filename), "rb") as f:
            return f.read()

    def _get_wizard_from_file(self, filename):
        """Helper to create and return wizard from file
        Args:
            filename (str): Name of file in fixtures folder
        Returns:
            afip.import.wizard: Created wizard record
        """
        with open(path.join(path.dirname(__file__), "fixtures", filename), "rb") as f:
            file_data = f.read()

        # Create attachment and get wizard through journal method
        result = self.company_data["default_journal_purchase"].import_bills_from_xls(
            [
                self.env["ir.attachment"].create(
                    {
                        "name": filename,
                        "raw": file_data,
                    }
                )
            ]
        )

        return self.env["afip.import.wizard"].browse(result["res_id"])

    def test_invoice_document_type(self):
        """Test correct assignment of document types based on file data"""
        # Load test file containing different invoice types
        wizard = self._get_wizard_from_file("invoice_type_test.xlsx")

        # Process the file
        view_return = wizard.action_confirm()
        invoice_ids = view_return["domain"][0][2]
        # Verify number of lines matches expected documents
        self.assertEqual(len(invoice_ids), 6, "Should find 6 invoices")

        # Expected document types in order
        expected_types = [
            "1",  # Factura A
            "2",  # Debit Note A
            "3",  # Credit Note A
            "6",  # Factura B
            "11",  # Factura C
            "15",  # Recibo C
        ]

        # Verify each line has correct type
        for idx, expected_code in enumerate(expected_types):
            invoice = self.env["account.move"].browse(invoice_ids)[idx]

            self.assertEqual(invoice.l10n_latam_document_type_id.code, expected_code)

    def test_exento_import_without_vat(self):
        """A VAT-exempt company imports B/C documents with no VAT tax at all.

        The test database ships a demo AR company that is exempt (code 4). The import
        goes through create_document_from_attachment so it also covers the routing of
        the importer for responsibilities other than VAT-registered.
        """
        company = self.env["res.company"].search([("l10n_ar_afip_responsibility_type_id.code", "=", "4")], limit=1)
        self.assertTrue(company, "No exempt company (code 4) in the test database")
        self.env.user.company_ids = [(4, company.id)]
        self.env.user.company_id = company

        journal = self.env["account.journal"].create(
            {"name": "Test Purchase Exento", "code": "TPEX", "type": "purchase", "company_id": company.id}
        )

        with open(path.join(path.dirname(__file__), "fixtures", "Exento.xlsx"), "rb") as f:
            attachment = self.env["ir.attachment"].create({"name": "Exento.xlsx", "raw": f.read()})

        action = journal.create_document_from_attachment(attachment_ids=[attachment.id])
        self.assertEqual(
            action.get("res_model"), "afip.import.wizard", "The importer must trigger for an exempt company"
        )
        wizard = self.env["afip.import.wizard"].browse(action["res_id"])

        # The suppliers are created up front to avoid the external AFIP lookup that
        # _get_partner_by_vat triggers when it creates a new CUIT partner.
        for line in wizard.line_ids:
            if not self.env["res.partner"].search([("vat", "=", line.partner_vat)], limit=1):
                self.env["res.partner"].create(
                    {
                        "name": line.partner_name,
                        "vat": line.partner_vat,
                        "country_id": self.env.ref("base.ar").id,
                    }
                )

        expected_total = sum(wizard.line_ids.mapped("amount_total"))
        view_return = wizard.action_confirm()
        moves = self.env["account.move"].browse(view_return["domain"][0][2])

        self.assertEqual(len(moves), 24, "The 24 invoices of the file must be created")

        for move in moves:
            iva_taxes = move.invoice_line_ids.tax_ids.filtered(lambda t: t.tax_group_id.l10n_ar_vat_afip_code)
            self.assertFalse(iva_taxes, f"An exempt company carries no VAT tax (invoice {move.name})")

        # The imported total has to be preserved (net without VAT + other taxes).
        self.assertAlmostEqual(sum(moves.mapped("amount_total")), expected_total, places=2)

    def test_partner_creation_and_search(self):
        """Test correct creation and search of partners based on identification"""
        # Load test file containing partner data
        wizard = self._get_wizard_from_file("invoice_partner_test.xlsx")

        # Process the file
        for line in wizard.line_ids:
            partner = line._find_partner()
            partner_by_vat = line._get_partner_by_vat()
            identifier_key = line._get_identifier_key()
            if partner:
                self.assertEqual(partner, partner_by_vat, f"Partner VAT should match for line {line}")
            elif identifier_key and identifier_key != AR_CUIT_IDENTIFIER_KEY:
                # A personal document lives in the additional identifiers, not in the CUIT.
                self.assertEqual(
                    (partner_by_vat.additional_identifiers or {}).get(identifier_key),
                    line.partner_vat,
                    f"Identification type should match for line {line}",
                )
                self.assertFalse(partner_by_vat.vat, f"A personal document is not a CUIT for line {line}")
            else:
                self.assertEqual(
                    partner_by_vat.vat, line.partner_vat, f"Created partner VAT should match for line {line}"
                )
                self.assertEqual(
                    partner_by_vat.l10n_ar_afip_code,
                    AR_CUIT_AFIP_CODE,
                    f"Identification type should match for line {line}",
                )

            # The next import of the same listing has to find the contact that this one
            # created, whichever field its document went to.
            self.assertEqual(
                line._find_partner(), partner_by_vat, f"The contact is found again by its document for line {line}"
            )

    def test_several_spreadsheets_in_one_wizard(self):
        """Every listing dropped at once is read, and a document repeated between two of
        them is shown but created once."""
        journal = self.company_data["default_journal_purchase"]
        attachments = self.env["ir.attachment"].create(
            [
                {"name": "uno.xlsx", "raw": self._fixture("invoice_partner_test.xlsx")},
                {"name": "repetido.xlsx", "raw": self._fixture("invoice_partner_test.xlsx")},
                {"name": "otro.xlsx", "raw": self._fixture("Exento.xlsx")},
            ]
        )

        action = journal.import_bills_from_xls(attachments)
        wizard = self.env["afip.import.wizard"].browse(action["res_id"])

        primero = self._get_wizard_from_file("invoice_partner_test.xlsx")
        segundo = self._get_wizard_from_file("Exento.xlsx")
        self.assertEqual(
            len(wizard.line_ids),
            2 * len(primero.line_ids) + len(segundo.line_ids),
            "Nothing of what was dropped is left out of the wizard",
        )
        self.assertEqual(
            wizard.total_bills_to_create,
            len(primero.line_ids) + len(segundo.line_ids),
            "The repeated document is created once",
        )
        self.assertEqual(wizard.total_bills_exists, len(primero.line_ids))

    def test_removing_the_first_of_two_equal_lines_frees_the_other(self):
        """Deleting a repeated line leaves the one that is left to be created."""
        journal = self.company_data["default_journal_purchase"]
        attachments = self.env["ir.attachment"].create(
            [
                {"name": "uno.xlsx", "raw": self._fixture("invoice_partner_test.xlsx")},
                {"name": "repetido.xlsx", "raw": self._fixture("invoice_partner_test.xlsx")},
            ]
        )
        wizard = self.env["afip.import.wizard"].browse(journal.import_bills_from_xls(attachments)["res_id"])
        to_create = wizard.total_bills_to_create
        self.assertTrue(wizard.total_bills_exists, "The second listing comes in as repeated")

        wizard.line_ids.filtered(lambda line: not line.exists)[0].action_remove()

        self.assertEqual(
            wizard.total_bills_to_create,
            to_create,
            "The copy that is left stops being a repetition of the line that was removed",
        )

    def test_an_imported_invoice_is_not_imported_again(self):
        """The second import of the same listing creates nothing: it is all already in."""
        wizard = self._get_wizard_from_file("invoice_type_test.xlsx")
        self.assertEqual(wizard.total_bills_exists, 0, "Nothing is imported yet")
        wizard.action_confirm()

        otra_vez = self._get_wizard_from_file("invoice_type_test.xlsx")

        self.assertEqual(
            otra_vez.total_bills_exists,
            len(otra_vez.line_ids),
            "Every document of the listing is recognized as already imported",
        )
        self.assertEqual(otra_vez.total_bills_to_create, 0)

    def _listing_in_dollars(self, rate):
        """One of the listings of the tests, cut down to its first document and turned into dollars."""
        workbook = openpyxl.load_workbook(BytesIO(self._fixture("invoice_type_test.xlsx")))
        sheet = workbook.active
        header = [cell.value for cell in sheet[2]]
        sheet.delete_rows(4, sheet.max_row)
        sheet.cell(3, header.index("Moneda") + 1, "USD")
        sheet.cell(3, header.index("Tipo Cambio") + 1, rate)
        listing = BytesIO()
        workbook.save(listing)
        return listing.getvalue()

    def test_a_foreign_currency_document_takes_the_rate_of_the_listing(self):
        """A document in dollars is booked at the exchange rate ARCA wrote in the listing,
        not at the one the database holds for that day."""
        self.env.ref("base.USD").active = True
        attachment = self.env["ir.attachment"].create({"name": "dolares.xlsx", "raw": self._listing_in_dollars(914)})
        journal = self.company_data["default_journal_purchase"]
        wizard = self.env["afip.import.wizard"].browse(journal.import_bills_from_xls(attachment)["res_id"])
        self.assertEqual(wizard.line_ids.mapped("amount_total"), [12100], "One document of 12,100 dollars")

        move = self.env["account.move"].browse(wizard.action_confirm()["res_id"])

        self.assertEqual(move.currency_id, self.env.ref("base.USD"))
        self.assertAlmostEqual(
            1 / move.invoice_currency_rate, 914, places=2, msg="The rate of the listing is the rate of the invoice"
        )
        self.assertAlmostEqual(
            abs(move.amount_total_signed), 12100 * 914, delta=0.05, msg="The total in pesos comes from that rate"
        )
