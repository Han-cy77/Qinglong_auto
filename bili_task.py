# -*- coding: utf-8 -*-
"""
new Env('B站日常助手');
cron: 15 9 * * *
"""

import requests
import os
import time
import re

# ================= 配置区域 =================
BARK_URL = 'https://api.day.app/在此处添加自己的bark推送id'
TOSS_COIN_COUNT = 1  # 每天自动投币的数量 (0代表不投币，最多可填5。每天投1个最健康)
# ============================================

def get_bili_csrf(cookie):
    # 从 Cookie 中提取 B 站必需的安全校验码 bili_jct (即 csrf token)
    match = re.search(r'bili_jct=([^;]+)', cookie)
    return match.group(1) if match else ""


def do_bili_task():
    cookie = os.getenv('BILI_COOKIE')
    if not cookie:
        return "⚠️ B站任务失败：未在青龙环境变量中找到 BILI_COOKIE"

    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/"
    }

    csrf = get_bili_csrf(cookie)
    if not csrf:
        return "❌ Cookie格式错误：缺少 bili_jct 字段，请重新抓取完整的 Cookie。"

    msg_list = []

    try:
        # 1. 检查登录状态并获取用户信息
        nav_url = "https://api.bilibili.com/x/web-interface/nav"
        res = requests.get(nav_url, headers=headers).json()
        if res.get('code') != 0:
            return f"❌ 登录失效，请重新抓取 B 站 Cookie！"

        data = res['data']
        uname = data['uname']
        level = data['level_info']['current_level']
        coins = data['money']
        msg_list.append(f"👤 账号：{uname} (Lv{level})")
        msg_list.append(f"💰 硬币余额：{coins}枚")

        # 2. 获取今日任务完成状态
        reward_url = "https://api.bilibili.com/x/member/web/exp/reward"
        reward_res = requests.get(reward_url, headers=headers).json()
        reward_data = reward_res.get('data', {})
        watch_exp = reward_data.get('watch')  # 是否完成观看
        share_exp = reward_data.get('share')  # 是否完成分享
        coin_exp = reward_data.get('coins', 0)  # 今日已投币获得的经验

        # 3. 获取全站排行榜第一的视频，用来执行观看和分享任务
        rank_url = "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all"
        rank_res = requests.get(rank_url, headers=headers).json()
        bvid = rank_res['data']['list'][0]['bvid']
        video_title = rank_res['data']['list'][0]['title']
        msg_list.append(f"🎯 今日锁定视频：《{video_title[:10]}...》")

        # 4. 模拟观看视频
        if not watch_exp:
            watch_url = "https://api.bilibili.com/x/click-interface/web/heartbeat"
            requests.post(watch_url, data={"bvid": bvid, "csrf": csrf, "played_time": 30}, headers=headers)
            msg_list.append("📺 观看任务：✅ 已完成 (+5经验)")
        else:
            msg_list.append("📺 观看任务：☕ 今日已达标")

        # 5. 模拟分享视频
        if not share_exp:
            share_url = "https://api.bilibili.com/x/web-interface/share/add"
            requests.post(share_url, data={"bvid": bvid, "csrf": csrf}, headers=headers)
            msg_list.append("↗️ 分享任务：✅ 已完成 (+5经验)")
        else:
            msg_list.append("↗️ 分享任务：☕ 今日已达标")

        # 6. 执行自动投币
        target_coins_exp = TOSS_COIN_COUNT * 10
        if TOSS_COIN_COUNT > 0:
            if coin_exp < target_coins_exp and coins > 0:
                coin_url = "https://api.bilibili.com/x/web-interface/coin/add"
                coin_data = {"bvid": bvid, "multiply": TOSS_COIN_COUNT, "select_like": 1, "cross_domain": "true",
                             "csrf": csrf}
                c_res = requests.post(coin_url, data=coin_data, headers=headers).json()
                if c_res.get('code') == 0:
                    msg_list.append(f"🪙 投币任务：✅ 成功投出{TOSS_COIN_COUNT}枚硬币 (+{TOSS_COIN_COUNT * 10}经验)")
                else:
                    msg_list.append(f"🪙 投币任务：❌ 失败 ({c_res.get('message')})")
            elif coin_exp >= target_coins_exp:
                msg_list.append(f"🪙 投币任务：☕ 今日投币量已达标")
            else:
                msg_list.append(f"🪙 投币任务：⚠️ 硬币余额不足")
        else:
            msg_list.append("🪙 投币任务：设置了不投币")

    except Exception as e:
        msg_list.append(f"❌ 执行任务时发生错误: {e}")

    return "\n".join(msg_list)


def send_bark(title, content):
    if not BARK_URL: return
    base_url = BARK_URL.rstrip('/')
    data = {
        "title": title,
        "body": content,
        "group": "B站日常助手",
        "icon": "https://cdn-icons-png.flaticon.com/512/3178/3178168.png"  # 换了个电视/视频小图标
    }
    try:
        requests.post(base_url, json=data)
    except:
        pass


if __name__ == '__main__':
    print(f"开始执行 B 站日常任务... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    result_msg = do_bili_task()
    print("=" * 30)
    print(result_msg)
    print("=" * 30)

    send_bark("📺 B站自动签到与升级", result_msg)
    print("任务执行完毕。")