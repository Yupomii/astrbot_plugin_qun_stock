import random
import time
from typing import Dict, List, Optional, Tuple
from astrbot.api import logger
from .db import StockDB


SECTOR_LIST = ["二次元纯度", "深夜修仙", "技术整活", "摸鱼躺平"]

ITEM_SHOP = {
    "做空报告": {
        "price": 3000,
        "desc": "做空机构密函：发布针对目标公司的深度沽空分析，使其股价遭遇砸盘阴跌"
    },
    "拔网线卡": {
        "price": 5000,
        "desc": "紧急熔断机制：强制冻结某支股票交易10分钟，无法买卖，方便主力逃顶"
    },
    "内幕消息卡": {
        "price": 2000,
        "desc": "资本窃听器：提前获悉下一个可能出现的宏观经济或板块波动风向"
    }
}


class MarketEngine:
    def __init__(self, db: StockDB, config: dict):
        self.db = db
        self.config = config

    def check_st_and_delist(self, stock: dict) -> Tuple[bool, bool, str]:
        """检查股票是否达到 ST 戴帽或强制退市条件。返回 (is_st, is_delisted, notice)"""
        stock_id = stock["stock_id"]
        price = stock["price"]
        issue_price = stock["issue_price"]
        last_active = stock["last_active_time"]
        now = time.time()

        # 1. 跌到 0 元直接退市
        if price <= 0.05 and not stock["is_delisted"]:
            self.db.update_stock_fields(stock_id, is_delisted=1, is_st=1, price=0.0)
            return True, True, f"【破产退市警告】{stock['name']}(${stock['symbol']}) 股价归零，已触碰强制退市线，即刻退市摘牌！投资人血本无归！"

        # 2. ST 判定：跌破发行价20% 或 连续3天未发言
        st_threshold = self.config.get("st_threshold", 0.20)
        st_days = self.config.get("st_inactive_days", 3)
        days_inactive = (now - last_active) / 86400

        needs_st = (price < issue_price * st_threshold) or (days_inactive >= st_days)

        if needs_st and not stock["is_st"]:
            self.db.update_stock_fields(stock_id, is_st=1)
            reason = "股价跌破发行价20%" if price < issue_price * st_threshold else f"连续 {int(days_inactive)} 天潜水失联"
            return True, False, f"【ST戴帽警示】{stock['name']}(${stock['symbol']}) 因{reason}，即日起披星戴帽实施退市风险警示，股票更名为 *ST{stock['symbol']}！"
        elif not needs_st and stock["is_st"]:
            # 摘帽
            self.db.update_stock_fields(stock_id, is_st=0)
            return False, False, f"【恢复摘帽】{stock['name']}(${stock['symbol']}) 经营状况改善，成功撤销退市风险警示，股票恢复正常上市！"

        return bool(stock["is_st"]), bool(stock["is_delisted"]), ""

    def trigger_event_impact(self, trigger_stock_id: str, event_type: str, custom_text: str = "") -> str:
        """事件驱动股价引擎：包含板块连坐恐慌机制"""
        stock = self.db.get_stock(trigger_stock_id)
        if not stock or stock["is_delisted"] or stock["is_suspended"]:
            return ""

        sector = stock["sector"]
        current_price = stock["price"]
        symbol = stock["symbol"]
        reports = []

        if event_type == "ban":
            # 被禁言：重大利空，暴跌 20%，并触发板块连坐
            drop_rate = 0.20
            new_price = round(current_price * (1 - drop_rate), 2)
            self.db.update_stock_price(trigger_stock_id, new_price)
            reports.append(f"【突发暴雷】{stock['name']}(${symbol}) 核心高管涉嫌严重违纪被强制双规（禁言），开盘一字跌停暴跌 {int(drop_rate*100)}%，现价 {new_price:.2f}！")

            # 板块连坐：同板块股票恐慌性抛售下跌 8%
            peers = self.db.list_stocks_by_sector(sector)
            affected = []
            for p in peers:
                if p["stock_id"] != trigger_stock_id and not p["is_suspended"]:
                    peer_drop = round(p["price"] * 0.08, 2)
                    self.db.update_stock_price(p["stock_id"], p["price"] - peer_drop)
                    affected.append(f"{p['symbol']}(-8%)")
            if affected:
                reports.append(f"【板块踩踏】受行业龙头暴雷连坐影响，【{sector}板块】全面承压：{', '.join(affected[:5])} 遭遇恐慌性踩踏！")

        elif event_type == "recall":
            # 撤回消息：涉嫌毁灭证据/财报造假，暴跌 10%
            drop_rate = 0.10
            new_price = round(current_price * (1 - drop_rate), 2)
            self.db.update_stock_price(trigger_stock_id, new_price)
            reports.append(f"【财务造假】{stock['name']}(${symbol}) 涉嫌紧急撤回毁灭虚假陈述证据，股价闪崩 -{int(drop_rate*100)}%，现价 {new_price:.2f}！")

        elif event_type == "essence":
            # 加精华：重大利好，大涨 18%，带动板块
            rise_rate = 0.18
            new_price = round(current_price * (1 + rise_rate), 2)
            self.db.update_stock_price(trigger_stock_id, new_price)
            reports.append(f"【重大利好】{stock['name']}(${symbol}) 核心产品荣获行业国家级重点认证（被设为精华），股价暴力拉升 +{int(rise_rate*100)}% 封死涨停，现价 {new_price:.2f}！")

            peers = self.db.list_stocks_by_sector(sector)
            lifted = []
            for p in peers:
                if p["stock_id"] != trigger_stock_id and not p["is_suspended"]:
                    peer_rise = round(p["price"] * 0.05, 2)
                    self.db.update_stock_price(p["stock_id"], p["price"] + peer_rise)
                    lifted.append(f"{p['symbol']}(+5%)")
            if lifted:
                reports.append(f"【板块联动】受利好催化，【{sector}板块】概念股全线走强：{', '.join(lifted[:5])} 同步上涨！")

        return "\n".join(reports)

    def handle_apply_suspend(self, user_id: str, reason: str) -> str:
        """申请停牌"""
        stock = self.db.get_stock(user_id)
        if not stock:
            return "你名下的公司尚未敲钟IPO上市，无法申请停牌！"
        if stock["is_suspended"]:
            return f"你的股票已经在停牌保护中，停牌事由：{stock['suspend_reason']}。"
        if stock["is_delisted"]:
            return "公司已经破产退市，无法停牌，请先提交破产重组申请！"

        self.db.update_stock_fields(user_id, is_suspended=1, suspend_reason=reason or "重大经营战略调整", suspend_time=time.time())
        return f"【临时停牌公告】{stock['name']}(${stock['symbol']}) 因筹划重大事项（{reason or '闭关修仙'}）即日起紧急停牌锁仓，期间股价冻结，暂停所有申购与抛售！"

    def handle_apply_resume(self, user_id: str, statement: str) -> str:
        """申请复牌：根据第一句话判定开盘走势"""
        stock = self.db.get_stock(user_id)
        if not stock:
            return "未找到你名下的上市公司！"
        if not stock["is_suspended"]:
            return "你的股票处于正常交易状态，无需复牌！"

        # 根据发言长度与诚意度计算复牌行情
        length = len(statement.strip())
        now = time.time()
        suspend_duration = int((now - stock.get("suspend_time", now)) / 3600)

        if length >= 20:
            # 准备充分，资产重组成功，大幅补涨
            gain = round(stock["price"] * 0.25, 2)
            self.db.update_stock_price(user_id, stock["price"] + gain)
            self.db.update_stock_fields(user_id, is_suspended=0, suspend_reason="", suspend_time=0)
            return (f"【重大资产重组成功复牌公告】\n"
                    f"{stock['name']}(${stock['symbol']}) 历经 {suspend_duration} 小时闭关潜修，携重大战略项目强势回归群聊！\n"
                    f"路演申诉：“{statement}”\n"
                    f"市场主力疯狂抢筹，开盘一字涨停暴涨 +25%！现价 {stock['price'] + gain:.2f} 金币！")
        else:
            # 敷衍了事，利好出尽，开盘补跌
            loss = round(stock["price"] * 0.15, 2)
            self.db.update_stock_price(user_id, stock["price"] - loss)
            self.db.update_stock_fields(user_id, is_suspended=0, suspend_reason="", suspend_time=0)
            return (f"【利好出尽复牌公告】\n"
                    f"{stock['name']}(${stock['symbol']}) 结束停牌，但复牌声明仅有寥寥数语（“{statement}”），重组预期落空引发主力散户夺路而逃，开盘暴跌 -15%！现价 {stock['price'] - loss:.2f} 金币！")

    def handle_reorganize_ipo(self, user_id: str, pitch_text: str, user_name: str) -> str:
        """破产重组二次上市审核：检查不少于50字的PPT路演检讨"""
        stock = self.db.get_stock(user_id)
        if stock and not stock["is_delisted"]:
            return f"你的股票 {stock['name']} 正在正常交易，无需破产重组！"

        clean_text = pitch_text.strip()
        if len(clean_text) < 50:
            return f"【重组审核驳回】退市重组路演材料极其敷衍！当前字数仅 {len(clean_text)} 字，必须提交不少于 50 字的诚恳破产检讨书或商业路演策划！"

        # 审核通过，重新按 5.0 元低价发行重新上市
        symbol = user_name[:4]
        new_stock = self.db.create_stock(user_id, user_name, symbol, issue_price=5.0, total_shares=10000, sector="摸鱼躺平")
        return (f"【破产重组敲钟上市公告】\n"
                f"在经历破产退市洗礼后，{user_name} 凭借诚意满满的商业重组路演重新获得上市资格！\n"
                f"路演宣誓摘要：“{clean_text[:40]}...”\n"
                f"新股代码：${symbol} | 发行价：5.00 金币 | 欢迎广大股民重新认购！")
