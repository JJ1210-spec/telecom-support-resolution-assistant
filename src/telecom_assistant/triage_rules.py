"""Conservative, explainable checks around small-model triage."""

from __future__ import annotations


def reconcile_triage(triage: dict, complaint: str, tickets: list[dict], taxonomy: list[dict]) -> tuple[dict, list[str]]:
    result = dict(triage)
    notes: list[str] = []
    known = {item["intent"]: item for item in taxonomy}
    strong = [ticket for ticket in tickets[:3] if ticket.get("score", 0) >= 0.64]
    labels = [ticket.get("payload", {}).get("intent") for ticket in strong]
    if len(strong) >= 3 and len(set(labels)) == 1 and labels[0] in known and labels[0] != result["intent"]:
        chosen = known[labels[0]]
        result["intent"] = chosen["intent"]
        result["category"] = chosen["category"]
        result["product"] = chosen["product"]
        result["confidence"] = min(result.get("confidence", 0.0), 0.8)
        notes.append("Triage label adjusted using agreement among three similar resolved cases")

    text = complaint.casefold()
    red_los_total_loss = "los" in text and any(word in text for word in ("red", "लाल")) and any(
        phrase in text for phrase in ("no internet", "internet is down", "इंटरनेट बंद", "net bilkul nahi")
    )
    area_loss = any(phrase in text for phrase in ("whole street", "entire building", "पूरी इमारत", "puri building")) and any(
        phrase in text for phrase in ("no internet", "offline", "इंटरनेट बंद", "net band")
    )
    if (red_los_total_loss or area_loss) and result["severity"] != "P1":
        result["severity"] = "P1"
        notes.append("Severity raised to P1 by the total-service-loss rule")
    elif result["severity"] in ("P3", "P4", "Unknown") and any(
        phrase in text for phrase in ("every evening", "bar bar cut", "बार बार बंद")
    ) and any(phrase in text for phrase in ("work from home", "wfh", "घर से काम")):
        result["severity"] = "P2"
        notes.append("Severity raised to P2 by recurring work-impact rule")
    elif result["severity"] in ("P1", "P2", "Unknown") and any(
        word in text for word in ("weak wi-fi", "weak wifi", "wifi weak", "wi-fi signal is weak")
    ) and any(phrase in text for phrase in ("works in the hall", "works in the living room", "works elsewhere")):
        result["severity"] = "P3"
        notes.append("Severity set to P3 for localized Wi-Fi coverage with service working elsewhere")
    return result, notes
