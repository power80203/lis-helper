import os
import threading
import time
import requests
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv

# 載入 .env 檔案
load_dotenv()

app = Flask(__name__)

# LINE Bot 設定
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))
_BROADCAST_USERS = os.getenv('BROADCAST_USERS', '').split(',')
keep_alive_minute = int(os.getenv('KEEP_ALIVE_MINUTE', 14))  # 保持活躍的間隔時間，預設為14分鐘

# 儲存用戶ID的列表
collected_user_ids = set()

# 群發用戶列表 (可以手動添加，或透過上面的程式自動收集)
BROADCAST_USERS = _BROADCAST_USERS

# 儲存每日提醒設定 {user_id: {'time': 'HH:MM', 'message': '提醒內容', 'enabled': True}}
daily_reminders = {}

# 預設的提醒設定
DEFAULT_REMINDER_TIME = "21:30"  # 下午9點半
DEFAULT_REMINDER_MESSAGE = "💪 記得要努力！加油！"

# 保持服務活躍 (防止 Render 免費方案休眠)
def keep_alive():
    """每14分鐘 ping 自己以保持服務活躍"""
    try:
        app_url = os.getenv('RENDER_EXTERNAL_URL', 'http://127.0.0.1:9527')
        print(app_url)
        response = requests.get(f"{app_url}/health", timeout=10)
        print(f"✅ Keep-alive ping sent: {response.status_code}")
    except Exception as e:
        print(f"❌ Keep-alive ping failed: {e}")

def keep_alive_worker():
    """背景執行緒：保持服務活躍"""
    while True:
        try:
            keep_alive()
            time.sleep(keep_alive_minute * 60)  # 每14分鐘執行一次
        except Exception as e:
            print(f"Keep-alive worker error: {e}")
            time.sleep(1 * 60)

def _msg_worker():
    """背景執行緒：定時提醒功能"""
    sent_today = {"12:30": False, "21:30": False}  # 記錄今天是否已發送
    
    while True:
        try:
            # 使用台灣時間 (UTC+8)
            import pytz
            taiwan_tz = pytz.timezone('Asia/Taipei')
            now = datetime.now(taiwan_tz)
            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")
            
            # 檢查是否是新的一天，重置發送狀態
            if hasattr(_msg_worker, 'last_date') and _msg_worker.last_date != current_date:
                sent_today = {"12:30": False, "21:30": False}
            _msg_worker.last_date = current_date

            # 中午12點半發送
            if current_time == "12:30" and not sent_today["12:30"]:
                message = f"🌞 中午好！現在是{now.strftime('%Y年%m月%d日 %H:%M')} 該執行任務喔！"
                send_startup_broadcast(message)
                sent_today["12:30"] = True
                print(f"✅ 中午提醒已發送：{current_time}")
            
            # 晚上9點半發送
            elif current_time == "21:30" and not sent_today["21:30"]:
                message = f"🌙 晚上好！現在是{now.strftime('%Y年%m月%d日 %H:%M')} 該執行任務喔！"
                send_startup_broadcast(message)
                sent_today["21:30"] = True
                print(f"✅ 晚上提醒已發送：{current_time}")
            
            # 每120秒檢查一次時間
            time.sleep(60)  # 每60秒檢查一次
            # 整點打印一次檢查時間
            if current_time.endswith(":00"):
                # 只在整點時打印
                current_time = now.strftime("%H:%M:%S")
                print(f"檢查時間：{current_time} 檢查完畢")  # 整點打印一次檢查時間
            
            
        except Exception as e:
            print(f"Message worker error: {e}")
            # 如果發生錯誤，等待1分鐘再重試
            print("等待1分鐘後重試...")
            time.sleep(60)

def send_startup_broadcast(msg="🎉 恭喜！機器人已成功啟動！"):
    """啟動時群發恭喜訊息"""
    # 合併手動設定的ID和自動收集的ID
    all_users = set(BROADCAST_USERS) | collected_user_ids
    
    if not all_users:
        print("⚠️ 沒有用戶ID可以發送訊息")
        return
    
    message = msg
    success_count = 0
    fail_count = 0
    
    for user_id in all_users:
        if user_id:  # 確保 user_id 不為空
            try:
                line_bot_api.push_message(
                    user_id,
                    TextSendMessage(text=message)
                )
                success_count += 1
                print(f"✅ 已發送給: {user_id}")
            except Exception as e:
                fail_count += 1
                print(f"❌ 發送失敗 {user_id}: {e}")
    
    print(f"🚀 啟動群發完成：成功 {success_count} 人，失敗 {fail_count} 人")

@app.route('/health')
def health():
    return 'OK', 200

@app.route('/')
def index():
    return 'LINE 機器人正在運行中！'

@app.route('/status')
def status():
    """狀態檢查端點"""
    return {
        'status': 'running',
        'collected_users': len(collected_user_ids),
        'broadcast_users': len([u for u in BROADCAST_USERS if u]),
        'total_users': len(set(BROADCAST_USERS) | collected_user_ids)
    }

# LINE Webhook
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    text = event.message.text
    
    # 自動收集用戶ID
    collected_user_ids.add(user_id)
    print(f"收集到用戶ID: {user_id}")
    
    # 如果用戶發送"我的ID"，就回傳用戶ID
    if text == "我的ID":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"🆔 你的用戶ID是：\n`{user_id}`\n\n💡 可以複製給管理員使用")
        )
        return
    
    # 如果用戶發送"所有ID"，就回傳所有收集到的ID
    if text == "所有ID":
        if collected_user_ids:
            ids_text = "\n".join(collected_user_ids)
            response = f"📋 目前收集到的用戶ID：\n\n{ids_text}\n\n📊 總共 {len(collected_user_ids)} 個用戶"
        else:
            response = "⚠️ 目前沒有收集到任何用戶ID"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=response)
        )
        return
    
    # 服務狀態查詢
    if text in ["狀態", "status", "ping"]:
        all_users = set(BROADCAST_USERS) | collected_user_ids
        response = f"""🤖 機器人狀態報告：

✅ 服務正常運行
🔄 Keep-Alive 已啟用
👥 總用戶數：{len(all_users)}
📝 收集用戶：{len(collected_user_ids)}
📋 預設用戶：{len([u for u in BROADCAST_USERS if u])}

💡 系統會每14分鐘自動保持清醒"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=response)
        )
        return
    
    # 測試群發功能 (限管理員)
    if text.startswith("群發:"):
        admin_id = os.getenv('ADMIN_USER_ID', '')
        if user_id == admin_id:
            broadcast_message = text[3:].strip()
            if broadcast_message:
                all_users = set(BROADCAST_USERS) | collected_user_ids
                success_count = 0
                fail_count = 0
                
                for target_user_id in all_users:
                    if target_user_id:
                        try:
                            line_bot_api.push_message(
                                target_user_id,
                                TextSendMessage(text=f"📢 系統通知：{broadcast_message}")
                            )
                            success_count += 1
                        except Exception as e:
                            print(f"群發失敗 {target_user_id}: {e}")
                            fail_count += 1
                
                response = f"📢 群發完成！\n✅ 成功：{success_count} 人\n❌ 失敗：{fail_count} 人"
            else:
                response = "❌ 群發訊息不能為空"
        else:
            response = "❌ 只有管理員可以使用群發功能"
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=response)
        )
        return
    
    # 預設回應
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"""👋 你好！歡迎使用 LINE 機器人！

                        📝 可用指令：
                        • 我的ID - 查看你的用戶ID
                        • 所有ID - 查看所有收集到的ID  
                        • 狀態 - 查看機器人狀態
                        • 群發:訊息 - 群發通知(限管理員)

                        🆔 你的用戶ID：{user_id}

                        🔄 系統已啟用自動保活功能，24/7 穩定運行！""")
                            )

if __name__ == "__main__":
    error_cnt = 0
    try:
        # 啟動 Keep-Alive 背景執行緒
        keep_alive_thread = threading.Thread(target=keep_alive_worker, daemon=True)
        keep_alive_thread.start()
        msg_thread = threading.Thread(target=_msg_worker, daemon=True)
        msg_thread.start()
        print("✅ Keep-Alive 功能已啟動")
        # 群發啟動訊息
        # send_startup_broadcast()
        print("🚀 服務器準備就緒！")
        print("💡 自動保活功能運行中，每14分鐘 ping 一次")
        # 啟動 Flask 應用
        app.run(host='0.0.0.0', port=9527, debug=False)
    except Exception as e:
        print(f"❌ 應用啟動失敗: {e}")
        error_cnt += 1
        time.sleep(60)