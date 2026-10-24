from datetime import date, datetime


def application_status_guidance(status, submitted_at, decided_at, *, today=None):
    if status == "APPROVED":
        return (
            "Your application is approved. Contact your Block Development Office "
            "for questions about the next pension-payment steps."
        )
    if status == "REJECTED":
        return (
            "A decision has been recorded. Contact your Block Development Office "
            "to understand the decision and any available review process."
        )
    if status == "DEEMED_APPROVED":
        return (
            "The portal records this application as deemed approved under its "
            "15-day service timeline. Contact your Block Development Office "
            "about the next steps."
        )
    if status != "PENDING":
        return "Contact your Block Development Office for help understanding this status."

    reference_date = today or date.today()
    submitted_date = (
        submitted_at.date() if isinstance(submitted_at, datetime) else submitted_at
    )
    if submitted_date and (reference_date - submitted_date).days >= 15:
        return (
            "Your application is still marked pending after the 15-day service "
            "timeline. Contact your Block Development Office to ask for help. "
            "This explanation does not change your application status."
        )
    return (
        "Your application is being processed. The portal's stated processing "
        "timeline is 15 days. You can check this page again for an update."
    )


def applicant_document_hints(form_data, ocr_suggestions):
    hints = []
    document_name = " ".join(
        ocr_suggestions.get("name", "").casefold().split()
    )
    entered_name = " ".join(
        form_data.get("applicant_name", "").casefold().split()
    )
    if document_name and entered_name:
        if document_name not in entered_name and entered_name not in document_name:
            hints.append(
                "The possible name read from the document looks different from "
                "the name entered. Compare both with the original document."
            )

    dob_raw = form_data.get("dob", "").strip()
    if dob_raw:
        entered_dates = set()
        for date_format in ("%d/%m/%Y", "%m/%d/%Y"):
            try:
                entered_dates.add(datetime.strptime(dob_raw, date_format).date())
            except ValueError:
                continue
        document_dates = set()
        for candidate in ocr_suggestions.get("dates", []):
            for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
                try:
                    document_dates.add(datetime.strptime(candidate, date_format).date())
                    break
                except ValueError:
                    continue
        if document_dates and entered_dates and not entered_dates.intersection(document_dates):
            hints.append(
                "The possible date read from the document looks different from "
                "the date entered. Check the original document and your form."
            )
    return hints


def staff_review_assistance(application, ocr_suggestions=None, ocr_message=""):
    fields = {
        "application_no": application[0],
        "name": application[1],
        "dob": application[3],
        "document_path": application[10],
        "status": application[11],
    }
    checks = []
    if not fields["document_path"]:
        checks.append("No age-proof document is linked to this application.")
    elif ocr_message:
        checks.append(
            "Local OCR could not read the age-proof document; compare it manually."
        )
    elif ocr_suggestions:
        possible_name = ocr_suggestions.get("name", "").strip()
        if possible_name and fields["name"]:
            normalized_name = " ".join(str(fields["name"]).casefold().split())
            normalized_document_name = " ".join(possible_name.casefold().split())
            if normalized_name not in normalized_document_name and normalized_document_name not in normalized_name:
                checks.append(
                    "The name suggestion may differ from the application; compare "
                    "the original document and entered name."
                )

        dob = fields["dob"]
        if isinstance(dob, datetime):
            dob = dob.date()
        if isinstance(dob, date):
            parsed_dates = set()
            for candidate in ocr_suggestions.get("dates", []):
                for date_format in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
                    try:
                        parsed_dates.add(datetime.strptime(candidate, date_format).date())
                        break
                    except ValueError:
                        continue
            if parsed_dates and dob not in parsed_dates:
                checks.append(
                    "The entered date of birth was not among the OCR date suggestions; "
                    "verify it against the original document."
                )
            elif not parsed_dates:
                checks.append(
                    "OCR found no date that could be compared; verify date of birth "
                    "against the original document."
                )
    else:
        checks.append(
            "Review the applicant's details against the original age-proof document."
        )

    if not checks:
        checks.append(
            "No discrepancy was flagged by these basic local checks. Review the "
            "original document and all required details."
        )
    return {
        "application_no": fields["application_no"],
        "status": fields["status"],
        "checks": checks,
        "notice": (
            "Local review assistance only. OCR may be wrong. Verify the original "
            "document and current policy; this tool does not determine eligibility "
            "or recommend approval or rejection."
        ),
    }


def operations_insights(by_status, overdue, by_block):
    status_counts = {str(status): int(count) for status, count in by_status}
    total = sum(status_counts.values())
    insights = []
    if total == 0:
        insights.append("No applications are currently in the dashboard totals.")
    elif status_counts.get("PENDING", 0):
        insights.append(
            f"{status_counts['PENDING']} of {total} applications are pending; "
            "review the pending queue regularly."
        )
    if overdue:
        insights.append(
            f"{int(overdue)} pending applications are beyond the stated service "
            "timeline and may need staff attention."
        )
    if by_block:
        block, count = by_block[0]
        insights.append(
            f"{block} has the largest pending queue in the current counts "
            f"({int(count)}). Check staffing and processing capacity."
        )
    if not insights:
        insights.append(
            "No pending backlog is shown in these counts. Continue monitoring "
            "the dashboard as applications change."
        )
    return insights
