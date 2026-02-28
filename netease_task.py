# -*- coding: utf-8 -*-
"""
new Env('网易云全能打卡');
cron: 30 9 * * *
"""

import requests
import os
import time
import random
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 配置区域 =================
BARK_URL = 'https://api.day.app/在此处添加自己的bark推送id'
# 调用网易云 Node.js 开源 API 服务节点
API_BASE = "https://netease-cloud-music-api.fe-mm.com"


# ============================================

def get_300_random_songs(cookie):
    """从各大榜单获取300首不重复的歌曲ID用于随机打卡"""
    song_ids = set()
    # 榜单ID：热歌榜, 新歌榜, 飙升榜, 原创榜
    playlist_ids = [3778678, 3779629, 19723756, 2884035]

    for pid in playlist_ids:
        try:
            res = requests.post(f"{API_BASE}/playlist/track/all", data={"cookie": cookie, "id": pid}, timeout=10).json()
            if res.get('code') == 200:
                for song in res.get('songs', []):
                    song_ids.add(song['id'])
            if len(song_ids) >= 300:
                break
        except Exception:
            continue

    song_list = list(song_ids)
    random.shuffle(song_list)
    return song_list[:300]

def setup_logger(account_name, task_name):
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 替换文件名中的非法字符
    safe_name = "".join([c for c in account_name if c.isalnum() or c in ('_', '-')])
    date_str = datetime.datetime.now().strftime('%Y%m%d')
    log_file = f"{log_dir}/{task_name}_{safe_name}_{date_str}.log"
    
    logger = logging.getLogger(f"{task_name}_{safe_name}")
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        logger.handlers = []

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

def do_netease_task(cookie, index):
    msg_list = []
    logger = None
    
    # 暂存日志，等获取到昵称后再初始化 Logger 并写入文件
    def log_and_append(content):
        msg_list.append(content)
        if logger:
            logger.info(content)

    if not cookie:
        return "⚠️ 网易云任务失败：未在青龙环境变量中找到 NETEASE_COOKIE"

    payload = {"cookie": cookie}

    try:
        # 1. 检查登录状态
        login_res = requests.post(f"{API_BASE}/login/status", data=payload, timeout=15).json()
        if login_res.get('data', {}).get('code') != 200 or not login_res.get('data', {}).get('profile'):
            # 登录失败，使用默认名称初始化 Logger
            logger = setup_logger(f"Account{index}", "Netease")
            log_and_append("❌ Cookie已失效或格式不正确，请重新去浏览器抓取 MUSIC_U 和 __csrf！")
            return "\n".join(msg_list)

        nickname = login_res['data']['profile']['nickname']
        # 获取到昵称，初始化 Logger
        logger = setup_logger(nickname, "Netease")
        
        vip_type = login_res['data']['profile'].get('vipType', 0)
        vip_str = "👑黑胶VIP" if vip_type > 0 else "👤普通用户"
        log_and_append(f"账号：{nickname} ({vip_str})")

        # 2. 手机端与网页端双端签到
        sign_m = requests.post(f"{API_BASE}/daily_signin", data={"cookie": cookie, "type": 0}, timeout=10).json()
        if sign_m.get('code') == 200:
            point = sign_m.get('point', 0)
            log_and_append(f"📱 手机端签到：✅ 成功 (+{point} 云贝)")
        elif sign_m.get('code') == -2:
            log_and_append(f"📱 手机端签到：☕ 今日已签到")

        sign_w = requests.post(f"{API_BASE}/daily_signin", data={"cookie": cookie, "type": 1}, timeout=10).json()
        if sign_w.get('code') == 200:
            log_and_append(f"💻 网页端签到：✅ 成功")
        elif sign_w.get('code') == -2:
            log_and_append(f"💻 网页端签到：☕ 今日已签到")

        # 3. 云贝中心自动打卡与任务领取
        yunbei = requests.post(f"{API_BASE}/yunbei/sign", data=payload, timeout=10).json()
        if yunbei.get('code') == 200:
            # 尝试提取领取的云贝数量
            yb_point = yunbei.get('data', {}).get('point') or yunbei.get('point', '未知')
            log_and_append(f"💰 云贝签到：✅ 签到成功 (+{yb_point} 云贝)")
        else:
            log_and_append("💰 云贝签到：☕ 今日已打卡")

        # 尝试自动领取云贝任务奖励
        try:
            tasks_res = requests.post(f"{API_BASE}/yunbei/tasks/todo", data=payload, timeout=10).json()
            if tasks_res.get('code') == 200:
                receipt_count = 0
                total_reward = 0
                for task in tasks_res.get('data', []):
                    # status 1 代表已完成可领取，0 可能是其他状态也尝试一下
                    if task.get('taskStatus') in [0, 1]:
                        rcv_res = requests.post(f"{API_BASE}/yunbei/task/receipt",
                                                data={"cookie": cookie, "userTaskId": str(task.get('userTaskId'))},
                                                timeout=5).json()
                        if rcv_res.get('code') == 200:
                            receipt_count += 1
                            # 累加任务奖励的具体云贝数
                            task_point = task.get('reward') or task.get('taskPoint') or 0
                            total_reward += int(task_point)
                if receipt_count > 0:
                    log_and_append(f"🎁 云贝任务：✅ 成功领取 {receipt_count} 个任务奖励 (+{total_reward} 云贝)")
                else:
                    log_and_append(f"🎁 云贝任务：☕ 暂无可领取的任务奖励")
        except Exception as e:
            log_and_append(f"🎁 云贝任务：⚠️ 奖励领取请求失败 ({e})")

        # 4. VIP专属：自动领取黑胶成长值
        if vip_type > 0:
            vip_sign = requests.post(f"{API_BASE}/vip/growthpoint/sign", data=payload, timeout=10).json()
            if vip_sign.get('code') == 200:
                # 解析获得的成长值
                growth_score = vip_sign.get('data', {}).get('score') or vip_sign.get('point', '未知')
                log_and_append(f"💎 VIP成长值：✅ 签到成功 (+{growth_score} 成长值)")
            elif vip_sign.get('code') == -2:
                log_and_append("💎 VIP成长值：☕ 今日已签到过")
            else:
                log_and_append(f"💎 VIP成长值：❌ {vip_sign.get('msg', '领取失败')}")

            # 尝试获取 VIP 任务奖励并额外获取成长值 (部分隐藏成长值在这个接口)
            try:
                vip_tasks = requests.post(f"{API_BASE}/vip/tasks", data=payload, timeout=10).json()
                # 这个接口调用本身就是一种“领取”，能触发额外成长值入账
            except:
                pass

        # 5. 核心：触发每日听歌300首任务 (随机抽取榜单歌曲)
        log_and_append("🎵 每日300首：正在获取各大榜单并模拟随机播放...")
        songs_to_play = get_300_random_songs(cookie)

        if not songs_to_play:
            log_and_append("   - ❌ 获取歌单失败，无法执行300首任务")
        else:
            success_cnt = 0
            for sid in songs_to_play:
                try:
                    # 随机生成模拟听歌时间 (60秒到250秒之间)
                    play_time = random.randint(60, 250)
                    res = requests.post(f"{API_BASE}/scrobble",
                                        data={"cookie": cookie, "id": sid, "sourceid": "al", "time": play_time},
                                        timeout=5).json()
                    if res.get('code') == 200:
                        success_cnt += 1
                    # 稍微延迟，防止请求过快被拦截 (如果青龙运行超时可以适当调低此数值)
                    time.sleep(0.15)
                except Exception:
                    continue
            log_and_append(f"   - ✅ 成功提交 {success_cnt}/{len(songs_to_play)} 首歌的播放记录")

        # 6. 获取当前听歌量与等级进度
        level_res = requests.post(f"{API_BASE}/user/level", data=payload, timeout=10).json()
        if level_res.get('code') == 200:
            now_level = level_res['data']['level']
            listen_songs = level_res['data']['nowPlayCount']
            log_and_append(f"📈 当前等级：Lv.{now_level}")
            log_and_append(f"🎧 累计听歌：{listen_songs}首 (听歌任务经验值和数量稍后在App内刷新)")

    except Exception as e:
        if not logger:
             logger = setup_logger(f"Account{index}_Error", "Netease")
        log_and_append(f"❌ 任务执行发生错误: {e}")

    return "\n".join(msg_list)


def send_bark(title, content):
    if not BARK_URL: return
    base_url = BARK_URL.rstrip('/')
    data = {
        "title": title,
        "body": content,
        "group": "网易云打卡",
        "icon": "https://cdn-icons-png.flaticon.com/512/3128/3128293.png"
    }
    try:
        requests.post(base_url, json=data)
    except:
        pass

def get_cookies():
    cookie_str = os.getenv('NETEASE_COOKIE', '')
    if not cookie_str:
        return []
    # 支持换行或&符号分割多账号
    if '&' in cookie_str and 'MUSIC_U' not in cookie_str.split('&')[0]:
         # 如果cookie内部本身不含&（网易云cookie通常用;分隔），
         # 这里假设用户用&分割了多个完整cookie字符串
         return [c.strip() for c in cookie_str.split('&') if c.strip()]
    
    # 简单的按行分割
    if '\n' in cookie_str:
        return [c.strip() for c in cookie_str.split('\n') if c.strip()]
    
    # 单个账号
    return [cookie_str]

if __name__ == '__main__':
    print(f"开始执行网易云音乐打卡任务... {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    cookies = get_cookies()
    if not cookies:
        print("⚠️ 未在青龙环境变量中找到 NETEASE_COOKIE")
        exit(0)

    print(f"检测到 {len(cookies)} 个账号，准备并发执行...")

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(do_netease_task, cookie, i+1): i for i, cookie in enumerate(cookies)}
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

    send_bark("🎵 网易云自动签到", final_msg)
    print("所有任务执行完毕。")