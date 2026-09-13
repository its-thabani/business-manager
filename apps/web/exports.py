"""CSV downloads of tables the pages already show."""

from __future__ import annotations

from django.http import HttpResponse

from apps.core.money import fmt


def csv_response(filename: str, headers: list[str], rows: list[list]) -> HttpResponse:
    import csv

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return response


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value)


def money_cell(value) -> str:
    if value is None:
        return ""
    return fmt(value, currency="")


def wants_csv(request) -> bool:
    return request.GET.get("export") in {"csv", "1", "download"}
