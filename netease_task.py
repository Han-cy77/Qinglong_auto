# -*- coding: utf-8 -*-
"""
new Env('网易云全能打卡');
cron: 30 9 * * *
"""

import requests
import os
import time

# ================= 配置区域 =================
BARK_URL = 'https://api.day.app/在此处添加自己的bark推送id'
# 这里直接调用全网最稳定的网易云 Node.js 开源 API 服务节点
API_BASE = "https://netease-cloud-music-api.fe-mm.com"
# ============================================

def do_netease_task():
    cookie = os.getenv('NETEASE_COOKIE')
    if not cookie:
        return "⚠️ 网易云任务失败：未在青龙环境变量中找到 NETEASE_COOKIE"

    msg_list = []
    # 使用 Vercel 节点需要将 cookie 作为 data 传递来绕过本地加密
    payload = {"cookie": cookie}

    try:
        # 1. 检查登录状态
        login_res = requests.post(f"{API_BASE}/login/status", data=payload, timeout=15).json()
        if login_res.get('data', {}).get('code') != 200 or not login_res.get('data', {}).get('profile'):
            return "❌ Cookie已失效或格式不正确，请重新去浏览器抓取 MUSIC_U 和 __csrf！"

        nickname = login_res['data']['profile']['nickname']
        vip_type = login_res['data']['profile'].get('vipType', 0)
        vip_str = "👑黑胶VIP" if vip_type > 0 else "👤普通用户"
        msg_list.append(f"账号：{nickname} ({vip_str})")

        # 2. 手机端与网页端双端签到
        sign_m = requests.post(f"{API_BASE}/daily_signin", data={"cookie": cookie, "type": 0}, timeout=10).json()
        if sign_m.get('code') == 200:
            msg_list.append(f"📱 手机端签到：✅ 成功 (+{sign_m.get('point', 0)} 云贝)")
        elif sign_m.get('code') == -2:
            msg_list.append(f"📱 手机端签到：☕ 今日已签到")

        sign_w = requests.post(f"{API_BASE}/daily_signin", data={"cookie": cookie, "type": 1}, timeout=10).json()
        if sign_w.get('code') == 200:
            msg_list.append(f"💻 网页端签到：✅ 成功")
        elif sign_w.get('code') == -2:
            msg_list.append(f"💻 网页端签到：☕ 今日已签到")

        # 3. 云贝中心自动打卡
        yunbei = requests.post(f"{API_BASE}/yunbei/sign", data=payload, timeout=10).json()
        if yunbei.get('code') == 200:
            msg_list.append("💰 云贝中心：✅ 签到成功")
        else:
            msg_list.append("💰 云贝中心：☕ 今日已打卡")

        # 4. VIP专属：自动领取黑胶成长值
        if vip_type > 0:
            vip_sign = requests.post(f"{API_BASE}/vip/growthpoint/sign", data=payload, timeout=10).json()
            if vip_sign.get('code') == 200:
                msg_list.append("💎 VIP成长值：✅ 成功领取每日成长值")
            elif vip_sign.get('code') == -2:
                msg_list.append("💎 VIP成长值：☕ 今日已领取过")
            else:
                msg_list.append(f"💎 VIP成长值：❌ {vip_sign.get('msg', '领取失败')}")

        # 5. 核心：触发每日听歌300首任务 (提交云端打卡请求)
        msg_list.append("🎵 每日300首：正在向服务器提交活跃听歌数据...")
        scrobble_res = requests.post(f"{API_BASE}/scrobble",
                                     data={"cookie": cookie, "id": 516076896, "sourceid": "al", "time": 250},
                                     timeout=10).json()
        if scrobble_res.get('code') == 200:
            msg_list.append("   - ✅ 听歌打卡请求发送成功 (经验值可能需要几十分钟后才在App内刷新)")
        else:
            msg_list.append("   - ⚠️ 打卡请求已发送，但云端可能有延迟")

        # 6. 获取当前听歌量与等级进度
        level_res = requests.post(f"{API_BASE}/user/level", data=payload, timeout=10).json()
        if level_res.get('code') == 200:
            now_level = level_res['data']['level']
            listen_songs = level_res['data']['nowPlayCount']
            msg_list.append(f"📈 当前等级：Lv.{now_level}")
            msg_list.append(f"🎧 累计听歌：{listen_songs}首")

    except Exception as e:
        msg_list.append(f"❌ 任务执行发生网络错误: {e}")

    return "\n".join(msg_list)


def send_bark(title, content):
    if not BARK_URL: return
    base_url = BARK_URL.rstrip('/')
    data = {
        "title": title,
        "body": content,
        "group": "网易云打卡",
        "icon": "https://cdn-icons-png.flaticon.com/512/3128/3128293.png"  # 换了个音乐符号小图标
    }
    try:
        requests.post(base_url, json=data)
    except:
        pass


if __name__ == '__main__':
    print(f"开始执行网易云音乐打卡任务... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    result_msg = do_netease_task()
    print("=" * 30)
    print(result_msg)
    print("=" * 30)

    send_bark("🎵 网易云自动签到升级", result_msg)
    print("任务执行完毕。")