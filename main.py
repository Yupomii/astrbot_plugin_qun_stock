import asyncio
import time
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from .src.db import StockDB
from .src.market import MarketEngine, ITEM_SHOP, SECTOR_LIST
from .src.utils import extract_target_id, parse_trade_args


@register(
    "astrbot_plugin_qun_stock",
    "Kirizuki",
    "群友股票交易所：行为证券化与赛博操盘手",
    "1.0.0",
)
class QunStockPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config if isinstance(config, dict) else {}
        self.db = StockDB()
        self.engine = MarketEngine(self.db, self.config)

    # ================= 消息监听驱动股价 =================
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_chat(self, event: AstrMessageEvent):
        """记录群友日常活跃，根据水群热度微调股价与解除退市预警"""
        if not self.config.get("enable", True):
            return

        sender_id = str(event.get_sender_id() or "")
        sender_name = event.get_sender_name() or "神秘股民"
        if not sender_id or sender_id == str(event.get_self_id()):
            return

        # 检查该公司是否存在
        stock = self.db.get_stock(sender_id)
        now = time.time()

        if stock and not stock["is_delisted"] and not stock["is_suspended"]:
            # 更新最后活跃时间
            self.db.update_stock_fields(sender_id, last_active_time=now)

            # 水群活跃度轻微拉盘 (0.01 - 0.03)
            if random_bump := (time.time() % 7 < 1):
                new_price = round(stock["price"] + 0.02, 2)
                self.db.update_stock_price(sender_id, new_price)

            # 检查 ST 戴帽或摘帽
            _, _, notice = self.engine.check_st_and_delist(stock)
            if notice:
                await event.send(event.plain_result(notice))

    # ================= 大盘行情与查询指令 =================
    @filter.command("股市", alias={"大盘", "行情"})
    async def cmd_market_board(self, event: AstrMessageEvent):
        """查看全群股票大盘实时行情"""
        stocks = self.db.list_all_stocks(include_delisted=False)
        if not stocks:
            yield event.plain_result("群友股票交易所目前还没有敲钟上市的公司！发送 /上市申请 即可免费敲钟IPO！")
            return

        lines = ["【群友股票交易所 · 实时大盘行情】"]
        for s in stocks[:15]:
            status_tag = ""
            if s["is_suspended"]:
                status_tag = "[停牌]"
            elif s["is_st"]:
                status_tag = "[*ST]"

            change_pct = round(((s["price"] - s["issue_price"]) / s["issue_price"]) * 100, 1)
            sign = "+" if change_pct >= 0 else ""
            ctrl_tag = f" (控:{s['controller_id'][:4]}..)" if s["controller_id"] else ""

            lines.append(f"• {status_tag}{s['name']} (${s['symbol']}) | 现价:{s['price']:.2f} ({sign}{change_pct}%) | 板块:{s['sector']}{ctrl_tag}")

        lines.append("\n常用指令：/查股价 @某人 | /买入 @某人 数量 | /持仓 | /打工")
        yield event.plain_result("\n".join(lines))

    @filter.command("查股价")
    async def cmd_check_stock(self, event: AstrMessageEvent, target: str = ""):
        """查看某只股票的详细财务报表与大股东信息"""
        target_id = extract_target_id(event, target) or target.strip().lstrip("@")
        if not target_id:
            yield event.plain_result("请指定要查询的股票！例如：/查股价 @某人 或 /查股价 代号")
            return

        stock = self.db.get_stock(target_id)
        if not stock:
            yield event.plain_result(f"未在交易所查询到公司：“{target_id}”！对方可能尚未IPO上市。")
            return

        # 检查ST状态
        _, _, st_notice = self.engine.check_st_and_delist(stock)
        change_pct = round(((stock["price"] - stock["issue_price"]) / stock["issue_price"]) * 100, 2)
        sign = "+" if change_pct >= 0 else ""

        # 股东排名
        holders = self.db.get_stock_holders(stock["stock_id"])
        top_holders_str = []
        for i, h in enumerate(holders[:3], 1):
            ratio = (h["shares"] / stock["total_shares"]) * 100
            top_holders_str.append(f"  {i}. 股民 {h['user_id']} : {h['shares']}股 ({ratio:.1f}%)")

        holder_text = "\n".join(top_holders_str) if top_holders_str else "  暂无机构或散户建仓持股"

        status_desc = "正常交易"
        if stock["is_delisted"]:
            status_desc = "已破产强制退市"
        elif stock["is_suspended"]:
            status_desc = f"临时停牌中（{stock['suspend_reason']}）"
        elif stock["is_st"]:
            status_desc = "*ST 退市风险警示"

        info_lines = [
            f"【上市公司财报档案 · {stock['name']}】",
            f"• 股票代码: ${stock['symbol']} (QQ: {stock['stock_id']})",
            f"• 当前市值单价: {stock['price']:.2f} 金币 ({sign}{change_pct}%)",
            f"• 发行初始价: {stock['issue_price']:.2f} 金币",
            f"• 所属概念板块: 【{stock['sector']}板块】",
            f"• 证券状态: {status_desc}",
        ]

        if stock["controller_id"]:
            info_lines.append(f"• 绝对控股母公司: 股民 {stock['controller_id']} (持股超50%实际控制)")
        if stock["slogan"]:
            info_lines.append(f"• 控股企业标语: “{stock['slogan']}”")

        info_lines.append("\n【十大流通股东持仓席位】:")
        info_lines.append(holder_text)

        yield event.plain_result("\n".join(info_lines))

    # ================= 交易核心指令 =================
    @filter.command("买入")
    async def cmd_buy(self, event: AstrMessageEvent, raw_target: str = "", raw_shares: str = ""):
        """市价买入多头股票：/买入 @某人 股数"""
        target_key, shares, err = parse_trade_args(event, raw_target, raw_shares)
        if err:
            yield event.plain_result(err)
            return

        user_id = str(event.get_sender_id())
        stock = self.db.get_stock(target_key)
        if not stock:
            yield event.plain_result(f"未找到目标股票“{target_key}”！请确认对方是否已上市。")
            return

        if stock["is_delisted"]:
            yield event.plain_result(f"公司 {stock['name']} 已经宣告破产退市，严禁场内交易！")
            return
        if stock["is_suspended"]:
            yield event.plain_result(f"公司 {stock['name']} 目前正处于停牌保护中（事由：{stock['suspend_reason']}），暂停买卖！")
            return

        tax_rate = self.config.get("trade_tax_rate", 0.01)
        success, msg = self.db.execute_buy(user_id, stock["stock_id"], shares, stock["price"], tax_rate)
        yield event.plain_result(msg)

    @filter.command("卖出")
    async def cmd_sell(self, event: AstrMessageEvent, raw_target: str = "", raw_shares: str = ""):
        """市价卖出持有的股票：/卖出 @某人 股数"""
        target_key, shares, err = parse_trade_args(event, raw_target, raw_shares)
        if err:
            yield event.plain_result(err)
            return

        user_id = str(event.get_sender_id())
        stock = self.db.get_stock(target_key)
        if not stock:
            yield event.plain_result(f"未找到目标股票“{target_key}”！")
            return

        if stock["is_suspended"]:
            yield event.plain_result(f"公司 {stock['name']} 处于停牌锁仓中，暂停卖出变现！")
            return

        tax_rate = self.config.get("trade_tax_rate", 0.01)
        success, msg = self.db.execute_sell(user_id, stock["stock_id"], shares, stock["price"], tax_rate)
        yield event.plain_result(msg)

    @filter.command("做空")
    async def cmd_short(self, event: AstrMessageEvent, raw_target: str = "", raw_shares: str = ""):
        """借券沽空机制：在股价下跌时获利。/做空 @某人 股数"""
        target_key, shares, err = parse_trade_args(event, raw_target, raw_shares)
        if err:
            yield event.plain_result(err)
            return

        user_id = str(event.get_sender_id())
        stock = self.db.get_stock(target_key)
        if not stock or stock["is_delisted"] or stock["is_suspended"]:
            yield event.plain_result("目标股票不存在或处于停牌/退市状态，无法建立空头仓位！")
            return

        success, msg = self.db.execute_short(user_id, stock["stock_id"], shares, stock["price"])
        yield event.plain_result(msg)

    # ================= 个人持仓与日常打工 =================
    @filter.command("持仓", alias={"我的持仓", "资产"})
    async def cmd_my_portfolio(self, event: AstrMessageEvent):
        """查看个人总资产、可用现金与持仓明细"""
        user_id = str(event.get_sender_id())
        user_info = self.db.get_or_create_user(user_id, self.config.get("initial_cash", 10000.0))
        positions = self.db.list_user_positions(user_id)

        lines = [
            f"【股民 {user_id} · 赛博投资账户】",
            f"• 可用金币余额: {user_info['balance']:.2f}",
        ]

        total_stock_value = 0.0
        pos_lines = []
        for p in positions:
            # 多头持仓
            if p["shares"] > 0:
                mkt_val = p["shares"] * p["current_price"]
                total_stock_value += mkt_val
                pnl = (p["current_price"] - p["cost_price"]) * p["shares"]
                sign = "+" if pnl >= 0 else ""
                pos_lines.append(f"  [多] {p['name']}(${p['symbol']}): {p['shares']}股 | 现价:{p['current_price']:.2f} | 盈亏:{sign}{pnl:.2f}")

            # 空头持仓
            if p["short_shares"] > 0:
                short_pnl = (p["short_price"] - p["current_price"]) * p["short_shares"]
                sign = "+" if short_pnl >= 0 else ""
                pos_lines.append(f"  [空] {p['name']}(${p['symbol']}): {p['short_shares']}股 | 沽空均价:{p['short_price']:.2f} | 浮盈:{sign}{short_pnl:.2f}")

        total_net_worth = user_info["balance"] + total_stock_value
        lines.append(f"• 股票总市值: {total_stock_value:.2f}")
        lines.append(f"• 个人总资产: {total_net_worth:.2f} 金币")
        lines.append("\n【持仓明细席位】:")
        if pos_lines:
            lines.extend(pos_lines)
        else:
            lines.append("  空仓观望中，尚无任何持股记录。")

        yield event.plain_result("\n".join(lines))

    @filter.command("打工", alias={"签到", "领工资"})
    async def cmd_work_salary(self, event: AstrMessageEvent):
        """每日签到打工领补贴，若被恶意控股将扣除抽水分红"""
        user_id = str(event.get_sender_id())
        base_salary = float(self.config.get("daily_salary", 1000))

        # 检查该群友名下的公司是否被大股东控股
        stock = self.db.get_stock(user_id)
        controller_id = stock.get("controller_id") if stock else None
        dividend_rate = self.config.get("dividend_tax_rate", 0.20)

        actual_salary = base_salary
        tax_cut = 0.0

        if controller_id and controller_id != user_id:
            tax_cut = round(base_salary * dividend_rate, 2)
            actual_salary = base_salary - tax_cut

        ok, cur_bal = self.db.record_sign_in(user_id, actual_salary)
        if not ok:
            yield event.plain_result(f"今天已经在流水线上打过工啦！请明日再来领取工资。当前余额：{cur_bal:.2f} 金币。")
            return

        resp = [f"打工领薪成功！今日基础工钱 {base_salary:.2f} 金币已发放。"]
        if tax_cut > 0:
            # 转给大股东
            self.db.update_balance(controller_id, tax_cut)
            resp.append(f"【资本家嗜血分红】因你的公司已被股民 {controller_id} 绝对控股，系统依法强制代扣 {int(dividend_rate*100)}% ({tax_cut:.2f}金币) 汇入母公司大股东金库！")
            resp.append(f"你实际到手：{actual_salary:.2f} 金币。")

        resp.append(f"当前账户可用金币：{cur_bal:.2f}。")
        yield event.plain_result("\n".join(resp))

    # ================= 恶意收购与控股特权 =================
    @filter.command("设置口号")
    async def cmd_set_slogan(self, event: AstrMessageEvent, raw_target: str = "", slogan: str = ""):
        """持股超50%的控股股东行使收购特权，给子公司强制打上专属Slogan"""
        target_id = extract_target_id(event, raw_target) or raw_target.strip().lstrip("@")
        user_id = str(event.get_sender_id())

        stock = self.db.get_stock(target_id)
        if not stock:
            yield event.plain_result("未找到目标公司！")
            return

        if stock["controller_id"] != user_id:
            yield event.plain_result(f"收购特权越权失败！你尚未对公司 {stock['name']} 达成 50% 以上的绝对控股席位，无法修改企业标语！")
            return

        clean_slogan = slogan.strip()
        if not clean_slogan:
            yield event.plain_result("请输入要设置的口号标语！例如：/设置口号 @某人 精神饱满全力打工")
            return

        self.db.update_stock_fields(stock["stock_id"], slogan=clean_slogan)
        yield event.plain_result(f"【控股股东令】实际控制人 {user_id} 强制修改了子公司 {stock['name']} 的企业标语：“{clean_slogan}”！已全网公告！")

    # ================= 停牌与复牌机制 =================
    @filter.command("申请停牌")
    async def cmd_suspend(self, event: AstrMessageEvent, reason: str = ""):
        """闭关备考/防沉迷锁仓停牌"""
        user_id = str(event.get_sender_id())
        yield event.plain_result(self.engine.handle_apply_suspend(user_id, reason))

    @filter.command("申请复牌")
    async def cmd_resume(self, event: AstrMessageEvent, statement: str = ""):
        """结束停牌复牌开盘，根据陈述诚意度判定涨跌停"""
        user_id = str(event.get_sender_id())
        if not statement:
            yield event.plain_result("请在申请复牌时附带一段复牌陈述或路演讲话！例如：/申请复牌 我带着全新大项目回来了")
            return
        yield event.plain_result(self.engine.handle_apply_resume(user_id, statement))

    @filter.command("重组路演")
    async def cmd_reorganize(self, event: AstrMessageEvent, pitch: str = ""):
        """破产退市公司提交不少于50字检讨书重新IPO"""
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or "重组创客"
        yield event.plain_result(self.engine.handle_reorganize_ipo(user_id, pitch, user_name))

    # ================= 自主敲钟上市 =================
    @filter.command("上市申请")
    async def cmd_apply_ipo(self, event: AstrMessageEvent, sector: str = "摸鱼躺平"):
        """群友自主敲钟上市成为股票代码"""
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or f"创客_{user_id[-4:]}"

        if self.db.get_stock(user_id):
            yield event.plain_result("你的公司已经在群友交易所上市敲钟啦！发送 /查股价 查看详情。")
            return

        if sector not in SECTOR_LIST:
            sector = "摸鱼躺平"

        symbol = user_name[:4]
        stock = self.db.create_stock(user_id, user_name, symbol, issue_price=10.0, total_shares=10000, sector=sector)
        yield event.plain_result(
            f"【IPO成功敲钟公告】\n"
            f"热烈祝贺 {user_name} 旗下核心资产成功在群友股票交易所挂牌上市！\n"
            f"• 股票代码: ${symbol} (QQ: {user_id})\n"
            f"• 发行初始价: 10.00 金币\n"
            f"• 归属板块: 【{sector}板块】\n"
            f"广大股民现已可通过 /买入 @{user_name} 进行挂单认购！"
        )

    # ================= 黑客道具商城 =================
    @filter.command("道具商城", alias={"股市商城"})
    async def cmd_shop(self, event: AstrMessageEvent):
        """查看黑客做空道具"""
        lines = ["【群友交易所 · 秘密金融黑客商城】"]
        for name, item in ITEM_SHOP.items():
            lines.append(f"• [{name}] 售价:{item['price']} 金币\n  功效: {item['desc']}")
        lines.append("\n购买方式：/购买道具 <名称> | 使用方式：/使用道具 <名称> @某人")
        yield event.plain_result("\n".join(lines))

    @filter.command("购买道具")
    async def cmd_buy_item(self, event: AstrMessageEvent, item_name: str = ""):
        """使用金币购买金融黑客道具"""
        user_id = str(event.get_sender_id())
        item = ITEM_SHOP.get(item_name.strip())
        if not item:
            yield event.plain_result("商城货架上没有这个道具！发送 /道具商城 查看在售商品。")
            return

        cost = item["price"]
        user_info = self.db.get_or_create_user(user_id)
        if user_info["balance"] < cost:
            yield event.plain_result(f"金币不足！购买【{item_name}】需要 {cost} 金币，你当前只有 {user_info['balance']:.2f}。")
            return

        self.db.update_balance(user_id, -cost)
        self.db.add_item(user_id, item_name.strip(), 1)
        yield event.plain_result(f"成功耗资 {cost} 金币购入【{item_name}】！已存入你的公文包。")

    @filter.command("使用道具")
    async def cmd_use_item(self, event: AstrMessageEvent, item_name: str = "", raw_target: str = ""):
        """使用金融黑客道具实施做空或熔断"""
        user_id = str(event.get_sender_id())
        item_name = item_name.strip()

        if not self.db.use_item(user_id, item_name):
            yield event.plain_result(f"你的公文包里没有【{item_name}】道具！请先在 /道具商城 购买。")
            return

        if item_name == "内幕消息卡":
            tips = [
                "【绝密内幕偷跑】据传【深夜修仙板块】某大户即将被管理重点盯防，小心遭遇暴雷连坐！",
                "【内幕风声】市场传闻某巨鲸正在大举吸筹，建议盯紧低估值题材股！",
                "【机构调研情报】二次元纯度板块近期热度攀升，预计资金即将抱团炒作！"
            ]
            yield event.plain_result(f"【内幕消息已阅即焚】\n{random.choice(tips)}")
            return

        target_id = extract_target_id(event, raw_target) or raw_target.strip().lstrip("@")
        stock = self.db.get_stock(target_id)
        if not stock:
            # 补回道具
            self.db.add_item(user_id, item_name, 1)
            yield event.plain_result(f"未找到目标公司“{raw_target}”！道具已原路退回公文包。")
            return

        if item_name == "做空报告":
            drop = round(stock["price"] * 0.18, 2)
            self.db.update_stock_price(stock["stock_id"], stock["price"] - drop)
            yield event.plain_result(
                f"【沽空重磅袭来】做空机构发布针对 {stock['name']}(${stock['symbol']}) 的致命做空研报！\n"
                f"指责其存在严重虚构流水与空壳运营，股价瞬间闪崩 -18%！跌至 {stock['price'] - drop:.2f} 金币！"
            )

        elif item_name == "拔网线卡":
            self.db.update_stock_fields(stock["stock_id"], is_suspended=1, suspend_reason="交易所服务器临时拔网线熔断", suspend_time=time.time())
            yield event.plain_result(
                f"【拔网线紧急熔断】针对 {stock['name']}(${stock['symbol']}) 实施交易通道拔网线熔断！\n"
                f"该股所有场内申购抛售即刻冻结，给散户与庄家争取宝贵的避险窗口！"
            )
