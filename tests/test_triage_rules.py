from telecom_assistant.triage_rules import reconcile_triage


def test_clear_fiber_loss_overrides_wrong_small_model_triage() -> None:
    triage = {"intent": "connectivity.intermittent_drop", "category": "Technical Support",
              "product": "Home Broadband", "severity": "P3", "confidence": 0.8}
    taxonomy = [{"intent": "fiber.loss_of_signal", "category": "Technical Support", "product": "Fiber"}]
    tickets = [{"score": 0.76, "payload": {"intent": "fiber.loss_of_signal"}} for _ in range(3)]
    result, notes = reconcile_triage(
        triage, "My fiber box has a red LOS light and no internet", tickets, taxonomy
    )
    assert result["intent"] == "fiber.loss_of_signal"
    assert result["product"] == "Fiber"
    assert result["severity"] == "P1"
    assert len(notes) == 2


def test_weak_retrieval_cannot_override_model_intent() -> None:
    triage = {"intent": "billing.unexpected_charge", "category": "Billing", "product": "Billing Account",
              "severity": "P3", "confidence": 0.7}
    taxonomy = [{"intent": "fiber.loss_of_signal", "category": "Technical Support", "product": "Fiber"}]
    tickets = [{"score": 0.4, "payload": {"intent": "fiber.loss_of_signal"}} for _ in range(3)]
    result, notes = reconcile_triage(triage, "My bill has an extra charge", tickets, taxonomy)
    assert result == triage
    assert notes == []


def test_localized_wifi_coverage_is_not_treated_as_major_outage() -> None:
    triage = {"intent": "connectivity.intermittent_drop", "category": "Technical Support",
              "product": "Router/CPE", "severity": "P2", "confidence": 0.8}
    result, notes = reconcile_triage(
        triage, "The Wi-Fi signal is weak in my bedroom, but the router works in the hall.", [], []
    )
    assert result["severity"] == "P3"
    assert len(notes) == 1


def test_three_wifi_cases_can_correct_small_model_label() -> None:
    triage = {"intent": "connectivity.intermittent_drop", "category": "Technical Support",
              "product": "Router/CPE", "severity": "P3", "confidence": 0.8}
    taxonomy = [{"intent": "wifi.coverage_or_interference", "category": "Technical Support",
                 "product": "Router/CPE"}]
    tickets = [{"score": score, "payload": {"intent": "wifi.coverage_or_interference"}}
               for score in (0.665, 0.657, 0.651)]
    result, notes = reconcile_triage(triage, "Weak Wi-Fi in one bedroom", tickets, taxonomy)
    assert result["intent"] == "wifi.coverage_or_interference"
    assert notes
