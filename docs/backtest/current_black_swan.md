# 目前實際倉位：黑天鵝壓力測試（Deribit index）

## Index 與 book equity（USDC 等值）
- index：`{'USDC': '1', 'ETH': '2313.61', 'BTC': '77879.74'}`
- equity_by_book_usdc：`{'USDC': '6377.00419335', 'ETH': '3510.26924586', 'BTC': '7284.131022070'}`

## 目前 options 部位（節錄）
- `BTC-15MAY26-70000-P` dir=sell qty=0.1 mark=0.00627398 strike=70000.0 settle=BTC
- `BTC-8MAY26-71000-P` dir=sell qty=0.1 mark=0.00364082 strike=71000.0 settle=BTC
- `BTC-8MAY26-72000-P` dir=sell qty=0.1 mark=0.00494084 strike=72000.0 settle=BTC
- `ETH-15MAY26-2000-P` dir=sell qty=1.0 mark=0.009647 strike=2000.0 settle=ETH
- `ETH-29MAY26-1950-P` dir=sell qty=1.0 mark=0.016995 strike=1950.0 settle=ETH
- `ETH-8MAY26-2100-P` dir=sell qty=2.0 mark=0.008506 strike=2100.0 settle=ETH
- `BTC_USDC-29MAY26-67000-P` dir=sell qty=0.04 mark=638.64530256 strike=67000.0 settle=USDC
- `BTC_USDC-29MAY26-68000-P` dir=sell qty=0.1 mark=740.54070736 strike=68000.0 settle=USDC
- `ETH_USDC-29MAY26-1900-P` dir=sell qty=3.5 mark=31.78882505 strike=1900.0 settle=USDC
- `ETH_USDC-29MAY26-1950-P` dir=sell qty=0.2 mark=39.31234112 strike=1950.0 settle=USDC

## 情境結果（由輕到重）
- shock=-0.10 slip=0.05 → total_loss=80.7413008494240000000000000 (0.47% of equity)
  - by_book={'BTC': '-191.6033357339760000000000000', 'ETH': '53.62139784600000000000000000', 'USDC': '218.7232387374'}
  - components={'base_move_usdc': '96.5987408494240000000000000', 'slippage_usdc': '-15.85744000000000000000000000'}
  - worst_leg=BTC-8MAY26-72000-P loss=-165.7333498876560000000000000 base_move=-156.1921798876560000000000000 slip=-9.541170000000000000000000008
- shock=-0.20 slip=0.10 → total_loss=-4342.865138051912000000000000 (-25.29% of equity)
  - by_book={'BTC': '-2777.192369541312000000000000', 'ETH': '-740.2941352479999999999999998', 'USDC': '-825.3786332626'}
  - components={'base_move_usdc': '-3912.415746051912000000000000', 'slippage_usdc': '-430.4493920000000000000000002'}
  - worst_leg=BTC-8MAY26-72000-P loss=-1035.799573233472000000000000 base_move=-938.8374932334719999999999999 slip=-96.96208000000000000000000011
- shock=-0.30 slip=0.15 → total_loss=-10569.17569995324800000000000 (-61.55% of equity)
  - by_book={'BTC': '-5606.056053348648000000000000', 'ETH': '-1851.976968342000000000000000', 'USDC': '-3111.1426782626'}
  - components={'base_move_usdc': '-9142.273372953248000000000000', 'slippage_usdc': '-1426.902327000000000000000000'}
  - worst_leg=BTC-8MAY26-72000-P loss=-1983.745536579288000000000000 base_move=-1721.482806579288000000000000 slip=-262.2627300000000000000000001
- shock=-0.40 slip=0.20 → total_loss=-15931.87715276860000000000000 (-92.78% of equity)
  - by_book={'BTC': '-7284.131022070', 'ETH': '-3056.204201435999999999999999', 'USDC': '-5591.5419292626'}
  - components={'base_move_usdc': '-13220.28878733619157349539808', 'slippage_usdc': '-2711.588365432408426504601922'}
  - worst_leg=BTC-8MAY26-72000-P loss=-3009.571239925104000000000000 base_move=-2504.128119925104000000000000 slip=-505.4431200000000000000000002
- shock=-0.50 slip=0.25 → total_loss=-17171.404461280 (-100.00% of equity)
  - by_book={'BTC': '-7284.131022070', 'ETH': '-3510.26924586', 'USDC': '-6377.00419335'}
  - components={'base_move_usdc': '-13688.18993671347222976353758', 'slippage_usdc': '-3483.214524566527770236462424'}
  - worst_leg=BTC-8MAY26-72000-P loss=-4113.276683270920000000000001 base_move=-3286.773433270920000000000000 slip=-826.5032500000000000000000014
- shock=-0.60 slip=0.30 → total_loss=-17171.404461280 (-100.00% of equity)
  - by_book={'BTC': '-7284.131022070', 'ETH': '-3510.26924586', 'USDC': '-6377.00419335'}
  - components={'base_move_usdc': '-13169.14944487114416027460289', 'slippage_usdc': '-4002.255016408855839725397110'}
  - worst_leg=BTC-8MAY26-72000-P loss=-5294.861866616736000000000000 base_move=-4069.418746616736000000000001 slip=-1225.443119999999999999999999

## 解讀
- `base_move_usdc` 主要代表「標的下跌 → put 變 ITM → 內在價跳升」造成的損失。
- `slippage_usdc` 是在回補時額外付出的流動性折價。
- 每本帳損失做了 equity 上限（最慘歸零），用來近似強制平倉/爆倉上限。
