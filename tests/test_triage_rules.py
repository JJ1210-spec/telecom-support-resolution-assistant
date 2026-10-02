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
