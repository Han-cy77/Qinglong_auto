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
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 配置区域 =================
BARK_URL = 'https://api.day.app/在此处添加自己的bark推送id'
TOSS_COIN_COUNT = 1  # 每天自动投币的数量 (0代表不投币，最多可填5。每天投1个最健康)
# ============================================

def get_bili_csrf(cookie):
    # 从 Cookie 中提取 B 站必需的安全校验码 bili_jct (即 csrf token)
    match = re.search(r'bili_jct=([^;]+)', cookie)
    return match.group(1) if match else ""

def get_bili_uid(cookie):
    match = re.search(r'DedeUserID=([^;]+)', cookie)
    return match.group(1) if match else "Unknown"

def setup_logger(account_name, task_name):
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    date_str = datetime.datetime.now().strftime('%Y%m%d')
    log_file = f"{log_dir}/{task_name}_{account_name}_{date_str}.log"
    
    logger = logging.getLogger(f"{task_name}_{account_name}")
    logger.setLevel(logging.INFO)
    
    # 清除旧的 handlers
    if logger.handlers:
        logger.handlers = []

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

def do_bili_task(cookie, index):
    account_uid = get_bili_uid(cookie)
    logger = setup_logger(f"Account{index}_{account_uid}", "Bilibili")
    
    # 修复1：补全了 Origin 头，这对 B 站的 POST 请求(观看/分享/投币)非常重要，防止报 -403
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com"
    }

    csrf = get_bili_csrf(cookie)
    if not csrf:
        msg = "❌ Cookie格式错误：缺少 bili_jct 字段，请重新抓取完整的 Cookie。"
        logger.error(msg)
        return f"Account {index}: {msg}"

    msg_list = []
    
    def log_and_append(msg):
        logger.info(msg)
        msg_list.append(msg)

    try:
        # 1. 检查登录状态并获取用户信息
        nav_url = "https://api.bilibili.com/x/web-interface/nav"
        res = requests.get(nav_url, headers=headers).json()
        if res.get('code') != 0:
            msg = f"❌ 登录失效，请重新抓取 B 站 Cookie！"
            log_and_append(msg)
            return "\n".join(msg_list)

        data = res['data']
        uname = data['uname']
        level = data['level_info']['current_level']
        coins = data['money']
        log_and_append(f"👤 账号：{uname} (Lv{level})")
        log_and_append(f"💰 硬币余额：{coins}枚")

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
            msg = f"❌ 获取推荐视频失败: {pop_res.get('message')}"
            log_and_append(msg)
            return "\n".join(msg_list)

        log_and_append(f"🎯 今日随机推荐视频：《{video_title[:10]}...》")

        # 4. 模拟观看视频
        if not watch_exp:
            watch_url = "https://api.bilibili.com/x/click-interface/web/heartbeat"
            watch_data = {"aid": aid, "bvid": bvid, "csrf": csrf, "played_time": 300}
            # 修复3：增加对返回结果的验证
            w_res = requests.post(watch_url, data=watch_data, headers=headers).json()
            if w_res.get('code') == 0:
                log_and_append("📺 观看任务：✅ 已完成 (+5经验)")
            else:
                log_and_append(f"📺 观看任务：❌ 失败 ({w_res.get('message')})")
            time.sleep(2)  # 修复4：增加安全延迟，防风控
        else:
            log_and_append("📺 观看任务：☕ 今日已达标")

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
                log_and_append("↗️ 分享任务：✅ 已完成 (+5经验)")
            else:
                log_and_append(f"↗️ 分享任务：❌ 失败 (code:{s_res.get('code')} - {s_res.get('message')})")
            time.sleep(2) # 安全延迟
        else:
            log_and_append("↗️ 分享任务：☕ 今日已达标")

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
                    log_and_append(f"🪙 投币任务：✅ 成功投出{TOSS_COIN_COUNT}枚硬币 (+{TOSS_COIN_COUNT * 10}经验)")
                else:
                    log_and_append(f"🪙 投币任务：❌ 失败 (code:{c_res.get('code')} - {c_res.get('message')})")
            elif coin_exp >= target_coins_exp:
                log_and_append(f"🪙 投币任务：☕ 今日投币量已达标")
            else:
                log_and_append(f"🪙 投币任务：⚠️ 硬币余额不足")
        else:
            log_and_append("🪙 投币任务：设置了不投币")

    except Exception as e:
        msg = f"❌ 执行任务时发生错误: {e}"
        log_and_append(msg)

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


def get_cookies():
    cookie_str = os.getenv('BILI_COOKIE', '')
    if not cookie_str:
        return []
    # 支持换行或&符号分割多账号
    if '&' in cookie_str and 'bili_jct' not in cookie_str.split('&')[0]:
         # 如果cookie内部本身不含&（bili_jct通常不含&，但cookie字段间用;），
         # 这里假设用户用&分割了多个完整cookie字符串
         return [c.strip() for c in cookie_str.split('&') if c.strip()]
    
    # 简单的按行分割，或者如果是一行且包含多个cookie（这种情况较少，通常是多行）
    if '\n' in cookie_str:
        return [c.strip() for c in cookie_str.split('\n') if c.strip()]
    
    # 单个账号
    return [cookie_str]

if __name__ == '__main__':
    print(f"开始执行 B 站日常任务... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    cookies = get_cookies()
    if not cookies:
        print("⚠️ 未在青龙环境变量中找到 BILI_COOKIE")
        exit(0)

    print(f"检测到 {len(cookies)} 个账号，准备并发执行...")
    
    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(do_bili_task, cookie, i+1): i for i, cookie in enumerate(cookies)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                res = future.result()
                results.append(f"--- 账号 {idx + 1} 执行结果 ---\n{res}")
            except Exception as exc:
                results.append(f"--- 账号 {idx + 1} 执行异常 ---\n{exc}")

    final_msg = "\n\n".join(results)
    print("=" * 30)
    print(final_msg)
    print("=" * 30)

    send_bark("📺 B站自动签到与升级", final_msg)
    print("所有任务执行完毕。")