"""Public strategy catalog. v1 ships Covered Call; other types are listed as upcoming.

Copy treats the reader as someone who has never traded options. Max profit and
max loss must stay explicit; this is not investment advice.
"""

from __future__ import annotations

from typing import Any

COVERED_CALL: dict[str, Any] = {
    "id": "covered_call",
    "name_zh": "掩護性買權（Covered Call）",
    "name_en": "Covered Call",
    "available": True,
    "status": "available",
    "one_liner_zh": "你已經持有 BTC 或 ETH，再賣出買權，先收下幣本位權利金；換來的是上漲時可能被結算扣幣、下跌時美元市值幾乎照跌。",
    "for_whom_zh": "已經持有現貨、願意用幣本位看這張買權的損益，並用 U 本位對照市值的人。",
    "beginner_zh": [
        "選擇權是一份合約，不是把幣「借出去收利息」。合約有買方與賣方。",
        "買方付錢，買到的是權利：到期時可以決定要不要用約定價格成交。賣方收錢，換來的是義務：如果買方要成交，賣方必須履行。",
        "買權給買方「用履約價買入標的」的權利。Deribit 這張是幣本位：權利金用 BTC／ETH 收，價內到期也用幣結算。",
        "「掩護」的意思是：你帳戶裡已經有對應的現貨，結算時交得出幣。這跟空手賣出買權（裸賣）不同。Canopy v1 只做掩護性買權。",
        "看損益時先看幣本位（多了或少了幾顆），再折成 U 本位看美元市值。現貨大跌時顆數可能還在，U 市值會掉很多。",
    ],
    "how_it_works_zh": [
        "先把要當擔保的 BTC 或 ETH 放進你自己的 Deribit 子帳（不是把幣交給 Canopy）。",
        "引擎依風險檔選出要賣出的買權（履約價、到期日）。",
        "成交後你立刻收到幣本位權利金；子帳裡的現貨被「罩住」，直到到期、被履約，或系統／你自己平倉。",
        "到期若現貨低於履約價，買權作廢，你留下現貨與已收的幣，之後可以再賣下一張。",
        "到期若現貨高於履約價，這張買權用幣結算：你會被扣幣，U 本位上也拿不到履約價以上的全部漲幅。",
    ],
    "max_profit": {
        "title_zh": "最大獲利",
        "headline_zh": "幣本位大約等於收到的權利金（示意 0.015 BTC）；進場時約 1,500 U，到期折算會隨現貨變。",
        "body_zh": (
            "選擇權這條腿在幣本位賺不到無限。價外到期時，你多到的就是進場收下的幣。"
            "U 本位是把這筆幣用到期現貨折成美元，所以同一個 0.015 BTC，現貨較高時折合的 U 也較多。"
            "若你提前買回買權平倉，實際獲利會更少，甚至可能虧這條腿。"
        ),
        "when_zh": "到期時現貨仍低於履約價（買權作廢），你留下權利金那筆幣。",
        "not_zh": "最大獲利不是現貨上漲的全部、不是 APR、也不是「每個月固定入帳」。",
    },
    "max_loss": {
        "title_zh": "最大虧損",
        "headline_zh": "幣本位：現貨大跌時顆數還在；價內結算最多會扣掉接近 1 BTC（減已收權利金）。U 本位：現貨市值可以接近零。",
        "body_zh": (
            "兩種尺不要混：幣本位看顆數，U 本位看市值。"
            "現貨從 10 萬 U 跌到 1 萬 U，你手上可能還是約 1 BTC，但美元市值少了九成；權利金那幾顆小數補不住。"
            "大漲價內時，逆價結算會扣幣，幣本位這條腿會往下走。樹冠是遮蔭不是屋頂。"
        ),
        "when_zh": "標的大跌（U 市值），或大漲價內被結算扣幣，或你被迫在不好的價格平倉。",
        "not_zh": "最大虧損不是「只虧權利金」，也不是訂閱費。",
    },
    "example_zh": {
        "title_zh": "用假數字走一遍（不是預測、不是回測）",
        "setup_zh": "假設你持有 1 BTC，現價 100,000 U。你賣出履約價 110,000 的買權，收到 0.015 BTC（進場約 1,500 U）。",
        "paths": [
            {
                "label_zh": "現貨不太漲也不太跌",
                "detail_zh": "到期 105,000 U。買權作廢。幣本位 +0.015 BTC；這張買權約 +1,575 U。現貨市值另算。",
            },
            {
                "label_zh": "現貨大漲，價內結算",
                "detail_zh": "到期 130,000 U。結算扣幣，這張買權幣本位約 −0.139 BTC（約 −18,050 U）。你拿不到現貨繼續漲的全部。",
            },
            {
                "label_zh": "現貨大跌",
                "detail_zh": "到期 50,000 U。買權作廢，幣本位仍約 +0.015 BTC，但 1 BTC 的 U 市值少了約 5 萬。這才是 U 本位最大虧損。",
            },
        ],
    },
    "risks_zh": [
        "現貨下跌是主風險，權利金不是保險。",
        "上漲價內時會被扣幣（逆價結算），U 本位也拿不到全部漲幅。",
        "提早平倉要付出買回買權的成本，可能把已收的幣吐回去。",
        "流動性差、滑價、手續費會吃掉帳面權利金。",
        "交易所、API、網路或程式故障時，暫停／緊急平倉只是送出指令，不保證成交。",
        "歷史 APR、回測、別人的績效都不是你會賺到的金額。",
    ],
    "not_this_zh": [
        "不是把幣存進平台收息。",
        "不是基金、代操、投顧或跟單。",
        "不是保護本金或「穩穩的被動收入」。",
        "不是保證每月都收到權利金。",
    ],
    "diagram": {
        "kind": "covered_call",
        "spot": 100000,
        "strike": 110000,
        "premium": 1500,
        "premium_coin": 0.015,
        "pnl_basis": "coin",
        "x_min": 40000,
        "x_max": 140000,
        "underlying": "BTC",
        "qty": 1,
        "note_zh": "示意數字。主軸是幣本位（這張買權多了或少了幾顆 BTC），旁邊折成 U 本位。可拖動到期現貨價格。",
        "x_label_zh": "到期現貨價格（U）",
        "y_label_zh": "幣本位損益（BTC）",
        "series": [
            {"id": "spot", "label_zh": "只持現貨（幣本位不變）"},
            {"id": "strategy", "label_zh": "掩護性買權（幣本位）"},
        ],
        "pieces": [
            {"id": "spot", "title_zh": "你已有現貨", "body_zh": "1 BTC 放在自己的子帳。顆數還在，U 市值會變。"},
            {"id": "call", "title_zh": "再賣出買權", "body_zh": "先收 0.015 BTC（進場約 1,500 U）。"},
            {"id": "sum", "title_zh": "合在一起", "body_zh": "價外：多那筆幣。價內：結算扣幣。下跌：U 市值照跌。"},
        ],
        "flow": [
            {"title_zh": "持有現貨", "body_zh": "不是把幣交給平台。"},
            {"title_zh": "賣出買權", "body_zh": "立刻收到幣本位權利金。"},
            {"title_zh": "價外到期", "body_zh": "留下幣與權利金。"},
            {"title_zh": "價內結算", "body_zh": "用幣結算，顆數變少。"},
        ],
        "scenarios": [
            {
                "id": "sideways",
                "spot": 105000,
                "label_zh": "不太漲跌",
                "caption_zh": "買權作廢。幣本位是權利金；U 本位隨到期現貨折算。",
            },
            {
                "id": "rally",
                "spot": 130000,
                "label_zh": "大漲，價內結算",
                "caption_zh": "結算扣幣。幣本位這條腿轉負；你拿不到現貨續漲的全部。",
            },
            {
                "id": "crash",
                "spot": 50000,
                "label_zh": "大跌",
                "caption_zh": "幣本位仍是那筆權利金，但 U 市值大虧。",
            },
        ],
    },
}

CASH_SECURED_PUT: dict[str, Any] = {
    "id": "cash_secured_put",
    "name_zh": "現金擔保賣權（Cash-Secured Put）",
    "name_en": "Cash-Secured Put",
    "available": False,
    "status": "coming_soon",
    "one_liner_zh": "你先準備好現金，賣出賣權收權利金；若價格跌破履約價，你有義務用該價格買入現貨。",
    "for_whom_zh": "v1 尚未提供。列在這裡只為說明「還有其他策略種類」，不是邀請你現在去手動做。",
    "beginner_zh": [
        "賣權給買方「用履約價把標的賣給你」的權利。你當賣方，等於答應：跌夠深時，你必須用較高的履約價買入。",
        "「現金擔保」表示帳戶裡已留好買入所需資金，不是無限槓桿。",
    ],
    "how_it_works_zh": [
        "即將推出，Canopy v1 不會下這類單。",
    ],
    "max_profit": {
        "title_zh": "最大獲利",
        "headline_zh": "大約等於收到的權利金（扣費用）。",
        "body_zh": "價格沒跌破履約價、賣權作廢時，你留下權利金。賺不到無限。",
        "when_zh": "到期時現貨高於履約價。",
        "not_zh": "不是保證入帳。",
    },
    "max_loss": {
        "title_zh": "最大虧損",
        "headline_zh": "標的可以接近零，你仍須用履約價買入；虧損約為履約價減去權利金，可以非常大。",
        "body_zh": "這不是「最多虧權利金」。權利金只是買方付給你的費用；你可能被迫高價買入正在崩跌的幣。",
        "when_zh": "標的大跌並被履約。",
        "not_zh": "不是有限像買方那樣只虧權利金。",
    },
    "example_zh": None,
    "risks_zh": [
        "被履約後持有下跌中的現貨。",
        "資金被鎖在擔保現金裡。",
        "v1 未提供，請勿把本頁當成操作手冊。",
    ],
    "not_this_zh": ["不是 Canopy 現在會幫你下的單。"],
    "diagram": {
        "kind": "cash_secured_put",
        "spot": 100000,
        "strike": 90000,
        "premium": 2000,
        "x_min": 0,
        "x_max": 130000,
        "underlying": "BTC",
        "qty": 1,
        "note_zh": "即將推出。示意圖只用來對照風險形狀，不是操作手冊。",
        "x_label_zh": "到期現貨價格",
        "y_label_zh": "這張賣出賣權的損益",
        "series": [
            {"id": "strategy", "label_zh": "現金擔保賣權"},
        ],
        "pieces": [
            {"id": "cash", "title_zh": "先留現金", "body_zh": "準備好用履約價買入所需的錢。"},
            {"id": "put", "title_zh": "賣出賣權", "body_zh": "收權利金，答應跌夠深時要買入。"},
            {"id": "sum", "title_zh": "合在一起", "body_zh": "沒跌破：留下權利金。大跌：高價買入正在崩的幣。"},
        ],
        "flow": [
            {"title_zh": "準備現金", "body_zh": "擔保買入。"},
            {"title_zh": "賣出賣權", "body_zh": "先收權利金。"},
            {"title_zh": "沒跌破", "body_zh": "賣權作廢，留下權利金。"},
            {"title_zh": "跌破被履約", "body_zh": "你必須用履約價買入。"},
        ],
        "scenarios": [
            {"id": "up", "spot": 110000, "label_zh": "現貨上漲", "caption_zh": "賣權作廢，最多賺到權利金。"},
            {"id": "flat", "spot": 95000, "label_zh": "小跌仍在履約價上", "caption_zh": "仍是賺權利金。"},
            {"id": "crash", "spot": 40000, "label_zh": "大跌被履約", "caption_zh": "用 90,000 買入只值 40,000 的幣。"},
        ],
    },
}

BULL_PUT_SPREAD: dict[str, Any] = {
    "id": "bull_put_spread",
    "name_zh": "牛市賣權價差（Bull Put Spread）",
    "name_en": "Bull Put Spread",
    "available": False,
    "status": "coming_soon",
    "one_liner_zh": "同時賣一張較高履約價的賣權、買一張較低履約價的賣權，用價差把最大虧損框在兩檔履約價之間。",
    "for_whom_zh": "v1 尚未提供。",
    "beginner_zh": [
        "價差是兩張選擇權組合：一邊收權利金、一邊付權利金，淨收或淨付一筆較小的金額。",
        "最大虧損通常等於兩檔履約價差距減去淨權利金，不再是現貨歸零那種「整筆現貨」。這跟掩護性買權的風險形狀不同。",
    ],
    "how_it_works_zh": ["即將推出，Canopy v1 不會下這類單。"],
    "max_profit": {
        "title_zh": "最大獲利",
        "headline_zh": "進場時的淨權利金（扣費用）。",
        "body_zh": "價格夠高、兩張賣權都作廢時，留下淨權利金。",
        "when_zh": "到期現貨高於賣出那檔履約價。",
        "not_zh": "不是無限獲利。",
    },
    "max_loss": {
        "title_zh": "最大虧損",
        "headline_zh": "大約是兩檔履約價的差距，減去你已收的淨權利金。",
        "body_zh": "比裸賣賣權有上限，但上限仍可能很大，而且保證金、提前平倉、流動性都可能讓實際虧損不同於教科書公式。",
        "when_zh": "到期現貨低於買入那檔履約價。",
        "not_zh": "不是零風險；有上限不等於小。",
    },
    "example_zh": None,
    "risks_zh": ["價差仍可能虧到上限。", "v1 未提供。"],
    "not_this_zh": ["不是 Canopy 現在會幫你下的單。"],
    "diagram": {
        "kind": "bull_put_spread",
        "spot": 100000,
        "short_strike": 90000,
        "long_strike": 80000,
        "premium": 1500,
        "x_min": 50000,
        "x_max": 120000,
        "underlying": "BTC",
        "qty": 1,
        "note_zh": "即將推出。最大虧損被兩檔履約價框住，但上限仍可能很大。",
        "x_label_zh": "到期現貨價格",
        "y_label_zh": "價差損益",
        "series": [
            {"id": "strategy", "label_zh": "牛市賣權價差"},
        ],
        "pieces": [
            {"id": "short", "title_zh": "賣較高履約價賣權", "body_zh": "收權利金。"},
            {"id": "long", "title_zh": "買較低履約價賣權", "body_zh": "付權利金，換一個虧損天花板。"},
            {"id": "sum", "title_zh": "淨權利金", "body_zh": "最多賺淨收；最多虧兩檔差距減淨收。"},
        ],
        "flow": [
            {"title_zh": "賣出高履約賣權", "body_zh": "收一筆。"},
            {"title_zh": "買入低履約賣權", "body_zh": "付一筆較小的。"},
            {"title_zh": "現貨夠高", "body_zh": "兩張都作廢，留下淨權利金。"},
            {"title_zh": "現貨夠低", "body_zh": "虧到兩檔差距減淨權利金。"},
        ],
        "scenarios": [
            {"id": "up", "spot": 100000, "label_zh": "現貨維持高檔", "caption_zh": "最大獲利 = 淨權利金。"},
            {"id": "mid", "spot": 85000, "label_zh": "落在兩檔之間", "caption_zh": "部分虧損。"},
            {"id": "crash", "spot": 70000, "label_zh": "跌破較低履約價", "caption_zh": "碰到最大虧損。"},
        ],
    },
}

NAKED_SHORT: dict[str, Any] = {
    "id": "naked_short",
    "name_zh": "裸賣選擇權（Naked Short）",
    "name_en": "Naked short options",
    "available": False,
    "status": "not_offered",
    "one_liner_zh": "帳戶裡沒有對應現貨或足額擔保就賣出選擇權。大漲或大跌時虧損可以遠大於權利金。",
    "for_whom_zh": "Canopy 不提供。列出來是為了對照：為什麼 v1 堅持做掩護性買權。",
    "beginner_zh": [
        "沒有現貨卻賣出買權，價格大漲時你必須到市場高價買幣再交割，虧損理論上無上限。",
        "這就是「樹冠」隱喻的反面：沒有樹，只賣遮蔭。",
    ],
    "how_it_works_zh": ["本產品不下這類單。"],
    "max_profit": {
        "title_zh": "最大獲利",
        "headline_zh": "仍大約只有權利金。",
        "body_zh": "報酬有限、風險可以極大，所以不適合當成「多賺一點權利金」的升級版。",
        "when_zh": "選擇權作廢。",
        "not_zh": "不是 Canopy 的策略。",
    },
    "max_loss": {
        "title_zh": "最大虧損",
        "headline_zh": "裸賣買權時理論上無上限；裸賣賣權時可接近履約價整段（標的歸零）。",
        "body_zh": "權利金完全無法代表最大虧損。",
        "when_zh": "標的劇烈朝不利方向移動。",
        "not_zh": "不要用本產品做這件事。",
    },
    "example_zh": None,
    "risks_zh": ["爆倉、追繳、無限損失。"],
    "not_this_zh": ["Canopy 不做裸賣選擇權。"],
    "diagram": {
        "kind": "naked_short_call",
        "spot": 100000,
        "strike": 110000,
        "premium": 1500,
        "x_min": 70000,
        "x_max": 160000,
        "underlying": "BTC",
        "qty": 1,
        "note_zh": "Canopy 不下這類單。右邊虧損沒有天花板，用來對照為什麼 v1 要做掩護性買權。",
        "x_label_zh": "到期現貨價格",
        "y_label_zh": "裸賣買權損益",
        "series": [
            {"id": "strategy", "label_zh": "裸賣買權（不提供）"},
        ],
        "pieces": [
            {"id": "none", "title_zh": "沒有現貨", "body_zh": "帳戶裡沒有對應的幣。"},
            {"id": "call", "title_zh": "仍賣出買權", "body_zh": "只收到薄薄的權利金。"},
            {"id": "sum", "title_zh": "大漲時", "body_zh": "必須到市場高價買幣交割。虧損理論上無上限。"},
        ],
        "flow": [
            {"title_zh": "沒有樹", "body_zh": "沒有現貨。"},
            {"title_zh": "只賣遮蔭", "body_zh": "收權利金。"},
            {"title_zh": "現貨不漲", "body_zh": "最多賺權利金。"},
            {"title_zh": "現貨狂漲", "body_zh": "虧損可以一直增加。"},
        ],
        "scenarios": [
            {"id": "flat", "spot": 100000, "label_zh": "沒大漲", "caption_zh": "賺到權利金。"},
            {"id": "strike", "spot": 110000, "label_zh": "剛好到履約價", "caption_zh": "仍約等於權利金。"},
            {"id": "moon", "spot": 160000, "label_zh": "大漲", "caption_zh": "虧損已遠大於權利金，而且還能更大。"},
        ],
    },
}

STRATEGIES: tuple[dict[str, Any], ...] = (
    COVERED_CALL,
    CASH_SECURED_PUT,
    BULL_PUT_SPREAD,
    NAKED_SHORT,
)

INTRO_ZH = (
    "用圖看懂：下面是到期損益圖，不是文章。"
    "掩護性買權以幣本位為主，旁邊搭配 U 本位；數字是假的示意，可拖動到期現貨價格。"
    "Canopy 現在只幫你做掩護性買權；其他圖是對照風險形狀，不是現在能開的開關。"
)
DISCLAIMER_ZH = (
    "不是投資建議。最大獲利／最大虧損是策略結構上的說明，實際數字還會被手續費、滑價、提前平倉與被履約時機改變。"
    "過去績效與 APR 不是收益承諾。"
)


def public_catalog() -> dict[str, Any]:
    return {
        "intro_zh": INTRO_ZH,
        "disclaimer_zh": DISCLAIMER_ZH,
        "v1_strategy": "covered_call",
        "strategies": list(STRATEGIES),
    }
