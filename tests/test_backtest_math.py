from deribit_demo.backtest import bs_delta


def test_bs_delta_call_between_0_and_1():
    d = bs_delta(spot=100.0, strike=100.0, t_years=0.5, sigma=0.5, option_type="call")
    assert 0.0 < d < 1.0


def test_bs_delta_put_between_minus1_and_0():
    d = bs_delta(spot=100.0, strike=100.0, t_years=0.5, sigma=0.5, option_type="put")
    assert -1.0 < d < 0.0


def test_bs_delta_put_more_negative_when_spot_drops():
    d1 = bs_delta(spot=100.0, strike=100.0, t_years=0.5, sigma=0.5, option_type="put")
    d2 = bs_delta(spot=80.0, strike=100.0, t_years=0.5, sigma=0.5, option_type="put")
    assert d2 < d1
