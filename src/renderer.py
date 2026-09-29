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

    def _get_pixiv_image(self) -> Tuple[Optional[Image.Image], str]:
        """获取一张最高画质的 P 站插画及元信息，支持本地缓存轮换"""
        cached_files = list(self.cache_dir.glob("*.jpg")) + list(self.cache_dir.glob("*.png"))

        if len(cached_files) < 6:
            self._fetch_remote_pixiv()
            cached_files = list(self.cache_dir.glob("*.jpg")) + list(self.cache_dir.glob("*.png"))

        if cached_files:
            chosen = random.choice(cached_files)
            try:
                im = Image.open(chosen).convert("RGBA")
                pid = chosen.stem.split("_")[0]
                credit = f"Pixiv ID: {pid}"
                return im, credit
            except Exception as e:
                logger.debug(f"[QunStock] 加载本地缓存图片异常: {e}")

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
                logger.info(f"[QunStock] 成功缓存一张 Pixiv 高清插画: {illust.get('title')}")
        except Exception as e:
            logger.debug(f"[QunStock] 抓取 Pixiv 插画异常: {e}")

    def render_card(self, title: str, subtitle: str, content_lines: List[str], footer_tip: str = "") -> str:
        """全画幅完整展示 P 站最高画质插画的赛博股市卡片，绝不切头截断！"""
        target_w = 900
        padding = 35

        # 字体初始化
        font_title = ImageFont.truetype(FONT_BOLD, 34)
        font_sub = ImageFont.truetype(FONT_REGULAR, 20)
        font_bold_text = ImageFont.truetype(FONT_BOLD, 22)
        font_text = ImageFont.truetype(FONT_REGULAR, 21)
        font_credit = ImageFont.truetype(FONT_REGULAR, 15)

        # 获取完整原图
        pixiv_img, pixiv_credit = self._get_pixiv_image()

        if pixiv_img:
            # 完整等比缩放至宽度 900，从顶部 0 开始完整呈现头部和上半身！
            scale = target_w / pixiv_img.width
            target_h = int(pixiv_img.height * scale)
            bg_image = pixiv_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        else:
            target_h = 1000
            bg_image = Image.new("RGBA", (target_w, target_h), (20, 24, 33, 255))

        # 计算文字内容高度
        line_height = 36
        panel_h = max(240, len(content_lines) * line_height + 70)
        footer_h = 55

        # 确定上半身展示区和文字面板位置
        # 头部预留区至少为原图高度的 38%，确保人物头部和五官完完整整露出来
        face_safe_h = max(380, int(target_h * 0.40))
        total_canvas_h = max(target_h, face_safe_h + panel_h + footer_h + 30)

        # 创建底图画布
        canvas = Image.new("RGBA", (target_w, total_canvas_h), (16, 20, 28, 255))
        # 从 (0, 0) 顶部贴入，绝不裁切头部！
        canvas.paste(bg_image, (0, 0))

        # 从人物颈部/腰部以下开始施加自然柔和的半透明暗色渐变，让股市文字极度清晰
        fade_start = int(face_safe_h * 0.70)
        mask = Image.new("RGBA", (target_w, total_canvas_h), (0, 0, 0, 0))
        mask_draw = ImageDraw.Draw(mask)

        for y in range(fade_start, total_canvas_h):
            progress = min(1.0, (y - fade_start) / max(1, (target_h - fade_start)))
            alpha = int(245 * (progress ** 1.3))
            mask_draw.line([(0, y), (target_w, y)], fill=(16, 20, 28, alpha))

        canvas = Image.alpha_composite(canvas, mask)
        draw = ImageDraw.Draw(canvas)

        # 绘制主标题与副标题 (位于人物安全区下方，带有高对比度底色阴影)
        title_y = face_safe_h - 60
        draw.text((padding + 1, title_y + 1), title, font=font_title, fill=(0, 0, 0, 220))
        draw.text((padding, title_y), title, font=font_title, fill=(255, 255, 255, 255))
        if subtitle:
            draw.text((padding + 1, title_y + 45), subtitle, font=font_sub, fill=(0, 0, 0, 200))
            draw.text((padding, title_y + 44), subtitle, font=font_sub, fill=(175, 195, 235, 255))

        # P 站作者角标
        draw.text((target_w - padding - 260, title_y + 45), f"🎨 {pixiv_credit}", font=font_credit, fill=(210, 220, 245, 220))

        # 绘制磨砂玻璃文字卡片
        panel_top = face_safe_h + 10
        panel_bottom = panel_top + panel_h
        draw.rounded_rectangle(
            [(padding, panel_top), (target_w - padding, panel_bottom)],
            radius=16,
            fill=(24, 30, 44, 235),
            outline=(60, 75, 105, 255),
            width=2
        )

        # 逐行排版
        cur_y = panel_top + 25
        for line in content_lines:
            text = line.strip()
            if not text:
                cur_y += 12
                continue

            fill_color = (235, 240, 255, 255)
            font_to_use = font_text

            if text.startswith("【"):
                fill_color = (255, 215, 110, 255)
                font_to_use = font_bold_text
            elif "+ " in text or "(+" in text or "暴涨" in text or "涨停" in text or "利好" in text:
                fill_color = (255, 95, 95, 255)
            elif "- " in text or "(-" in text or "跌停" in text or "闪崩" in text or "暴跌" in text or "造假" in text:
                fill_color = (65, 220, 140, 255)
            elif "*ST" in text or "警告" in text or "熔断" in text or "退市" in text:
                fill_color = (255, 135, 60, 255)
            elif text.startswith("•") or text.startswith("  "):
                fill_color = (210, 225, 245, 255)

            draw.text((padding + 25, cur_y), text, font=font_to_use, fill=fill_color)
            cur_y += line_height

        # 底部页脚
        tip_text = footer_tip or "Cyber Stock Market · 赛博交易所 · 市场有风险 投资需谨慎"
        draw.text((padding, total_canvas_h - 35), tip_text, font=font_credit, fill=(130, 145, 175, 255))
        draw.text((target_w - padding - 180, total_canvas_h - 35), "Powered by Yupomii", font=font_credit, fill=(110, 130, 160, 255))

        out_file = self.data_dir / f"stock_render_{int(time.time() * 1000)}.png"
        canvas.save(str(out_file), "PNG")
        return str(out_file)
