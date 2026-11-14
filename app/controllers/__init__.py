"""Controller layer: one blueprint per screen."""


def register_blueprints(app):
    from . import admin, auth, bills, crud, dashboard, menu, menu_category, menu_pdf, notes, online, reports, staff, tables, uploads

    for module in (auth, dashboard, menu_category, menu, menu_pdf, bills, tables, reports, admin, crud, notes, online, staff, uploads):
        app.register_blueprint(module.bp)
    app.register_blueprint(notes.formula_bp)
    app.register_blueprint(staff.att_bp)
    from . import mobile_api
    app.register_blueprint(mobile_api.bp)
    app.register_blueprint(mobile_api.legacy)  # catch-all for every other old URL: registered last
