import os
import re
import time

import openai
from openai.error import RateLimitError
from werobot.messages.messages import TextMessage
from werobot.robot import WeRoBot
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=(os.cpu_count() or 1) * 2 + 1)

openai.api_key = os.getenv("OPENAI_API_KEY")
robot = WeRoBot(token=os.getenv("TOKEN"))
robot.config["APP_ID"] = os.getenv("APP_ID")
robot.config["APP_SECRET"] = os.getenv("APP_SECRET")
robot.config["ENCODING_AES_KEY"] = os.getenv("ENCODING_AES_KEY")
MAX_RETRIES = 3


def rate_limit_wrapper(func, *args, **kwargs):
    for _ in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except RateLimitError:
            time.sleep(1)


def gpt_reply(pre_messages: list, user_id: int):
    rsp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo", messages=pre_messages, user=str(user_id)
    )
    return rsp.choices[0].message["content"].strip("\n").strip()  # type: ignore


@robot.subscribe
def intro():
    return "欢迎订阅~\n请向我回复文字以开始对话~\n回复 /system 以设置系统消息~\n回复 /reset 以重置会话，避免会话过长等待时间较久~"


@robot.filter(re.compile("^/system$"))
def set_system_message(message: TextMessage, session: dict):
    session[f"{message.source}_system_flag"] = True
    session[message.source] = []
    return "请向我回复文字以设置系统消息~"


@robot.filter(re.compile("^/reset$"))
def reset_session(message: TextMessage):
    message_list = robot.session_storage.get(f"{message.source}_message")
    if message_list and len(message_list) > 1 and message_list[0]["role"] == "system":
        message_list = message_list[:1]
        robot.session_storage[f"{message.source}_message"] = message_list
        robot.logger.info(message_list)
        return "已重置system外的会话~\n再次请求 /reset 以重置所有会话~"
    robot.session_storage[f"{message.source}_message"] = []
    return "会话已重置，请向我回复文字以开始对话~\n回复 /system 以设置系统消息~\n回复 /reset 以重置会话，避免会话过长等待时间较久~"


@robot.text
def reply(message: TextMessage, session):
    if session.get(f"{message.source}_system_flag"):
        session[f"{message.source}_system_flag"] = False
        robot.session_storage[f"{message.source}_message"] = [
            {"role": "system", "content": message.content}
        ]
        return "已设置为 {}\n请向我回复文字以开始对话~".format(message.content)
    # 提交回复任务到线程池，先行返回空字符串
    executor.submit(reply_task, message)
    return ""


def reply_task(message: TextMessage):
    message_list = robot.session_storage.get(f"{message.source}_message")
    if not message_list:
        message_list = []
    message_list.append({"role": "user", "content": message.content})
    robot.logger.info(message_list)
    reply = rate_limit_wrapper(gpt_reply, message_list, message.source)
    robot.logger.info(reply)
    message_list.append({"role": "assistant", "content": reply})
    # 线程池无法直接用传递的session，所以直接用session_storage
    robot.session_storage[f"{message.source}_message"] = message_list
    # 认证号才可以发送客服消息
    robot.client.send_text_message(message.source, reply)


if __name__ == "__main__":
    robot.run()
