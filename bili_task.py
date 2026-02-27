# -*- coding: utf-8 -*-
"""
new Env('B站日常助手');
cron: 15 9 * * *
"""

import requests
import os
import time
import re
import random

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

    # 修复1：补全了 Origin 头，这对 B 站的 POST 请求(观看/分享/投币)非常重要，防止报 -403
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com"
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

        # 3. 获取 B 站全站热门推荐视频，避免重复投币
        popular_url = "https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1"
        pop_res = requests.get(popular_url, headers=headers).json()

        if pop_res.get('code') == 0:
            video_list = pop_res['data']['list']
            random_video = random.choice(video_list)
            bvid = random_video['bvid']
            aid = random_video['aid']  # 修复2：同时获取 aid，有些老接口强依赖 aid
            video_title = random_video['title']
        else:
            return f"❌ 获取推荐视频失败: {pop_res.get('message')}"

        msg_list.append(f"🎯 今日随机推荐视频：《{video_title[:10]}...》")

        # 4. 模拟观看视频
        if not watch_exp:
            watch_url = "https://api.bilibili.com/x/click-interface/web/heartbeat"
            watch_data = {"aid": aid, "bvid": bvid, "csrf": csrf, "played_time": 300}
            # 修复3：增加对返回结果的验证
            w_res = requests.post(watch_url, data=watch_data, headers=headers).json()
            if w_res.get('code') == 0:
                msg_list.append("📺 观看任务：✅ 已完成 (+5经验)")
            else:
                msg_list.append(f"📺 观看任务：❌ 失败 ({w_res.get('message')})")
            time.sleep(2)  # 修复4：增加安全延迟，防风控
        else:
            msg_list.append("📺 观看任务：☕ 今日已达标")

        # 5. 模拟分享视频
        if not share_exp:
            share_url = "https://api.bilibili.com/x/web-interface/share/add"
            # 修复5：增加了 aid 和 share_channel 参数，模拟真实“复制链接”的分享动作
            share_data = {
                "aid": aid,
                "bvid": bvid,
                "csrf": csrf,
                "share_channel": "copy"
            }
            s_res = requests.post(share_url, data=share_data, headers=headers).json()
            if s_res.get('code') == 0:
                msg_list.append("↗️ 分享任务：✅ 已完成 (+5经验)")
            else:
                msg_list.append(f"↗️ 分享任务：❌ 失败 (code:{s_res.get('code')} - {s_res.get('message')})")
            time.sleep(2) # 安全延迟
        else:
            msg_list.append("↗️ 分享任务：☕ 今日已达标")

        # 6. 执行自动投币
        target_coins_exp = TOSS_COIN_COUNT * 10
        if TOSS_COIN_COUNT > 0:
            if coin_exp < target_coins_exp and coins > 0:
                coin_url = "https://api.bilibili.com/x/web-interface/coin/add"
                coin_data = {
                    "aid": aid,
                    "bvid": bvid,
                    "multiply": TOSS_COIN_COUNT,
                    "select_like": 1,
                    "cross_domain": "true",
                    "csrf": csrf
                }
                c_res = requests.post(coin_url, data=coin_data, headers=headers).json()
                if c_res.get('code') == 0:
                    msg_list.append(f"🪙 投币任务：✅ 成功投出{TOSS_COIN_COUNT}枚硬币 (+{TOSS_COIN_COUNT * 10}经验)")
                else:
                    msg_list.append(f"🪙 投币任务：❌ 失败 (code:{c_res.get('code')} - {c_res.get('message')})")
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
        "icon": "https://cdn-icons-png.flaticon.com/512/3178/3178168.png"
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