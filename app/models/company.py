from __future__ import annotations
from .. import db
from ..errors import NotFoundError


def get(company_id: int) -> dict:
    company = db.fetch_one(
        "SELECT company_detail_id AS id, company_name_l1 AS name, company_name_l2 AS address_line, "
        "abbreviation, gst_no, address FROM company_detail WHERE company_detail_id = %s",
        (company_id,),
    )
    if not company:
        raise NotFoundError("Your company profile could not be found. Please contact the administrator.")
    company["abbreviation"] = (company.get("abbreviation") or "").strip()
    gst = (company.get("gst_no") or "").strip()
    company["gst_no"] = gst if gst and set(gst.upper()) != {"X"} else ""
    return company
