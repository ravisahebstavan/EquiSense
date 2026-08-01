"""Prediction magnitude tracking: predicted-vs-realized forecast error at a
claim's full horizon, plus an interim checkpoint (~1/4 horizon) that surfaces
drift long before the full claim matures — the literal 'prediction made at T
for T+checkpoint, compared once T+checkpoint arrives' loop."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from equisense import ledger
from equisense.db import Base
from equisense.models import Company, PriceObservation
from equisense.research import learning as L


# ---------------------------------------------------------- calibrated_magnitude

def _mag_dossier(h, net, direction):
    return {"kind": "dossier", "hash": h,
            "verdict": "long_candidate" if direction > 0 else "avoid_short_candidate",
            "net_score": net, "cluster_scores": {}, "claim": {"direction": direction}}


def _mag_score(h, realized, direction):
    hit = (realized > 0) == (direction > 0)
    return {"kind": "score", "scores_dossier_hash": h, "company": "T",
            "realized_excess_pct": realized, "hit": hit,
            "stated_probability": 0.55, "brier": (0.55 - (1 if hit else 0)) ** 2}


def _mag_history(n, direction, realized):
    recs = []
    for i in range(n):
        net = 0.3 if direction > 0 else -0.3
        recs.append(_mag_dossier(f"m{direction}-{i}", net, direction))
        recs.append(_mag_score(f"m{direction}-{i}", realized, direction))
    return recs


def test_magnitude_provisional_below_min():
    pred, basis = L.calibrated_magnitude(0.5, horizon_days=126, records=_mag_history(10, 1, 6.0))
    assert pred == pytest.approx(0.5 * L.PROVISIONAL_MAG_SCALE, rel=1e-6)
    assert "provisional" in basis


def test_magnitude_scales_with_claim_horizon():
    pred63, _ = L.calibrated_magnitude(0.5, horizon_days=63, records=[])
    pred126, _ = L.calibrated_magnitude(0.5, horizon_days=126, records=[])
    assert pred63 == pytest.approx(pred126 / 2, rel=1e-6)


def test_magnitude_calibrates_per_direction_without_blending():
    # 40 long claims all realize +6%, 40 short claims all realize -5% — a
    # long-side query must calibrate toward +6%, never averaged with shorts
    recs = _mag_history(40, 1, 6.0) + _mag_history(40, -1, -5.0)
    pred_long, basis_long = L.calibrated_magnitude(0.5, horizon_days=126, records=recs)
    pred_short, basis_short = L.calibrated_magnitude(-0.5, horizon_days=126, records=recs)
    assert "calibrated" in basis_long and "calibrated" in basis_short
    assert pred_long == pytest.approx(6.0, abs=0.5)
    assert pred_short == pytest.approx(-5.0, abs=0.5)


# --------------------------------------------------------- ledger integration

@pytest.fixture
def mem_ledger(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    SL = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(ledger, "get_session", lambda: SL())
    monkeypatch.setattr(ledger, "STORAGE", "db")
    return SL()


def test_register_dossier_embeds_predicted_magnitude_and_checkpoint(mem_ledger):
    dossier = {"synthesis": {"verdict": "long_candidate", "net_score": 0.5,
                             "conviction_band": "high"},
               "company": {"ticker": "T1", "price": 100.0}, "claim_horizon_days": 40}
    rec = ledger.register_dossier(dossier)
    claim = rec["claim"]
    assert claim["predicted_excess_pct"] is not None
    assert "provisional" in claim["magnitude_basis"]
    assert claim["horizon_days"] == 40
    assert claim["checkpoint_days"] == max(21, round(40 / 4))  # 21


def test_checkpoint_then_final_score_track_forecast_error(mem_ledger):
    s = mem_ledger
    t1 = Company(ticker="T1", name="T1", sector="IT")
    p1 = Company(ticker="P1", name="P1", sector="IT")
    p2 = Company(ticker="P2", name="P2", sector="IT")
    s.add_all([t1, p1, p2]); s.commit()

    entry = date.today()
    dossier = {"synthesis": {"verdict": "long_candidate", "net_score": 0.5,
                             "conviction_band": "high"},
               "company": {"ticker": "T1", "price": 100.0}, "claim_horizon_days": 40}
    rec = ledger.register_dossier(dossier)
    claim = rec["claim"]
    checkpoint_days, horizon_days = claim["checkpoint_days"], claim["horizon_days"]
    predicted = claim["predicted_excess_pct"]

    checkpoint_target = entry + timedelta(days=int(checkpoint_days * 1.45))
    horizon_target = entry + timedelta(days=int(horizon_days * 1.45))

    def add(cid, prices):
        for d, px in prices.items():
            s.add(PriceObservation(company_id=cid, obs_date=d, close=px))

    # T1 up 6% by the checkpoint, up 12% by the full horizon; peers flat, so
    # excess return == raw return in both cases
    add(t1.id, {entry: 100.0, checkpoint_target: 106.0, horizon_target: 112.0})
    add(p1.id, {entry: 100.0, checkpoint_target: 100.0, horizon_target: 100.0})
    add(p2.id, {entry: 100.0, checkpoint_target: 100.0, horizon_target: 100.0})
    s.commit()

    # interim checkpoint fires once checkpoint_target has arrived — well
    # before the full horizon
    cp = ledger.score_interim_checkpoints(s, as_of=checkpoint_target)
    assert cp["checkpointed"] == 1
    c = cp["results"][0]
    assert c["realized_so_far_pct"] == pytest.approx(6.0, abs=0.05)
    expected_so_far = round(predicted * (checkpoint_days / horizon_days), 2)
    assert c["expected_so_far_pct"] == pytest.approx(expected_so_far, abs=0.01)
    assert c["forecast_error_pct"] == pytest.approx(6.0 - expected_so_far, abs=0.05)
    assert c["on_track"] is True

    # never double-checkpoints the same claim
    cp2 = ledger.score_interim_checkpoints(s, as_of=checkpoint_target)
    assert cp2["checkpointed"] == 0

    # full-horizon score is independent of (and unblocked by) the checkpoint
    sc = ledger.score_due_claims(s, as_of=horizon_target)
    assert sc["scored"] == 1
    r = sc["results"][0]
    assert r["realized_excess_pct"] == pytest.approx(12.0, abs=0.05)
    assert r["predicted_excess_pct"] == predicted
    assert r["forecast_error_pct"] == pytest.approx(12.0 - predicted, abs=0.05)
    assert r["abs_forecast_error_pct"] == pytest.approx(abs(12.0 - predicted), abs=0.05)


def test_calibration_report_includes_magnitude_and_checkpoint_metrics(monkeypatch):
    recs = [
        {"kind": "dossier", "hash": "d1", "verdict": "long_candidate", "net_score": 0.4,
         "claim": {"direction": 1}},
        {"kind": "score", "claim_type": "directional_excess", "scores_dossier_hash": "d1",
         "company": "T1", "realized_excess_pct": 10.0, "hit": True, "stated_probability": 0.6,
         "brier": 0.16, "predicted_excess_pct": 4.0, "forecast_error_pct": 6.0,
         "abs_forecast_error_pct": 6.0},
        {"kind": "checkpoint", "scores_dossier_hash": "d1", "company": "T1",
         "elapsed_days": 21, "horizon_days": 40, "realized_so_far_pct": 5.0,
         "expected_so_far_pct": 2.0, "forecast_error_pct": 3.0, "on_track": True},
    ]
    monkeypatch.setattr(ledger, "read_all", lambda: recs)
    report = ledger.calibration_report()
    assert report["mean_abs_forecast_error_pct"] == pytest.approx(6.0)
    assert report["rmse_forecast_error_pct"] == pytest.approx(6.0)
    assert report["interim_checkpoints"] == 1
    assert report["interim_on_track_rate"] == pytest.approx(1.0)
    assert report["mean_abs_interim_forecast_error_pct"] == pytest.approx(3.0)


def test_daily_forecasts_are_registered_independently_of_trading(tmp_path, monkeypatch):
    """The loop that lets this system learn at all.

    Calibrated probabilities (30 scored claims), calibrated magnitudes and
    learned cluster weights (150 per family) ALL unlock from realised forecasts,
    and a forecast exists only once a dossier is registered in the ledger.
    Registration used to happen solely when a human clicked "Generate dossier",
    so the calibration ledger sat at 0 indefinitely and every weight stayed
    provisional forever.

    Registration is decoupled from trading on purpose: tying it to executed
    trades would starve the record and bias it toward names that happened to
    clear the sizing and liquidity gates.
    """
    import inspect

    from equisense.api import autopilot
    src = inspect.getsource(autopilot.register_daily_forecasts)
    assert "qualified_candidates" in src, "must forecast the ranked candidates"
    assert "build_dossier" in src, "a forecast IS a registered dossier"
    # decoupled from trading: no order placement anywhere in the function
    assert "place_trade" not in src and "PaperTrade" not in src


def test_forecast_registration_is_idempotent_within_a_day():
    """The cron can be retried, and a doubled forecast would double-count that
    name in every calibration statistic derived from the ledger."""
    import inspect

    from equisense.api import autopilot
    src = inspect.getsource(autopilot.register_daily_forecasts)
    assert "already" in src and "created_at" in src, "no same-day guard"


def test_cron_registers_forecasts_before_scoring():
    """Order matters: a claim made today must be in the chain and start its
    horizon now, rather than waiting a day."""
    import inspect

    from equisense.api import app as A
    src = inspect.getsource(A.cron_refresh)
    assert "register_daily_forecasts" in src
    assert src.index("register_daily_forecasts") < src.index("score_due_claims")
    # and a failure here must never take down the daily ingest
    assert "never block the cron" in src
