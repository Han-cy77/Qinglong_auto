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
BARK_URL = os.getenv('BARK_URL', '').strip()
# 调用网易云 Node.js 开源 API 服务节点
API_BASE = os.getenv("NETEASE_API_BASE", "https://netease-cloud-music-api.fe-mm.com").rstrip('/')
REQUEST_TIMEOUT = 15
ERROR_BODY_LIMIT = 200


# ============================================

def compact_response_body(response, limit=ERROR_BODY_LIMIT):
    """返回适合写入日志的响应片段，避免把整段错误页刷进日志。"""
    try:
        body = response.text or ""
    except Exception:
        body = ""
    body = " ".join(body.split())
    return body[:limit] or "<empty body>"


def get_nested(data, path):
    value = data
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def first_value(data, *paths, default=None):
    """按候选路径取值，保留 0/False 等有效值，修复日志显示“未知”。"""
    for path in paths:
        value = get_nested(data, path)
        if value is not None:
            return value
    return default


def response_code(payload):
    if not isinstance(payload, dict):
        return None
    return first_value(payload, ("code",), ("data", "code"))


def response_message(payload, default="接口未返回消息"):
    return first_value(
        payload,
        ("message",),
        ("msg",),
        ("data", "message"),
        ("data", "msg"),
        default=default,
    )


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def collect_unget_ids(payload):
    """从 /vip/tasks 的不同版本响应中提取待领取成长值任务 ID。"""
    result = []

    def walk(value):
        if isinstance(value, dict):
            ids = value.get("unGetIds")
            if isinstance(ids, str):
                result.extend(item.strip() for item in ids.split(',') if item.strip())
            elif isinstance(ids, list):
                result.extend(str(item).strip() for item in ids if str(item).strip())
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return list(dict.fromkeys(result))


def collect_task_items(payload):
    """兼容不同 API 版本，从任务响应中收集包含 userTaskId 的任务项。"""
    items = []

    def walk(value):
        if isinstance(value, dict):
            if value.get("userTaskId") is not None:
                items.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    unique = {}
    for item in items:
        unique[str(item["userTaskId"])] = item
    return list(unique.values())


def task_status(task):
    return task.get("taskStatus", task.get("status", task.get("taskStatusCode")))


def is_claimable_task(task):
    status = task_status(task)
    if task.get("needReceive") is True:
        return True
    if isinstance(status, str):
        status = status.strip().lower()
    return status in (1, 2, 20, "1", "2", "20", "completed", "done", "finished")


def api_post(path, data, log, timeout=REQUEST_TIMEOUT, log_errors=True):
    """统一处理网易云 API 的网络、HTTP 和 JSON 错误。"""
    url = f"{API_BASE}/{path.lstrip('/')}"
    try:
        response = requests.post(url, data=data, timeout=timeout)
    except requests.RequestException as exc:
        if log_errors:
            log(f"❌ 请求失败 [{path}]：{exc.__class__.__name__}: {exc}")
        return None
    except Exception as exc:
        if log_errors:
            log(f"❌ 请求发生未知异常 [{path}]：{exc}")
        return None

    if not response.ok:
        if log_errors:
            log(
                f"❌ 接口返回 HTTP {response.status_code} [{path}]，"
                f"响应片段：{compact_response_body(response)}"
            )
        return None

    try:
        payload = response.json()
    except ValueError:
        if log_errors:
            log(
                f"❌ 接口返回非 JSON [{path}]，状态码：{response.status_code}，"
                f"响应片段：{compact_response_body(response)}"
            )
        return None

    if not isinstance(payload, dict):
        if log_errors:
            log(f"❌ 接口 JSON 类型异常 [{path}]：实际为 {type(payload).__name__}")
        return None
    return payload


def get_300_random_songs(cookie, log):
    """从各大榜单获取300首不重复的歌曲ID用于随机打卡"""
    song_ids = set()
    # 榜单ID：热歌榜, 新歌榜, 飙升榜, 原创榜
    playlist_ids = [3778678, 3779629, 19723756, 2884035]

    for pid in playlist_ids:
        res = api_post("/playlist/track/all", {"cookie": cookie, "id": pid}, log)
        if response_code(res) in (0, 200):
            songs = res.get('songs') or first_value(res, ("data", "songs"), default=[])
            if isinstance(songs, list):
                for song in songs:
                    if isinstance(song, dict) and song.get('id') is not None:
                        song_ids.add(song['id'])
        if len(song_ids) >= 300:
            break

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
    log_file = f"{log_dir}/{task_name}_{safe_name or 'Unknown'}_{date_str}.log"
    
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

    def attach_logger(new_logger):
        """账号昵称只有登录成功后才知道，先把登录阶段的诊断补写入日志。"""
        nonlocal logger
        logger = new_logger
        for content in msg_list:
            logger.info(content)

    if not cookie:
        return "⚠️ 网易云任务失败：未在青龙环境变量中找到 NETEASE_COOKIE"

    payload = {"cookie": cookie}

    try:
        # 1. 检查登录状态
        login_res = api_post("/login/status", payload, log_and_append)
        profile = first_value(login_res, ("data", "profile"), ("profile",), default=None)
        if response_code(login_res) not in (0, 200) or not isinstance(profile, dict):
            attach_logger(setup_logger(f"Account{index}", "Netease"))
            log_and_append(
                f"❌ Cookie已失效或格式不正确：{response_message(login_res)}；"
                "请重新抓取 MUSIC_U 和 __csrf。"
            )
            return "\n".join(msg_list)

        nickname = profile.get('nickname') or f"Account{index}"
        attach_logger(setup_logger(nickname, "Netease"))
        vip_type = to_int(profile.get('vipType'), 0)
        vip_str = "👑黑胶VIP" if vip_type > 0 else "👤普通用户"
        log_and_append(f"账号：{nickname} ({vip_str})")

        # 2. 手机端与网页端双端签到
        sign_m = api_post("/daily_signin", {"cookie": cookie, "type": 0}, log_and_append)
        sign_m_code = response_code(sign_m)
        if sign_m_code in (0, 200):
            point = first_value(sign_m, ("point",), ("data", "point"), default=0)
            if to_int(point, 0) > 0:
                log_and_append(f"📱 手机端签到：✅ 成功 (+{point} 经验)")
            else:
                log_and_append("📱 手机端签到：✅ 已检查，本次新增 0 经验（可能今日已签到）")
        elif sign_m_code == -2:
            log_and_append("📱 手机端签到：☕ 今日已签到")
        elif sign_m is not None:
            log_and_append(f"📱 手机端签到：❌ {response_message(sign_m)}")

        sign_w = api_post("/daily_signin", {"cookie": cookie, "type": 1}, log_and_append)
        sign_w_code = response_code(sign_w)
        if sign_w_code in (0, 200):
            log_and_append("💻 网页端签到：✅ 成功")
        elif sign_w_code == -2:
            log_and_append("💻 网页端签到：☕ 今日已签到")
        elif sign_w is not None:
            log_and_append(f"💻 网页端签到：❌ {response_message(sign_w)}")

        # 3. 云贝中心自动打卡与任务领取
        yunbei = api_post("/yunbei/sign", payload, log_and_append)
        if response_code(yunbei) in (0, 200):
            yb_point = first_value(yunbei, ("point",), ("data", "point"), default=0)
            if to_int(yb_point, 0) > 0:
                log_and_append(f"💰 云贝签到：✅ 签到成功 (+{yb_point} 云贝)")
            else:
                log_and_append("💰 云贝签到：✅ 已检查，本次新增 0 云贝（可能今日已签到）")
        elif yunbei is not None:
            log_and_append(f"💰 云贝签到：❌ {response_message(yunbei)}")

        tasks_res = api_post("/yunbei/tasks/todo", payload, log_and_append)
        if response_code(tasks_res) in (0, 200):
            tasks = collect_task_items(tasks_res)
            status_counts = {}
            for task in tasks:
                status = str(task_status(task) if task_status(task) is not None else "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            if status_counts:
                status_text = ", ".join(f"{key}:{value}" for key, value in sorted(status_counts.items()))
                log_and_append(f"🎁 云贝任务：接口返回 {len(tasks)} 个任务（状态 {status_text}）")
            else:
                log_and_append("🎁 云贝任务：接口返回 0 个任务")
            receipt_count = 0
            total_reward = 0
            claimable_count = 0
            for task in tasks:
                if not is_claimable_task(task):
                    continue
                claimable_count += 1
                task_id = task.get('userTaskId')
                if task_id is None:
                    continue
                finish_payload = {"cookie": cookie, "userTaskId": str(task_id)}
                if task.get('depositCode') is not None:
                    finish_payload["depositCode"] = str(task['depositCode'])
                rcv_res = api_post(
                    "/yunbei/task/finish",
                    finish_payload,
                    log_and_append,
                    timeout=5,
                )
                if response_code(rcv_res) in (0, 200):
                    receipt_count += 1
                    task_point = first_value(
                        task,
                        ("reward",),
                        ("taskPoint",),
                        ("point",),
                        default=0,
                    )
                    total_reward += to_int(task_point, 0)
            if receipt_count:
                log_and_append(f"🎁 云贝任务：✅ 成功领取 {receipt_count} 个任务奖励 (+{total_reward} 云贝)")
            elif claimable_count:
                log_and_append(f"🎁 云贝任务：⚠️ 检测到 {claimable_count} 个可领取任务，但领取接口未成功")
            elif tasks:
                log_and_append("🎁 云贝任务：当前没有已完成、可领取的任务；需要先完成对应任务动作")
            else:
                log_and_append("🎁 云贝任务：当前没有待处理任务")
        elif tasks_res is not None:
            log_and_append(f"🎁 云贝任务：❌ {response_message(tasks_res)}")

        # 4. VIP专属：领取已完成的黑胶成长值任务
        if vip_type > 0:
            vip_tasks = api_post("/vip/tasks", payload, log_and_append)
            vip_claim = api_post("/vip/growthpoint/getall", payload, log_and_append)
            if response_code(vip_claim) in (0, 200):
                growth_score = first_value(
                    vip_claim,
                    ("growthPoint",),
                    ("score",),
                    ("point",),
                    ("data", "growthPoint"),
                    ("data", "score"),
                    ("data", "point"),
                    default=0,
                )
                if to_int(growth_score, 0) > 0:
                    log_and_append(f"💎 VIP成长值：✅ 领取成功 (+{growth_score} 成长值)")
                else:
                    log_and_append("💎 VIP成长值：✅ 已检查，本次没有新增成长值")
            else:
                vip_ids = collect_unget_ids(vip_tasks)
                if vip_ids:
                    vip_claim = api_post(
                        "/vip/growthpoint/get",
                        {"cookie": cookie, "ids": ",".join(vip_ids)},
                        log_and_append,
                    )
                    if response_code(vip_claim) in (0, 200):
                        growth_score = first_value(
                            vip_claim,
                            ("growthPoint",),
                            ("score",),
                            ("point",),
                            ("data", "growthPoint"),
                            ("data", "score"),
                            ("data", "point"),
                            default=0,
                        )
                        if to_int(growth_score, 0) > 0:
                            log_and_append(f"💎 VIP成长值：✅ 领取成功 (+{growth_score} 成长值)")
                        else:
                            log_and_append("💎 VIP成长值：✅ 已检查，本次没有新增成长值")
                    elif vip_claim is not None:
                        log_and_append(f"💎 VIP成长值：❌ {response_message(vip_claim, '领取失败')}")
                elif vip_tasks is not None:
                    log_and_append("💎 VIP成长值：☕ 暂无可领取任务")

        # 5. 核心：触发每日听歌300首任务 (随机抽取榜单歌曲)
        log_and_append("🎵 每日300首：正在获取各大榜单并模拟随机播放...")
        songs_to_play = get_300_random_songs(cookie, log_and_append)

        if not songs_to_play:
            log_and_append("   - ❌ 获取歌单失败，无法执行300首任务")
        else:
            success_cnt = 0
            failed_cnt = 0
            for sid in songs_to_play:
                play_time = random.randint(60, 250)
                res = api_post(
                    "/scrobble",
                    {"cookie": cookie, "id": sid, "sourceid": "al", "time": play_time},
                    log_and_append,
                    timeout=5,
                    log_errors=False,
                )
                if response_code(res) in (0, 200):
                    success_cnt += 1
                else:
                    failed_cnt += 1
                time.sleep(0.15)
            log_and_append(
                f"   - ✅ 成功提交 {success_cnt}/{len(songs_to_play)} 首歌的播放记录"
                + (f"，失败 {failed_cnt} 首" if failed_cnt else "")
            )

        # 6. 获取当前听歌量与等级进度
        level_res = api_post("/user/level", payload, log_and_append)
        if response_code(level_res) in (0, 200):
            level_data = first_value(level_res, ("data",), default={})
            if isinstance(level_data, dict):
                now_level = first_value(level_data, ("level",), default="未知")
                listen_songs = first_value(level_data, ("nowPlayCount",), ("playCount",), default="未知")
                log_and_append(f"📈 当前等级：Lv.{now_level}")
                log_and_append(f"🎧 累计听歌：{listen_songs}首 (听歌任务经验值和数量稍后在App内刷新)")

    except Exception as e:
        if not logger:
            attach_logger(setup_logger(f"Account{index}_Error", "Netease"))
        log_and_append(f"❌ 任务执行发生未知错误：{e.__class__.__name__}: {e}")

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
        requests.post(base_url, json=data, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        pass

def get_cookies():
    cookie_str = os.getenv('NETEASE_COOKIE', '')
    if not cookie_str:
        return []
    # 完整网易云 Cookie 的 P_INFO 等字段本身可能含有 '&'，不能直接按 '&' 拆分。
    # 多账号请使用换行分隔；检测到完整 MUSIC_U + __csrf 后优先按单账号处理。
    normalized = cookie_str.replace('\r\n', '\n').replace('\r', '\n').strip()
    lines = [line.strip() for line in normalized.split('\n') if line.strip()]
    if len(lines) > 1 and all('MUSIC_U=' in line and '__csrf=' in line for line in lines):
        return lines

    if 'MUSIC_U=' in normalized and '__csrf=' in normalized:
        return [normalized.replace('\n', '')]

    return [normalized]

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
