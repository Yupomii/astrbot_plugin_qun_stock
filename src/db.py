import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from astrbot.api import logger
from astrbot.api.star import StarTools


class StockDB:
    def __init__(self):
        try:
            self.data_dir = StarTools.get_data_dir("astrbot_plugin_qun_stock")
        except Exception:
            self.data_dir = Path("data/plugin_data/astrbot_plugin_qun_stock")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "qun_stock.db"
        self._init_tables()

    def get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            # 1. 股票表
            c.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                stock_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                issue_price REAL NOT NULL,
                total_shares INTEGER NOT NULL,
                circulating_shares INTEGER NOT NULL,
                sector TEXT NOT NULL DEFAULT '摸鱼躺平',
                is_st INTEGER NOT NULL DEFAULT 0,
                is_delisted INTEGER NOT NULL DEFAULT 0,
                is_suspended INTEGER NOT NULL DEFAULT 0,
                suspend_reason TEXT DEFAULT '',
                suspend_time REAL DEFAULT 0,
                slogan TEXT DEFAULT '',
                controller_id TEXT DEFAULT '',
                last_active_time REAL NOT NULL,
                freeze_until REAL DEFAULT 0
            )
            """)

            # 2. 股民钱包表
            c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 10000.0,
                last_sign_time REAL DEFAULT 0
            )
            """)

            # 3. 持仓表 (支持做多与做空)
            c.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                user_id TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                shares INTEGER NOT NULL DEFAULT 0,
                cost_price REAL NOT NULL DEFAULT 0.0,
                short_shares INTEGER NOT NULL DEFAULT 0,
                short_price REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (user_id, stock_id)
            )
            """)

            # 4. 道具背包表
            c.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_id)
            )
            """)

            # 5. 市场小道消息/黑天鹅预测缓存表
            c.execute("""
            CREATE TABLE IF NOT EXISTS market_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """)
            conn.commit()

    # ---------------- 股民账户操作 ----------------
    def get_or_create_user(self, user_id: str, initial_cash: float = 10000.0) -> dict:
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            if not row:
                c.execute("INSERT INTO users (user_id, balance, last_sign_time) VALUES (?, ?, 0)", (user_id, initial_cash))
                conn.commit()
                return {"user_id": user_id, "balance": initial_cash, "last_sign_time": 0}
            return dict(row)

    def update_balance(self, user_id: str, delta: float) -> float:
        with self.get_conn() as conn:
            c = conn.cursor()
            self.get_or_create_user(user_id)
            c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            new_bal = c.fetchone()["balance"]
            conn.commit()
            return new_bal

    def record_sign_in(self, user_id: str, amount: float) -> Tuple[bool, float]:
        """签到打工发放薪水，返回 (是否成功, 当前余额)"""
        with self.get_conn() as conn:
            c = conn.cursor()
            user = self.get_or_create_user(user_id)
            now = time.time()
            last_time = user.get("last_sign_time", 0)

            # 判断是否同一自然日
            if time.strftime("%Y-%m-%d", time.localtime(last_time)) == time.strftime("%Y-%m-%d", time.localtime(now)):
                return False, user["balance"]

            c.execute("UPDATE users SET balance = balance + ?, last_sign_time = ? WHERE user_id = ?", (amount, now, user_id))
            conn.commit()
            return True, user["balance"] + amount

    # ---------------- 股票与上市操作 ----------------
    def get_stock(self, stock_key: str) -> Optional[dict]:
        """通过 stock_id (QQ号) 或 股票名称/代号 查询"""
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("""
            SELECT * FROM stocks 
            WHERE stock_id = ? OR name = ? OR symbol = ? OR ('$' || symbol) = ?
            LIMIT 1
            """, (stock_key, stock_key, stock_key, stock_key))
            row = c.fetchone()
            return dict(row) if row else None

    def create_stock(self, stock_id: str, name: str, symbol: str, issue_price: float = 10.0, total_shares: int = 10000, sector: str = "摸鱼躺平") -> dict:
        now = time.time()
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("""
            INSERT OR REPLACE INTO stocks 
            (stock_id, name, symbol, price, issue_price, total_shares, circulating_shares, sector, is_st, is_delisted, is_suspended, last_active_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?)
            """, (stock_id, name, symbol, issue_price, issue_price, total_shares, total_shares, sector, now))
            conn.commit()
            return self.get_stock(stock_id)

    def list_all_stocks(self, include_delisted: bool = False) -> List[dict]:
        with self.get_conn() as conn:
            c = conn.cursor()
            if include_delisted:
                c.execute("SELECT * FROM stocks ORDER BY price DESC")
            else:
                c.execute("SELECT * FROM stocks WHERE is_delisted = 0 ORDER BY price DESC")
            return [dict(r) for r in c.fetchall()]

    def list_stocks_by_sector(self, sector: str) -> List[dict]:
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM stocks WHERE sector = ? AND is_delisted = 0 ORDER BY price DESC", (sector,))
            return [dict(r) for r in c.fetchall()]

    def update_stock_price(self, stock_id: str, new_price: float):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("UPDATE stocks SET price = ? WHERE stock_id = ?", (max(0.01, round(new_price, 2)), stock_id))
            conn.commit()

    def update_stock_fields(self, stock_id: str, **kwargs):
        if not kwargs:
            return
        fields = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [stock_id]
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute(f"UPDATE stocks SET {fields} WHERE stock_id = ?", values)
            conn.commit()

    # ---------------- 持仓与交易操作 ----------------
    def get_position(self, user_id: str, stock_id: str) -> dict:
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM positions WHERE user_id = ? AND stock_id = ?", (user_id, stock_id))
            row = c.fetchone()
            if not row:
                return {"user_id": user_id, "stock_id": stock_id, "shares": 0, "cost_price": 0.0, "short_shares": 0, "short_price": 0.0}
            return dict(row)

    def list_user_positions(self, user_id: str) -> List[dict]:
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("""
            SELECT p.*, s.name, s.symbol, s.price as current_price, s.is_st, s.is_suspended, s.sector, s.controller_id, s.slogan
            FROM positions p
            JOIN stocks s ON p.stock_id = s.stock_id
            WHERE p.user_id = ? AND (p.shares > 0 OR p.short_shares > 0)
            """, (user_id,))
            return [dict(r) for r in c.fetchall()]

    def get_stock_holders(self, stock_id: str) -> List[dict]:
        """获取某只股票的多头持仓排名"""
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM positions WHERE stock_id = ? AND shares > 0 ORDER BY shares DESC", (stock_id,))
            return [dict(r) for r in c.fetchall()]

    def execute_buy(self, user_id: str, stock_id: str, shares: int, trade_price: float, tax_rate: float) -> Tuple[bool, str]:
        total_cost = shares * trade_price
        tax = round(total_cost * tax_rate, 2)
        grand_total = total_cost + tax

        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            u_row = c.fetchone()
            if not u_row or u_row["balance"] < grand_total:
                return False, f"资金不足！需要 {grand_total:.2f} 金币（含税 {tax:.2f}），当前余额仅 {u_row['balance'] if u_row else 0:.2f}。"

            # 扣款
            c.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (grand_total, user_id))

            # 更新持仓
            c.execute("SELECT * FROM positions WHERE user_id = ? AND stock_id = ?", (user_id, stock_id))
            pos = c.fetchone()
            if not pos:
                c.execute("INSERT INTO positions (user_id, stock_id, shares, cost_price) VALUES (?, ?, ?, ?)",
                          (user_id, stock_id, shares, trade_price))
            else:
                old_shares = pos["shares"]
                old_cost = pos["cost_price"]
                new_shares = old_shares + shares
                new_cost = round(((old_shares * old_cost) + total_cost) / new_shares, 2)
                c.execute("UPDATE positions SET shares = ?, cost_price = ? WHERE user_id = ? AND stock_id = ?",
                          (new_shares, new_cost, user_id, stock_id))

            # 稍微推动股价上涨 (交易拉盘)
            c.execute("UPDATE stocks SET price = ROUND(price * (1 + 0.001 * ?), 2) WHERE stock_id = ?", (min(50, shares), stock_id))

            conn.commit()

        # 检查是否达成恶意收购控股 (持股超过50%)
        self.check_takeover(stock_id)
        return True, f"成交！以单价 {trade_price:.2f} 买入 {shares} 股，扣除佣金印花税 {tax:.2f}，总支出 {grand_total:.2f} 金币。"

    def execute_sell(self, user_id: str, stock_id: str, shares: int, trade_price: float, tax_rate: float) -> Tuple[bool, str]:
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM positions WHERE user_id = ? AND stock_id = ?", (user_id, stock_id))
            pos = c.fetchone()
            if not pos or pos["shares"] < shares:
                return False, f"持仓不足！当前仅持有 {pos['shares'] if pos else 0} 股，无法卖出 {shares} 股。"

            gross = shares * trade_price
            tax = round(gross * tax_rate, 2)
            net_income = gross - tax

            new_shares = pos["shares"] - shares
            if new_shares == 0:
                c.execute("UPDATE positions SET shares = 0, cost_price = 0 WHERE user_id = ? AND stock_id = ?", (user_id, stock_id))
            else:
                c.execute("UPDATE positions SET shares = ? WHERE user_id = ? AND stock_id = ?", (new_shares, user_id, stock_id))

            c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net_income, user_id))

            # 稍微打压股价 (砸盘)
            c.execute("UPDATE stocks SET price = MAX(0.01, ROUND(price * (1 - 0.001 * ?), 2)) WHERE stock_id = ?", (min(50, shares), stock_id))

            conn.commit()

        self.check_takeover(stock_id)
        return True, f"抛售成功！以单价 {trade_price:.2f} 卖出 {shares} 股，扣税 {tax:.2f}，净回笼资金 {net_income:.2f} 金币。"

    def execute_short(self, user_id: str, stock_id: str, shares: int, trade_price: float) -> Tuple[bool, str]:
        """做空借券沽空机制：需要冻结 100% 保证金"""
        margin_required = shares * trade_price
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            u = c.fetchone()
            if not u or u["balance"] < margin_required:
                return False, f"保证金不足！做空 {shares} 股需要保证金 {margin_required:.2f} 金币，当前余额仅 {u['balance'] if u else 0:.2f}。"

            c.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (margin_required, user_id))
            c.execute("SELECT * FROM positions WHERE user_id = ? AND stock_id = ?", (user_id, stock_id))
            pos = c.fetchone()
            if not pos:
                c.execute("INSERT INTO positions (user_id, stock_id, short_shares, short_price) VALUES (?, ?, ?, ?)",
                          (user_id, stock_id, shares, trade_price))
            else:
                old_short = pos["short_shares"]
                old_price = pos["short_price"]
                new_short = old_short + shares
                new_price = round(((old_short * old_price) + (shares * trade_price)) / new_short, 2)
                c.execute("UPDATE positions SET short_shares = ?, short_price = ? WHERE user_id = ? AND stock_id = ?",
                          (new_short, new_price, user_id, stock_id))

            # 做空砸盘
            c.execute("UPDATE stocks SET price = MAX(0.01, ROUND(price * (1 - 0.0015 * ?), 2)) WHERE stock_id = ?", (min(50, shares), stock_id))
            conn.commit()

        return True, f"做空建仓成功！以 {trade_price:.2f} 借券沽空 {shares} 股，冻结保证金 {margin_required:.2f} 金币。股价下跌时买券还仓即可获利！"

    def check_takeover(self, stock_id: str):
        """检查是否有股东持股比例超过50%达成绝对控股"""
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT total_shares, controller_id FROM stocks WHERE stock_id = ?", (stock_id,))
            stock_info = c.fetchone()
            if not stock_info:
                return
            total = stock_info["total_shares"]
            old_ctrl = stock_info["controller_id"]

            c.execute("SELECT user_id, shares FROM positions WHERE stock_id = ? ORDER BY shares DESC LIMIT 1", (stock_id,))
            top_holder = c.fetchone()
            new_ctrl = ""
            if top_holder and (top_holder["shares"] / total) >= 0.50:
                new_ctrl = top_holder["user_id"]

            if new_ctrl != old_ctrl:
                c.execute("UPDATE stocks SET controller_id = ? WHERE stock_id = ?", (new_ctrl, stock_id))
                conn.commit()

    # ---------------- 道具背包操作 ----------------
    def get_user_items(self, user_id: str) -> Dict[str, int]:
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT item_id, count FROM inventory WHERE user_id = ? AND count > 0", (user_id,))
            return {r["item_id"]: r["count"] for r in c.fetchall()}

    def add_item(self, user_id: str, item_id: str, count: int = 1):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("""
            INSERT INTO inventory (user_id, item_id, count) VALUES (?, ?, ?)
            ON CONFLICT(user_id, item_id) DO UPDATE SET count = count + ?
            """, (user_id, item_id, count, count))
            conn.commit()

    def use_item(self, user_id: str, item_id: str) -> bool:
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT count FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id))
            row = c.fetchone()
            if not row or row["count"] <= 0:
                return False
            c.execute("UPDATE inventory SET count = count - 1 WHERE user_id = ? AND item_id = ?", (user_id, item_id))
            conn.commit()
            return True
