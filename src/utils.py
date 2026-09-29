import re
from typing import Optional, Tuple
from astrbot.api.event import AstrMessageEvent
import astrbot.api.message_components as Comp


def extract_target_id(event: AstrMessageEvent, arg: str = "") -> Optional[str]:
    """从消息事件的艾特组件或参数中提取目标QQ号/股票ID"""
    # 1. 优先从消息组件中查找 At 组件
    for comp in getattr(event.message_obj, "message", []):
        if isinstance(comp, Comp.At):
            target_qq = str(getattr(comp, "qq", "") or "")
            if target_qq and target_qq != str(event.get_self_id()):
                return target_qq

    # 2. 检查消息中的 CQ 码 [CQ:at,qq=123456]
    raw_str = str(getattr(event, "message_str", "") or "")
    cq_match = re.search(r"\[CQ:at,qq=(\d+)\]", raw_str)
    if cq_match:
        return cq_match.group(1)

    # 3. 检查参数文本中是否有带括号的QQ号，例如 @雨落喵~(2201462529) 或 @123456
    paren_match = re.search(r"\((\d{5,12})\)", arg)
    if paren_match:
        return paren_match.group(1)

    # 4. 如果参数本身就是纯数字 QQ号
    clean_arg = arg.strip().lstrip("@")
    if clean_arg.isdigit() and len(clean_arg) >= 5:
        return clean_arg

    return None


def parse_trade_args(event: AstrMessageEvent, raw_target: str, raw_shares: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """解析交易参数，返回 (target_id_or_name, shares, error_msg)"""
    # 提取目标
    target_id = extract_target_id(event, raw_target)
    target_key = target_id if target_id else raw_target.strip().lstrip("@")

    if not target_key:
        return None, None, "未指定交易目标股票！支持 @某人、输入QQ号或输入股票代码。"

    try:
        shares = int(raw_shares.strip())
        if shares <= 0:
            return None, None, "交易股数必须是大于 0 的整数！"
    except Exception:
        return None, None, f"股数格式不正确：“{raw_shares}”，请输入正整数！"

    return target_key, shares, None
