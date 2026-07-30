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
BARK_URL = os.getenv('BARK_URL', '').strip()
TOSS_COIN_COUNT = 1  # 每天自动投币的数量 (0代表不投币，最多可填5。每天投1个最健康)
REQUEST_TIMEOUT = 15
ERROR_BODY_LIMIT = 200
# ============================================

def get_bili_csrf(cookie):
    # 从 Cookie 中提取 B 站必需的安全校验码 bili_jct (即 csrf token)
    match = re.search(r'bili_jct=([^;]+)', cookie)
    return match.group(1) if match else ""

def get_bili_uid(cookie):
    match = re.search(r'DedeUserID=([^;]+)', cookie)
    return match.group(1) if match else "Unknown"


def normalize_number(value):
    """将接口中的数字字符串转成便于计算和展示的数字。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def get_bili_experience(data):
    """读取 B站 nav 接口的当前经验值，兼容不同字段命名。"""
    if not isinstance(data, dict):
        return None
    level_exp = data.get("level_exp")
    level_info = data.get("level_info")
    candidates = []
    if isinstance(level_exp, dict):
        candidates.extend(level_exp.get(key) for key in ("current_exp", "currentExp", "exp"))
    if isinstance(level_info, dict):
        candidates.extend(level_info.get(key) for key in ("current_exp", "currentExp", "exp"))
    candidates.extend(data.get(key) for key in ("current_exp", "currentExp", "exp"))
    for value in candidates:
        normalized = normalize_number(value)
        if normalized is not None:
            return normalized
    return None


def format_experience(value):
    return str(value) if value is not None else "未知"


def compact_response_body(response, limit=ERROR_BODY_LIMIT):
    """返回适合写入日志的响应片段，避免把整段 HTML/错误页刷进日志。"""
    try:
        body = response.text or ""
    except Exception:
        body = ""
    body = " ".join(body.split())
    return body[:limit] or "<empty body>"


def request_json(method, url, log, **kwargs):
    """请求并严格校验 JSON，避免 Issue #6 中的裸 JSONDecodeError。"""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    try:
        response = requests.request(method.upper(), url, **kwargs)
    except requests.RequestException as exc:
        log(f"❌ 请求失败 [{method.upper()} {url}]：{exc.__class__.__name__}: {exc}")
        return None
    except Exception as exc:
        log(f"❌ 请求发生未知异常 [{method.upper()} {url}]：{exc}")
        return None

    content_type = response.headers.get("Content-Type", "")
    if not response.ok:
        log(
            f"❌ 接口返回 HTTP {response.status_code} [{method.upper()} {url}]"
            f"，响应片段：{compact_response_body(response)}"
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        log(
            f"❌ 接口返回非 JSON [{method.upper()} {url}]"
            f"，状态码：{response.status_code}，Content-Type：{content_type or '未提供'}"
            f"，响应片段：{compact_response_body(response)}"
        )
        return None

    if not isinstance(payload, dict):
        log(
            f"❌ 接口 JSON 类型异常 [{method.upper()} {url}]："
            f"期望 object，实际为 {type(payload).__name__}"
        )
        return None
    return payload

def setup_logger(account_name, task_name):
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    date_str = datetime.datetime.now().strftime('%Y%m%d')
    safe_name = "".join(c for c in str(account_name) if c.isalnum() or c in ('_', '-')) or "Unknown"
    log_file = f"{log_dir}/{task_name}_{safe_name}_{date_str}.log"
    
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
    
    # 修复：极大地丰富了请求头，降低被 WAF (防火墙) 风控的概率
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive"
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

    nav_url = "https://api.bilibili.com/x/web-interface/nav"
    experience_before = None

    def finish_report():
        """任务结束前重新读取经验值，并把变化加入最终报告。"""
        if experience_before is None:
            log_and_append("📊 经验值：执行前无法从接口读取")
            return "\n".join(msg_list)

        final_res = request_json('GET', nav_url, log_and_append, headers=headers)
        final_data = final_res.get('data') if final_res and final_res.get('code') == 0 else None
        experience_after = get_bili_experience(final_data)
        if experience_after is None:
            log_and_append(
                f"📊 经验值：执行前 {format_experience(experience_before)}，执行后无法读取"
            )
            return "\n".join(msg_list)

        delta = experience_after - experience_before
        delta_text = f"+{delta}" if delta >= 0 else str(delta)
        log_and_append(
            f"📊 经验值：执行前 {format_experience(experience_before)} → "
            f"执行后 {format_experience(experience_after)}，本次增加 {delta_text}"
        )
        return "\n".join(msg_list)

    try:
        # 1. 检查登录状态并获取用户信息
        res = request_json('GET', nav_url, log_and_append, headers=headers)
        if not res or res.get('code') != 0:
            code = res.get('code') if res else '无响应'
            api_message = res.get('message', '未返回接口消息') if res else '请查看上面的请求诊断'
            msg = f"❌ 登录失效或接口被拦截（code: {code}，message: {api_message}）"
            log_and_append(msg)
            return "\n".join(msg_list)

        data = res.get('data')
        if not isinstance(data, dict):
            log_and_append("❌ 登录接口返回缺少 data 对象，无法读取账号信息")
            return "\n".join(msg_list)
        uname = data.get('uname', 'Unknown')
        level = data.get('level_info', {}).get('current_level', 'Unknown')
        coins = data.get('money', 0)
        experience_before = get_bili_experience(data)
        log_and_append(f"👤 账号：{uname} (Lv{level})")
        log_and_append(f"💰 硬币余额：{coins}枚")

        # 2. 获取今日任务完成状态
        reward_url = "https://api.bilibili.com/x/member/web/exp/reward"
        reward_res = request_json('GET', reward_url, log_and_append, headers=headers)
        if not reward_res:
            return finish_report() # 接口被拦截，直接终止该账号后续任务
            
        reward_data = reward_res.get('data', {})
        watch_exp = reward_data.get('watch')  # 是否完成观看
        share_exp = reward_data.get('share')  # 是否完成分享
        coin_exp = reward_data.get('coins', 0)  # 今日已投币获得的经验

        # 3. 获取 B 站全站热门推荐视频，避免重复投币
        popular_url = "https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1"
        pop_res = request_json('GET', popular_url, log_and_append, headers=headers)

        if pop_res and pop_res.get('code') == 0:
            video_list = (pop_res.get('data') or {}).get('list')
            if not isinstance(video_list, list) or not video_list:
                log_and_append("❌ 推荐接口返回空视频列表，无法执行观看/分享任务")
                return finish_report()
            random_video = random.choice(video_list)
            bvid = random_video.get('bvid')
            aid = random_video.get('aid')
            video_title = random_video.get('title', '未命名视频')
            if not bvid or not aid:
                log_and_append("❌ 推荐接口返回的视频缺少 bvid/aid，无法执行任务")
                return finish_report()
        else:
            msg = f"❌ 获取推荐视频失败：{pop_res.get('message') if pop_res else '请查看上面的请求诊断'}"
            log_and_append(msg)
            return finish_report()

        log_and_append(f"🎯 今日随机推荐视频：《{video_title[:10]}...》")

        # 4. 模拟观看视频
        if not watch_exp:
            watch_url = "https://api.bilibili.com/x/click-interface/web/heartbeat"
            watch_data = {"aid": aid, "bvid": bvid, "csrf": csrf, "played_time": 300}
            
            w_res = request_json('POST', watch_url, log_and_append, data=watch_data, headers=headers)
            if w_res and w_res.get('code') == 0:
                log_and_append("📺 观看任务：✅ 已完成 (+5经验)")
            else:
                err_msg = w_res.get('message') if w_res else '接口解析失败'
                log_and_append(f"📺 观看任务：❌ 失败 ({err_msg})")
            time.sleep(2)  # 安全延迟
        else:
            log_and_append("📺 观看任务：☕ 今日已达标")

        # 5. 模拟分享视频
        if not share_exp:
            share_url = "https://api.bilibili.com/x/web-interface/share/add"
            share_data = {
                "aid": aid,
                "bvid": bvid,
                "csrf": csrf,
                "share_channel": "copy"
            }
            s_res = request_json('POST', share_url, log_and_append, data=share_data, headers=headers)
            if s_res and s_res.get('code') == 0:
                log_and_append("↗️ 分享任务：✅ 已完成 (+5经验)")
            else:
                err_msg = s_res.get('message') if s_res else '接口解析失败'
                log_and_append(f"↗️ 分享任务：❌ 失败 ({err_msg})")
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
                c_res = request_json('POST', coin_url, log_and_append, data=coin_data, headers=headers)
                if c_res and c_res.get('code') == 0:
                    log_and_append(f"🪙 投币任务：✅ 成功投出{TOSS_COIN_COUNT}枚硬币 (+{TOSS_COIN_COUNT * 10}经验)")
                else:
                    err_msg = f"code:{c_res.get('code')} - {c_res.get('message')}" if c_res else "接口解析失败"
                    log_and_append(f"🪙 投币任务：❌ 失败 ({err_msg})")
            elif coin_exp >= target_coins_exp:
                log_and_append(f"🪙 投币任务：☕ 今日投币量已达标")
            else:
                log_and_append(f"🪙 投币任务：⚠️ 硬币余额不足")
        else:
            log_and_append("🪙 投币任务：设置了不投币")

    except Exception as e:
        msg = f"❌ 执行任务时发生未知错误: {e}"
        log_and_append(msg)

    return finish_report()


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
        requests.post(base_url, json=data, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        pass


def get_cookies():
    cookie_str = os.getenv('BILI_COOKIE', '')
    if not cookie_str:
        return []
    # 支持换行或&符号分割多账号
    if '&' in cookie_str and 'bili_jct' not in cookie_str.split('&')[0]:
         return [c.strip() for c in cookie_str.split('&') if c.strip()]
    
    if '\n' in cookie_str:
        return [c.strip() for c in cookie_str.split('\n') if c.strip()]
    
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
