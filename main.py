import asyncio
import time
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from .src.db import StockDB
from .src.market import MarketEngine, ITEM_SHOP, SECTOR_LIST
from .src.renderer import CardRenderer
from .src.utils import extract_target_id, parse_trade_args


@register(
    "astrbot_plugin_qun_stock",
    "Yupomii",
    "群友股票交易所：行为资产证券化与赛博操盘手",
    "1.0.3",
)
class QunStockPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config if isinstance(config, dict) else {}
        self.db = StockDB()
        self.engine = MarketEngine(self.db, self.config)
        self.renderer = CardRenderer()

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

        stock = self.db.get_stock(sender_id)
        now = time.time()

        if stock and not stock["is_delisted"] and not stock["is_suspended"]:
            self.db.update_stock_fields(sender_id, last_active_time=now)

            # 水群活跃度轻微拉盘 (0.01 - 0.03)
            if (now % 7 < 1):
                new_price = round(stock["price"] + 0.02, 2)
                self.db.update_stock_price(sender_id, new_price)

            # 检查 ST 戴帽或摘帽
            _, _, notice = self.engine.check_st_and_delist(stock)
            if notice:
                card_img = self.renderer.render_card(
                    title="【交易所突发风险通告】",
                    subtitle="ST 戴帽或退市风控执行",
                    content_lines=[notice],
                    footer_tip="Cyber Stock Market · 风控管理委员会"
                )
                await event.send(event.image_result(card_img))

    # ================= 大盘行情与查询指令 =================
    @filter.command("股市", alias={"大盘", "行情"})
    async def cmd_market_board(self, event: AstrMessageEvent):
        """查看全群股票大盘实时行情 (带P站最高画质插画长图)"""
        stocks = self.db.list_all_stocks(include_delisted=False)
        if not stocks:
            card_img = self.renderer.render_card(
                title="【群友股票交易所 · 实时大盘】",
                subtitle="全群行为资产证券化交易大厅",
                content_lines=["交易所目前还没有敲钟上市的公司！", "发送 /上市申请 即可免费敲钟挂牌IPO！"]
            )
            yield event.image_result(card_img)
            return

        lines = ["【股票实时报价与板块分布】:"]
        for s in stocks[:16]:
            status_tag = ""
            if s["is_suspended"]:
                status_tag = "[停牌] "
            elif s["is_st"]:
                status_tag = "[*ST] "

            change_pct = round(((s["price"] - s["issue_price"]) / s["issue_price"]) * 100, 1)
            sign = "+" if change_pct >= 0 else ""
            ctrl_tag = f" [控:{s['controller_id'][:4]}..]" if s["controller_id"] else ""

            lines.append(f"• {status_tag}{s['name']} (${s['symbol']}) | 现价:{s['price']:.2f} ({sign}{change_pct}%) | 【{s['sector']}】{ctrl_tag}")

        lines.append("")
        lines.append("【快捷交易指令指南】:")
        lines.append("• 查股价: /查股价 @某人")
        lines.append("• 买入建仓: /买入 @某人 数量 (支持直接@某人、QQ号或股票代码)")
        lines.append("• 卖出平仓: /卖出 @某人 数量")
        lines.append("• 沽空获利: /做空 @某人 数量")
        lines.append("• 资产持仓: /持仓  |  每日领薪: /打工")

        card_img = self.renderer.render_card(
            title="群友股票交易所 · 实时大盘行情",
            subtitle="实时撮合交易大厅 · 行为价值证券化看板",
            content_lines=lines,
            footer_tip="Pixiv 顶级画质支持 · 赛博金融研究所"
        )
        yield event.image_result(card_img)

    @filter.command("查股价")
    async def cmd_check_stock(self, event: AstrMessageEvent, target: str = ""):
        """查看某只股票的详细财务报表与大股东信息"""
        target_id = extract_target_id(event, target) or target.strip().lstrip("@")
        if not target_id:
            card_img = self.renderer.render_card(
                title="【参数提示】",
                subtitle="证券代码输入缺失",
                content_lines=["请指定要查询的股票！", "支持：/查股价 @某人 或 /查股价 代号"]
            )
            yield event.image_result(card_img)
            return

        stock = self.db.get_stock(target_id)
        if not stock:
            # 自动绿色通道保荐
            if target_id.isdigit():
                target_name = f"群友_{target_id[-4:]}"
                symbol = target_name[:4]
                stock = self.db.create_stock(target_id, target_name, symbol, issue_price=10.0, total_shares=10000, sector="二次元纯度")
            else:
                card_img = self.renderer.render_card(
                    title="【未收录上市公司】",
                    subtitle="股票代码查询无结果",
                    content_lines=[f"未在交易所查询到公司：“{target_id}”！", "对方名下资产可能尚未IPO上市。"]
                )
                yield event.image_result(card_img)
                return

        # 检查ST状态
        _, _, _ = self.engine.check_st_and_delist(stock)
        change_pct = round(((stock["price"] - stock["issue_price"]) / stock["issue_price"]) * 100, 2)
        sign = "+" if change_pct >= 0 else ""

        # 股东排名
        holders = self.db.get_stock_holders(stock["stock_id"])
        top_holders_str = []
        for i, h in enumerate(holders[:3], 1):
            ratio = (h["shares"] / stock["total_shares"]) * 100
            top_holders_str.append(f"  {i}. 股民席位 {h['user_id']} : {h['shares']}股 ({ratio:.1f}%)")

        status_desc = "正常上市交易中"
        if stock["is_delisted"]:
            status_desc = "已破产清算强制退市"
        elif stock["is_suspended"]:
            status_desc = f"临时停牌锁仓中（{stock['suspend_reason']}）"
        elif stock["is_st"]:
            status_desc = "*ST 退市风险警示监控中"

        lines = [
            f"【公司概况与证券代号】",
            f"• 股票全称: {stock['name']}  |  股票代号: ${stock['symbol']}",
            f"• 所属唯一ID (QQ): {stock['stock_id']}",
            f"• 所属概念板块: 【{stock['sector']}板块】",
            f"• 现行证券状态: {status_desc}",
            "",
            f"【行情财务指标】",
            f"• 最新成交单价: {stock['price']:.2f} 金币 ({sign}{change_pct}%)",
            f"• 历史发行价: {stock['issue_price']:.2f} 金币",
            f"• 注册总股本: {stock['total_shares']} 股",
        ]

        if stock["controller_id"]:
            lines.append("")
            lines.append(f"【绝对控股母公司席位】")
            lines.append(f"• 控股实控人: 股民 {stock['controller_id']} (持股占比超 50%)")
        if stock["slogan"]:
            lines.append(f"• 控股强制标语: “{stock['slogan']}”")

        lines.append("")
        lines.append("【前三大流通股东席位】:")
        if top_holders_str:
            lines.extend(top_holders_str)
        else:
            lines.append("  暂无机构或主力资金建仓记录")

        card_img = self.renderer.render_card(
            title=f"上市公司档案 · {stock['name']}",
            subtitle=f"代码: ${stock['symbol']} · 概念: {stock['sector']}",
            content_lines=lines,
            footer_tip="点击持仓可查看个人盈亏 · 投资有风险 入市需谨慎"
        )
        yield event.image_result(card_img)

    # ================= 交易核心指令 =================
    @filter.command("买入")
    async def cmd_buy(self, event: AstrMessageEvent, raw_target: str = "", raw_shares: str = ""):
        """市价买入多头股票：/买入 @某人 股数"""
        target_key, shares, err = parse_trade_args(event, raw_target, raw_shares)
        if err:
            card_img = self.renderer.render_card(
                title="【订单提交失败】", subtitle="交易委托参数错误", content_lines=[err]
            )
            yield event.image_result(card_img)
            return

        user_id = str(event.get_sender_id())
        stock = self.db.get_stock(target_key)
        if not stock:
            # 尝试通过绿色通道自动保荐上市
            target_id = extract_target_id(event, raw_target) or (target_key if target_key.isdigit() else None)
            if target_id:
                target_name = f"群友_{target_id[-4:]}"
                symbol = target_name[:4]
                stock = self.db.create_stock(target_id, target_name, symbol, issue_price=10.0, total_shares=10000, sector="二次元纯度")
            else:
                card_img = self.renderer.render_card(
                    title="【买单被拒】", subtitle="未找到目标标的", content_lines=[f"未找到目标公司“{target_key}”！请确认对方账号。"]
                )
                yield event.image_result(card_img)
                return

        if stock["is_delisted"] or stock["is_suspended"]:
            reason = "已破产退市" if stock["is_delisted"] else f"处于停牌保护中（{stock['suspend_reason']}）"
            card_img = self.renderer.render_card(
                title="【交易通道关闭】", subtitle="标的禁止交易",
                content_lines=[f"公司 {stock['name']} {reason}，暂停场内撮合成交！"]
            )
            yield event.image_result(card_img)
            return

        tax_rate = self.config.get("trade_tax_rate", 0.01)
        success, msg = self.db.execute_buy(user_id, stock["stock_id"], shares, stock["price"], tax_rate)
        card_img = self.renderer.render_card(
            title="【证券买入成交确认函】" if success else "【证券买单被拒】",
            subtitle=f"交易标的: {stock['name']}(${stock['symbol']})",
            content_lines=[msg, "", f"当前持仓已更新，可发送 /持仓 查询您的账户明细。"]
        )
        yield event.image_result(card_img)

    @filter.command("卖出")
    async def cmd_sell(self, event: AstrMessageEvent, raw_target: str = "", raw_shares: str = ""):
        """市价卖出持有的股票：/卖出 @某人 股数"""
        target_key, shares, err = parse_trade_args(event, raw_target, raw_shares)
        if err:
            card_img = self.renderer.render_card(
                title="【订单提交失败】", subtitle="交易委托参数错误", content_lines=[err]
            )
            yield event.image_result(card_img)
            return

        user_id = str(event.get_sender_id())
        stock = self.db.get_stock(target_key)
        if not stock:
            card_img = self.renderer.render_card(
                title="【卖单被拒】", subtitle="未找到标的", content_lines=[f"未找到股票“{target_key}”！"]
            )
            yield event.image_result(card_img)
            return

        if stock["is_suspended"]:
            card_img = self.renderer.render_card(
                title="【停牌锁仓】", subtitle="禁止抛售变现",
                content_lines=[f"公司 {stock['name']} 处于停牌锁仓期，严禁任何卖出操作！"]
            )
            yield event.image_result(card_img)
            return

        tax_rate = self.config.get("trade_tax_rate", 0.01)
        success, msg = self.db.execute_sell(user_id, stock["stock_id"], shares, stock["price"], tax_rate)
        card_img = self.renderer.render_card(
            title="【证券抛售成交确认函】" if success else "【证券卖单被拒】",
            subtitle=f"平仓标的: {stock['name']}(${stock['symbol']})",
            content_lines=[msg, "", f"变现资金已回笼至账户可用余额。"]
        )
        yield event.image_result(card_img)

    @filter.command("做空")
    async def cmd_short(self, event: AstrMessageEvent, raw_target: str = "", raw_shares: str = ""):
        """借券沽空机制：在股价下跌时获利。/做空 @某人 股数"""
        target_key, shares, err = parse_trade_args(event, raw_target, raw_shares)
        if err:
            card_img = self.renderer.render_card(
                title="【空头委托失败】", subtitle="做空参数校验错误", content_lines=[err]
            )
            yield event.image_result(card_img)
            return

        user_id = str(event.get_sender_id())
        stock = self.db.get_stock(target_key)
        if not stock or stock["is_delisted"] or stock["is_suspended"]:
            card_img = self.renderer.render_card(
                title="【融券沽空被拒】", subtitle="券源不足或停牌退市",
                content_lines=["目标标的目前处于停牌或退市状态，融券池关闭！"]
            )
            yield event.image_result(card_img)
            return

        success, msg = self.db.execute_short(user_id, stock["stock_id"], shares, stock["price"])
        card_img = self.renderer.render_card(
            title="【融券做空建仓通知书】" if success else "【融券做空失败】",
            subtitle=f"做空标的: {stock['name']}(${stock['symbol']})",
            content_lines=[msg, "", "风险警示：若标的股价暴涨，将面临保证金穿仓爆仓风险！"]
        )
        yield event.image_result(card_img)

    # ================= 个人持仓与日常打工 =================
    @filter.command("持仓", alias={"我的持仓", "资产"})
    async def cmd_my_portfolio(self, event: AstrMessageEvent):
        """查看个人总资产、可用现金与持仓明细"""
        user_id = str(event.get_sender_id())
        user_info = self.db.get_or_create_user(user_id, self.config.get("initial_cash", 10000.0))
        positions = self.db.list_user_positions(user_id)

        lines = [
            f"【账户资金资产总览】",
            f"• 股民账号 (QQ): {user_id}",
            f"• 可用金币余额: {user_info['balance']:.2f} 金币",
        ]

        total_stock_value = 0.0
        pos_lines = []
        for p in positions:
            if p["shares"] > 0:
                mkt_val = p["shares"] * p["current_price"]
                total_stock_value += mkt_val
                pnl = (p["current_price"] - p["cost_price"]) * p["shares"]
                sign = "+" if pnl >= 0 else ""
                pos_lines.append(f"• [多头] {p['name']}(${p['symbol']}): {p['shares']}股 | 市价:{p['current_price']:.2f} | 浮动盈亏:{sign}{pnl:.2f}")

            if p["short_shares"] > 0:
                short_pnl = (p["short_price"] - p["current_price"]) * p["short_shares"]
                sign = "+" if short_pnl >= 0 else ""
                pos_lines.append(f"• [空头] {p['name']}(${p['symbol']}): {p['short_shares']}股 | 沽空均价:{p['short_price']:.2f} | 浮动盈亏:{sign}{short_pnl:.2f}")

        total_net_worth = user_info["balance"] + total_stock_value
        lines.append(f"• 持有证券总市值: {total_stock_value:.2f} 金币")
        lines.append(f"• 个人总净资产: {total_net_worth:.2f} 金币")
        lines.append("")
        lines.append("【持有证券席位详情】:")
        if pos_lines:
            lines.extend(pos_lines)
        else:
            lines.append("  空仓避险中，暂无任何证券持仓。")

        card_img = self.renderer.render_card(
            title=f"个人资产与持仓账单",
            subtitle=f"股民: {user_id} · 净资产: {total_net_worth:.2f} 金币",
            content_lines=lines,
            footer_tip="点击 /打工 每日可领取现金补贴"
        )
        yield event.image_result(card_img)

    @filter.command("打工", alias={"签到", "领工资"})
    async def cmd_work_salary(self, event: AstrMessageEvent):
        """每日签到打工领补贴，若被恶意控股将扣除抽水分红"""
        user_id = str(event.get_sender_id())
        base_salary = float(self.config.get("daily_salary", 1000))

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
            card_img = self.renderer.render_card(
                title="【流水线防刷警报】",
                subtitle="今日工钱已领讫",
                content_lines=[f"今天已经在流水线上打过工啦！请明日再来领取工资。", f"当前账户可用金币：{cur_bal:.2f}。"]
            )
            yield event.image_result(card_img)
            return

        resp = [f"打工领薪成功！今日基础工钱 {base_salary:.2f} 金币已发放。"]
        if tax_cut > 0:
            self.db.update_balance(controller_id, tax_cut)
            resp.append("")
            resp.append(f"【资本家恶意控股抽水】")
            resp.append(f"由于您的公司已被控股大股东 {controller_id} 绝对控股，")
            resp.append(f"系统强制代扣 {int(dividend_rate*100)}% ({tax_cut:.2f}金币) 汇入母公司金库！")
            resp.append(f"实际落袋工资：{actual_salary:.2f} 金币。")

        resp.append("")
        resp.append(f"当前账户可用金币余额: {cur_bal:.2f} 金币")

        card_img = self.renderer.render_card(
            title="【每日打工工资结算单】",
            subtitle=f"员工: {user_id} · 今日补贴到账",
            content_lines=resp,
            footer_tip="努力打工赚取筹码 争夺上市公司控股权"
        )
        yield event.image_result(card_img)

    # ================= 恶意收购与控股特权 =================
    @filter.command("设置口号")
    async def cmd_set_slogan(self, event: AstrMessageEvent, raw_target: str = "", slogan: str = ""):
        """持股超50%的控股股东行使收购特权，给子公司强制打上专属Slogan"""
        target_id = extract_target_id(event, raw_target) or raw_target.strip().lstrip("@")
        user_id = str(event.get_sender_id())

        stock = self.db.get_stock(target_id)
        if not stock:
            card_img = self.renderer.render_card(
                title="【特权行使失败】", subtitle="标的公司不存在", content_lines=["未找到目标公司！"]
            )
            yield event.image_result(card_img)
            return

        if stock["controller_id"] != user_id:
            card_img = self.renderer.render_card(
                title="【收购特权越权驳回】",
                subtitle="股权不足50%无控股权",
                content_lines=[f"你尚未对公司 {stock['name']} 达成 50% 以上绝对控股席位，无权设置企业口号！"]
            )
            yield event.image_result(card_img)
            return

        clean_slogan = slogan.strip()
        if not clean_slogan:
            card_img = self.renderer.render_card(
                title="【参数缺失】", subtitle="缺少口号内容",
                content_lines=["请输入要强制设置的企业口号！例如：/设置口号 @某人 精神饱满全力打工"]
            )
            yield event.image_result(card_img)
            return

        self.db.update_stock_fields(stock["stock_id"], slogan=clean_slogan)
        card_img = self.renderer.render_card(
            title="【控股母公司股东令】",
            subtitle=f"实控人修改企业Slogan",
            content_lines=[
                f"实际控制人大股东 {user_id} 依法行使恶意收购特权！",
                f"子公司 {stock['name']} 被强制挂牌企业口号：",
                f"“{clean_slogan}”",
                f"此标语已向全群永久公示！"
            ]
        )
        yield event.image_result(card_img)

    # ================= 停牌与复牌机制 =================
    @filter.command("申请停牌")
    async def cmd_suspend(self, event: AstrMessageEvent, reason: str = ""):
        """闭关备考/防沉迷锁仓停牌"""
        user_id = str(event.get_sender_id())
        msg = self.engine.handle_apply_suspend(user_id, reason)
        card_img = self.renderer.render_card(
            title="【重大事项停牌公告】", subtitle="股票停牌锁仓申请", content_lines=[msg]
        )
        yield event.image_result(card_img)

    @filter.command("申请复牌")
    async def cmd_resume(self, event: AstrMessageEvent, statement: str = ""):
        """结束停牌复牌开盘，根据陈述诚意度判定涨跌停"""
        user_id = str(event.get_sender_id())
        if not statement:
            card_img = self.renderer.render_card(
                title="【复牌申请被拒】", subtitle="缺少复牌路演讲话",
                content_lines=["请在申请复牌时附带一段复牌陈述或路演讲话！例如：/申请复牌 我带着全新大项目回来了"]
            )
            yield event.image_result(card_img)
            return

        msg = self.engine.handle_apply_resume(user_id, statement)
        card_img = self.renderer.render_card(
            title="【复牌交易开盘公告】", subtitle="股票恢复场内交易", content_lines=msg.split("\n")
        )
        yield event.image_result(card_img)

    @filter.command("重组路演")
    async def cmd_reorganize(self, event: AstrMessageEvent, pitch: str = ""):
        """破产退市公司提交不少于50字检讨书重新IPO"""
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or "重组创客"
        msg = self.engine.handle_reorganize_ipo(user_id, pitch, user_name)
        card_img = self.renderer.render_card(
            title="【破产重组二次上市审核】", subtitle="发审委复核决议", content_lines=msg.split("\n")
        )
        yield event.image_result(card_img)

    # ================= 自主敲钟上市 =================
    @filter.command("上市申请")
    async def cmd_apply_ipo(self, event: AstrMessageEvent, sector: str = "摸鱼躺平"):
        """群友自主敲钟上市成为股票代码"""
        user_id = str(event.get_sender_id())
        user_name = event.get_sender_name() or f"创客_{user_id[-4:]}"

        if self.db.get_stock(user_id):
            card_img = self.renderer.render_card(
                title="【上市申请驳回】", subtitle="重复申报上市",
                content_lines=["您的核心资产已经在群友股票交易所成功挂牌敲钟！", "发送 /查股价 可查看详情。"]
            )
            yield event.image_result(card_img)
            return

        if sector not in SECTOR_LIST:
            sector = "摸鱼躺平"

        symbol = user_name[:4]
        self.db.create_stock(user_id, user_name, symbol, issue_price=10.0, total_shares=10000, sector=sector)
        card_img = self.renderer.render_card(
            title="【IPO成功敲钟挂牌盛典】",
            subtitle=f"新股上市: {user_name}(${symbol})",
            content_lines=[
                f"热烈祝贺 {user_name} 旗下资产成功在交易所挂牌敲钟！",
                f"• 股票代码: ${symbol} (QQ: {user_id})",
                f"• 发行初始价: 10.00 金币",
                f"• 归属概念板块: 【{sector}板块】",
                f"全群股民现已可通过 /买入 @{user_name} 数量 认购股份！"
            ]
        )
        yield event.image_result(card_img)

    # ================= 黑客道具商城 =================
    @filter.command("道具商城", alias={"股市商城"})
    async def cmd_shop(self, event: AstrMessageEvent):
        """查看黑客做空道具"""
        lines = ["【秘密金融黑客做空工具箱】:"]
        for name, item in ITEM_SHOP.items():
            lines.append(f"• [{name}] 售价:{item['price']} 金币")
            lines.append(f"  功效: {item['desc']}")
        lines.append("")
        lines.append("【指令用法】:")
        lines.append("• 购买: /购买道具 <名称>")
        lines.append("• 使用: /使用道具 <名称> @某人")

        card_img = self.renderer.render_card(
            title="【秘密金融黑客道具商城】",
            subtitle="做空机构专供武器库",
            content_lines=lines,
            footer_tip="合理利用黑客道具 扭转大盘多空格局"
        )
        yield event.image_result(card_img)

    @filter.command("购买道具")
    async def cmd_buy_item(self, event: AstrMessageEvent, item_name: str = ""):
        """使用金币购买金融黑客道具"""
        user_id = str(event.get_sender_id())
        item = ITEM_SHOP.get(item_name.strip())
        if not item:
            card_img = self.renderer.render_card(
                title="【购买失败】", subtitle="道具未上架", content_lines=["货架上没有这个道具！发送 /道具商城 查看在售商品。"]
            )
            yield event.image_result(card_img)
            return

        cost = item["price"]
        user_info = self.db.get_or_create_user(user_id)
        if user_info["balance"] < cost:
            card_img = self.renderer.render_card(
                title="【支付被拒】", subtitle="金币余额不足",
                content_lines=[f"购买【{item_name}】需要 {cost} 金币，你当前只有 {user_info['balance']:.2f}。"]
            )
            yield event.image_result(card_img)
            return

        self.db.update_balance(user_id, -cost)
        self.db.add_item(user_id, item_name.strip(), 1)
        card_img = self.renderer.render_card(
            title="【道具采购成功凭条】",
            subtitle=f"道具: {item_name} · 扣款: {cost}金币",
            content_lines=[f"成功耗资 {cost} 金币购入【{item_name}】！", "该武器已存入你的公文包，可随时使用。"]
        )
        yield event.image_result(card_img)

    @filter.command("使用道具")
    async def cmd_use_item(self, event: AstrMessageEvent, item_name: str = "", raw_target: str = ""):
        """使用金融黑客道具实施做空或熔断"""
        user_id = str(event.get_sender_id())
        item_name = item_name.strip()

        if not self.db.use_item(user_id, item_name):
            card_img = self.renderer.render_card(
                title="【使用失败】", subtitle="背包无此道具",
                content_lines=[f"你的公文包里没有【{item_name}】道具！请先在 /道具商城 购买。"]
            )
            yield event.image_result(card_img)
            return

        if item_name == "内幕消息卡":
            tips = [
                "【绝密内幕偷跑】据传【深夜修仙板块】某大户即将被管理重点盯防，小心遭遇暴雷连坐！",
                "【内幕风声】市场传闻某神秘巨鲸正在大举吸筹，建议盯紧低估值题材股！",
                "【机构调研情报】二次元纯度板块近期热度攀升，预计资金即将抱团炒作！"
            ]
            import random
            card_img = self.renderer.render_card(
                title="【内幕消息已阅即焚】", subtitle="高层加密情报", content_lines=[random.choice(tips)]
            )
            yield event.image_result(card_img)
            return

        target_id = extract_target_id(event, raw_target) or raw_target.strip().lstrip("@")
        stock = self.db.get_stock(target_id)
        if not stock:
            self.db.add_item(user_id, item_name, 1)
            card_img = self.renderer.render_card(
                title="【释放失败】", subtitle="目标公司未上市",
                content_lines=[f"未找到目标公司“{raw_target}”！道具已原路退回公文包。"]
            )
            yield event.image_result(card_img)
            return

        if item_name == "做空报告":
            drop = round(stock["price"] * 0.18, 2)
            self.db.update_stock_price(stock["stock_id"], stock["price"] - drop)
            card_img = self.renderer.render_card(
                title="【做空机构重磅研报】",
                subtitle=f"恶意沽空狙击: {stock['name']}",
                content_lines=[
                    f"针对 {stock['name']}(${stock['symbol']}) 的做空研报已正式发布！",
                    f"直指其核心业务造假严重，股价瞬间闪崩 -18%！",
                    f"最新跌至：{stock['price'] - drop:.2f} 金币！"
                ]
            )
            yield event.image_result(card_img)

        elif item_name == "拔网线卡":
            self.db.update_stock_fields(stock["stock_id"], is_suspended=1, suspend_reason="交易所服务器临时拔网线熔断", suspend_time=time.time())
            card_img = self.renderer.render_card(
                title="【紧急熔断断网警报】",
                subtitle=f"拔网线干预: {stock['name']}",
                content_lines=[
                    f"针对 {stock['name']}(${stock['symbol']}) 实施交易通道拔网线熔断！",
                    f"该股所有场内申购抛售即刻强制冻结，给散户与庄家争取宝贵的避险窗口！"
                ]
            )
            yield event.image_result(card_img)
