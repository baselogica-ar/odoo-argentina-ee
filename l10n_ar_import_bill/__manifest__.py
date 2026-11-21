{
    "name": "Argentinian Importing Bills from ARCA",
    "version": "20.0.1.0.0",
    "category": "Localization/Argentina",
    "sequence": 8,
    "author": "ADHOC SA",
    "website": "www.adhoc.com.ar",
    "license": "LGPL-3",
    "summary": "Import invoices from the ARCA 'Mis Comprobantes' spreadsheet",
    "depends": ["account_accountant", "l10n_ar_edi", "account_invoice_tax"],
    "data": [
        "security/ir.access.csv",
        "wizards/afip_import_wizard.xml",
    ],
    "installable": True,
    "auto_install": False,
    "application": False,
}
