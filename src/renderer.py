import json
import os
import random
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger
from astrbot.api.star import StarTools


FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


class CardRenderer:
    def __init__(self):
        try:
            self.data_dir = StarTools.get_data_dir("astrbot_plugin_qun_stock")
        except Exception:
            self.data_dir = Path("data/plugin_data/astrbot_plugin_qun_stock")
        self.cache_dir = self.data_dir / "pixiv_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.meta_file = self.cache_dir / "meta.json"

    def _get_pixiv_image(self) -> Tuple[Optional[Image.Image], str]:
        """获取一张最高质量的 P 站插画及信息，支持本地缓存轮换与异步补充"""
        cached_files = list(self.cache_dir.glob("*.jpg")) + list(self.cache_dir.glob("*.png"))

        # 如果缓存较少，尝试后台抓取一张
        if len(cached_files) < 8:
            self._fetch_remote_pixiv()
            cached_files = list(self.cache_dir.glob("*.jpg")) + list(self.cache_dir.glob("*.png"))

        if cached_files:
            chosen = random.choice(cached_files)
            try:
                im = Image.open(chosen).convert("RGBA")
                # 读取 metadata
                credit = f"Pixiv ID: {chosen.stem.split('_')[0]}"
                return im, credit
            except Exception as e:
                logger.debug(f"[QunStock] 加载本地缓存图片失败: {e}")

        # 若暂无缓存，先生成兜底背景
        return None, "Pixiv High Quality Illustration"

    def _fetch_remote_pixiv(self):
        """调用 lolicon API 获取 P 站超高质量原画"""
        try:
            env = os.environ.copy()
            env["LD_PRELOAD"] = "/opt/bypass-vpn/libbypassvpn.so"
            env["BYPASS_IFACE"] = "tun0"

            cmd_api = ["curl", "-s", "-m", "5", "https://api.lolicon.app/setu/v2?r18=0&size=regular"]
            res = subprocess.run(cmd_api, capture_output=True, text=True, env=env)
            data = json.loads(res.stdout)
            illust = data.get("data", [])[0]
            pid = illust.get("pid")
            url = illust.get("urls", {}).get("regular") or illust.get("urls", {}).get("original")

            if url and pid:
                out_path = self.cache_dir / f"{pid}_{int(time.time())}.jpg"
                cmd_dl = [
                    "curl", "-s", "-m", "10",
                    "-H", "Referer: https://www.pixiv.net/",
                    "-o", str(out_path),
                    url
                ]
                subprocess.run(cmd_dl, capture_output=True, env=env)
                logger.info(f"[QunStock] 成功缓存一张 Pixiv 高清壁纸: {illust.get('title')}")
        except Exception as e:
            logger.debug(f"[QunStock] 抓取 Pixiv 插画异常: {e}")

    def render_card(self, title: str, subtitle: str, content_lines: List[str], footer_tip: str = "") -> str:
        """动态生成包含 P 站最高画质插画的赛博股市看板长图"""
        width = 900
        padding = 35

        # 字体初始化
        font_title = ImageFont.truetype(FONT_BOLD, 36)
        font_sub = ImageFont.truetype(FONT_REGULAR, 20)
        font_text = ImageFont.truetype(FONT_REGULAR, 22)
        font_bold_text = ImageFont.truetype(FONT_BOLD, 22)
        font_tag = ImageFont.truetype(FONT_BOLD, 16)
        font_credit = ImageFont.truetype(FONT_REGULAR, 15)

        # 准备 P 站高清图
        pixiv_img, pixiv_credit = self._get_pixiv_image()
        banner_h = 360  # 顶部 P 站插画展示区高度

        # 计算文字区高度
        line_height = 36
        content_h = max(240, len(content_lines) * line_height + 80)
        footer_h = 60
        total_h = banner_h + content_h + footer_h

        # 创建基础画布 (深色科技质感背景)
        card = Image.new("RGBA", (width, total_h), (20, 24, 33, 255))
        draw = ImageDraw.Draw(card)

        # 1. 绘制顶部 P 站插画横幅
        if pixiv_img:
            # 保持比例缩放并居中裁剪
            scale = max(width / pixiv_img.width, banner_h / pixiv_img.height)
            nw, nh = int(pixiv_img.width * scale), int(pixiv_img.height * scale)
            resized = pixiv_img.resize((nw, nh), Image.Resampling.LANCZOS)
            x_crop = (nw - width) // 2
            y_crop = (nh - banner_h) // 2
            cropped = resized.crop((x_crop, y_crop, x_crop + width, y_crop + banner_h))
            card.paste(cropped, (0, 0))

            # 加上漂亮的底部渐变遮罩，使图片与下方深色卡片完美融合
            mask = Image.new("L", (width, banner_h), 0)
            for y in range(banner_h):
                alpha = int(255 * (y / banner_h) ** 1.8)
                ImageDraw.Draw(mask).line([(0, y), (width, y)], fill=alpha)
            gradient_layer = Image.new("RGBA", (width, banner_h), (20, 24, 33, 255))
            card.paste(gradient_layer, (0, 0), mask)
        else:
            # 渐变底色备用
            for y in range(banner_h):
                color = (30 + int(y * 0.05), 35 + int(y * 0.05), 55 + int(y * 0.08), 255)
                draw.line([(0, y), (width, y)], fill=color)

        # 2. 标题区
        draw.text((padding, banner_h - 100), title, font=font_title, fill=(255, 255, 255, 255))
        if subtitle:
            draw.text((padding, banner_h - 50), subtitle, font=font_sub, fill=(160, 185, 230, 240))

        # P 站插画角标
        draw.text((width - padding - 280, banner_h - 40), f"🎨 {pixiv_credit}", font=font_credit, fill=(200, 210, 235, 180))

        # 3. 磨砂卡片主体
        card_top = banner_h + 15
        card_bottom = banner_h + content_h - 15
        draw.rounded_rectangle(
            [(padding, card_top), (width - padding, card_bottom)],
            radius=16,
            fill=(28, 34, 48, 240),
            outline=(55, 68, 95, 255),
            width=2
        )

        # 4. 逐行绘制文字内容
        cur_y = card_top + 25
        for line in content_lines:
            text = line.strip()
            if not text:
                cur_y += 15
                continue

            # 区分不同类型的配色 (涨红跌绿科技风)
            fill_color = (235, 240, 255, 255)
            font_to_use = font_text

            if text.startswith("【"):
                fill_color = (255, 215, 110, 255)  # 金色高亮小标题
                font_to_use = font_bold_text
            elif "+ " in text or "(+" in text or "暴涨" in text or "涨停" in text or "利好" in text:
                fill_color = (255, 95, 95, 255)    # 飘红
            elif "- " in text or "(-" in text or "跌停" in text or "闪崩" in text or "暴跌" in text or "造假" in text:
                fill_color = (65, 220, 140, 255)   # 绿跌
            elif "*ST" in text or "警告" in text or "熔断" in text or "退市" in text:
                fill_color = (255, 130, 60, 255)   # 橙色警示
            elif text.startswith("  ") or text.startswith("•"):
                fill_color = (210, 225, 245, 255)

            draw.text((padding + 25, cur_y), text, font=font_to_use, fill=fill_color)
            cur_y += line_height

        # 5. 底部页脚
        tip_text = footer_tip or "Cyber Stock Market · 赛博交易所 · 市场有风险 投资需谨慎"
        draw.text((padding, total_h - 38), tip_text, font=font_credit, fill=(120, 135, 165, 255))
        draw.text((width - padding - 180, total_h - 38), "Powered by Yupomii", font=font_credit, fill=(100, 120, 150, 255))

        # 保存图片
        out_file = self.data_dir / f"stock_render_{int(time.time() * 1000)}.png"
        card.save(str(out_file), "PNG")
        return str(out_file)
